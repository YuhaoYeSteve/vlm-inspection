import json
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

import app.domain.usage as usage_domain
from app.domain.usage import (
    AttemptUsageCollector,
    AttemptUsageTrace,
    ProtocolName,
    TokenUsageCall,
    TokenValues,
    UsageAvailability,
    normalize_provider_call,
    parse_attempt_trace,
    summarize_attempt_payloads,
)

SIGNED_INT64_MAX = 2**63 - 1

RESPONSES_USAGE = {
    "input_tokens": 58,
    "input_tokens_details": {"cached_tokens": 8},
    "output_tokens": 1647,
    "output_tokens_details": {"reasoning_tokens": 1273},
    "total_tokens": 1705,
}

CHAT_USAGE = {
    "prompt_tokens": 35,
    "prompt_tokens_details": {"cached_tokens": 5},
    "completion_tokens": 164,
    "completion_tokens_details": {"reasoning_tokens": 114},
    "total_tokens": 199,
}


def test_normalizes_official_responses_usage() -> None:
    call = normalize_provider_call(
        protocol="responses",
        sequence=1,
        purpose="inspection",
        status="succeeded",
        raw_usage=RESPONSES_USAGE,
    )

    assert call.usage_available is True
    assert call.input_tokens == 58
    assert call.cached_input_tokens == 8
    assert call.output_tokens == 1647
    assert call.reasoning_tokens == 1273
    assert call.total_tokens == 1705
    assert call.raw_usage == RESPONSES_USAGE


def test_normalizes_chat_usage_to_the_same_fields() -> None:
    call = normalize_provider_call(
        protocol="chat",
        sequence=1,
        purpose="inspection",
        status="succeeded",
        raw_usage=CHAT_USAGE,
    )

    assert call.input_tokens == 35
    assert call.cached_input_tokens == 5
    assert call.output_tokens == 164
    assert call.reasoning_tokens == 114
    assert call.total_tokens == 199
    assert call.raw_usage == CHAT_USAGE


@pytest.mark.parametrize(
    ("protocol", "raw_usage", "expected"),
    [
        pytest.param(
            "responses",
            {
                **RESPONSES_USAGE,
                "input_tokens_details": {
                    "cached_tokens": 8,
                    "prompt": "do not persist",
                },
                "output_tokens_details": {
                    "reasoning_tokens": 1273,
                    "response": object(),
                },
                "api_key": "secret",
                "prompt": "inspect this image",
                "response": object(),
                "extra_values": {"unsupported": {1, 2}},
            },
            RESPONSES_USAGE,
            id="responses",
        ),
        pytest.param(
            "chat",
            {
                **CHAT_USAGE,
                "prompt_tokens_details": {
                    "cached_tokens": 5,
                    "prompt": "do not persist",
                },
                "completion_tokens_details": {
                    "reasoning_tokens": 114,
                    "response": object(),
                },
                "api_key": "secret",
                "prompt": "inspect this image",
                "response": object(),
                "extra_values": {"unsupported": {1, 2}},
            },
            CHAT_USAGE,
            id="chat",
        ),
    ],
)
def test_raw_usage_keeps_only_the_protocol_token_whitelist(
    protocol: ProtocolName,
    raw_usage: dict[str, object],
    expected: dict[str, object],
) -> None:
    call = normalize_provider_call(
        protocol=protocol,
        sequence=1,
        purpose="inspection",
        status="succeeded",
        raw_usage=raw_usage,
    )

    assert call.usage_available is True
    assert call.raw_usage == expected


def test_invalid_usage_is_preserved_but_excluded_from_totals() -> None:
    call = normalize_provider_call(
        protocol="responses",
        sequence=1,
        purpose="inspection",
        status="succeeded",
        raw_usage={
            **RESPONSES_USAGE,
            "input_tokens_details": {"cached_tokens": 59},
        },
    )
    trace = AttemptUsageTrace.from_calls("responses", [call])

    assert call.usage_available is False
    assert call.usage_error == "invalid_usage"
    assert call.raw_usage is not None
    assert trace.totals.known_call_count == 0
    assert trace.totals.unknown_call_count == 1
    assert trace.totals.total_tokens == 0


