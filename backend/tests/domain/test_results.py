import json

import pytest
from pydantic import ValidationError

from app.domain.results import Finding, FindingStatus, InspectionResult, Verdict


def test_result_recomputes_fail_from_findings() -> None:
    result = InspectionResult(
        image_valid=True,
        verdict=Verdict.PASS,
        summary="工牌缺失",
        findings=[
            Finding(
                rule_id="badge",
                rule_name="工牌佩戴",
                status=FindingStatus.FAIL,
                observation="左胸未见工牌",
                visual_evidence="左胸区域无遮挡",
            )
        ],
    ).normalized()

    assert result.verdict is Verdict.FAIL


def test_invalid_image_requires_reason() -> None:
    with pytest.raises(ValidationError):
        InspectionResult(
            image_valid=False,
            verdict=Verdict.UNVERIFIABLE,
            summary="无法检查",
            findings=[],
        )


def test_result_recomputes_unverifiable_when_nothing_is_visible() -> None:
    result = InspectionResult(
        image_valid=True,
        verdict=Verdict.PASS,
        summary="手部未入镜",
        findings=[
            Finding(
                rule_id="hands",
                rule_name="手部与指甲",
                status=FindingStatus.NOT_VISIBLE,
                observation="手部未入镜",
                visual_evidence="画面仅包含上半身",
            )
        ],
    ).normalized()

    assert result.verdict is Verdict.UNVERIFIABLE


def test_unknown_finding_fields_remain_forbidden() -> None:
    with pytest.raises(ValidationError):
        Finding(
            rule_id="badge",
            rule_name="工牌佩戴",
            status=FindingStatus.PASS,
            observation="已佩戴工牌",
            visual_evidence="左胸位置可见工牌",
            unexpected="not allowed",  # type: ignore[call-arg]
        )


def test_result_schema_does_not_request_model_confidence() -> None:
    schema = json.dumps(InspectionResult.model_json_schema())

    assert "overall_confidence" not in schema
    assert '"confidence"' not in schema


def test_legacy_confidence_fields_are_ignored() -> None:
    result = InspectionResult.model_validate(
        {
            "image_valid": True,
            "verdict": "pass",
            "summary": "工牌清晰可见",
            "overall_confidence": 0.92,
            "findings": [
                {
                    "rule_id": "badge",
                    "rule_name": "工牌佩戴",
                    "status": "pass",
                    "observation": "已佩戴工牌",
                    "visual_evidence": "左胸位置可见工牌",
                    "confidence": 0.92,
                }
            ],
        }
    )

    serialized = result.model_dump()
    assert "overall_confidence" not in serialized
    assert "confidence" not in serialized["findings"][0]
