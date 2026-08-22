import json
from types import SimpleNamespace
from typing import ClassVar

from app.domain.results import Verdict
from app.services.ark import ArkInspectionService, ArkRuntimeConfig

VALID = {
    "schema_version": 1,
    "image_valid": True,
    "unverifiable_reason": None,
    "verdict": "pass",
    "summary": "uniform is clean",
    "overall_confidence": 0.9,
    "findings": [
        {
            "rule_id": "uniform",
            "rule_name": "uniform",
            "status": "pass",
            "observation": "no visible stain",
            "visual_evidence": "the front of the shirt is visible",
            "confidence": 0.9,
        }
    ],
}


class FakeResponses:
    def __init__(self, outputs: list[str | Exception]) -> None:
        self.outputs = outputs
        self.calls: list[dict] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        output = self.outputs.pop(0)
        if isinstance(output, Exception):
            raise output
        return SimpleNamespace(output_text=output, usage=None)


class FakeCompletions:
    def __init__(self, outputs: list[str | Exception]) -> None:
        self.outputs = outputs
        self.calls: list[dict] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        output = self.outputs.pop(0)
        if isinstance(output, Exception):
            raise output
        message = SimpleNamespace(content=output)
        return SimpleNamespace(choices=[SimpleNamespace(message=message)], usage=None)


class UnsupportedSchemaError(RuntimeError):
    status_code = 400
    code = "UnsupportedParameter"
    headers: ClassVar[dict[str, str]] = {}

    def __init__(self) -> None:
        super().__init__("json_schema response format is unsupported")


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

    result = service.inspect_responses(runtime(), "system", "user", image)

    assert result.verdict is Verdict.PASS
    content = responses.calls[0]["input"][0]["content"]
    assert sum(item["type"] == "input_image" for item in content) == 1
    assert responses.calls[0]["text"]["format"]["type"] == "json_schema"


def test_chat_adapter_sends_one_image(tmp_path) -> None:
    image = tmp_path / "image.jpg"
    image.write_bytes(b"jpeg-bytes")
    completions = FakeCompletions([json.dumps(VALID)])
    client = SimpleNamespace(chat=SimpleNamespace(completions=completions))
    service = ArkInspectionService(client=client)

    result = service.inspect_chat(runtime("chat"), "system", "user", image)

    assert result.verdict is Verdict.PASS
    content = completions.calls[0]["messages"][1]["content"]
    assert sum(item["type"] == "image_url" for item in content) == 1
    assert completions.calls[0]["response_format"]["type"] == "json_schema"


def test_invalid_response_uses_one_text_only_repair_call(tmp_path) -> None:
    image = tmp_path / "image.jpg"
    image.write_bytes(b"jpeg-bytes")
    responses = FakeResponses(["```json\nnot-json\n```", json.dumps(VALID)])
    service = ArkInspectionService(client=SimpleNamespace(responses=responses))

    result = service.inspect_responses(runtime(), "system", "user", image)

    assert result.verdict is Verdict.PASS
    assert len(responses.calls) == 2
    first_content = responses.calls[0]["input"][0]["content"]
    repair_content = responses.calls[1]["input"][0]["content"]
    assert sum(item["type"] == "input_image" for item in first_content) == 1
    assert all(item["type"] != "input_image" for item in repair_content)


def test_unsupported_native_schema_retries_with_json_object(tmp_path) -> None:
    image = tmp_path / "image.jpg"
    image.write_bytes(b"jpeg-bytes")
    responses = FakeResponses([UnsupportedSchemaError(), json.dumps(VALID)])
    service = ArkInspectionService(client=SimpleNamespace(responses=responses))

    result = service.inspect_responses(runtime(), "system", "user", image)

    assert result.verdict is Verdict.PASS
    assert responses.calls[1]["text"]["format"] == {"type": "json_object"}
    assert "schema_version" in responses.calls[1]["input"][0]["content"][1]["text"]


def test_result_verdict_is_normalized_from_findings(tmp_path) -> None:
    image = tmp_path / "image.jpg"
    image.write_bytes(b"jpeg-bytes")
    invalid_verdict = json.loads(json.dumps(VALID))
    invalid_verdict["findings"][0]["status"] = "fail"
    responses = FakeResponses([json.dumps(invalid_verdict)])
    service = ArkInspectionService(client=SimpleNamespace(responses=responses))

    result = service.inspect(runtime(), "system", "user", image)

    assert result.verdict is Verdict.FAIL
