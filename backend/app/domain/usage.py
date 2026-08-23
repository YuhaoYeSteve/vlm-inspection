import math
from collections.abc import Mapping, Sequence
from enum import StrEnum
from typing import Any, Literal, Self, cast

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)

ProtocolName = Literal["responses", "chat"]
CallPurpose = Literal["inspection", "schema_fallback", "json_repair"]
CallStatus = Literal["succeeded", "failed"]
UsageError = Literal["not_returned", "invalid_usage"]
MAX_TOKEN_COUNT = 2**63 - 1

_RawUsageSchema = Mapping[str, frozenset[str] | None]
_RESPONSES_RAW_USAGE_SCHEMA: dict[str, frozenset[str] | None] = {
    "input_tokens": None,
    "output_tokens": None,
    "total_tokens": None,
    "input_tokens_details": frozenset({"cached_tokens"}),
    "output_tokens_details": frozenset({"reasoning_tokens"}),
}
_CHAT_RAW_USAGE_SCHEMA: dict[str, frozenset[str] | None] = {
    "prompt_tokens": None,
    "completion_tokens": None,
    "total_tokens": None,
    "prompt_tokens_details": frozenset({"cached_tokens"}),
    "completion_tokens_details": frozenset({"reasoning_tokens"}),
}
_ALL_RAW_USAGE_SCHEMA = {
    **_RESPONSES_RAW_USAGE_SCHEMA,
    **_CHAT_RAW_USAGE_SCHEMA,
}


class UsageAvailability(StrEnum):
    NOT_STARTED = "not_started"
    COMPLETE = "complete"
    PARTIAL = "partial"
    UNAVAILABLE = "unavailable"
    UNTRACKED = "untracked"


