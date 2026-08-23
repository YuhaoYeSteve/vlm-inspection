# Token Usage Observability Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Capture every Ark model call's returned Token usage, aggregate it by image and task, and show accurate known/unknown usage in the task workspace, history, and Feishu report.

**Architecture:** Add a pure usage domain module that normalizes Responses and Chat usage into a versioned attempt trace stored in the existing `InferenceAttempt.usage_json`. Ark returns an inspection outcome plus its trace (or attaches the trace to an error), Runner persists every retry attempt, and one shared aggregator feeds API responses and Feishu reports. React renders task-level summaries and progressively disclosed per-call details without recomputing totals.

**Tech Stack:** Python 3.11+, Pydantic 2, SQLAlchemy 2, FastAPI, pytest, React 19, TypeScript, Vitest/Testing Library, Playwright, Feishu Docx blocks.

---

## File map

- Create `backend/app/domain/usage.py` — protocol-neutral Token usage models, normalization, validation, trace construction, and aggregation.
- Create `backend/tests/domain/test_usage.py` — official Responses/Chat fixtures and aggregation edge cases.
- Modify `backend/app/services/ark.py` — capture every SDK call and return/raise its usage trace.
- Modify `backend/tests/services/test_ark.py` — verify normal, fallback, repair, missing, and invalid usage capture.
- Modify `backend/app/services/runner.py` — persist usage on both successful and failed attempts.
- Modify `backend/app/repositories.py` — batch-load attempts for task and history projections.
- Modify backend fake inspectors in `backend/tests/conftest.py`, `backend/tests/services/test_runner.py`, `backend/tests/integration/test_100_image_run.py`, and `backend/tests/e2e_server.py` — return usage-aware outcomes.
- Modify `backend/app/schemas.py` and `backend/app/api/tasks.py` — expose task/image summaries and per-call details.
- Modify `backend/tests/api/test_tasks.py` and `backend/tests/persistence/test_tasks.py` — verify persistence and API aggregation.
- Create `frontend/src/features/tasks/TokenUsageSummary.tsx` — task-level usage panel and shared number/status formatting.
- Create `frontend/src/features/tasks/TokenUsageDetails.tsx` — image-level summary and expandable call table.
- Modify `frontend/src/api/types.ts`, `frontend/src/features/tasks/TaskToolbar.tsx`, `frontend/src/features/tasks/ImageDetail.tsx`, `frontend/src/features/tasks/TaskPage.test.tsx`, and `frontend/src/styles.css` — integrate the task workspace UI.
- Modify `frontend/src/features/history/HistoryPage.tsx` and `frontend/src/features/history/HistoryPage.test.tsx` — show task Token status in history.
- Modify `backend/app/services/reports.py` and `backend/tests/services/test_reports.py` — add the shared task summary to Feishu reports.
- Modify `frontend/e2e/inspection-flow.spec.ts` and `README.md` — cover the complete visible flow and document the feature.

## Task 1: Define and test the normalized Token usage domain

**Files:**
- Create: `backend/app/domain/usage.py`
- Create: `backend/tests/domain/test_usage.py`

- [ ] **Step 1: Write failing tests for Responses, Chat, invalid usage, and aggregation**

Create `backend/tests/domain/test_usage.py` with fixed provider payloads, including the exact nested fields shown by Ark:

```python
from app.domain.usage import (
    AttemptUsageCollector,
    UsageAvailability,
    normalize_provider_call,
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

    assert call.usage_available is False
    assert call.usage_error == "invalid_usage"
    assert call.raw_usage is not None


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

    summary = summarize_attempt_payloads(
        [collector.snapshot().model_dump(mode="json")]
    )

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
```

- [ ] **Step 2: Run the new tests and confirm the module is missing**

Run:

```bash
backend/.venv/bin/pytest backend/tests/domain/test_usage.py -v
```

Expected: collection fails with `ModuleNotFoundError: No module named 'app.domain.usage'`.

- [ ] **Step 3: Implement the complete usage domain module**

Create `backend/app/domain/usage.py`:

```python
from collections.abc import Mapping, Sequence
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator


ProtocolName = Literal["responses", "chat"]
CallPurpose = Literal["inspection", "schema_fallback", "json_repair"]
CallStatus = Literal["succeeded", "failed"]


class UsageAvailability(StrEnum):
    NOT_STARTED = "not_started"
    COMPLETE = "complete"
    PARTIAL = "partial"
    UNAVAILABLE = "unavailable"
    UNTRACKED = "untracked"


class TokenValues(BaseModel):
    model_config = ConfigDict(extra="forbid")

    input_tokens: int = Field(ge=0)
    cached_input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    reasoning_tokens: int = Field(ge=0)
    total_tokens: int = Field(ge=0)

    @model_validator(mode="after")
    def validate_relationships(self) -> "TokenValues":
        if self.cached_input_tokens > self.input_tokens:
            raise ValueError("cached input tokens exceed input tokens")
        if self.reasoning_tokens > self.output_tokens:
            raise ValueError("reasoning tokens exceed output tokens")
        if self.total_tokens != self.input_tokens + self.output_tokens:
            raise ValueError("total tokens do not equal input plus output")
        return self


class TokenUsageCall(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sequence: int = Field(ge=1)
    purpose: CallPurpose
    status: CallStatus
    usage_available: bool
    usage_error: Literal["not_returned", "invalid_usage"] | None
    input_tokens: int | None
    cached_input_tokens: int | None
    output_tokens: int | None
    reasoning_tokens: int | None
    total_tokens: int | None
    raw_usage: dict[str, Any] | None

    @model_validator(mode="after")
    def validate_shape(self) -> "TokenUsageCall":
        values = (
            self.input_tokens,
            self.cached_input_tokens,
            self.output_tokens,
            self.reasoning_tokens,
            self.total_tokens,
        )
        if self.usage_available:
            if any(value is None for value in values) or self.usage_error is not None:
                raise ValueError("available usage requires every normalized value")
            assert self.input_tokens is not None
            assert self.cached_input_tokens is not None
            assert self.output_tokens is not None
            assert self.reasoning_tokens is not None
            assert self.total_tokens is not None
            TokenValues(
                input_tokens=self.input_tokens,
                cached_input_tokens=self.cached_input_tokens,
                output_tokens=self.output_tokens,
                reasoning_tokens=self.reasoning_tokens,
                total_tokens=self.total_tokens,
            )
        elif any(value is not None for value in values) or self.usage_error is None:
            raise ValueError("unavailable usage cannot contain normalized values")
        return self


class TokenUsageTotals(BaseModel):
    model_config = ConfigDict(extra="forbid")

    known_call_count: int = Field(ge=0)
    unknown_call_count: int = Field(ge=0)
    input_tokens: int = Field(ge=0)
    cached_input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    reasoning_tokens: int = Field(ge=0)
    total_tokens: int = Field(ge=0)


class AttemptUsageTrace(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    protocol: ProtocolName
    calls: list[TokenUsageCall]
    totals: TokenUsageTotals

    @classmethod
    def from_calls(
        cls,
        protocol: ProtocolName,
        calls: Sequence[TokenUsageCall],
    ) -> "AttemptUsageTrace":
        return cls(protocol=protocol, calls=list(calls), totals=_sum_calls(calls))


class TokenUsageSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    availability: UsageAvailability
    input_tokens: int = Field(ge=0)
    cached_input_tokens: int = Field(ge=0)
    uncached_input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    reasoning_tokens: int = Field(ge=0)
    total_tokens: int = Field(ge=0)
    known_call_count: int = Field(ge=0)
    unknown_call_count: int = Field(ge=0)


def _json_value(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Mapping):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    dumper = getattr(value, "model_dump", None)
    if callable(dumper):
        return _json_value(dumper(mode="json"))
    attributes = getattr(value, "__dict__", None)
    if isinstance(attributes, dict):
        return {
            str(key): _json_value(item)
            for key, item in attributes.items()
            if not str(key).startswith("_")
        }
    raise TypeError("usage is not JSON serializable")


def _mapping(value: Any) -> dict[str, Any]:
    converted = _json_value(value)
    if not isinstance(converted, dict):
        raise TypeError("usage value is not an object")
    return converted


def _required_int(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError("usage value is not an integer")
    return value


def _unknown_call(
    sequence: int,
    purpose: CallPurpose,
    status: CallStatus,
    error: Literal["not_returned", "invalid_usage"],
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
        raw = _mapping(raw_usage)
    except TypeError:
        return _unknown_call(sequence, purpose, status, "invalid_usage", None)
    try:
        if protocol == "responses":
            input_details = _mapping(raw["input_tokens_details"])
            output_details = _mapping(raw["output_tokens_details"])
            values = TokenValues(
                input_tokens=_required_int(raw["input_tokens"]),
                cached_input_tokens=_required_int(input_details["cached_tokens"]),
                output_tokens=_required_int(raw["output_tokens"]),
                reasoning_tokens=_required_int(output_details["reasoning_tokens"]),
                total_tokens=_required_int(raw["total_tokens"]),
            )
        else:
            prompt_details = _mapping(raw["prompt_tokens_details"])
            completion_details = _mapping(raw["completion_tokens_details"])
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
        **values.model_dump(),
        raw_usage=raw,
    )


def _sum_calls(calls: Sequence[TokenUsageCall]) -> TokenUsageTotals:
    known = [call for call in calls if call.usage_available]
    return TokenUsageTotals(
        known_call_count=len(known),
        unknown_call_count=len(calls) - len(known),
        input_tokens=sum(int(call.input_tokens or 0) for call in known),
        cached_input_tokens=sum(int(call.cached_input_tokens or 0) for call in known),
        output_tokens=sum(int(call.output_tokens or 0) for call in known),
        reasoning_tokens=sum(int(call.reasoning_tokens or 0) for call in known),
        total_tokens=sum(int(call.total_tokens or 0) for call in known),
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


def parse_attempt_trace(payload: dict[str, Any] | None) -> AttemptUsageTrace | None:
    if payload is None:
        return None
    try:
        return AttemptUsageTrace.model_validate(payload)
    except ValidationError:
        return None


def summarize_attempt_payloads(
    payloads: Sequence[dict[str, Any] | None],
) -> TokenUsageSummary:
    if not payloads:
        availability = UsageAvailability.NOT_STARTED
        totals = TokenUsageTotals(
            known_call_count=0,
            unknown_call_count=0,
            input_tokens=0,
            cached_input_tokens=0,
            output_tokens=0,
            reasoning_tokens=0,
            total_tokens=0,
        )
    else:
        traces = [parse_attempt_trace(payload) for payload in payloads]
        untracked_attempts = sum(trace is None for trace in traces)
        calls = [call for trace in traces if trace is not None for call in trace.calls]
        call_totals = _sum_calls(calls)
        totals = call_totals.model_copy(
            update={"unknown_call_count": call_totals.unknown_call_count + untracked_attempts}
        )
        if untracked_attempts == len(payloads):
            availability = UsageAvailability.UNTRACKED
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
```

