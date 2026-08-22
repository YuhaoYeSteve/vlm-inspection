from enum import StrEnum
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Verdict(StrEnum):
    PASS = "pass"
    FAIL = "fail"
    UNVERIFIABLE = "unverifiable"


class FindingStatus(StrEnum):
    PASS = "pass"
    FAIL = "fail"
    NOT_VISIBLE = "not_visible"


class Finding(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rule_id: str = Field(min_length=1)
    rule_name: str = Field(min_length=1)
    status: FindingStatus
    observation: str = Field(min_length=1)
    visual_evidence: str = Field(min_length=1)
    confidence: float = Field(ge=0, le=1)


class InspectionResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: int = 1
    image_valid: bool
    unverifiable_reason: str | None = None
    verdict: Verdict
    summary: str = Field(min_length=1)
    overall_confidence: float = Field(ge=0, le=1)
    findings: list[Finding]

    @model_validator(mode="after")
    def validate_visibility(self) -> Self:
        if not self.image_valid and not self.unverifiable_reason:
            raise ValueError("invalid images require unverifiable_reason")
        return self

    def normalized(self) -> Self:
        visible = [
            finding
            for finding in self.findings
            if finding.status is not FindingStatus.NOT_VISIBLE
        ]
        if not self.image_valid or not visible:
            verdict = Verdict.UNVERIFIABLE
        elif any(finding.status is FindingStatus.FAIL for finding in visible):
            verdict = Verdict.FAIL
        else:
            verdict = Verdict.PASS
        return self.model_copy(update={"verdict": verdict})
