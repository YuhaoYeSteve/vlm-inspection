import json
from types import SimpleNamespace
from typing import ClassVar, TypeAlias

import pytest

from app.domain.results import Verdict
from app.services.ark import (
    ArkInspectionService,
    ArkRequestError,
    ArkRuntimeConfig,
    KeychainArkInspectionService,
    MalformedInspectionResult,
)

VALID = {
    "schema_version": 1,
    "image_valid": True,
    "unverifiable_reason": None,
    "verdict": "pass",
    "summary": "uniform is clean",
    "findings": [
        {
            "rule_id": "uniform",
            "rule_name": "uniform",
            "status": "pass",
            "observation": "no visible stain",
            "visual_evidence": "the front of the shirt is visible",
        }
    ],
}

RESPONSES_USAGE = SimpleNamespace(
    input_tokens=100,
    input_tokens_details=SimpleNamespace(cached_tokens=40),
    output_tokens=20,
    output_tokens_details=SimpleNamespace(reasoning_tokens=4),
    total_tokens=120,
)

CHAT_USAGE = SimpleNamespace(
    prompt_tokens=80,
    prompt_tokens_details=SimpleNamespace(cached_tokens=30),
    completion_tokens=15,
    completion_tokens_details=SimpleNamespace(reasoning_tokens=3),
    total_tokens=95,
)

FakeOutput: TypeAlias = str | tuple[str, object] | Exception


class FakeResponses:
    def __init__(self, outputs: list[FakeOutput]) -> None:
        self.outputs = outputs
        self.calls: list[dict] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        output = self.outputs.pop(0)
        if isinstance(output, Exception):
            raise output
        if isinstance(output, tuple):
            text, usage = output
        else:
            text, usage = output, None
        return SimpleNamespace(output_text=text, usage=usage)


class FakeCompletions:
    def __init__(self, outputs: list[FakeOutput]) -> None:
        self.outputs = outputs
        self.calls: list[dict] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        output = self.outputs.pop(0)
        if isinstance(output, Exception):
            raise output
        if isinstance(output, tuple):
            text, usage = output
        else:
            text, usage = output, None
        message = SimpleNamespace(content=text)
        return SimpleNamespace(choices=[SimpleNamespace(message=message)], usage=usage)


class UnsupportedSchemaError(RuntimeError):
    status_code = 400
    code = "UnsupportedParameter"
    headers: ClassVar[dict[str, str]] = {}

    def __init__(self) -> None:
        super().__init__("json_schema response format is unsupported")


class ProviderError(RuntimeError):
    status_code = 503
    code = "ServiceUnavailable"
    headers: ClassVar[dict[str, str]] = {"x-request-id": "request-123"}


class FakeSecretStore:
    def __init__(self, value: str | None = None, error: RuntimeError | None = None) -> None:
        self.value = value
        self.error = error

    def get(self, _key: str) -> str | None:
        if self.error is not None:
            raise self.error
        return self.value


def runtime(protocol: str = "responses") -> ArkRuntimeConfig:
    return ArkRuntimeConfig(
        base_url="https://ark.example/v3",
        model="seed",
        protocol=protocol,
        thinking="disabled",
        timeout_seconds=120,
        max_retries=2,
    )


def test_responses_adapter_sends_one_image_and_parses_schema(tmp_path) -> None:
    image = tmp_path / "image.jpg"
    image.write_bytes(b"jpeg-bytes")
    responses = FakeResponses([json.dumps(VALID)])
    service = ArkInspectionService(client=SimpleNamespace(responses=responses))

    outcome = service.inspect_responses(runtime(), "system", "user", image)

    assert outcome.result.verdict is Verdict.PASS
    content = responses.calls[0]["input"][0]["content"]
    assert sum(item["type"] == "input_image" for item in content) == 1
    assert responses.calls[0]["text"]["format"]["type"] == "json_schema"


def test_responses_adapter_reads_nested_output_when_sdk_helper_is_missing(tmp_path) -> None:
    image = tmp_path / "image.jpg"
    image.write_bytes(b"jpeg-bytes")
    response = SimpleNamespace(
        output_text=None,
        output=[
            SimpleNamespace(
                type="message",
                content=[SimpleNamespace(type="output_text", text=json.dumps(VALID))],
            )
        ],
    )
    responses = SimpleNamespace(create=lambda **_kwargs: response)
    service = ArkInspectionService(client=SimpleNamespace(responses=responses))

    outcome = service.inspect_responses(runtime(), "system", "user", image)

    assert outcome.result.verdict is Verdict.PASS