- [ ] **Step 4: Run the domain tests and type checker**

Run:

```bash
backend/.venv/bin/pytest backend/tests/domain/test_usage.py -v
backend/.venv/bin/mypy backend/app/domain/usage.py
```

Expected: six tests pass and mypy reports no errors.

- [ ] **Step 5: Commit the usage domain**

```bash
git add backend/app/domain/usage.py backend/tests/domain/test_usage.py
git commit -m "feat: define token usage domain"
```

## Task 2: Capture every Ark SDK call

**Files:**
- Modify: `backend/app/services/ark.py`
- Modify: `backend/tests/services/test_ark.py`

- [ ] **Step 1: Extend Ark adapter tests with realistic usage objects**

Update the fake response builders in `backend/tests/services/test_ark.py` so each queued output can include usage, then add these tests:

```python
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


def test_responses_adapter_returns_normalized_usage(tmp_path) -> None:
    image = tmp_path / "image.jpg"
    image.write_bytes(b"jpeg-bytes")
    responses = FakeResponses([(json.dumps(VALID), RESPONSES_USAGE)])
    service = ArkInspectionService(client=SimpleNamespace(responses=responses))

    outcome = service.inspect_responses(runtime(), "system", "user", image)

    assert outcome.result.verdict is Verdict.PASS
    assert outcome.usage.totals.total_tokens == 120
    assert outcome.usage.calls[0].cached_input_tokens == 40


def test_json_repair_counts_both_responses(tmp_path) -> None:
    image = tmp_path / "image.jpg"
    image.write_bytes(b"jpeg-bytes")
    responses = FakeResponses(
        [
            ("not-json", RESPONSES_USAGE),
            (json.dumps(VALID), RESPONSES_USAGE),
        ]
    )
    service = ArkInspectionService(client=SimpleNamespace(responses=responses))

    outcome = service.inspect_responses(runtime(), "system", "user", image)

    assert [call.purpose for call in outcome.usage.calls] == ["inspection", "json_repair"]
    assert outcome.usage.totals.total_tokens == 240


def test_schema_fallback_keeps_failed_and_successful_call_entries(tmp_path) -> None:
    image = tmp_path / "image.jpg"
    image.write_bytes(b"jpeg-bytes")
    responses = FakeResponses(
        [UnsupportedSchemaError(), (json.dumps(VALID), RESPONSES_USAGE)]
    )
    service = ArkInspectionService(client=SimpleNamespace(responses=responses))

    outcome = service.inspect_responses(runtime(), "system", "user", image)

    assert [call.purpose for call in outcome.usage.calls] == [
        "inspection",
        "schema_fallback",
    ]
    assert outcome.usage.totals.known_call_count == 1
    assert outcome.usage.totals.unknown_call_count == 1


def test_chat_adapter_maps_chat_usage(tmp_path) -> None:
    image = tmp_path / "image.jpg"
    image.write_bytes(b"jpeg-bytes")
    completions = FakeCompletions([(json.dumps(VALID), CHAT_USAGE)])
    service = ArkInspectionService(
        client=SimpleNamespace(chat=SimpleNamespace(completions=completions))
    )

    outcome = service.inspect_chat(runtime("chat"), "system", "user", image)

    assert outcome.usage.totals.input_tokens == 80
    assert outcome.usage.totals.cached_input_tokens == 30


def test_failed_repair_attaches_usage_collected_before_the_error(tmp_path) -> None:
    image = tmp_path / "image.jpg"
    image.write_bytes(b"jpeg-bytes")
    responses = FakeResponses(
        [("not-json", RESPONSES_USAGE), RuntimeError("repair failed")]
    )
    service = ArkInspectionService(client=SimpleNamespace(responses=responses))

    with pytest.raises(ArkRequestError) as caught:
        service.inspect_responses(runtime(), "system", "user", image)

    assert caught.value.usage is not None
    assert caught.value.usage.totals.known_call_count == 1
    assert caught.value.usage.totals.unknown_call_count == 1
```

Change `FakeResponses.create()` and `FakeCompletions.create()` so a tuple is unpacked into `(text, usage)` and a plain string continues to produce `usage=None`. Update existing adapter assertions from `result.verdict` to `outcome.result.verdict`.

Use these exact fake implementations:

```python
class FakeResponses:
    def __init__(self, outputs: list[str | tuple[str, object] | Exception]) -> None:
        self.outputs = outputs
        self.calls: list[dict] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        output = self.outputs.pop(0)
        if isinstance(output, Exception):
            raise output
        text, usage = output if isinstance(output, tuple) else (output, None)
        return SimpleNamespace(output_text=text, usage=usage)


class FakeCompletions:
    def __init__(self, outputs: list[str | tuple[str, object] | Exception]) -> None:
        self.outputs = outputs
        self.calls: list[dict] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        output = self.outputs.pop(0)
        if isinstance(output, Exception):
            raise output
        text, usage = output if isinstance(output, tuple) else (output, None)
        message = SimpleNamespace(content=text)
        return SimpleNamespace(
            choices=[SimpleNamespace(message=message)],
            usage=usage,
        )
```

