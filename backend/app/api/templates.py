from typing import Annotated

from fastapi import APIRouter, Depends, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.errors import ApiError
from app.dependencies import get_session
from app.models import PromptTemplate, PromptTemplateVersion
from app.repositories import TemplateRepository
from app.schemas import TemplateDetail, TemplateRule, TemplateSummary, TemplateWrite
from app.services.prompts import SYSTEM_EVIDENCE_POLICY, starter_templates

router = APIRouter(prefix="/api/templates", tags=["templates"])

_STARTER_SLUGS = {
    template["name"]: template["slug"] for template in starter_templates()
}


def _slug(template: PromptTemplate) -> str:
    return str(_STARTER_SLUGS.get(template.name, template.id))


def _detail(template: PromptTemplate, version: PromptTemplateVersion) -> TemplateDetail:
    return TemplateDetail(
        id=template.id,
        slug=_slug(template),
        name=template.name,
        description=template.description,
        enabled=template.enabled,
        version=version.version,
        version_id=version.id,
        system_prompt=version.system_prompt,
        task_prompt=version.task_prompt,
        rules=[TemplateRule.model_validate(rule) for rule in version.rules_json],
        model_params=version.model_params_json,
        created_at=template.created_at,
    )


def _summary(template: PromptTemplate, version: PromptTemplateVersion) -> TemplateSummary:
    return TemplateSummary(
        id=template.id,
        slug=_slug(template),
        name=template.name,
        description=template.description,
        enabled=template.enabled,
        version=version.version,
        created_at=template.created_at,
    )


def _not_found(identifier: str) -> ApiError:
    return ApiError(404, "template_not_found", f"未找到提示词模板：{identifier}")


@router.get("", response_model=list[TemplateSummary])
def list_templates(
    session: Annotated[Session, Depends(get_session)],
) -> list[TemplateSummary]:
    repo = TemplateRepository(session)
    return [_summary(item, repo.get_current_version(item.id)) for item in repo.list_templates()]


@router.post("", response_model=TemplateDetail, status_code=status.HTTP_201_CREATED)
def create_template(
    body: TemplateWrite,
    session: Annotated[Session, Depends(get_session)],
) -> TemplateDetail:
    repo = TemplateRepository(session)
    if repo.get_by_name(body.name) is not None:
        raise ApiError(409, "template_name_exists", "模板名称已存在。")
    try:
        template = repo.create(body.name, body.description)
        version = repo.add_version(
            template.id,
            [rule.model_dump() for rule in body.rules],
            body.model_params.model_dump(),
            system_prompt=SYSTEM_EVIDENCE_POLICY,
            task_prompt=body.task_prompt,
        )
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        raise ApiError(409, "template_name_exists", "模板名称已存在。") from exc
    return _detail(template, version)


@router.get("/{template_id}", response_model=TemplateDetail)
def get_template(
    template_id: str,
    session: Annotated[Session, Depends(get_session)],
) -> TemplateDetail:
    repo = TemplateRepository(session)
    try:
        template = repo.get(template_id)
        version = repo.get_current_version(template_id)
    except LookupError as exc:
        raise _not_found(template_id) from exc
    return _detail(template, version)


@router.get("/{template_id}/versions/{version_number}", response_model=TemplateDetail)
def get_template_version(
    template_id: str,
    version_number: int,
    session: Annotated[Session, Depends(get_session)],
) -> TemplateDetail:
    repo = TemplateRepository(session)
    try:
        template = repo.get(template_id)
        version = repo.get_version_number(template_id, version_number)
    except LookupError as exc:
        raise _not_found(f"{template_id}:v{version_number}") from exc
    return _detail(template, version)


@router.put("/{template_id}", response_model=TemplateDetail)
def update_template(
    template_id: str,
    body: TemplateWrite,
    session: Annotated[Session, Depends(get_session)],
) -> TemplateDetail:
    repo = TemplateRepository(session)
    try:
        template = repo.get(template_id)
    except LookupError as exc:
        raise _not_found(template_id) from exc
    duplicate = repo.get_by_name(body.name)
    if duplicate is not None and duplicate.id != template_id:
        raise ApiError(409, "template_name_exists", "模板名称已存在。")
    template.name = body.name
    template.description = body.description
    version = repo.add_version(
        template_id,
        [rule.model_dump() for rule in body.rules],
        body.model_params.model_dump(),
        system_prompt=SYSTEM_EVIDENCE_POLICY,
        task_prompt=body.task_prompt,
    )
    try:
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        raise ApiError(409, "template_name_exists", "模板名称已存在。") from exc
    return _detail(template, version)


def seed_templates_if_empty(session: Session) -> None:
    repo = TemplateRepository(session)
    if repo.count() != 0:
        return
    for seed in starter_templates():
        template = repo.create(str(seed["name"]), str(seed["description"]))
        repo.add_version(
            template.id,
            seed["rules"],
            seed["model_params"],
            SYSTEM_EVIDENCE_POLICY,
            str(seed["task_prompt"]),
        )
    session.commit()