def test_chat_adapter_sends_one_image(tmp_path) -> None:
    image = tmp_path / "image.jpg"
    image.write_bytes(b"jpeg-bytes")
    completions = FakeCompletions([json.dumps(VALID)])
    client = SimpleNamespace(chat=SimpleNamespace(completions=completions))
    service = ArkInspectionService(client=client)

    outcome = service.inspect_chat(runtime("chat"), "system", "user", image)

    assert outcome.result.verdict is Verdict.PASS
    content = completions.calls[0]["messages"][1]["content"]
    assert sum(item["type"] == "image_url" for item in content) == 1
    assert completions.calls[0]["response_format"]["type"] == "json_schema"


def test_invalid_response_uses_one_text_only_repair_call(tmp_path) -> None:
    image = tmp_path / "image.jpg"
    image.write_bytes(b"jpeg-bytes")
    responses = FakeResponses(
        [
            ("```json\nnot-json\n```", RESPONSES_USAGE),
            (json.dumps(VALID), RESPONSES_USAGE),
        ]
    )
    service = ArkInspectionService(client=SimpleNamespace(responses=responses))

    outcome = service.inspect_responses(runtime(), "system", "user", image)

    assert outcome.result.verdict is Verdict.PASS
    assert len(responses.calls) == 2
    first_content = responses.calls[0]["input"][0]["content"]
    repair_content = responses.calls[1]["input"][0]["content"]
    assert sum(item["type"] == "input_image" for item in first_content) == 1
    assert all(item["type"] != "input_image" for item in repair_content)
    assert [call.purpose for call in outcome.usage.calls] == ["inspection", "json_repair"]
    assert outcome.usage.totals.total_tokens == 240


def test_unsupported_native_schema_retries_with_json_object(tmp_path) -> None:
    image = tmp_path / "image.jpg"
    image.write_bytes(b"jpeg-bytes")
    responses = FakeResponses(
        [UnsupportedSchemaError(), (json.dumps(VALID), RESPONSES_USAGE)]
    )
    service = ArkInspectionService(client=SimpleNamespace(responses=responses))

    outcome = service.inspect_responses(runtime(), "system", "user", image)

    assert outcome.result.verdict is Verdict.PASS
    assert responses.calls[1]["text"]["format"] == {"type": "json_object"}
    assert "schema_version" in responses.calls[1]["input"][0]["content"][1]["text"]
    inspection, fallback = outcome.usage.calls
    assert (inspection.purpose, inspection.status, inspection.usage_available) == (
        "inspection",
        "failed",
        False,
    )
    assert (fallback.purpose, fallback.status, fallback.total_tokens) == (
        "schema_fallback",
        "succeeded",
        120,
    )


def test_result_verdict_is_normalized_from_findings(tmp_path) -> None:
    image = tmp_path / "image.jpg"
    image.write_bytes(b"jpeg-bytes")
    invalid_verdict = json.loads(json.dumps(VALID))
    invalid_verdict["findings"][0]["status"] = "fail"
    responses = FakeResponses([json.dumps(invalid_verdict)])
    service = ArkInspectionService(client=SimpleNamespace(responses=responses))

    outcome = service.inspect(runtime(), "system", "user", image)

    assert outcome.result.verdict is Verdict.FAIL


def test_responses_usage_is_normalized_before_result_parsing(tmp_path) -> None:
    image = tmp_path / "image.jpg"
    image.write_bytes(b"jpeg-bytes")
    responses = FakeResponses([(json.dumps(VALID), RESPONSES_USAGE)])
    service = ArkInspectionService(client=SimpleNamespace(responses=responses))

    outcome = service.inspect_responses(runtime(), "system", "user", image)

    call = outcome.usage.calls[0]
    assert outcome.result.verdict is Verdict.PASS
    assert outcome.usage.protocol == "responses"
    assert call.purpose == "inspection"
    assert call.status == "succeeded"
    assert call.usage_available is True
    assert (
        call.input_tokens,
        call.cached_input_tokens,
        call.output_tokens,
        call.reasoning_tokens,
        call.total_tokens,
    ) == (100, 40, 20, 4, 120)
    assert outcome.usage.totals.total_tokens == 120