- [ ] **Step 2: Run the Ark tests and verify the new outcome API is missing**

Run:

```bash
backend/.venv/bin/pytest backend/tests/services/test_ark.py -v
```

Expected: failures show that `inspect*()` still returns `InspectionResult` and no usage trace exists.

- [ ] **Step 3: Add usage-aware outcomes and errors**

Add these imports and contracts near the top of `backend/app/services/ark.py`:

```python
from collections.abc import Callable, Mapping
from dataclasses import dataclass

from app.domain.usage import (
    AttemptUsageCollector,
    AttemptUsageTrace,
    CallPurpose,
)


@dataclass(frozen=True)
class InspectionOutcome:
    result: InspectionResult
    usage: AttemptUsageTrace
```

Change `InspectionServiceProtocol.inspect()`, `KeychainArkInspectionService.inspect()`, `ArkInspectionService.inspect()`, `inspect_responses()`, and `inspect_chat()` to return `InspectionOutcome`.

When keychain access fails before a provider request is made, attach an empty trace so the attempt is new-format telemetry rather than mistaken for a legacy row:

```python
empty_usage = AttemptUsageTrace.from_calls(config.protocol, [])
raise ArkRequestError(code="keychain_unavailable", usage=empty_usage)
```

Use the same empty trace for `ark_api_key_missing`.

Give both error types a trace without changing existing status/header behavior:

```python
class ArkRequestError(RuntimeError):
    def __init__(
        self,
        status_code: int | None = None,
        headers: Mapping[str, str] | None = None,
        code: str | None = None,
        usage: AttemptUsageTrace | None = None,
    ) -> None:
        super().__init__("Ark request failed")
        self.status_code = status_code
        self.headers = dict(headers or {})
        self.code = code
        self.usage = usage


class MalformedInspectionResult(ValueError):
    def __init__(self, message: str, usage: AttemptUsageTrace | None = None) -> None:
        super().__init__(message)
        self.usage = usage
```

- [ ] **Step 4: Wrap each provider invocation and return the completed trace**

Add one helper to `ArkInspectionService`:

```python
    @staticmethod
    def _tracked_call(
        collector: AttemptUsageCollector,
        purpose: CallPurpose,
        call: Callable[[], Any],
    ) -> Any:
        try:
            response = call()
        except Exception:
            collector.add_missing(purpose, status="failed")
            raise
        collector.add_response(purpose, getattr(response, "usage", None))
        return response
```

Refactor `inspect_responses()` and `inspect_chat()` so every `client.*.create()` call runs through `_tracked_call()`. Use `inspection` for the first call, `schema_fallback` for the JSON Object fallback, and `json_repair` for repair. Return:

```python
return InspectionOutcome(result=result, usage=collector.snapshot())
```

When a provider call fails, call `_request_error(exc, collector.snapshot())`; when final parsing fails, raise `MalformedInspectionResult(str(exc), collector.snapshot())`. Change `_request_error()` to preserve an existing trace and otherwise attach the supplied one:

```python
    @staticmethod
    def _request_error(
        exc: Exception,
        usage: AttemptUsageTrace,
    ) -> ArkRequestError:
        if isinstance(exc, ArkRequestError):
            if exc.usage is None:
                exc.usage = usage
            return exc
        response = getattr(exc, "response", None)
        status = getattr(exc, "status_code", None) or getattr(response, "status_code", None)
        raw_headers = getattr(exc, "headers", None) or getattr(response, "headers", None) or {}
        headers = {str(key): str(value) for key, value in dict(raw_headers).items()}
        code_value = getattr(exc, "code", None)
        code = None if code_value is None else str(code_value)
        return ArkRequestError(
            status_code=status,
            headers=headers,
            code=code,
            usage=usage,
        )
```

The repair helpers must return the provider response object rather than only its text, so the outer method can capture usage before calling `_responses_text()` or `_chat_text()`. Remove their internal exception conversion, as well as the internal conversion in both JSON Object fallback helpers. The two outer protocol methods become the only places that call `_request_error(exc, collector.snapshot())`; this guarantees that every exception carries all calls recorded so far and prevents a helper from dropping the trace.

- [ ] **Step 5: Run Ark tests, lint, and type checks**

Run:

```bash
backend/.venv/bin/pytest backend/tests/services/test_ark.py -v
backend/.venv/bin/ruff check backend/app/services/ark.py backend/tests/services/test_ark.py
backend/.venv/bin/mypy backend/app/services/ark.py
```

Expected: all Ark tests pass with no Ruff or mypy errors.

- [ ] **Step 6: Commit the Ark capture layer**

```bash
git add backend/app/services/ark.py backend/tests/services/test_ark.py
git commit -m "feat: capture Ark token usage"
```

## Task 3: Persist usage for success, failure, retry, and test doubles

**Files:**
- Modify: `backend/app/services/runner.py`
- Modify: `backend/tests/services/test_runner.py`
- Modify: `backend/tests/conftest.py`
- Modify: `backend/tests/integration/test_100_image_run.py`
- Modify: `backend/tests/e2e_server.py`
- Modify: `backend/tests/persistence/test_tasks.py`

- [ ] **Step 1: Add failing Runner assertions for persisted traces**

In `backend/tests/services/test_runner.py`, add a helper that produces a real usage-aware result:

```python
from app.domain.usage import AttemptUsageCollector
from app.services.ark import InspectionOutcome


def passing_outcome(marker: str, input_tokens: int = 10) -> InspectionOutcome:
    collector = AttemptUsageCollector("responses")
    collector.add_response(
        "inspection",
        {
            "input_tokens": input_tokens,
            "input_tokens_details": {"cached_tokens": 4},
            "output_tokens": 2,
            "output_tokens_details": {"reasoning_tokens": 0},
            "total_tokens": input_tokens + 2,
        },
    )
    return InspectionOutcome(result=passing_result(marker), usage=collector.snapshot())
```

Change the successful branch of `FakeInspector.inspect()` to return `passing_outcome(image_path.name)`. For simulated errors, create a collector, call `add_missing("inspection", status="failed")`, and attach `collector.snapshot()` to `ArkRequestError` or `MalformedInspectionResult`.

Add these assertions/tests:

```python
    def attempt_usages(self) -> list[dict | None]:
        with self.sessions() as session:  # type: ignore[operator]
            attempts = session.scalars(
                select(InferenceAttempt).order_by(InferenceAttempt.created_at)
            ).all()
            return [attempt.usage_json for attempt in attempts]


@pytest.mark.asyncio
async def test_runner_persists_successful_usage_trace(tmp_path) -> None:
    fixture = make_task_fixture(tmp_path, image_count=1)
    runner = TaskRunner(fixture.sessions, FakeInspector(), concurrency=1)

    await runner.run_task(fixture.task_id)

    payload = fixture.attempt_usages()[0]
    assert payload is not None
    assert payload["totals"]["input_tokens"] == 10
    assert payload["totals"]["cached_input_tokens"] == 4
    assert payload["totals"]["total_tokens"] == 12


@pytest.mark.asyncio
async def test_runner_keeps_unknown_retry_and_known_success(tmp_path) -> None:
    fixture = make_task_fixture(tmp_path, image_count=1)
    inspector = FakeInspector(rate_limit_failures=1)
    runner = TaskRunner(fixture.sessions, inspector, concurrency=1, sleep=lambda _delay: None)

    await runner.run_task(fixture.task_id)

    payloads = fixture.attempt_usages()
    assert len(payloads) == 2
    assert payloads[0]["totals"]["unknown_call_count"] == 1
    assert payloads[1]["totals"]["total_tokens"] == 12
```

