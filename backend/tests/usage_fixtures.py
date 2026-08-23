from app.domain.usage import AttemptUsageCollector, AttemptUsageTrace, ProtocolName


def provider_usage(protocol: ProtocolName) -> dict[str, object]:
    if protocol == "responses":
        return {
            "input_tokens": 10,
            "input_tokens_details": {"cached_tokens": 4},
            "output_tokens": 2,
            "output_tokens_details": {"reasoning_tokens": 0},
            "total_tokens": 12,
        }
    return {
        "prompt_tokens": 10,
        "prompt_tokens_details": {"cached_tokens": 4},
        "completion_tokens": 2,
        "completion_tokens_details": {"reasoning_tokens": 0},
        "total_tokens": 12,
    }


def successful_usage(protocol: ProtocolName) -> AttemptUsageTrace:
    collector = AttemptUsageCollector(protocol)
    collector.add_response("inspection", provider_usage(protocol))
    return collector.snapshot()


def failed_usage(protocol: ProtocolName) -> AttemptUsageTrace:
    collector = AttemptUsageCollector(protocol)
    collector.add_missing("inspection")
    return collector.snapshot()
