import base64
import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Protocol

from pydantic import ValidationError
from volcenginesdkarkruntime import Ark  # type: ignore[import-untyped]

from app.core.secrets import SecretStoreProtocol
from app.domain.results import InspectionResult
from app.domain.usage import AttemptUsageCollector, AttemptUsageTrace, CallPurpose


@dataclass(frozen=True)
class ArkRuntimeConfig:
    base_url: str
    model: str
    protocol: Literal["responses", "chat"]
    thinking: Literal["disabled", "auto", "enabled"]
    timeout_seconds: int
    max_retries: int


@dataclass(frozen=True)
class InspectionOutcome:
    result: InspectionResult
    usage: AttemptUsageTrace


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


class InspectionServiceProtocol(Protocol):
    def inspect(
        self,
        config: ArkRuntimeConfig,
        system_prompt: str,
        user_prompt: str,
        image_path: Path,
    ) -> InspectionOutcome: ...


class KeychainArkInspectionService:
    def __init__(self, secrets: SecretStoreProtocol) -> None:
        self.secrets = secrets

    def inspect(
        self,
        config: ArkRuntimeConfig,
        system_prompt: str,
        user_prompt: str,
        image_path: Path,
    ) -> InspectionOutcome:
        empty_usage = AttemptUsageCollector(config.protocol).snapshot()
        try:
            api_key = self.secrets.get("ark_api_key")
        except RuntimeError as exc:
            raise ArkRequestError(code="keychain_unavailable", usage=empty_usage) from exc
        if api_key is None:
            raise ArkRequestError(
                status_code=401,
                code="ark_api_key_missing",
                usage=empty_usage,
            )
        return ArkInspectionService(api_key=api_key).inspect(
            config,
            system_prompt,
            user_prompt,
            image_path,
        )