- [ ] **Step 2: Run Runner tests and verify they fail because usage is discarded**

Run:

```bash
backend/.venv/bin/pytest backend/tests/services/test_runner.py -v
```

Expected: the new assertions fail because `_record_attempt()` receives no usage and successful inspectors still return only a result.

- [ ] **Step 3: Persist the outcome or error trace in every Runner branch**

Change `_inspect_with_retry()` in `backend/app/services/runner.py` to use `InspectionOutcome`:

```python
    def _inspect_with_retry(self, task_id: str, image: TaskImage) -> InspectionResult:
        config, system_prompt, user_prompt = self._inspection_context(task_id, image)
        policy = RetryPolicy(config.max_retries)
        retry_index = 0
        while True:
            started = time.monotonic()
            try:
                outcome = self.inspector.inspect(
                    config,
                    system_prompt,
                    user_prompt,
                    Path(image.inference_path),
                )
            except ArkRequestError as exc:
                self._record_attempt(
                    image.id,
                    status="failed",
                    status_code=exc.status_code,
                    error_code=exc.code or "ark_request_failed",
                    duration_ms=self._duration_ms(started),
                    usage=None if exc.usage is None else exc.usage.model_dump(mode="json"),
                )
                decision = policy.classify(exc.status_code, exc.headers, retry_index)
                if decision.pause_task:
                    raise PauseTaskError(
                        exc.code or "ark_configuration_error",
                        "Ark request requires configuration review",
                    ) from exc
                if not decision.retry:
                    raise InspectionFailed(
                        exc.code or "ark_request_failed",
                        "Ark request failed after retries",
                    ) from exc
                self.sleep(decision.delay_seconds)
                retry_index += 1
                continue
            except MalformedInspectionResult as exc:
                self._record_attempt(
                    image.id,
                    status="failed",
                    error_code="invalid_model_output",
                    duration_ms=self._duration_ms(started),
                    usage=None if exc.usage is None else exc.usage.model_dump(mode="json"),
                )
                raise InspectionFailed("invalid_model_output", str(exc)) from exc
            self._record_attempt(
                image.id,
                status="succeeded",
                duration_ms=self._duration_ms(started),
                usage=outcome.usage.model_dump(mode="json"),
            )
            return outcome.result
```

Add `usage: dict[str, object] | None = None` to `_record_attempt()` and forward it to `TaskRepository.record_attempt()`.

- [ ] **Step 4: Make every backend fake inspector return an `InspectionOutcome`**

Update the fake inspectors in the four listed test files. Each successful fake must wrap its current `InspectionResult` with an `AttemptUsageCollector` trace. Use stable fixture values, for example 10 input, 4 cached, 2 output, 0 reasoning, and 12 total. Error fakes must attach a trace containing one failed, missing-usage `inspection` call.

For example, the return in `backend/tests/conftest.py` becomes:

```python
        result = InspectionResult(
            image_valid=True,
            verdict=Verdict.PASS,
            summary="test image is readable",
            findings=[
                Finding(
                    rule_id="readable",
                    rule_name="readable",
                    status=FindingStatus.PASS,
                    observation="image decoded",
                    visual_evidence=image_path.name,
                )
            ],
        )
        collector = AttemptUsageCollector(config.protocol)
        collector.add_response(
            "inspection",
            {
                "input_tokens": 10,
                "input_tokens_details": {"cached_tokens": 4},
                "output_tokens": 2,
                "output_tokens_details": {"reasoning_tokens": 0},
                "total_tokens": 12,
            },
        )
        return InspectionOutcome(result=result, usage=collector.snapshot())
```

When `config.protocol == "chat"`, use the Chat field names from Task 1 so the fake remains valid for both settings modes.

- [ ] **Step 5: Store a versioned trace in the persistence test**

In `backend/tests/persistence/test_tasks.py`, replace the legacy `usage={"input_tokens": 10}` fixture with an `AttemptUsageCollector("responses").snapshot().model_dump(mode="json")`, then assert the saved attempt retains `schema_version == 1` and `totals.total_tokens == 12`.

- [ ] **Step 6: Run Runner, persistence, API fixture, and integration tests**

Run:

```bash
backend/.venv/bin/pytest \
  backend/tests/services/test_runner.py \
  backend/tests/persistence/test_tasks.py \
  backend/tests/api/test_settings.py \
  backend/tests/integration/test_100_image_run.py -v
backend/.venv/bin/ruff check backend/app backend/tests
backend/.venv/bin/mypy backend/app
```

Expected: all selected tests pass, Ruff is clean, and mypy reports no errors.

- [ ] **Step 7: Commit persistence through the Runner**

```bash
git add \
  backend/app/services/runner.py \
  backend/tests/services/test_runner.py \
  backend/tests/conftest.py \
  backend/tests/integration/test_100_image_run.py \
  backend/tests/e2e_server.py \
  backend/tests/persistence/test_tasks.py
git commit -m "feat: persist token usage for inspection attempts"
```

## Task 4: Expose task, image, and call-level usage through the API

**Files:**
- Modify: `backend/app/repositories.py`
- Modify: `backend/app/schemas.py`
- Modify: `backend/app/api/tasks.py`
- Modify: `backend/tests/persistence/test_tasks.py`
- Modify: `backend/tests/api/test_tasks.py`

- [ ] **Step 1: Write failing repository and API tests**

Add a repository assertion that attempts for several images are loaded in one ordered collection:

```python
        attempts = repo.list_attempts_for_tasks([task.id])
        assert [attempt.image_id for attempt in attempts[task.id]] == [first.id]
```

Extend `test_create_upload_start_and_fetch_results()` in `backend/tests/api/test_tasks.py`:

```python
    assert detail["token_usage"] == {
        "availability": "complete",
        "input_tokens": 10,
        "cached_input_tokens": 4,
        "uncached_input_tokens": 6,
        "output_tokens": 2,
        "reasoning_tokens": 0,
        "total_tokens": 12,
        "known_call_count": 1,
        "unknown_call_count": 0,
    }
    assert detail["images"][0]["token_usage"]["total_tokens"] == 12
    assert detail["images"][0]["token_calls"][0] == {
        "attempt_number": 1,
        "sequence": 1,
        "protocol": "responses",
        "purpose": "inspection",
        "status": "succeeded",
        "usage_error": None,
        "input_tokens": 10,
        "cached_input_tokens": 4,
        "output_tokens": 2,
        "reasoning_tokens": 0,
        "total_tokens": 12,
    }
```

In the retry test, assert the final image and task summaries are `partial`, have one known call, one unknown call, and show only the successful call's 12 known Tokens.

- [ ] **Step 2: Run the focused tests and confirm the API fields are absent**

Run:

```bash
backend/.venv/bin/pytest \
  backend/tests/persistence/test_tasks.py \
  backend/tests/api/test_tasks.py -v
```

Expected: failures mention missing `list_attempts_for_tasks`, `token_usage`, and `token_calls`.

- [ ] **Step 3: Batch-load attempts in `TaskRepository`**

Add this method to `TaskRepository`:

```python
    def list_attempts_for_tasks(
        self,
        task_ids: list[str],
    ) -> dict[str, list[InferenceAttempt]]:
        grouped = {task_id: [] for task_id in task_ids}
        if not task_ids:
            return grouped
        rows = self.session.execute(
            select(TaskImage.task_id, InferenceAttempt)
            .join(InferenceAttempt, InferenceAttempt.image_id == TaskImage.id)
            .where(TaskImage.task_id.in_(task_ids))
            .order_by(
                TaskImage.task_id,
                TaskImage.position,
                InferenceAttempt.attempt_number,
                InferenceAttempt.created_at,
                InferenceAttempt.id,
            )
        ).all()
        for task_id, attempt in rows:
            grouped[str(task_id)].append(attempt)
        return grouped
```

- [ ] **Step 4: Add typed API usage structures**

Import `TokenUsageSummary` from `app.domain.usage` in `backend/app/schemas.py`, then add:

```python
class TokenUsageCallRead(BaseModel):
    attempt_number: int
    sequence: int
    protocol: Literal["responses", "chat"]
    purpose: Literal["inspection", "schema_fallback", "json_repair"]
    status: Literal["succeeded", "failed"]
    usage_error: Literal["not_returned", "invalid_usage"] | None
    input_tokens: int | None
    cached_input_tokens: int | None
    output_tokens: int | None
    reasoning_tokens: int | None
    total_tokens: int | None
```

Add `token_usage: TokenUsageSummary` and `token_calls: list[TokenUsageCallRead]` to `TaskImageRead`. Add `token_usage: TokenUsageSummary` to `TaskSummary`.

- [ ] **Step 5: Build summaries and safe call details in the task API**

Import `InferenceAttempt`, `TokenUsageSummary`, `parse_attempt_trace`, `summarize_attempt_payloads`, and `TokenUsageCallRead` in `backend/app/api/tasks.py`. Add:

```python
def _usage_summary(attempts: list[InferenceAttempt]) -> TokenUsageSummary:
    return summarize_attempt_payloads([attempt.usage_json for attempt in attempts])


def _usage_calls(attempts: list[InferenceAttempt]) -> list[TokenUsageCallRead]:
    result: list[TokenUsageCallRead] = []
    for attempt in attempts:
        trace = parse_attempt_trace(attempt.usage_json)
        if trace is None:
            continue
        for call in trace.calls:
            result.append(
                TokenUsageCallRead(
                    attempt_number=attempt.attempt_number,
                    sequence=call.sequence,
                    protocol=trace.protocol,
                    purpose=call.purpose,
                    status=call.status,
                    usage_error=call.usage_error,
                    input_tokens=call.input_tokens,
                    cached_input_tokens=call.cached_input_tokens,
                    output_tokens=call.output_tokens,
                    reasoning_tokens=call.reasoning_tokens,
                    total_tokens=call.total_tokens,
                )
            )
    return result
```

Replace the projection helpers with usage-aware versions:

```python
def _task_summary(
    deps: TaskDependencies,
    task: InspectionTask,
    attempts: list[InferenceAttempt],
) -> TaskSummary:
    template = task.template_snapshot_json
    model = task.model_snapshot_json
    report = deps.repo.get_active_export(task.id)
    return TaskSummary(
        id=task.id,
        name=task.name,
        status=task.status,
        template_name=str(template.get("template_name", "")),
        template_version=int(template.get("template_version", 0)),
        model=str(model.get("model", "")),
        created_at=task.created_at,
        started_at=task.started_at,
        completed_at=task.completed_at,
        counts=_task_counts(deps, task.id),
        report_id=None if report is None else report.id,
        report_status=None if report is None else report.status,
        report_url=None if report is None else report.document_url,
        token_usage=_usage_summary(attempts),
    )


def _task_detail(
    deps: TaskDependencies,
    task: InspectionTask,
    attempts: list[InferenceAttempt],
) -> TaskDetail:
    summary = _task_summary(deps, task, attempts)
    attempts_by_image: dict[str, list[InferenceAttempt]] = {}
    for attempt in attempts:
        attempts_by_image.setdefault(attempt.image_id, []).append(attempt)
    images = []
    for image in deps.repo.list_images(task.id):
        result = deps.repo.get_current_result(image.id)
        image_attempts = attempts_by_image.get(image.id, [])
        images.append(
            TaskImageRead(
                id=image.id,
                position=image.position,
                original_name=image.original_name,
                sha256=image.sha256,
                status=image.status,
                attempt_count=image.attempt_count,
                error_code=image.error_code,
                error_message=image.error_message,
                original_url=f"/api/tasks/{task.id}/images/{image.id}/content?variant=original",
                thumbnail_url=f"/api/tasks/{task.id}/images/{image.id}/content?variant=thumbnail",
                result=None if result is None else result.result_json,
                token_usage=_usage_summary(image_attempts),
                token_calls=_usage_calls(image_attempts),
            )
        )
    return TaskDetail(
        **summary.model_dump(),
        template_snapshot=task.template_snapshot_json,
        model_snapshot=task.model_snapshot_json,
        images=images,
    )
```

Load attempts once in each route:

```python
# create_task(), after commit-ready construction
return _task_detail(deps, task, [])

# list_tasks()
tasks = deps.repo.list_tasks()
attempts_by_task = deps.repo.list_attempts_for_tasks([task.id for task in tasks])
return [
    _task_summary(deps, task, attempts_by_task[task.id])
    for task in tasks
]

# get_task()
attempts = deps.repo.list_attempts_for_tasks([task.id])[task.id]
return _task_detail(deps, task, attempts)
```

- [ ] **Step 6: Run API, persistence, lint, and type checks**

Run:

```bash
backend/.venv/bin/pytest \
  backend/tests/persistence/test_tasks.py \
  backend/tests/api/test_tasks.py -v
backend/.venv/bin/ruff check backend/app/repositories.py backend/app/schemas.py backend/app/api/tasks.py
backend/.venv/bin/mypy backend/app
```

Expected: focused tests pass with no lint or type errors.

- [ ] **Step 7: Commit the API contract**

```bash
git add \
  backend/app/repositories.py \
  backend/app/schemas.py \
  backend/app/api/tasks.py \
  backend/tests/persistence/test_tasks.py \
  backend/tests/api/test_tasks.py
git commit -m "feat: expose task token usage"
```

## Task 5: Add task and image Token usage UI

**Files:**
- Modify: `frontend/src/api/types.ts`
- Create: `frontend/src/features/tasks/TokenUsageSummary.tsx`
- Create: `frontend/src/features/tasks/TokenUsageDetails.tsx`
- Modify: `frontend/src/features/tasks/TaskToolbar.tsx`
- Modify: `frontend/src/features/tasks/ImageDetail.tsx`
- Modify: `frontend/src/features/tasks/TaskPage.test.tsx`
- Modify: `frontend/src/styles.css`

- [ ] **Step 1: Add failing component-level assertions to the task page test**

Add these typed usage fixtures near the top of `frontend/src/features/tasks/TaskPage.test.tsx`:

```tsx
const taskUsage = {
  availability: "partial" as const,
  input_tokens: 1701,
  cached_input_tokens: 520,
  uncached_input_tokens: 1181,
  output_tokens: 299,
  reasoning_tokens: 0,
  total_tokens: 2000,
  known_call_count: 3,
  unknown_call_count: 2,
};

const selectedImageUsage = {
  availability: "partial" as const,
  input_tokens: 1101,
  cached_input_tokens: 420,
  uncached_input_tokens: 681,
  output_tokens: 183,
  reasoning_tokens: 0,
  total_tokens: 1284,
  known_call_count: 2,
  unknown_call_count: 1,
};

const selectedImageCalls = [
  {
    attempt_number: 1,
    sequence: 1,
    protocol: "responses" as const,
    purpose: "inspection" as const,
    status: "succeeded" as const,
    usage_error: null,
    input_tokens: 712,
    cached_input_tokens: 420,
    output_tokens: 108,
    reasoning_tokens: 0,
    total_tokens: 820,
  },
  {
    attempt_number: 1,
    sequence: 2,
    protocol: "responses" as const,
    purpose: "json_repair" as const,
    status: "succeeded" as const,
    usage_error: null,
    input_tokens: 389,
    cached_input_tokens: 0,
    output_tokens: 75,
    reasoning_tokens: 0,
    total_tokens: 464,
  },
  {
    attempt_number: 2,
    sequence: 1,
    protocol: "responses" as const,
    purpose: "inspection" as const,
    status: "failed" as const,
    usage_error: "not_returned" as const,
    input_tokens: null,
    cached_input_tokens: null,
    output_tokens: null,
    reasoning_tokens: null,
    total_tokens: null,
  },
];
```

