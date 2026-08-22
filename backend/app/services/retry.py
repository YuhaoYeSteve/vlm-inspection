import random
from collections.abc import Mapping
from dataclasses import dataclass


@dataclass(frozen=True)
class RetryDecision:
    retry: bool
    delay_seconds: float = 0
    pause_task: bool = False


class RetryPolicy:
    def __init__(self, max_retries: int) -> None:
        self.max_retries = max_retries

    def classify(
        self,
        status: int | None,
        headers: Mapping[str, str],
        attempt: int,
    ) -> RetryDecision:
        if status in {401, 403}:
            return RetryDecision(False, pause_task=True)
        if status == 429 and attempt < self.max_retries:
            retry_after = next(
                (value for key, value in headers.items() if key.lower() == "retry-after"),
                "0",
            )
            try:
                delay = float(retry_after)
            except ValueError:
                delay = 0
            return RetryDecision(True, delay or self._delay(attempt))
        if (status is None or status >= 500) and attempt < self.max_retries:
            return RetryDecision(True, self._delay(attempt))
        if status is not None and 400 <= status < 500:
            return RetryDecision(False, pause_task=True)
        return RetryDecision(False)

    @staticmethod
    def _delay(attempt: int) -> float:
        return min(30.0, float(2**attempt)) + random.uniform(0, 0.5)