class ArkInspectionService:
    def __init__(self, api_key: str | None = None, client: Any | None = None) -> None:
        if api_key is None and client is None:
            raise ValueError("api_key is required when no Ark client is injected")
        self.api_key = api_key
        self.client = client

    def inspect(
        self,
        config: ArkRuntimeConfig,
        system_prompt: str,
        user_prompt: str,
        image_path: Path,
    ) -> InspectionOutcome:
        if config.protocol == "responses":
            return self.inspect_responses(config, system_prompt, user_prompt, image_path)
        return self.inspect_chat(config, system_prompt, user_prompt, image_path)

    def inspect_responses(
        self,
        config: ArkRuntimeConfig,
        system_prompt: str,
        user_prompt: str,
        image_path: Path,
    ) -> InspectionOutcome:
        client = self._client(config)
        image_data_url = self._image_data_url(image_path)
        collector = AttemptUsageCollector("responses")
        try:
            try:
                response = self._tracked_call(
                    collector,
                    "inspection",
                    client.responses.create,
                    model=config.model,
                    input=[
                        {
                            "role": "user",
                            "content": [
                                {"type": "input_image", "image_url": image_data_url},
                                {"type": "input_text", "text": user_prompt},
                            ],
                        }
                    ],
                    instructions=system_prompt,
                    text={"format": self._responses_schema_format()},
                    thinking={"type": config.thinking},
                    timeout=config.timeout_seconds,
                )
            except Exception as exc:
                if not self._is_unsupported_schema(exc):
                    raise
                fallback_prompt = self._json_object_prompt(user_prompt)
                response = self._responses_json_object_call(
                    client,
                    config,
                    system_prompt,
                    fallback_prompt,
                    image_data_url,
                    collector,
                )

            text = self._responses_text(response)
            try:
                result = self._parse(text)
            except MalformedInspectionResult:
                repaired = self._repair_responses(client, config, text, collector)
                result = self._parse(self._responses_text(repaired))
            return InspectionOutcome(result=result, usage=collector.snapshot())
        except MalformedInspectionResult as exc:
            malformed_error = self._malformed_error(exc, collector.snapshot())
            if malformed_error is exc:
                raise
            raise malformed_error from exc
        except Exception as exc:
            request_error = self._request_error(exc, collector.snapshot())
            if request_error is exc:
                raise
            raise request_error from exc

    def inspect_chat(
        self,
        config: ArkRuntimeConfig,
        system_prompt: str,
        user_prompt: str,
        image_path: Path,
    ) -> InspectionOutcome:
        client = self._client(config)
        image_data_url = self._image_data_url(image_path)
        collector = AttemptUsageCollector("chat")
        try:
            try:
                response = self._tracked_call(
                    collector,
                    "inspection",
                    client.chat.completions.create,
                    model=config.model,
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {
                            "role": "user",
                            "content": [
                                {"type": "text", "text": user_prompt},
                                {"type": "image_url", "image_url": {"url": image_data_url}},
                            ],
                        },
                    ],
                    response_format={
                        "type": "json_schema",
                        "json_schema": self._chat_schema_format(),
                    },
                    timeout=config.timeout_seconds,
                )
            except Exception as exc:
                if not self._is_unsupported_schema(exc):
                    raise
                response = self._chat_json_object_call(
                    client,
                    config,
                    system_prompt,
                    self._json_object_prompt(user_prompt),
                    image_data_url,
                    collector,
                )

            text = self._chat_text(response)
            try:
                result = self._parse(text)
            except MalformedInspectionResult:
                repaired = self._repair_chat(client, config, text, collector)
                result = self._parse(self._chat_text(repaired))
            return InspectionOutcome(result=result, usage=collector.snapshot())
        except MalformedInspectionResult as exc:
            malformed_error = self._malformed_error(exc, collector.snapshot())
            if malformed_error is exc:
                raise
            raise malformed_error from exc
        except Exception as exc:
            request_error = self._request_error(exc, collector.snapshot())
            if request_error is exc:
                raise
            raise request_error from exc

    def _client(self, config: ArkRuntimeConfig) -> Any:
        if self.client is not None:
            return self.client
        return Ark(base_url=config.base_url, api_key=self.api_key)

    @staticmethod
    def _image_data_url(image_path: Path) -> str:
        encoded = base64.b64encode(image_path.read_bytes()).decode("ascii")
        return f"data:image/jpeg;base64,{encoded}"

    @staticmethod
    def _responses_schema_format() -> dict[str, Any]:
        return {
            "type": "json_schema",
            "name": "inspection_result",
            "strict": True,
            "schema": InspectionResult.model_json_schema(),
        }

    @staticmethod
    def _chat_schema_format() -> dict[str, Any]:
        return {
            "name": "inspection_result",
            "strict": True,
            "schema": InspectionResult.model_json_schema(),
        }

    @staticmethod
    def _responses_text(response: Any) -> str:
        text = getattr(response, "output_text", None)
        if isinstance(text, str) and text.strip():
            return text

        chunks: list[str] = []
        for item in getattr(response, "output", None) or []:
            content = item.get("content") if isinstance(item, Mapping) else getattr(item, "content", None)
            for part in content or []:
                part_type = part.get("type") if isinstance(part, Mapping) else getattr(part, "type", None)
                part_text = part.get("text") if isinstance(part, Mapping) else getattr(part, "text", None)
                if part_type == "output_text" and isinstance(part_text, str) and part_text.strip():
                    chunks.append(part_text)
        if chunks:
            return "\n".join(chunks)
        raise MalformedInspectionResult("Ark Responses output did not contain text")

    @staticmethod
    def _chat_text(response: Any) -> str:
        try:
            text = response.choices[0].message.content
        except (AttributeError, IndexError, TypeError) as exc:
            raise MalformedInspectionResult("Ark Chat output did not contain text") from exc
        if not isinstance(text, str) or not text.strip():
            raise MalformedInspectionResult("Ark Chat output did not contain text")
        return text

    @classmethod
    def _parse(cls, text: str) -> InspectionResult:
        cleaned = cls._strip_code_fence(text)
        try:
            payload = json.loads(cleaned)
            return InspectionResult.model_validate(payload).normalized()
        except (json.JSONDecodeError, ValidationError) as exc:
            raise MalformedInspectionResult("Ark output did not match the inspection schema") from exc

    @staticmethod
    def _strip_code_fence(text: str) -> str:
        cleaned = text.strip()
        if not cleaned.startswith("```"):
            return cleaned
        first_newline = cleaned.find("\n")
        if first_newline == -1:
            return cleaned
        cleaned = cleaned[first_newline + 1 :]
        if cleaned.rstrip().endswith("```"):
            cleaned = cleaned.rstrip()[:-3]
        return cleaned.strip()

    @staticmethod
    def _json_object_prompt(user_prompt: str) -> str:
        schema = json.dumps(InspectionResult.model_json_schema(), ensure_ascii=False)
        return f"{user_prompt}\nReturn one JSON object matching this schema exactly:\n{schema}"

    def _responses_json_object_call(
        self,
        client: Any,
        config: ArkRuntimeConfig,
        system_prompt: str,
        user_prompt: str,
        image_data_url: str,
        collector: AttemptUsageCollector,
    ) -> Any:
        return self._tracked_call(
            collector,
            "schema_fallback",
            client.responses.create,
            model=config.model,
            input=[
                {
                    "role": "user",
                    "content": [
                        {"type": "input_image", "image_url": image_data_url},
                        {"type": "input_text", "text": user_prompt},
                    ],
                }
            ],
            instructions=system_prompt,
            text={"format": {"type": "json_object"}},
            thinking={"type": config.thinking},
            timeout=config.timeout_seconds,
        )

    def _chat_json_object_call(
        self,
        client: Any,
        config: ArkRuntimeConfig,
        system_prompt: str,
        user_prompt: str,
        image_data_url: str,
        collector: AttemptUsageCollector,
    ) -> Any:
        return self._tracked_call(
            collector,
            "schema_fallback",
            client.chat.completions.create,
            model=config.model,
            messages=[
                {"role": "system", "content": system_prompt},
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": user_prompt},
                        {"type": "image_url", "image_url": {"url": image_data_url}},
                    ],
                },
            ],
            response_format={"type": "json_object"},
            timeout=config.timeout_seconds,
        )

    def _repair_responses(
        self,
        client: Any,
        config: ArkRuntimeConfig,
        invalid: str,
        collector: AttemptUsageCollector,
    ) -> Any:
        prompt = self._repair_prompt(invalid)
        return self._tracked_call(
            collector,
            "json_repair",
            client.responses.create,
            model=config.model,
            input=[
                {
                    "role": "user",
                    "content": [{"type": "input_text", "text": prompt}],
                }
            ],
            instructions="只修复 JSON 格式，不增加或推断图片事实。",
            text={"format": self._responses_schema_format()},
            thinking={"type": "disabled"},
            timeout=config.timeout_seconds,
        )

    def _repair_chat(
        self,
        client: Any,
        config: ArkRuntimeConfig,
        invalid: str,
        collector: AttemptUsageCollector,
    ) -> Any:
        prompt = self._repair_prompt(invalid)
        return self._tracked_call(
            collector,
            "json_repair",
            client.chat.completions.create,
            model=config.model,
            messages=[
                {"role": "system", "content": "只修复 JSON 格式，不增加或推断图片事实。"},
                {"role": "user", "content": prompt},
            ],
            response_format={
                "type": "json_schema",
                "json_schema": self._chat_schema_format(),
            },
            timeout=config.timeout_seconds,
        )

    @staticmethod
    def _tracked_call(
        collector: AttemptUsageCollector,
        purpose: CallPurpose,
        create: Callable[..., Any],
        **kwargs: Any,
    ) -> Any:
        try:
            response = create(**kwargs)
        except Exception:
            collector.add_missing(purpose)
            raise

        try:
            raw_usage = getattr(response, "usage", None)
        except Exception:  # noqa: BLE001
            raw_usage = None
        try:
            collector.add_response(purpose, raw_usage)
        except Exception:  # noqa: BLE001
            collector.add_missing(purpose, status="succeeded")
        return response

    @staticmethod
    def _repair_prompt(invalid: str) -> str:
        schema = json.dumps(InspectionResult.model_json_schema(), ensure_ascii=False)
        return f"Repair this text into one JSON object matching the schema.\nSchema: {schema}\nText: {invalid}"

    @staticmethod
    def _is_unsupported_schema(exc: Exception) -> bool:
        status = getattr(exc, "status_code", None)
        code = str(getattr(exc, "code", "")).lower()
        message = str(exc).lower()
        mentions_schema = "json_schema" in message or "response_format" in message
        mentions_unsupported = "unsupported" in code or "unsupported" in message
        return status == 400 and mentions_schema and mentions_unsupported

    @staticmethod
    def _request_error(exc: Exception, usage: AttemptUsageTrace) -> ArkRequestError:
        if isinstance(exc, ArkRequestError):
            if exc.usage is not None:
                return exc
            return ArkRequestError(exc.status_code, exc.headers, exc.code, usage)
        response = getattr(exc, "response", None)
        status = getattr(exc, "status_code", None) or getattr(response, "status_code", None)
        raw_headers = getattr(exc, "headers", None) or getattr(response, "headers", None) or {}
        headers = {str(key): str(value) for key, value in dict(raw_headers).items()}
        code_value = getattr(exc, "code", None)
        code = None if code_value is None else str(code_value)
        return ArkRequestError(status_code=status, headers=headers, code=code, usage=usage)

    @staticmethod
    def _malformed_error(
        exc: MalformedInspectionResult,
        usage: AttemptUsageTrace,
    ) -> MalformedInspectionResult:
        if exc.usage is not None:
            return exc
        return MalformedInspectionResult(str(exc), usage=usage)