def test_chat_usage_is_normalized(tmp_path) -> None:
    image = tmp_path / "image.jpg"
    image.write_bytes(b"jpeg-bytes")
    completions = FakeCompletions([(json.dumps(VALID), CHAT_USAGE)])
    service = ArkInspectionService(
        client=SimpleNamespace(chat=SimpleNamespace(completions=completions))
    )

    outcome = service.inspect_chat(runtime("chat"), "system", "user", image)

    call = outcome.usage.calls[0]
    assert outcome.result.verdict is Verdict.PASS
    assert outcome.usage.protocol == "chat"
    assert (
        call.input_tokens,
        call.cached_input_tokens,
        call.output_tokens,
        call.reasoning_tokens,
        call.total_tokens,
    ) == (80, 30, 15, 3, 95)
    assert outcome.usage.totals.total_tokens == 95


@pytest.mark.parametrize(
    ("usage", "expected_error"),
    [
        (None, "not_returned"),
        (SimpleNamespace(input_tokens="invalid"), "invalid_usage"),
    ],
)
def test_missing_or_invalid_usage_does_not_fail_valid_inspection(
    tmp_path,
    usage: object,
    expected_error: str,
) -> None:
    image = tmp_path / "image.jpg"
    image.write_bytes(b"jpeg-bytes")
    responses = FakeResponses([(json.dumps(VALID), usage)])
    service = ArkInspectionService(client=SimpleNamespace(responses=responses))

    outcome = service.inspect_responses(runtime(), "system", "user", image)

    assert outcome.result.verdict is Verdict.PASS
    assert outcome.usage.calls[0].usage_available is False
    assert outcome.usage.calls[0].usage_error == expected_error


def test_failed_repair_carries_known_and_unknown_calls(tmp_path) -> None:
    image = tmp_path / "image.jpg"
    image.write_bytes(b"jpeg-bytes")
    responses = FakeResponses(
        [("```json\nnot-json\n```", RESPONSES_USAGE), ProviderError()]
    )
    service = ArkInspectionService(client=SimpleNamespace(responses=responses))

    with pytest.raises(ArkRequestError) as raised:
        service.inspect_responses(runtime(), "system", "user", image)

    error = raised.value
    assert error.status_code == 503
    assert error.headers == {"x-request-id": "request-123"}
    assert error.code == "ServiceUnavailable"
    assert error.usage is not None
    assert [(call.purpose, call.status) for call in error.usage.calls] == [
        ("inspection", "succeeded"),
        ("json_repair", "failed"),
    ]
    assert error.usage.totals.known_call_count == 1
    assert error.usage.totals.unknown_call_count == 1


def test_malformed_repair_result_carries_complete_trace(tmp_path) -> None:
    image = tmp_path / "image.jpg"
    image.write_bytes(b"jpeg-bytes")
    responses = FakeResponses(
        [
            ("not-json", RESPONSES_USAGE),
            ("still-not-json", RESPONSES_USAGE),
        ]
    )
    service = ArkInspectionService(client=SimpleNamespace(responses=responses))

    with pytest.raises(MalformedInspectionResult) as raised:
        service.inspect_responses(runtime(), "system", "user", image)

    assert raised.value.usage is not None
    assert [call.purpose for call in raised.value.usage.calls] == [
        "inspection",
        "json_repair",
    ]
    assert raised.value.usage.totals.total_tokens == 240


@pytest.mark.parametrize(
    ("secrets", "protocol", "expected_status", "expected_code"),
    [
        (FakeSecretStore(), "responses", 401, "ark_api_key_missing"),
        (
            FakeSecretStore(error=RuntimeError("keychain failed")),
            "chat",
            None,
            "keychain_unavailable",
        ),
    ],
)
def test_keychain_failures_carry_an_empty_protocol_trace(
    tmp_path,
    secrets: FakeSecretStore,
    protocol: str,
    expected_status: int | None,
    expected_code: str,
) -> None:
    image = tmp_path / "image.jpg"
    image.write_bytes(b"jpeg-bytes")
    service = KeychainArkInspectionService(secrets)

    with pytest.raises(ArkRequestError) as raised:
        service.inspect(runtime(protocol), "system", "user", image)

    error = raised.value
    assert error.status_code == expected_status
    assert error.code == expected_code
    assert error.usage is not None
    assert error.usage.protocol == protocol
    assert error.usage.calls == []
    assert error.usage.totals.known_call_count == 0
    assert error.usage.totals.unknown_call_count == 0