def test_collector_keeps_known_and_unknown_calls_in_order() -> None:
    collector = AttemptUsageCollector("responses")
    collector.add_response("inspection", RESPONSES_USAGE)
    collector.add_missing("json_repair", status="failed")

    trace = collector.snapshot()

    assert [call.sequence for call in trace.calls] == [1, 2]
    assert trace.totals.known_call_count == 1
    assert trace.totals.unknown_call_count == 1
    assert trace.totals.total_tokens == 1705


def test_aggregate_marks_partial_and_does_not_double_count_subsets() -> None:
    collector = AttemptUsageCollector("responses")
    collector.add_response("inspection", RESPONSES_USAGE)
    collector.add_missing("json_repair", status="failed")

    summary = summarize_attempt_payloads([collector.snapshot().model_dump(mode="json")])

    assert summary.availability is UsageAvailability.PARTIAL
    assert summary.input_tokens == 58
    assert summary.cached_input_tokens == 8
    assert summary.uncached_input_tokens == 50
    assert summary.output_tokens == 1647
    assert summary.reasoning_tokens == 1273
    assert summary.total_tokens == 1705
    assert summary.known_call_count == 1
    assert summary.unknown_call_count == 1


def test_empty_and_legacy_attempts_have_distinct_states() -> None:
    assert summarize_attempt_payloads([]).availability is UsageAvailability.NOT_STARTED

    legacy = summarize_attempt_payloads([None, None])

    assert legacy.availability is UsageAvailability.UNTRACKED
    assert legacy.unknown_call_count == 2


def test_sdk_objects_keep_only_whitelisted_numeric_usage_fields() -> None:
    raw_usage = SimpleNamespace(
        input_tokens=58,
        input_tokens_details=SimpleNamespace(
            cached_tokens=8,
            prompt="do not persist",
        ),
        output_tokens=1647,
        output_tokens_details=SimpleNamespace(
            reasoning_tokens=1273,
            response=object(),
        ),
        total_tokens=1705,
        api_key="secret",
        prompt="inspect this image",
        extra_values={"unsupported": {1, 2}},
    )

    call = normalize_provider_call(
        protocol="responses",
        sequence=1,
        purpose="inspection",
        status="succeeded",
        raw_usage=raw_usage,
    )

    assert call.usage_available is True
    assert call.raw_usage == RESPONSES_USAGE


def test_model_dump_usage_is_requested_with_only_whitelisted_fields() -> None:
    class ModelDumpUsage:
        __slots__ = ("include",)

        def __init__(self) -> None:
            self.include: dict[str, object] | None = None

        def model_dump(
            self,
            *,
            mode: str,
            include: dict[str, object],
        ) -> dict[str, object]:
            assert mode == "python"
            self.include = include
            return {
                **RESPONSES_USAGE,
                "api_key": "secret",
                "unsupported": {1, 2},
            }

    raw_usage = ModelDumpUsage()

    call = normalize_provider_call(
        protocol="responses",
        sequence=1,
        purpose="inspection",
        status="succeeded",
        raw_usage=raw_usage,
    )

    assert call.usage_available is True
    assert call.raw_usage == RESPONSES_USAGE
    assert raw_usage.include == {
        "input_tokens": True,
        "output_tokens": True,
        "total_tokens": True,
        "input_tokens_details": {"cached_tokens"},
        "output_tokens_details": {"reasoning_tokens"},
    }


def test_bool_token_value_is_invalid_usage() -> None:
    call = normalize_provider_call(
        protocol="responses",
        sequence=1,
        purpose="inspection",
        status="succeeded",
        raw_usage={**RESPONSES_USAGE, "input_tokens": True},
    )

    assert call.usage_available is False
    assert call.usage_error == "invalid_usage"
    assert call.raw_usage is not None
    assert "input_tokens" not in call.raw_usage