Set `taskDetail.token_usage = taskUsage`. Give `IMG_001.png` a complete 716-Token summary with no unknown calls, `IMG_002.png` the `selectedImageUsage` and `selectedImageCalls`, and `IMG_003.png` an unavailable summary with one unknown call and one failed inspection call. Then add:

```tsx
test("shows task totals and expands every call for the selected image", async () => {
  const user = userEvent.setup();
  renderTask();

  const taskUsage = await screen.findByRole("region", { name: "任务 Token 用量" });
  expect(within(taskUsage).getByText("2,000")).toBeInTheDocument();
  expect(within(taskUsage).getByText("缓存命中")).toBeInTheDocument();
  expect(within(taskUsage).getByText("2 次调用用量未知")).toBeInTheDocument();

  await user.click(screen.getByRole("button", { name: "查看 IMG_002.png" }));
  const imageUsage = screen.getByRole("region", { name: "IMG_002.png Token 用量" });
  await user.click(within(imageUsage).getByRole("button", { name: "展开调用明细" }));

  expect(within(imageUsage).getByText("尝试 1 · 初始检查")).toBeInTheDocument();
  expect(within(imageUsage).getByText("尝试 1 · JSON 修复")).toBeInTheDocument();
  expect(within(imageUsage).getByText("方舟未返回 usage")).toBeInTheDocument();
});


test("labels old usage as untracked instead of zero", async () => {
  renderTask({
    ...taskDetail,
    token_usage: {
      availability: "untracked",
      input_tokens: 0,
      cached_input_tokens: 0,
      uncached_input_tokens: 0,
      output_tokens: 0,
      reasoning_tokens: 0,
      total_tokens: 0,
      known_call_count: 0,
      unknown_call_count: 1,
    },
  });

  expect(await screen.findByText("该任务运行时尚未采集 Token 用量")).toBeInTheDocument();
  expect(screen.queryByText("总 Token 0")).not.toBeInTheDocument();
});
```

- [ ] **Step 2: Run the task-page test and confirm the UI is absent**

Run:

```bash
npm test -- src/features/tasks/TaskPage.test.tsx
```

Run from `frontend/`. Expected: the new regions and labels are not found.

- [ ] **Step 3: Add frontend API types**

Add these definitions to `frontend/src/api/types.ts`:

```typescript
export type TokenUsageAvailability =
  | "not_started"
  | "complete"
  | "partial"
  | "unavailable"
  | "untracked";

export interface TokenUsageSummary {
  availability: TokenUsageAvailability;
  input_tokens: number;
  cached_input_tokens: number;
  uncached_input_tokens: number;
  output_tokens: number;
  reasoning_tokens: number;
  total_tokens: number;
  known_call_count: number;
  unknown_call_count: number;
}

export interface TokenUsageCall {
  attempt_number: number;
  sequence: number;
  protocol: "responses" | "chat";
  purpose: "inspection" | "schema_fallback" | "json_repair";
  status: "succeeded" | "failed";
  usage_error: "not_returned" | "invalid_usage" | null;
  input_tokens: number | null;
  cached_input_tokens: number | null;
  output_tokens: number | null;
  reasoning_tokens: number | null;
  total_tokens: number | null;
}
```

Add `token_usage: TokenUsageSummary` and `token_calls: TokenUsageCall[]` to `TaskImage`, and add `token_usage: TokenUsageSummary` to `TaskSummary`.

- [ ] **Step 4: Implement the task summary component**

Create `frontend/src/features/tasks/TokenUsageSummary.tsx` with:

```tsx
import type { TokenUsageSummary as Usage } from "../../api/types";

const formatter = new Intl.NumberFormat("zh-CN");

export function formatTokens(value: number) {
  return formatter.format(value);
}

export function TokenUsageSummary({ usage }: { usage: Usage }) {
  if (usage.availability === "not_started") {
    return <section className="token-empty" aria-label="任务 Token 用量">尚无模型调用</section>;
  }
  if (usage.availability === "untracked") {
    return (
      <section className="token-empty" aria-label="任务 Token 用量">
        该任务运行时尚未采集 Token 用量
      </section>
    );
  }
  if (usage.availability === "unavailable") {
    return (
      <section className="token-empty token-warning" aria-label="任务 Token 用量">
        方舟未返回 Token 用量
      </section>
    );
  }
  const cacheRate = usage.input_tokens === 0
    ? null
    : `${((usage.cached_input_tokens / usage.input_tokens) * 100).toFixed(1)}%`;
  return (
    <section className="token-summary" aria-label="任务 Token 用量">
      <header>
        <strong>任务 Token 用量</strong>
        {usage.unknown_call_count > 0 && (
          <span>{usage.unknown_call_count} 次调用用量未知</span>
        )}
      </header>
      <div className="token-summary-grid">
        <div><span>{usage.availability === "partial" ? "已知总 Token" : "总 Token"}</span><strong>{formatTokens(usage.total_tokens)}</strong></div>
        <div><span>输入</span><strong>{formatTokens(usage.input_tokens)}</strong></div>
        <div><span>缓存命中</span><strong>{formatTokens(usage.cached_input_tokens)}</strong><small>{cacheRate ?? "—"}</small></div>
        <div><span>未缓存输入</span><strong>{formatTokens(usage.uncached_input_tokens)}</strong></div>
        <div><span>输出</span><strong>{formatTokens(usage.output_tokens)}</strong></div>
        <div><span>推理 Token</span><strong>{formatTokens(usage.reasoning_tokens)}</strong><small>包含在输出中</small></div>
      </div>
    </section>
  );
}
```

Render `<TokenUsageSummary usage={task.token_usage} />` immediately after `.task-metrics` in `TaskToolbar.tsx`.

- [ ] **Step 5: Implement expandable image call details**

Create `frontend/src/features/tasks/TokenUsageDetails.tsx`:

```tsx
import { useState } from "react";

import type { TokenUsageCall, TokenUsageSummary } from "../../api/types";
import { formatTokens } from "./TokenUsageSummary";

const purposeLabels = {
  inspection: "初始检查",
  schema_fallback: "Schema 降级",
  json_repair: "JSON 修复",
} as const;

function emptyMessage(usage: TokenUsageSummary) {
  if (usage.availability === "not_started") return "尚无模型调用";
  if (usage.availability === "untracked") return "该图片运行时尚未采集 Token 用量";
  return "方舟未返回 Token 用量";
}

function callError(call: TokenUsageCall) {
  return call.usage_error === "invalid_usage" ? "usage 数据异常" : "方舟未返回 usage";
}

export function TokenUsageDetails({
  imageName,
  usage,
  calls,
}: {
  imageName: string;
  usage: TokenUsageSummary;
  calls: TokenUsageCall[];
}) {
  const [expanded, setExpanded] = useState(false);
  const hasKnownUsage = usage.availability === "complete" || usage.availability === "partial";

  return (
    <section className="image-token-usage" aria-label={`${imageName} Token 用量`}>
      <div className="image-token-heading">
        <strong>本图 Token 用量</strong>
        {calls.length > 0 && (
          <button
            type="button"
            aria-expanded={expanded}
            onClick={() => setExpanded((current) => !current)}
          >
            {expanded ? "收起调用明细" : "展开调用明细"}
          </button>
        )}
      </div>

      {!hasKnownUsage ? (
        <p className="token-state-message">{emptyMessage(usage)}</p>
      ) : (
        <>
          <div className="image-token-grid">
            <div><span>{usage.availability === "partial" ? "已知总 Token" : "总 Token"}</span><strong>{formatTokens(usage.total_tokens)}</strong></div>
            <div><span>输入</span><strong>{formatTokens(usage.input_tokens)}</strong></div>
            <div><span>缓存命中</span><strong>{formatTokens(usage.cached_input_tokens)}</strong></div>
            <div><span>输出</span><strong>{formatTokens(usage.output_tokens)}</strong></div>
            <div><span>推理 Token</span><strong>{formatTokens(usage.reasoning_tokens)}</strong></div>
          </div>
          {usage.unknown_call_count > 0 && (
            <p className="token-call-warning">{usage.unknown_call_count} 次调用用量未知，未计入已知总量。</p>
          )}
        </>
      )}

      {expanded && (
        <div className="token-call-table-wrap">
          <table className="token-call-table">
            <thead>
              <tr><th>调用</th><th>输入</th><th>缓存</th><th>输出</th><th>推理</th><th>总计</th></tr>
            </thead>
            <tbody>
              {calls.map((call) => {
                const rowLabel = `尝试 ${call.attempt_number} · ${purposeLabels[call.purpose]}`;
                return (
                  <tr key={`${call.attempt_number}-${call.sequence}`}>
                    <td>{rowLabel}</td>
                    {call.total_tokens === null ? (
                      <td colSpan={5} className="token-call-error">{callError(call)}</td>
                    ) : (
                      <>
                        <td>{call.input_tokens === null ? "—" : formatTokens(call.input_tokens)}</td>
                        <td>{call.cached_input_tokens === null ? "—" : formatTokens(call.cached_input_tokens)}</td>
                        <td>{call.output_tokens === null ? "—" : formatTokens(call.output_tokens)}</td>
                        <td>{call.reasoning_tokens === null ? "—" : formatTokens(call.reasoning_tokens)}</td>
                        <td>{formatTokens(call.total_tokens)}</td>
                      </>
                    )}
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}
```

