from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import AppSetting, PromptTemplate, PromptTemplateVersion


class SettingsRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def get(self, key: str) -> Any | None:
        setting = self.session.get(AppSetting, key)
        return None if setting is None else setting.value_json

    def set(self, key: str, value: Any) -> None:
        setting = self.session.get(AppSetting, key)
        if setting is None:
            self.session.add(AppSetting(key=key, value_json=value))
        else:
            setting.value_json = value


class TemplateRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def create(self, name: str, description: str) -> PromptTemplate:
        template = PromptTemplate(name=name, description=description)
        self.session.add(template)
        self.session.flush()
        return template

    def add_version(
        self,
        template_id: str,
        rules: list[dict[str, Any]],
        model_params: dict[str, Any],
        system_prompt: str = "",
        task_prompt: str = "",
    ) -> PromptTemplateVersion:
        next_version = self.session.scalar(
            select(func.coalesce(func.max(PromptTemplateVersion.version), 0) + 1).where(
                PromptTemplateVersion.template_id == template_id
            )
        )
        version = PromptTemplateVersion(
            template_id=template_id,
            version=int(next_version or 1),
            rules_json=rules,
            model_params_json=model_params,
            system_prompt=system_prompt,
            task_prompt=task_prompt,
        )
        self.session.add(version)
        self.session.flush()
        template = self.session.get(PromptTemplate, template_id)
        if template is None:
            raise LookupError(template_id)
        template.current_version_id = version.id
        return version

    def get_version(self, version_id: str) -> PromptTemplateVersion:
        version = self.session.get(PromptTemplateVersion, version_id)
        if version is None:
            raise LookupError(version_id)
        return version