@pytest.mark.parametrize("value", [None, 58.5])
def test_null_and_finite_float_are_retained_as_invalid_diagnostics(
    value: float | None,
) -> None:
    call = normalize_provider_call(
        protocol="responses",
        sequence=1,
        purpose="inspection",
        status="succeeded",
        raw_usage={**RESPONSES_USAGE, "input_tokens": value},
    )

    assert call.usage_available is False
    assert call.usage_error == "invalid_usage"
    assert call.raw_usage is not None
    assert call.raw_usage["input_tokens"] == value


@pytest.mark.parametrize("value", [float("inf"), float("-inf"), float("nan")])
def test_non_finite_float_is_not_persisted(value: float) -> None:
    call = normalize_provider_call(
        protocol="responses",
        sequence=1,
        purpose="inspection",
        status="succeeded",
        raw_usage={**RESPONSES_USAGE, "input_tokens": value},
    )

    assert call.usage_available is False
    assert call.usage_error == "invalid_usage"
    assert call.raw_usage is not None
    assert "input_tokens" not in call.raw_usage


def test_max_token_count_is_signed_int64_max() -> None:
    assert usage_domain.MAX_TOKEN_COUNT == SIGNED_INT64_MAX


@pytest.mark.parametrize(
    ("protocol", "raw_usage", "expected_raw"),
    [
        pytest.param(
            "responses",
            {
                "input_tokens": 10**5000,
                "input_tokens_details": {"cached_tokens": 0},
                "output_tokens": 0,
                "output_tokens_details": {"reasoning_tokens": 0},
                "total_tokens": 10**5000,
            },
            {
                "input_tokens_details": {"cached_tokens": 0},
                "output_tokens": 0,
                "output_tokens_details": {"reasoning_tokens": 0},
            },
            id="responses",
        ),
        pytest.param(
            "chat",
            {
                "prompt_tokens": 10**5000,
                "prompt_tokens_details": {"cached_tokens": 0},
                "completion_tokens": 0,
                "completion_tokens_details": {"reasoning_tokens": 0},
                "total_tokens": 10**5000,
            },
            {
                "prompt_tokens_details": {"cached_tokens": 0},
                "completion_tokens": 0,
                "completion_tokens_details": {"reasoning_tokens": 0},
            },
            id="chat",
        ),
    ],
)
def test_excessive_token_counts_are_excluded_from_raw_and_totals(
    protocol: ProtocolName,
    raw_usage: dict[str, object],
    expected_raw: dict[str, object],
) -> None:
    call = normalize_provider_call(
        protocol=protocol,
        sequence=1,
        purpose="inspection",
        status="succeeded",
        raw_usage=raw_usage,
    )
    trace = AttemptUsageTrace.from_calls(protocol, [call])

    assert call.usage_available is False
    assert call.usage_error == "invalid_usage"
    assert call.raw_usage == expected_raw
    assert trace.totals.known_call_count == 0
    assert trace.totals.unknown_call_count == 1
    assert trace.totals.total_tokens == 0
    json.dumps(trace.model_dump(mode="json"))


def test_direct_available_call_rejects_token_count_above_signed_int64() -> None:
    with pytest.raises(ValidationError):
        TokenUsageCall(
            sequence=1,
            purpose="inspection",
            status="succeeded",
            usage_available=True,
            usage_error=None,
            input_tokens=SIGNED_INT64_MAX + 1,
            cached_input_tokens=0,
            output_tokens=0,
            reasoning_tokens=0,
            total_tokens=SIGNED_INT64_MAX + 1,
            raw_usage=None,
        )