Render the component after `ResultPanel` and before execution-state messages in `ImageDetail.tsx`:

```tsx
<TokenUsageDetails
  imageName={image.original_name}
  usage={image.token_usage}
  calls={image.token_calls}
/>
```

- [ ] **Step 6: Add responsive styles matching the existing workspace**

Add these rules to `frontend/src/styles.css`:

```css
.token-summary,
.token-empty,
.image-token-usage {
  margin-bottom: 20px;
  background: #f6f8f4;
  border: 1px solid #cfd9ca;
  border-radius: var(--radius);
}

.token-summary > header,
.image-token-heading {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 11px 16px;
  border-bottom: 1px solid #dbe2d8;
  font-size: 11px;
}

.token-summary > header > span,
.token-call-warning {
  color: var(--warning);
  font-size: 10px;
  font-weight: 700;
}

.token-summary-grid,
.image-token-grid {
  display: grid;
  grid-template-columns: repeat(6, minmax(0, 1fr));
}

.token-summary-grid > div,
.image-token-grid > div {
  padding: 14px 16px;
  border-right: 1px solid #dbe2d8;
}

.token-summary-grid > div:last-child,
.image-token-grid > div:last-child {
  border-right: 0;
}

.token-summary-grid span,
.image-token-grid span {
  display: block;
  color: var(--ink-muted);
  font-size: 10px;
}

.token-summary-grid strong,
.image-token-grid strong {
  display: block;
  margin-top: 5px;
  font-family: "SF Mono", "JetBrains Mono", monospace;
  font-size: 18px;
}

.token-summary-grid small {
  color: var(--ink-muted);
  font-size: 9px;
}

.token-empty {
  padding: 15px 18px;
  color: var(--ink-muted);
  font-size: 12px;
}

.token-warning,
.token-call-error {
  color: var(--warning);
}

.image-token-usage {
  margin: 18px 0 0;
  overflow: hidden;
}

.image-token-heading button {
  padding: 0;
  color: var(--accent);
  background: transparent;
  border: 0;
  cursor: pointer;
  font-size: 10px;
  font-weight: 750;
}

.image-token-grid {
  grid-template-columns: repeat(5, minmax(0, 1fr));
}

.image-token-grid > div {
  padding: 11px;
}

.image-token-grid strong {
  font-size: 13px;
}

.token-state-message,
.token-call-warning {
  margin: 0;
  padding: 13px 16px;
}

.token-call-table-wrap {
  overflow-x: auto;
  border-top: 1px solid #dbe2d8;
}

.token-call-table {
  width: 100%;
  min-width: 620px;
  border-collapse: collapse;
  font-size: 10px;
}

.token-call-table th,
.token-call-table td {
  padding: 9px 10px;
  border-bottom: 1px solid #e4e9e1;
  text-align: right;
}

.token-call-table th:first-child,
.token-call-table td:first-child {
  text-align: left;
}

.token-call-table tbody tr:last-child td {
  border-bottom: 0;
}
```

Inside the existing `@media (max-width: 760px)` block add:

```css
  .token-summary-grid,
  .image-token-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }

  .token-summary-grid > div,
  .image-token-grid > div {
    border-bottom: 1px solid #dbe2d8;
  }
```

- [ ] **Step 7: Run task UI tests and build**

Run from `frontend/`:

```bash
npm test -- src/features/tasks/TaskPage.test.tsx
npm run build
```

Expected: all task-page tests pass and the TypeScript/Vite build exits zero.

- [ ] **Step 8: Commit the task usage UI**

```bash
git add \
  frontend/src/api/types.ts \
  frontend/src/features/tasks/TokenUsageSummary.tsx \
  frontend/src/features/tasks/TokenUsageDetails.tsx \
  frontend/src/features/tasks/TaskToolbar.tsx \
  frontend/src/features/tasks/ImageDetail.tsx \
  frontend/src/features/tasks/TaskPage.test.tsx \
  frontend/src/styles.css
git commit -m "feat: show task and image token usage"
```

## Task 6: Show Token status in history

**Files:**
- Modify: `frontend/src/features/history/HistoryPage.tsx`
- Modify: `frontend/src/features/history/HistoryPage.test.tsx`

- [ ] **Step 1: Add failing history assertions for complete and untracked tasks**

Add `token_usage` to both `TaskSummary` fixtures in `HistoryPage.test.tsx`. Give the newer task a partial summary with total 1284 and one unknown call; give the older task an `untracked` summary. Extend the first test:

```tsx
  expect(within(rows[1]).getByText("已知 1,284 Token")).toBeInTheDocument();
  expect(within(rows[1]).getByText("1 次未知")).toBeInTheDocument();
  expect(within(rows[2]).getByText("未采集")).toBeInTheDocument();
```

- [ ] **Step 2: Run the history test and confirm the Token column is absent**

Run from `frontend/`:

```bash
npm test -- src/features/history/HistoryPage.test.tsx
```

Expected: the new Token labels are not found.

- [ ] **Step 3: Implement a history Token cell**

Import `formatTokens` from `../tasks/TokenUsageSummary` and add this component to `HistoryPage.tsx`:

```tsx
function TokenUsageCell({ task }: { task: TaskSummary }) {
  const usage = task.token_usage;
  if (usage.availability === "not_started") return <span className="report-state">尚无调用</span>;
  if (usage.availability === "untracked") return <span className="report-state">未采集</span>;
  if (usage.availability === "unavailable") return <span className="report-state">用量未知</span>;
  return (
    <span className="history-token-usage">
      <strong>
        {usage.availability === "partial" ? "已知 " : ""}
        {formatTokens(usage.total_tokens)} Token
      </strong>
      {usage.unknown_call_count > 0 && <small>{usage.unknown_call_count} 次未知</small>}
    </span>
  );
}
```

Add a `Token 用量` header after the result-count header and render `<TokenUsageCell task={task} />` in the corresponding body cell. Increase `.history-table`'s minimum width enough to prevent the report and action columns from collapsing, and style `.history-token-usage` with the existing monospace numeric treatment.

Add these exact styles:

```css
.history-table {
  min-width: 1100px;
}

.history-token-usage strong,
.history-token-usage small {
  display: block;
  white-space: nowrap;
}

.history-token-usage strong {
  font-family: "SF Mono", "JetBrains Mono", monospace;
  font-size: 11px;
}

.history-token-usage small {
  margin-top: 4px;
  color: var(--warning);
  font-size: 9px;
}
```

- [ ] **Step 4: Run history tests and the frontend build**

