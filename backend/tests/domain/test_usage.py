from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from app.domain.usage import (
    AttemptUsageCollector,
    AttemptUsageTrace,
    TokenUsageCall,
    TokenValues,
    UsageAvailability,
    normalize_provider_call,
    parse_attempt_trace,
    summarize_attempt_payloads,
)

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


def test_sdk_objects_are_converted_to_json_safe_raw_usage() -> None:
    raw_usage = SimpleNamespace(
        input_tokens=58,
        input_tokens_details=SimpleNamespace(cached_tokens=8),
        output_tokens=1647,
        output_tokens_details=SimpleNamespace(reasoning_tokens=1273),
        total_tokens=1705,
        extra_values=("kept", 1),
    )

    call = normalize_provider_call(
        protocol="responses",
        sequence=1,
        purpose="inspection",
        status="succeeded",
        raw_usage=raw_usage,
    )

    assert call.usage_available is True
    assert call.raw_usage is not None
    assert call.raw_usage["extra_values"] == ["kept", 1]
    assert call.raw_usage["input_tokens_details"] == {"cached_tokens": 8}


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


def test_invalid_versioned_payload_is_treated_as_untracked() -> None:
    collector = AttemptUsageCollector("responses")
    collector.add_missing("inspection")
    payload = collector.snapshot().model_dump(mode="json")
    payload["schema_version"] = 2

    assert parse_attempt_trace(payload) is None
    summary = summarize_attempt_payloads([payload])
    assert summary.availability is UsageAvailability.UNTRACKED
    assert summary.unknown_call_count == 1
