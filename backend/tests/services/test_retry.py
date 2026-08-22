from app.services.retry import RetryDecision, RetryPolicy


def test_429_uses_retry_after() -> None:
    decision = RetryPolicy(max_retries=2).classify(429, {"retry-after": "3"}, 0)

    assert decision == RetryDecision(retry=True, delay_seconds=3.0, pause_task=False)


def test_auth_error_pauses_without_retry() -> None:
    decision = RetryPolicy(max_retries=2).classify(401, {}, 0)

    assert decision.retry is False
    assert decision.pause_task is True


def test_timeout_and_server_errors_retry_only_up_to_limit() -> None:
    policy = RetryPolicy(max_retries=2)

    assert policy.classify(None, {}, 0).retry is True
    assert policy.classify(503, {}, 1).retry is True
    assert policy.classify(503, {}, 2) == RetryDecision(retry=False)


def test_parameter_error_pauses_task() -> None:
    decision = RetryPolicy(max_retries=2).classify(400, {}, 0)

    assert decision == RetryDecision(retry=False, pause_task=True)