Run from `frontend/`:

```bash
npm test -- src/features/history/HistoryPage.test.tsx
npm run build
```

Expected: all history tests pass and the build exits zero.

- [ ] **Step 5: Commit the history update**

```bash
git add \
  frontend/src/features/history/HistoryPage.tsx \
  frontend/src/features/history/HistoryPage.test.tsx \
  frontend/src/styles.css
git commit -m "feat: show token usage in task history"
```

## Task 7: Add the shared Token summary to Feishu reports

**Files:**
- Modify: `backend/app/services/reports.py`
- Modify: `backend/tests/services/test_reports.py`

- [ ] **Step 1: Seed report usage and write a failing report assertion**

In the `report_fixture`, set each image's `attempt_count` to 1 and record a versioned trace. Use 10 input, 4 cached, 2 output, 0 reasoning, and 12 total per image. Add:

```python
@pytest.mark.asyncio
async def test_report_renders_the_shared_token_summary(report_fixture) -> None:
    await report_fixture.builder.export(report_fixture.task_id)

    rendered = json.dumps(report_fixture.feishu.block_payloads, ensure_ascii=False)
    assert "模型 Token 用量" in rendered
    assert "总 Token：36" in rendered
    assert "输入：30" in rendered
    assert "缓存命中：12" in rendered
    assert "未缓存输入：18" in rendered
    assert "输出：6" in rendered
    assert "推理 Token：0" in rendered
```

Add a second projection test with one `usage_json=None` attempt and one known trace. Assert that the projected summary is `partial`, total is the known total only, and the rendered report contains `已知总 Token` plus the unknown-count disclaimer.

- [ ] **Step 2: Run report tests and verify the Token section is absent**

Run:

```bash
backend/.venv/bin/pytest backend/tests/services/test_reports.py -v
```

Expected: the new report assertions fail because `ReportProjection` has no Token summary.

- [ ] **Step 3: Put the shared aggregate on the report projection**

Import `TokenUsageSummary` and `summarize_attempt_payloads` in `backend/app/services/reports.py`. Add `token_usage: TokenUsageSummary` to `ReportProjection`. In `ReportBuilder.project()`, load the task's attempts once and set:

```python
token_usage=summarize_attempt_payloads(
    [attempt.usage_json for attempt in repo.list_attempts_for_tasks([task.id])[task.id]]
),
```

- [ ] **Step 4: Render the report section from the projection**

Add these helpers to `ReportBuilder`:

```python
    @staticmethod
    def _format_tokens(value: int) -> str:
        return f"{value:,}"

    def _token_usage_blocks(self, projection: ReportProjection) -> list[dict[str, Any]]:
        usage = projection.token_usage
        if usage.availability.value == "not_started":
            text = "尚无模型调用。"
        elif usage.availability.value == "untracked":
            text = "该任务运行时尚未采集 Token 用量。"
        elif usage.availability.value == "unavailable":
            text = "方舟未返回可用的 Token 用量。"
        else:
            total_label = "已知总 Token" if usage.availability.value == "partial" else "总 Token"
            lines = [
                f"{total_label}：{self._format_tokens(usage.total_tokens)}",
                f"输入：{self._format_tokens(usage.input_tokens)}",
                f"缓存命中：{self._format_tokens(usage.cached_input_tokens)}",
                f"未缓存输入：{self._format_tokens(usage.uncached_input_tokens)}",
                f"输出：{self._format_tokens(usage.output_tokens)}",
                f"推理 Token：{self._format_tokens(usage.reasoning_tokens)}（包含在输出中）",
                f"已知调用：{usage.known_call_count}",
            ]
            if usage.unknown_call_count:
                lines.append(
                    f"未返回或未采集 usage：至少 {usage.unknown_call_count} 次；不计入已知总量。"
                )
            text = "\n".join(lines)
        return [
            self._text_block("模型 Token 用量", heading=True),
            self._text_block(text),
        ]
```

Insert `*self._token_usage_blocks(projection)` in `_overview_blocks()` after the normal result-count blocks and before the problem summary.

- [ ] **Step 5: Run report tests and backend checks**

Run:

```bash
backend/.venv/bin/pytest backend/tests/services/test_reports.py -v
backend/.venv/bin/ruff check backend/app/services/reports.py backend/tests/services/test_reports.py
backend/.venv/bin/mypy backend/app
```

Expected: all report tests pass with no lint or type errors.

- [ ] **Step 6: Commit report output**

```bash
git add backend/app/services/reports.py backend/tests/services/test_reports.py
git commit -m "feat: add token usage to Feishu reports"
```

## Task 8: Complete end-to-end coverage, documentation, and full verification

**Files:**
- Modify: `frontend/e2e/inspection-flow.spec.ts`
- Modify: `README.md`

- [ ] **Step 1: Add end-to-end acceptance assertions for the visible task total**

Extend `frontend/e2e/inspection-flow.spec.ts` after report completion:

```typescript
  const taskUsage = page.getByRole("region", { name: "任务 Token 用量" });
  await expect(taskUsage).toContainText("总 Token");
  await expect(taskUsage).toContainText("36");
  await expect(taskUsage).toContainText("缓存命中");
```

The deterministic e2e inspector from Task 3 returns 12 total Tokens per image, so three images must total 36.

- [ ] **Step 2: Run the E2E acceptance test**

Run from `frontend/`:

```bash
npm run e2e -- inspection-flow.spec.ts
```

Expected: the test passes. The lower-level tests in Tasks 1–7 supplied the required red-green evidence; this step verifies the assembled workflow.

- [ ] **Step 3: Document Token semantics in the README**

Add a `Token 用量` subsection under the task workflow in `README.md` with this content:

```markdown
### Token 用量

任务页汇总每张图片关联的全部模型调用，包括重试、Schema 降级和 JSON 修复。展示输入、缓存命中、未缓存输入、输出、推理和总 Token；缓存属于输入，推理属于输出，不会重复计入总量。方舟未返回 usage 的调用显示为未知，历史任务没有采集数据时显示为未采集。每图数字代表包含提示词与图片的完整请求用量，不代表图片内容单独占用的 Token。
```

- [ ] **Step 4: Run the complete backend verification suite**

Run from the repository root:

```bash
backend/.venv/bin/ruff check backend/app backend/tests
backend/.venv/bin/mypy backend/app
backend/.venv/bin/pytest backend/tests -v
```

Expected: Ruff and mypy exit zero; pytest reports zero failures.

- [ ] **Step 5: Run the complete frontend verification suite**

Run from `frontend/`:

```bash
npm test
npm run build
npm run e2e
```

Expected: Vitest reports zero failures, the production build exits zero, and Playwright reports zero failures.

- [ ] **Step 6: Inspect the final diff against the approved specification**

Run:

```bash
git diff --check
git status --short
git diff --stat
```

Verify explicitly that:

- every provider request path records a call entry;
- every retry attempt persists `usage_json`;
- no UI or report adds cache/reasoning to total;
- unknown and legacy usage never render as zero;
- raw usage stores no prompt, image, model response, or credential;
- the unrelated Feishu QR image files remain untracked and uncommitted.

- [ ] **Step 7: Commit end-to-end coverage and documentation**

```bash
git add frontend/e2e/inspection-flow.spec.ts README.md
git commit -m "test: cover token usage workflow"
```

## Spec coverage check

| Approved requirement | Implemented by |
| --- | --- |
| Responses and Chat field normalization | Tasks 1–2 |
| Initial, retry, rerun, fallback, and repair calls all count | Tasks 2–4 |
| Missing usage is unknown, never zero | Tasks 1, 3–5, 7 |
| Task, image, and per-call local detail | Tasks 4–5 |
| History summary | Task 6 |
| Feishu task-level summary | Task 7 |
| No currency estimate or image/text split | Tasks 1, 5, 7–8 |
| Old task compatibility | Tasks 1, 4–6 |
| Full regression and end-to-end verification | Task 8 |
