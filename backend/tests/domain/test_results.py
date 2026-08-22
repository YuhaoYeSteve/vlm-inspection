import pytest
from pydantic import ValidationError

from app.domain.results import Finding, FindingStatus, InspectionResult, Verdict


def test_result_recomputes_fail_from_findings() -> None:
    result = InspectionResult(
        image_valid=True,
        verdict=Verdict.PASS,
        summary="工牌缺失",
        overall_confidence=0.92,
        findings=[
            Finding(
                rule_id="badge",
                rule_name="工牌佩戴",
                status=FindingStatus.FAIL,
                observation="左胸未见工牌",
                visual_evidence="左胸区域无遮挡",
                confidence=0.92,
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
            overall_confidence=1.0,
            findings=[],
        )


def test_result_recomputes_unverifiable_when_nothing_is_visible() -> None:
    result = InspectionResult(
        image_valid=True,
        verdict=Verdict.PASS,
        summary="手部未入镜",
        overall_confidence=0.85,
        findings=[
            Finding(
                rule_id="hands",
                rule_name="手部与指甲",
                status=FindingStatus.NOT_VISIBLE,
                observation="手部未入镜",
                visual_evidence="画面仅包含上半身",
                confidence=0.85,
            )
        ],
    ).normalized()

    assert result.verdict is Verdict.UNVERIFIABLE


def test_confidence_is_bounded() -> None:
    with pytest.raises(ValidationError):
        Finding(
            rule_id="badge",
            rule_name="工牌佩戴",
            status=FindingStatus.PASS,
            observation="已佩戴工牌",
            visual_evidence="左胸位置可见工牌",
            confidence=1.1,
        )