def test_invalid_string_token_is_not_persisted_but_numeric_context_is() -> None:
    call = normalize_provider_call(
        protocol="responses",
        sequence=1,
        purpose="inspection",
        status="succeeded",
        raw_usage={
            **RESPONSES_USAGE,
            "input_tokens": "secret prompt",
            "api_key": "secret",
            "unsupported": {1, 2},
        },
    )

    assert call.usage_available is False
    assert call.usage_error == "invalid_usage"
    assert call.raw_usage == {
        "input_tokens_details": {"cached_tokens": 8},
        "output_tokens": 1647,
        "output_tokens_details": {"reasoning_tokens": 1273},
        "total_tokens": 1705,
    }


@pytest.mark.parametrize(
    "raw_usage",
    [
        pytest.param(
            {
                "input_tokens": 58,
                "input_tokens_details": {"cached_tokens": 8},
                "output_tokens": 1647,
                "output_tokens_details": {"reasoning_tokens": 1273},
            },
            id="missing-field",
        ),
        pytest.param(
            {
                "input_tokens": -1,
                "input_tokens_details": {"cached_tokens": 0},
                "output_tokens": 1647,
                "output_tokens_details": {"reasoning_tokens": 1273},
                "total_tokens": 1646,
            },
            id="negative-field",
        ),
        pytest.param(
            {
                **RESPONSES_USAGE,
                "output_tokens_details": {"reasoning_tokens": 1648},
            },
            id="reasoning-exceeds-output",
        ),
        pytest.param(
            {**RESPONSES_USAGE, "total_tokens": 1706},
            id="total-mismatch",
        ),
    ],
)
def test_malformed_token_values_are_preserved_and_marked_invalid(
    raw_usage: dict[str, object],
) -> None:
    call = normalize_provider_call(
        protocol="responses",
        sequence=1,
        purpose="inspection",
        status="succeeded",
        raw_usage=raw_usage,
    )

    assert call.usage_available is False
    assert call.usage_error == "invalid_usage"
    assert call.raw_usage == raw_usage


def test_call_shape_and_token_relationships_are_enforced() -> None:
    with pytest.raises(ValidationError):
        TokenValues(
            input_tokens=10,
            cached_input_tokens=11,
            output_tokens=4,
            reasoning_tokens=0,
            total_tokens=14,
        )

    with pytest.raises(ValidationError):
        TokenUsageCall(
            sequence=1,
            purpose="inspection",
            status="failed",
            usage_available=False,
            usage_error="not_returned",
            input_tokens=1,
            cached_input_tokens=None,
            output_tokens=None,
            reasoning_tokens=None,
            total_tokens=None,
            raw_usage=None,
        )


def test_non_null_invalid_payload_is_unavailable_not_untracked() -> None:
    collector = AttemptUsageCollector("responses")
    collector.add_missing("inspection")
    payload = collector.snapshot().model_dump(mode="json")
    payload["schema_version"] = 2

    assert parse_attempt_trace(payload) is None
    summary = summarize_attempt_payloads([payload])
    assert summary.availability is UsageAvailability.UNAVAILABLE
    assert summary.unknown_call_count == 1


def test_non_null_malformed_payload_is_unavailable_not_untracked() -> None:
    summary = summarize_attempt_payloads([{"schema_version": 1}])

    assert summary.availability is UsageAvailability.UNAVAILABLE
    assert summary.known_call_count == 0
    assert summary.unknown_call_count == 1


def test_known_and_invalid_payloads_are_partial() -> None:
    collector = AttemptUsageCollector("responses")
    collector.add_response("inspection", RESPONSES_USAGE)
    known_payload = collector.snapshot().model_dump(mode="json")

    summary = summarize_attempt_payloads([known_payload, {"schema_version": 2}])

    assert summary.availability is UsageAvailability.PARTIAL
    assert summary.known_call_count == 1
    assert summary.unknown_call_count == 1
    assert summary.total_tokens == 1705