class TokenValues(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    input_tokens: int = Field(ge=0, le=MAX_TOKEN_COUNT)
    cached_input_tokens: int = Field(ge=0, le=MAX_TOKEN_COUNT)
    output_tokens: int = Field(ge=0, le=MAX_TOKEN_COUNT)
    reasoning_tokens: int = Field(ge=0, le=MAX_TOKEN_COUNT)
    total_tokens: int = Field(ge=0, le=MAX_TOKEN_COUNT)

    @model_validator(mode="after")
    def validate_relationships(self) -> Self:
        if self.cached_input_tokens > self.input_tokens:
            raise ValueError("cached input tokens exceed input tokens")
        if self.reasoning_tokens > self.output_tokens:
            raise ValueError("reasoning tokens exceed output tokens")
        if self.total_tokens != self.input_tokens + self.output_tokens:
            raise ValueError("total tokens do not equal input plus output")
        return self


class TokenUsageCall(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    sequence: int = Field(ge=1)
    purpose: CallPurpose
    status: CallStatus
    usage_available: bool
    usage_error: UsageError | None
    input_tokens: int | None
    cached_input_tokens: int | None
    output_tokens: int | None
    reasoning_tokens: int | None
    total_tokens: int | None
    raw_usage: dict[str, Any] | None

    @field_validator("raw_usage", mode="before")
    @classmethod
    def validate_raw_usage(cls, value: Any) -> dict[str, Any] | None:
        if value is None:
            return None
        try:
            return _project_raw_usage(value, _ALL_RAW_USAGE_SCHEMA)
        except Exception as error:
            raise ValueError("raw usage must be a JSON-safe object") from error

    @model_validator(mode="after")
    def validate_shape(self) -> Self:
        values = (
            self.input_tokens,
            self.cached_input_tokens,
            self.output_tokens,
            self.reasoning_tokens,
            self.total_tokens,
        )
        if self.usage_available:
            if any(value is None for value in values) or self.usage_error is not None:
                raise ValueError("available usage requires every normalized value and no error")
            TokenValues(
                input_tokens=cast(int, self.input_tokens),
                cached_input_tokens=cast(int, self.cached_input_tokens),
                output_tokens=cast(int, self.output_tokens),
                reasoning_tokens=cast(int, self.reasoning_tokens),
                total_tokens=cast(int, self.total_tokens),
            )
        elif any(value is not None for value in values) or self.usage_error is None:
            raise ValueError("unavailable usage requires null normalized values and an error")
        return self


class TokenUsageTotals(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    known_call_count: int = Field(ge=0)
    unknown_call_count: int = Field(ge=0)
    input_tokens: int = Field(ge=0)
    cached_input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    reasoning_tokens: int = Field(ge=0)
    total_tokens: int = Field(ge=0)


class AttemptUsageTrace(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    schema_version: Literal[1] = 1
    protocol: ProtocolName
    calls: list[TokenUsageCall]
    totals: TokenUsageTotals

    @model_validator(mode="after")
    def validate_totals(self) -> Self:
        if self.totals != _sum_calls(self.calls):
            raise ValueError("usage totals do not match calls")
        return self

    @classmethod
    def from_calls(
        cls,
        protocol: ProtocolName,
        calls: Sequence[TokenUsageCall],
    ) -> Self:
        return cls(protocol=protocol, calls=list(calls), totals=_sum_calls(calls))


class TokenUsageSummary(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    availability: UsageAvailability
    input_tokens: int = Field(ge=0)
    cached_input_tokens: int = Field(ge=0)
    uncached_input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    reasoning_tokens: int = Field(ge=0)
    total_tokens: int = Field(ge=0)
    known_call_count: int = Field(ge=0)
    unknown_call_count: int = Field(ge=0)


def _selected_members(value: Any, schema: _RawUsageSchema) -> dict[str, Any]:
    source: Mapping[Any, Any] | None = None
    if isinstance(value, Mapping):
        source = value
    else:
        attributes = getattr(value, "__dict__", None)
        if isinstance(attributes, Mapping):
            source = attributes

    selected: dict[str, Any] = {}
    if source is not None:
        for key in schema:
            try:
                selected[key] = source[key]
            except KeyError:
                pass

    missing = {key: details for key, details in schema.items() if key not in selected}
    dumper = None
    if missing and callable(getattr(type(value), "model_dump", None)):
        dumper = value.model_dump
        include = {
            key: set(details) if details is not None else True
            for key, details in missing.items()
        }
        dumped = dumper(mode="python", include=include)
        if not isinstance(dumped, Mapping):
            raise TypeError("usage model dump is not an object")
        for key in missing:
            try:
                selected[key] = dumped[key]
            except KeyError:
                pass

    if source is None and not callable(dumper):
        raise TypeError("usage value is not an object")
    return selected


def _safe_raw_number(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, bool):
        return False
    if isinstance(value, int):
        return -(MAX_TOKEN_COUNT + 1) <= value <= MAX_TOKEN_COUNT
    return isinstance(value, float) and math.isfinite(value)


def _project_raw_usage(value: Any, schema: _RawUsageSchema) -> dict[str, Any]:
    selected = _selected_members(value, schema)
    projected: dict[str, Any] = {}
    for key, detail_keys in schema.items():
        if key not in selected:
            continue
        item = selected[key]
        if detail_keys is None:
            if _safe_raw_number(item):
                projected[key] = item
            continue

        try:
            details = _selected_members(item, {detail_key: None for detail_key in detail_keys})
        except TypeError:
            continue
        projected[key] = {
            detail_key: details[detail_key]
            for detail_key in detail_keys
            if detail_key in details and _safe_raw_number(details[detail_key])
        }
    return projected


def _required_int(value: Any) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or not 0 <= value <= MAX_TOKEN_COUNT
    ):
        raise TypeError("usage value is not an in-range non-negative integer")
    return cast(int, value)


def _unknown_call(
    sequence: int,
    purpose: CallPurpose,
    status: CallStatus,
    error: UsageError,
    raw_usage: dict[str, Any] | None,
) -> TokenUsageCall:
    return TokenUsageCall(
        sequence=sequence,
        purpose=purpose,
        status=status,
        usage_available=False,
        usage_error=error,
        input_tokens=None,
        cached_input_tokens=None,
        output_tokens=None,
        reasoning_tokens=None,
        total_tokens=None,
        raw_usage=raw_usage,
    )


def normalize_provider_call(
    protocol: ProtocolName,
    sequence: int,
    purpose: CallPurpose,
    status: CallStatus,
    raw_usage: Any,
) -> TokenUsageCall:
    if raw_usage is None:
        return _unknown_call(sequence, purpose, status, "not_returned", None)

    try:
        schema = (
            _RESPONSES_RAW_USAGE_SCHEMA
            if protocol == "responses"
            else _CHAT_RAW_USAGE_SCHEMA
        )
        raw = _project_raw_usage(raw_usage, schema)
    except Exception:  # noqa: BLE001
        # Usage telemetry must never interrupt otherwise valid inspection behavior.
        return _unknown_call(sequence, purpose, status, "invalid_usage", None)

    try:
        if protocol == "responses":
            input_details = raw["input_tokens_details"]
            output_details = raw["output_tokens_details"]
            values = TokenValues(
                input_tokens=_required_int(raw["input_tokens"]),
                cached_input_tokens=_required_int(input_details["cached_tokens"]),
                output_tokens=_required_int(raw["output_tokens"]),
                reasoning_tokens=_required_int(output_details["reasoning_tokens"]),
                total_tokens=_required_int(raw["total_tokens"]),
            )
        else:
            prompt_details = raw["prompt_tokens_details"]
            completion_details = raw["completion_tokens_details"]
            values = TokenValues(
                input_tokens=_required_int(raw["prompt_tokens"]),
                cached_input_tokens=_required_int(prompt_details["cached_tokens"]),
                output_tokens=_required_int(raw["completion_tokens"]),
                reasoning_tokens=_required_int(completion_details["reasoning_tokens"]),
                total_tokens=_required_int(raw["total_tokens"]),
            )
    except (KeyError, TypeError, ValidationError, ValueError):
        return _unknown_call(sequence, purpose, status, "invalid_usage", raw)

    return TokenUsageCall(
        sequence=sequence,
        purpose=purpose,
        status=status,
        usage_available=True,
        usage_error=None,
        input_tokens=values.input_tokens,
        cached_input_tokens=values.cached_input_tokens,
        output_tokens=values.output_tokens,
        reasoning_tokens=values.reasoning_tokens,
        total_tokens=values.total_tokens,
        raw_usage=raw,
    )


def _sum_calls(calls: Sequence[TokenUsageCall]) -> TokenUsageTotals:
    known = [call for call in calls if call.usage_available]
    return TokenUsageTotals(
        known_call_count=len(known),
        unknown_call_count=len(calls) - len(known),
        input_tokens=sum(call.input_tokens or 0 for call in known),
        cached_input_tokens=sum(call.cached_input_tokens or 0 for call in known),
        output_tokens=sum(call.output_tokens or 0 for call in known),
        reasoning_tokens=sum(call.reasoning_tokens or 0 for call in known),
        total_tokens=sum(call.total_tokens or 0 for call in known),
    )


class AttemptUsageCollector:
    def __init__(self, protocol: ProtocolName) -> None:
        self.protocol = protocol
        self._calls: list[TokenUsageCall] = []

    def add_response(self, purpose: CallPurpose, raw_usage: Any) -> None:
        self._calls.append(
            normalize_provider_call(
                protocol=self.protocol,
                sequence=len(self._calls) + 1,
                purpose=purpose,
                status="succeeded",
                raw_usage=raw_usage,
            )
        )

    def add_missing(self, purpose: CallPurpose, status: CallStatus = "failed") -> None:
        self._calls.append(
            _unknown_call(
                sequence=len(self._calls) + 1,
                purpose=purpose,
                status=status,
                error="not_returned",
                raw_usage=None,
            )
        )

    def snapshot(self) -> AttemptUsageTrace:
        return AttemptUsageTrace.from_calls(self.protocol, self._calls)


def parse_attempt_trace(payload: Any) -> AttemptUsageTrace | None:
    if payload is None:
        return None
    try:
        return AttemptUsageTrace.model_validate(payload)
    except ValidationError:
        return None


def summarize_attempt_payloads(payloads: Sequence[Any]) -> TokenUsageSummary:
    if not payloads:
        availability = UsageAvailability.NOT_STARTED
        totals = _sum_calls([])
    else:
        traces = [parse_attempt_trace(payload) for payload in payloads]
        legacy_attempts = sum(payload is None for payload in payloads)
        invalid_attempts = sum(
            payload is not None and trace is None
            for payload, trace in zip(payloads, traces, strict=True)
        )
        calls = [call for trace in traces if trace is not None for call in trace.calls]
        call_totals = _sum_calls(calls)
        totals = call_totals.model_copy(
            update={
                "unknown_call_count": (
                    call_totals.unknown_call_count + legacy_attempts + invalid_attempts
                )
            }
        )

        if legacy_attempts == len(payloads):
            availability = UsageAvailability.UNTRACKED
        elif not calls and legacy_attempts == 0 and invalid_attempts == 0:
            availability = UsageAvailability.NOT_STARTED
        elif totals.known_call_count == 0:
            availability = UsageAvailability.UNAVAILABLE
        elif totals.unknown_call_count > 0:
            availability = UsageAvailability.PARTIAL
        else:
            availability = UsageAvailability.COMPLETE

    return TokenUsageSummary(
        availability=availability,
        input_tokens=totals.input_tokens,
        cached_input_tokens=totals.cached_input_tokens,
        uncached_input_tokens=totals.input_tokens - totals.cached_input_tokens,
        output_tokens=totals.output_tokens,
        reasoning_tokens=totals.reasoning_tokens,
        total_tokens=totals.total_tokens,
        known_call_count=totals.known_call_count,
        unknown_call_count=totals.unknown_call_count,
    )