def test_valid_empty_trace_is_not_started() -> None:
    payload = AttemptUsageCollector("responses").snapshot().model_dump(mode="json")

    summary = summarize_attempt_payloads([payload])

    assert summary.availability is UsageAvailability.NOT_STARTED
    assert summary.known_call_count == 0
    assert summary.unknown_call_count == 0


def test_complete_and_unavailable_summaries_are_distinct() -> None:
    complete_collector = AttemptUsageCollector("responses")
    complete_collector.add_response("inspection", RESPONSES_USAGE)
    unavailable_collector = AttemptUsageCollector("responses")
    unavailable_collector.add_missing("inspection")

    complete = summarize_attempt_payloads(
        [complete_collector.snapshot().model_dump(mode="json")]
    )
    unavailable = summarize_attempt_payloads(
        [unavailable_collector.snapshot().model_dump(mode="json")]
    )

    assert complete.availability is UsageAvailability.COMPLETE
    assert complete.known_call_count == 1
    assert complete.unknown_call_count == 0
    assert unavailable.availability is UsageAvailability.UNAVAILABLE
    assert unavailable.known_call_count == 0
    assert unavailable.unknown_call_count == 1


def test_attempt_trace_rejects_totals_that_do_not_match_calls() -> None:
    collector = AttemptUsageCollector("responses")
    collector.add_response("inspection", RESPONSES_USAGE)
    payload = collector.snapshot().model_dump(mode="json")
    payload["totals"]["total_tokens"] = 1706

    with pytest.raises(ValidationError):
        AttemptUsageTrace.model_validate(payload)
    assert parse_attempt_trace(payload) is None


def test_raw_usage_is_sanitized_when_models_are_parsed_directly() -> None:
    collector = AttemptUsageCollector("responses")
    collector.add_response("inspection", RESPONSES_USAGE)
    call_payload = collector.snapshot().calls[0].model_dump(mode="json")
    call_payload["raw_usage"] = {
        **RESPONSES_USAGE,
        "api_key": "secret",
        "prompt": "inspect this image",
        "unsupported": {1, 2},
    }

    call = TokenUsageCall.model_validate(call_payload)
    assert call.raw_usage == RESPONSES_USAGE

    trace_payload = collector.snapshot().model_dump(mode="json")
    trace_payload["calls"][0]["raw_usage"] = call_payload["raw_usage"]
    trace = parse_attempt_trace(trace_payload)

    assert trace is not None
    assert trace.calls[0].raw_usage == RESPONSES_USAGE


def test_invalid_direct_raw_usage_drops_strings_and_unsupported_values() -> None:
    raw_usage = {
        **RESPONSES_USAGE,
        "input_tokens": "secret prompt",
        "api_key": "secret",
        "unsupported": object(),
    }
    call_payload = {
        "sequence": 1,
        "purpose": "inspection",
        "status": "succeeded",
        "usage_available": False,
        "usage_error": "invalid_usage",
        "input_tokens": None,
        "cached_input_tokens": None,
        "output_tokens": None,
        "reasoning_tokens": None,
        "total_tokens": None,
        "raw_usage": raw_usage,
    }
    expected_raw = {
        "input_tokens_details": {"cached_tokens": 8},
        "output_tokens": 1647,
        "output_tokens_details": {"reasoning_tokens": 1273},
        "total_tokens": 1705,
    }

    call = TokenUsageCall.model_validate(call_payload)

    assert call.usage_error == "invalid_usage"
    assert call.raw_usage == expected_raw

    trace = parse_attempt_trace(
        {
            "schema_version": 1,
            "protocol": "responses",
            "calls": [call_payload],
            "totals": {
                "known_call_count": 0,
                "unknown_call_count": 1,
                "input_tokens": 0,
                "cached_input_tokens": 0,
                "output_tokens": 0,
                "reasoning_tokens": 0,
                "total_tokens": 0,
            },
        }
    )

    assert trace is not None
    assert trace.calls[0].usage_error == "invalid_usage"
    assert trace.calls[0].raw_usage == expected_raw
