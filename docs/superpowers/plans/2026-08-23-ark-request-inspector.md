# Ark Request Inspector Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在提示词模板页用右侧抽屉展示基于未保存草稿和测试图片生成的真实方舟请求，并保证从抽屉发送的请求与预览对象完全一致。

**Architecture:** 后端在 `ark.py` 中建立唯一的 `PreparedInspection` 请求构造与发送路径，使用内存 TTL 存储保存短期预览会话，并通过模板 API 提供准备、读取原始请求、发送和删除操作。前端以统一的请求/结果抽屉替代现有不完整提示词预览和独立结果抽屉，Base64 默认折叠，完整正文仅在显式复制时获取。

**Tech Stack:** Python 3.11、FastAPI、Pydantic v2、Pillow、Volcengine Ark SDK、pytest、React 19、TypeScript、TanStack Query、MSW、Vitest、Playwright。

---

## 文件结构

- Modify: `backend/app/services/ark.py` — 唯一构造和发送主请求、降级请求与修复请求。
- Create: `backend/app/services/request_previews.py` — 线程安全的内存 TTL 会话、指纹和安全序列化。
- Modify: `backend/app/main.py` — 创建预览存储并负责过期清理。
- Modify: `backend/app/dependencies.py` — 暴露预览存储依赖。
- Modify: `backend/app/schemas.py` — 请求预览、实际尝试、原始请求和发送结果模型。
- Modify: `backend/app/api/templates.py` — 增加准备、读取、发送和删除接口。
- Modify: `backend/tests/conftest.py` — 让测试替身支持发送已准备请求。
- Modify: `backend/tests/e2e_server.py` — 让端到端替身支持新发送路径。
- Modify: `backend/tests/services/test_ark.py` — 验证准确正文、URL、图片摘要和实际尝试。
- Create: `backend/tests/services/test_request_previews.py` — 验证 TTL、状态、指纹和删除。
- Create: `backend/tests/api/test_template_request_previews.py` — 验证草稿、Base64、复制和发送接口。
- Modify: `frontend/src/api/types.ts` — 请求检查器类型。
- Modify: `frontend/src/api/client.ts` — 请求预览 API 方法。
- Create: `frontend/src/features/templates/RequestInspectorDrawer.tsx` — 统一请求与结果抽屉。
- Create: `frontend/src/features/templates/RequestInspectorDrawer.test.tsx` — 抽屉组件测试。
- Modify: `frontend/src/features/templates/TemplateEditor.tsx` — 增加“查看请求”入口。
- Modify: `frontend/src/features/templates/TemplatesPage.tsx` — 连接草稿、预览、发送与清理状态。
- Modify: `frontend/src/features/templates/TemplatesPage.test.tsx` — 页面流程测试。
- Modify: `frontend/src/styles.css` — 抽屉与移动端样式。
- Modify: `frontend/e2e/inspection-flow.spec.ts` — 浏览器端完整流程。

### Task 1: 建立唯一的已准备请求模型

**Files:**
- Modify: `backend/app/services/ark.py:16-290`
- Modify: `backend/tests/services/test_ark.py:1-170`

- [ ] **Step 1: 写 Responses 与 Chat 请求构造的失败测试**

在 `backend/tests/services/test_ark.py` 增加 Pillow 导入、有效 JPEG 帮助函数及以下测试：

```python
from PIL import Image

from app.services.ark import prepare_inspection_request


def write_jpeg(path) -> None:
    Image.new("RGB", (32, 20), "white").save(path, format="JPEG")


def test_prepare_responses_request_contains_exact_body_and_image_metadata(tmp_path) -> None:
    image = tmp_path / "employee.jpg"
    write_jpeg(image)
    prepared = prepare_inspection_request(runtime(), "system", "user", image)
    request = prepared.primary

    assert request.method == "POST"
    assert request.url == "https://ark.example/v3/responses"
    assert request.protocol == "responses"
    assert request.mode == "json_schema"
    assert request.purpose == "inspection"
    assert request.timeout_seconds == 120
    assert request.body["model"] == "seed"
    assert request.body["instructions"] == "system"
    assert request.body["text"]["format"]["type"] == "json_schema"
    data_url = request.body["input"][0]["content"][0]["image_url"]
    assert data_url.startswith("data:image/jpeg;base64,")
    assert request.image.width == 32
    assert request.image.height == 20
    assert request.image.byte_length == image.stat().st_size
    assert len(request.image.sha256) == 64


def test_prepare_chat_request_contains_exact_body(tmp_path) -> None:
    image = tmp_path / "employee.jpg"
    write_jpeg(image)
    prepared = prepare_inspection_request(runtime("chat"), "system", "user", image)
    request = prepared.primary

    assert request.url == "https://ark.example/v3/chat/completions"
    assert request.protocol == "chat"
    assert request.body["messages"][0] == {"role": "system", "content": "system"}
    assert request.body["response_format"]["type"] == "json_schema"
    image_url = request.body["messages"][1]["content"][1]["image_url"]["url"]
    assert image_url.startswith("data:image/jpeg;base64,")
```

- [ ] **Step 2: 运行测试并确认因为构造入口不存在而失败**

Run:

```bash
backend/.venv/bin/pytest backend/tests/services/test_ark.py::test_prepare_responses_request_contains_exact_body_and_image_metadata backend/tests/services/test_ark.py::test_prepare_chat_request_contains_exact_body -v
```

Expected: FAIL，导入 `prepare_inspection_request` 失败。

- [ ] **Step 3: 在 `ark.py` 定义领域类型和纯构造入口**

在 `ArkRuntimeConfig` 后加入：

```python
RequestMode = Literal["json_schema", "json_object", "repair"]
RequestStatus = Literal["succeeded", "failed"]


@dataclass(frozen=True)
class PreparedImageMetadata:
    mime_type: str
    byte_length: int
    width: int
    height: int
    sha256: str


@dataclass(frozen=True)
class PreparedArkRequest:
    method: Literal["POST"]
    url: str
    protocol: Literal["responses", "chat"]
    purpose: CallPurpose
    mode: RequestMode
    body: dict[str, Any]
    timeout_seconds: int
    image: PreparedImageMetadata | None


@dataclass(frozen=True)
class PreparedInspection:
    config: ArkRuntimeConfig
    system_prompt: str
    user_prompt: str
    image_data_url: str
    image: PreparedImageMetadata
    primary: PreparedArkRequest


@dataclass(frozen=True)
class ArkRequestTrace:
    sequence: int
    request: PreparedArkRequest
    status: RequestStatus
    status_code: int | None
    error_code: str | None


RequestTraceCallback = Callable[[ArkRequestTrace], None]
```

增加 `hashlib` 和 Pillow `Image` 导入。实现 `prepare_inspection_request(config, system_prompt, user_prompt, image_path)`：读取 JPEG 字节和尺寸、计算 SHA-256、生成 Data URL，并按协议构造正文。Responses 正文必须包含 `model`、`input`、`instructions`、`text.format=json_schema` 和 `thinking`；Chat 正文必须包含 `model`、`messages` 和 `response_format=json_schema`。URL 分别为 `base_url.rstrip('/') + '/responses'` 和 `base_url.rstrip('/') + '/chat/completions'`。

- [ ] **Step 4: 让发送路径只消费已准备对象**

增加固定 SDK 参数转换：

```python
def _sdk_kwargs(request: PreparedArkRequest) -> dict[str, Any]:
    return {**request.body, "timeout": request.timeout_seconds}


def _create_for(self, client: Any, request: PreparedArkRequest) -> Callable[..., Any]:
    if request.protocol == "responses":
        return client.responses.create
    return client.chat.completions.create
```

扩展 `InspectionServiceProtocol`、`KeychainArkInspectionService` 和 `ArkInspectionService`，增加：

```python
def inspect_prepared(
    self,
    prepared: PreparedInspection,
    on_request: RequestTraceCallback | None = None,
) -> InspectionOutcome:
    if prepared.config.protocol == "responses":
        return self._inspect_prepared_responses(prepared, on_request)
    return self._inspect_prepared_chat(prepared, on_request)
```

现有 `inspect`、`inspect_responses` 和 `inspect_chat` 先调用 `prepare_inspection_request`，再调用 `inspect_prepared`。第一次 SDK 调用只能来自 `prepared.primary.body` 和 `prepared.primary.timeout_seconds`。

- [ ] **Step 5: 运行 Ark 服务测试并提交**

```bash
backend/.venv/bin/pytest backend/tests/services/test_ark.py -v
git add backend/app/services/ark.py backend/tests/services/test_ark.py
git commit -m "refactor: prepare Ark requests before sending"
```

Expected: 全部 PASS，现有解析、usage 和 verdict normalization 测试继续通过。

### Task 2: 记录 schema fallback 与 JSON repair 的真实请求

**Files:**
- Modify: `backend/app/services/ark.py:117-510`
- Modify: `backend/tests/services/test_ark.py`

- [ ] **Step 1: 写实际尝试顺序的失败测试**

```python
def test_schema_fallback_records_both_exact_requests(tmp_path) -> None:
    image = tmp_path / "image.jpg"
    write_jpeg(image)
    responses = FakeResponses([UnsupportedSchemaError(), json.dumps(VALID)])
    service = ArkInspectionService(client=SimpleNamespace(responses=responses))
    prepared = prepare_inspection_request(runtime(), "system", "user", image)
    traces = []

    service.inspect_prepared(prepared, on_request=traces.append)

    assert [(trace.sequence, trace.request.mode, trace.status) for trace in traces] == [
        (1, "json_schema", "failed"),
        (2, "json_object", "succeeded"),
    ]
    assert traces[0].request.body == prepared.primary.body
    assert traces[1].request.body["text"]["format"] == {"type": "json_object"}
    sent = dict(responses.calls[1])
    sent.pop("timeout")
    assert traces[1].request.body == sent


def test_json_repair_records_text_only_request(tmp_path) -> None:
    image = tmp_path / "image.jpg"
    write_jpeg(image)
    responses = FakeResponses(["not-json", json.dumps(VALID)])
    service = ArkInspectionService(client=SimpleNamespace(responses=responses))
    prepared = prepare_inspection_request(runtime(), "system", "user", image)
    traces = []

    service.inspect_prepared(prepared, on_request=traces.append)

    assert [trace.request.mode for trace in traces] == ["json_schema", "repair"]
    repair_content = traces[1].request.body["input"][0]["content"]
    assert all(part["type"] != "input_image" for part in repair_content)
```

- [ ] **Step 2: 运行测试并确认缺少轨迹回调**

```bash
backend/.venv/bin/pytest backend/tests/services/test_ark.py::test_schema_fallback_records_both_exact_requests backend/tests/services/test_ark.py::test_json_repair_records_text_only_request -v
```

Expected: FAIL，`inspect_prepared` 尚未记录全部请求。

- [ ] **Step 3: 用已准备对象构造 fallback 和 repair**

实现 `_fallback_request(prepared)`：保留模型、URL、系统提示词、图片和 thinking，把用户提示词替换为 `_json_object_prompt(prepared.user_prompt)`，并把 Responses 的 `text.format` 或 Chat 的 `response_format` 改成 `json_object`。返回对象固定使用 `purpose="schema_fallback"`、`mode="json_object"`。

实现 `_repair_request(prepared, invalid)`：复用 `_repair_prompt(invalid)`，不包含图片，使用 `purpose="json_repair"`、`mode="repair"`；Responses 使用 `thinking={"type": "disabled"}`，Chat 使用现有 JSON Schema response format。

- [ ] **Step 4: 用单一发送器记录成功和失败**

用以下签名替换 `_tracked_call` 的请求职责：

```python
def _call_prepared(
    self,
    client: Any,
    collector: AttemptUsageCollector,
    request: PreparedArkRequest,
    on_request: RequestTraceCallback | None,
) -> Any:
```

实现顺序固定为：调用 SDK；失败时先 `collector.add_missing(request.purpose)`，再记录 failed trace 后抛 `_ProviderInvocationError`；成功时按现有容错逻辑读取 usage，再记录 succeeded trace。trace sequence 必须等于 usage call sequence。主请求、fallback 和 repair 都只能调用 `_call_prepared`。

- [ ] **Step 5: 运行回归并提交**

```bash
backend/.venv/bin/pytest backend/tests/services/test_ark.py backend/tests/domain/test_usage.py -v
backend/.venv/bin/ruff check backend/app/services/ark.py backend/tests/services/test_ark.py
git add backend/app/services/ark.py backend/tests/services/test_ark.py
git commit -m "feat: trace exact Ark request attempts"
```

Expected: 全部 PASS，ruff 无输出。

### Task 3: 增加线程安全的临时请求预览存储

**Files:**
- Create: `backend/app/services/request_previews.py`
- Create: `backend/tests/services/test_request_previews.py`

- [ ] **Step 1: 写 TTL、状态和模板归属测试**

创建 `backend/tests/services/test_request_previews.py`：

```python
from datetime import UTC, datetime, timedelta

import pytest

from app.services.ark import (
    ArkRuntimeConfig,
    PreparedArkRequest,
    PreparedImageMetadata,
    PreparedInspection,
)
from app.services.request_previews import (
    PreviewConflict,
    PreviewExpired,
    PreviewNotFound,
    PreviewStale,
    RequestPreviewStore,
)


def prepared() -> PreparedInspection:
    config = ArkRuntimeConfig(
        base_url="https://ark.example/v3",
        model="seed-test",
        protocol="responses",
        thinking="disabled",
        timeout_seconds=120,
        max_retries=2,
    )
    image = PreparedImageMetadata(
        mime_type="image/jpeg",
        byte_length=5,
        width=1,
        height=1,
        sha256="image-sha",
    )
    primary = PreparedArkRequest(
        method="POST",
        url="https://ark.example/v3/responses",
        protocol="responses",
        purpose="inspection",
        mode="json_schema",
        body={
            "model": "seed-test",
            "input": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "input_image",
                            "image_url": "data:image/jpeg;base64,aGVsbG8=",
                        }
                    ],
                }
            ],
        },
        timeout_seconds=120,
        image=image,
    )
    return PreparedInspection(
        config=config,
        system_prompt="system",
        user_prompt="user",
        image_data_url="data:image/jpeg;base64,aGVsbG8=",
        image=image,
        primary=primary,
    )


def test_preview_expires_and_is_removed() -> None:
    now = [datetime(2026, 8, 23, tzinfo=UTC)]
    store = RequestPreviewStore(ttl=timedelta(minutes=10), now=lambda: now[0])
    item = store.create("template-1", prepared(), "draft-hash", "settings-hash")
    assert store.read("template-1", item.preview_id).preview_id == item.preview_id

    now[0] += timedelta(minutes=11)
    with pytest.raises(PreviewExpired):
        store.read("template-1", item.preview_id)


def test_preview_rejects_wrong_template_stale_settings_and_duplicate_send() -> None:
    store = RequestPreviewStore()
    first = store.create("template-1", prepared(), "draft-hash", "settings-hash")
    with pytest.raises(PreviewNotFound):
        store.read("template-2", first.preview_id)
    with pytest.raises(PreviewStale):
        store.claim_for_send("template-1", first.preview_id, "changed-settings")

    second = store.create("template-1", prepared(), "draft-hash", "settings-hash")
    store.claim_for_send("template-1", second.preview_id, "settings-hash")
    with pytest.raises(PreviewConflict):
        store.claim_for_send("template-1", second.preview_id, "settings-hash")


def test_cleanup_keeps_in_flight_request_and_resets_ttl_on_finish() -> None:
    now = [datetime(2026, 8, 23, tzinfo=UTC)]
    store = RequestPreviewStore(ttl=timedelta(minutes=10), now=lambda: now[0])
    item = store.create("template-1", prepared(), "draft-hash", "settings-hash")
    store.claim_for_send("template-1", item.preview_id, "settings-hash")
    now[0] += timedelta(minutes=11)

    store.purge_expired()
    store.finish(item.preview_id, result=None, error_code="provider_error")

    assert store.read("template-1", item.preview_id).state == "failed"
```

- [ ] **Step 2: 运行测试并确认模块不存在**

```bash
backend/.venv/bin/pytest backend/tests/services/test_request_previews.py -v
```

Expected: FAIL，`app.services.request_previews` 尚不存在。

- [ ] **Step 3: 实现预览会话和存储状态机**

创建 `backend/app/services/request_previews.py`，定义：

```python
import asyncio
import copy
import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from threading import RLock
from typing import Any, Literal
from uuid import uuid4

from app.domain.results import InspectionResult
from app.services.ark import ArkRequestTrace, PreparedInspection


PreviewState = Literal["ready", "sending", "completed", "failed"]


class PreviewNotFound(LookupError):
    pass


class PreviewExpired(LookupError):
    pass


class PreviewStale(RuntimeError):
    pass


class PreviewConflict(RuntimeError):
    pass


@dataclass
class PreparedRequestSession:
    preview_id: str
    template_id: str
    prepared: PreparedInspection
    draft_fingerprint: str
    settings_fingerprint: str
    created_at: datetime
    expires_at: datetime
    state: PreviewState = "ready"
    attempts: list[ArkRequestTrace] = field(default_factory=list)
    result: InspectionResult | None = None
    error_code: str | None = None
```

`RequestPreviewStore` 使用 `threading.RLock` 和 `dict[str, PreparedRequestSession]`。核心状态机按以下代码实现：

```python
class RequestPreviewStore:
    def __init__(
        self,
        ttl: timedelta = timedelta(minutes=10),
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self.ttl = ttl
        self.now = now or (lambda: datetime.now(UTC))
        self._items: dict[str, PreparedRequestSession] = {}
        self._lock = RLock()

    def create(
        self,
        template_id: str,
        prepared: PreparedInspection,
        draft_fingerprint: str,
        settings_fingerprint: str,
    ) -> PreparedRequestSession:
        with self._lock:
            self.purge_expired()
            created_at = self.now()
            item = PreparedRequestSession(
                preview_id=str(uuid4()),
                template_id=template_id,
                prepared=prepared,
                draft_fingerprint=draft_fingerprint,
                settings_fingerprint=settings_fingerprint,
                created_at=created_at,
                expires_at=created_at + self.ttl,
            )
            self._items[item.preview_id] = item
            return item

    def _read_locked(self, template_id: str, preview_id: str) -> PreparedRequestSession:
        item = self._items.get(preview_id)
        if item is None or item.template_id != template_id:
            raise PreviewNotFound(preview_id)
        if item.state != "sending" and self.now() >= item.expires_at:
            self._items.pop(preview_id, None)
            raise PreviewExpired(preview_id)
        return item

    def read(self, template_id: str, preview_id: str) -> PreparedRequestSession:
        with self._lock:
            return self._read_locked(template_id, preview_id)

    def claim_for_send(
        self,
        template_id: str,
        preview_id: str,
        current_settings_fingerprint: str,
    ) -> PreparedRequestSession:
        with self._lock:
            item = self._read_locked(template_id, preview_id)
            if item.settings_fingerprint != current_settings_fingerprint:
                raise PreviewStale(preview_id)
            if item.state != "ready":
                raise PreviewConflict(preview_id)
            item.state = "sending"
            return item

    def record_attempt(self, preview_id: str, trace: ArkRequestTrace) -> None:
        with self._lock:
            item = self._items.get(preview_id)
            if item is not None and item.state == "sending":
                item.attempts.append(trace)

    def finish(
        self,
        preview_id: str,
        result: InspectionResult | None,
        error_code: str | None,
    ) -> None:
        with self._lock:
            item = self._items.get(preview_id)
            if item is None:
                return
            item.result = result
            item.error_code = error_code
            item.state = "completed" if error_code is None else "failed"
            item.expires_at = self.now() + self.ttl

    def delete(self, template_id: str, preview_id: str) -> None:
        with self._lock:
            item = self._read_locked(template_id, preview_id)
            if item.state == "sending":
                raise PreviewConflict(preview_id)
            self._items.pop(preview_id, None)

    def purge_expired(self) -> None:
        with self._lock:
            current = self.now()
            expired = [
                key
                for key, item in self._items.items()
                if item.state != "sending" and current >= item.expires_at
            ]
            for key in expired:
                self._items.pop(key, None)

    def clear(self) -> None:
        with self._lock:
            self._items.clear()
```

构造函数默认 `ttl=timedelta(minutes=10)`，默认时钟为 `datetime.now(UTC)`。`claim_for_send` 只接受 `ready`，并原子地改成 `sending`；`purge_expired` 不删除 `sending` 对象；`finish` 改成 `completed` 或 `failed` 并把 `expires_at` 重置为完成时间加 10 分钟。`delete` 遇到 `sending` 返回 `PreviewConflict`，前端在发送中禁用关闭。读取到已过期对象时先删除再抛 `PreviewExpired`。

- [ ] **Step 4: 写安全序列化和指纹测试**

```python
def test_preview_collapses_image_but_raw_keeps_the_same_data_url() -> None:
    store = RequestPreviewStore()
    item = store.create("template-1", prepared(), "draft-hash", "settings-hash")

    preview = store.preview_request("template-1", item.preview_id)
    image = preview["body"]["input"][0]["content"][0]["image_url"]
    assert image == {
        "collapsed": True,
        "mime_type": "image/jpeg",
        "byte_length": 5,
        "width": 1,
        "height": 1,
        "sha256": "image-sha",
    }

    raw = store.raw_request("template-1", item.preview_id)
    assert raw["body"]["input"][0]["content"][0]["image_url"] == (
        "data:image/jpeg;base64,aGVsbG8="
    )
    assert raw["headers"]["Authorization"] == "Bearer ••••••"
```

增加 `_fingerprint`、`draft_fingerprint` 和 `settings_fingerprint`。统一使用：

```python
encoded = json.dumps(
    payload,
    ensure_ascii=False,
    sort_keys=True,
    separators=(",", ":"),
).encode("utf-8")
return hashlib.sha256(encoded).hexdigest()
```

增加以下序列化逻辑；它深拷贝正文，绝不能修改会被发送的对象：

```python
def _request_for_sequence(
    item: PreparedRequestSession,
    sequence: int | None,
) -> PreparedArkRequest:
    if sequence is None:
        return item.prepared.primary
    for trace in item.attempts:
        if trace.sequence == sequence:
            return trace.request
    raise PreviewNotFound(f"attempt:{sequence}")


def _collapse_data_url(value: Any, item: PreparedRequestSession) -> Any:
    if value == item.prepared.image_data_url:
        image = item.prepared.image
        return {
            "collapsed": True,
            "mime_type": image.mime_type,
            "byte_length": image.byte_length,
            "width": image.width,
            "height": image.height,
            "sha256": image.sha256,
        }
    if isinstance(value, dict):
        return {key: _collapse_data_url(child, item) for key, child in value.items()}
    if isinstance(value, list):
        return [_collapse_data_url(child, item) for child in value]
    return value


def _request_package(request: PreparedArkRequest, body: dict[str, Any]) -> dict[str, Any]:
    return {
        "method": request.method,
        "url": request.url,
        "headers": {
            "Content-Type": "application/json",
            "Authorization": "Bearer ••••••",
        },
        "client_options": {"timeout_seconds": request.timeout_seconds},
        "body": body,
    }
```

`preview_request` 在锁内读取 item 和 request，调用 `_collapse_data_url(copy.deepcopy(request.body), item)`；`raw_request` 返回 `_request_package(request, copy.deepcopy(request.body))`。两个方法都接受可选 `sequence`，未传时读取主请求，传入时读取相应实际尝试。

- [ ] **Step 5: 加入自动清理循环并提交**

在 `request_previews.py` 增加：

```python
async def purge_request_previews(
    store: RequestPreviewStore,
    interval_seconds: float = 60,
) -> None:
    while True:
        await asyncio.sleep(interval_seconds)
        store.purge_expired()
```

Run:

```bash
backend/.venv/bin/pytest backend/tests/services/test_request_previews.py -v
backend/.venv/bin/ruff check backend/app/services/request_previews.py backend/tests/services/test_request_previews.py
git add backend/app/services/request_previews.py backend/tests/services/test_request_previews.py
git commit -m "feat: add temporary Ark request preview store"
```

Expected: 全部 PASS，ruff 无输出。

### Task 4: 暴露模板请求预览 API

**Files:**
- Modify: `backend/app/schemas.py:53-95`
- Modify: `backend/app/api/templates.py:1-210`
- Modify: `backend/app/main.py:1-145`
- Modify: `backend/app/dependencies.py:1-105`
- Modify: `backend/tests/conftest.py:36-210`
- Create: `backend/tests/api/test_template_request_previews.py`
- Modify: `backend/tests/api/test_template_test_run.py`

- [ ] **Step 1: 写当前草稿与折叠 Base64 的失败 API 测试**

创建 `backend/tests/api/test_template_request_previews.py`：

```python
import json


def configure_ark(app_harness) -> None:
    response = app_harness.client.put(
        "/api/settings/ark",
        json={
            "base_url": "https://ark.example/v3",
            "protocol": "responses",
            "model": "seed-test",
            "api_key": "preview-secret-key",
            "concurrency": 3,
            "timeout_seconds": 120,
            "max_retries": 2,
            "thinking": "disabled",
        },
    )
    assert response.status_code == 200


def draft_payload() -> dict:
    return {
        "name": "未保存模板名",
        "description": "未保存说明",
        "task_prompt": "UNSAVED_TASK_PROMPT",
        "rules": [
            {
                "rule_id": "unsaved_badge",
                "name": "未保存工牌规则",
                "text": "检查当前草稿中的工牌规则。",
            }
        ],
        "model_params": {"temperature": 0.1, "max_output_tokens": 4096},
    }


def test_prepare_request_uses_unsaved_draft_and_collapses_image(app_harness, png_file) -> None:
    configure_ark(app_harness)
    response = app_harness.client.post(
        f"/api/templates/{app_harness.seeded_template_id}/request-previews",
        data={"draft": json.dumps(draft_payload(), ensure_ascii=False)},
        files={"file": ("employee.png", png_file, "image/png")},
    )

    assert response.status_code == 201
    payload = response.json()
    assert payload["request"]["url"] == "https://ark.example/v3/responses"
    assert payload["request"]["mode"] == "json_schema"
    request_text = json.dumps(payload["request"]["body_preview"], ensure_ascii=False)
    assert "UNSAVED_TASK_PROMPT" in request_text
    assert "unsaved_badge" in request_text
    assert "未保存模板名" not in request_text
    assert "preview-secret-key" not in response.text
    image = payload["request"]["body_preview"]["input"][0]["content"][0]["image_url"]
    assert image["collapsed"] is True
    assert image["mime_type"] == "image/jpeg"
```

- [ ] **Step 2: 写 raw、send、stale 和 delete 的失败测试**

```python
def test_raw_send_and_delete_use_the_same_prepared_request(app_harness, png_file) -> None:
    configure_ark(app_harness)
    prepared = app_harness.client.post(
        f"/api/templates/{app_harness.seeded_template_id}/request-previews",
        data={"draft": json.dumps(draft_payload(), ensure_ascii=False)},
        files={"file": ("employee.png", png_file, "image/png")},
    ).json()
    preview_id = prepared["preview_id"]
    base = f"/api/templates/{app_harness.seeded_template_id}/request-previews/{preview_id}"

    raw = app_harness.client.get(f"{base}/raw")
    assert raw.status_code == 200
    assert raw.headers["cache-control"] == "no-store"
    data_url = raw.json()["body"]["input"][0]["content"][0]["image_url"]
    assert data_url.startswith("data:image/jpeg;base64,")
    assert "preview-secret-key" not in raw.text

    sent = app_harness.client.post(f"{base}/send")
    assert sent.status_code == 200
    assert sent.json()["result"]["verdict"] == "pass"
    assert sent.json()["attempts"][0]["mode"] == "json_schema"
    assert app_harness.inspector.prepared_calls[0].primary.body == raw.json()["body"]

    deleted = app_harness.client.delete(base)
    assert deleted.status_code == 204
    assert app_harness.client.get(f"{base}/raw").status_code == 404
```

另加四个独立测试：修改模型设置后 `/send` 返回 `409 preview_stale`；错误草稿返回 `422 invalid_template_draft`；错误图片返回 `422 unsupported_image`；模型调用失败时 502 响应的 `details.attempts` 包含已发生的 failed 请求且不含 API Key。

Run:

```bash
backend/.venv/bin/pytest backend/tests/api/test_template_request_previews.py -v
```

Expected: FAIL，新路由返回 404。

- [ ] **Step 3: 增加 API 响应 Schema**

从 `app.domain.results` 导入 `InspectionResult`，在 `backend/app/schemas.py` 增加：

```python
class ArkRequestRead(BaseModel):
    method: Literal["POST"]
    url: str
    protocol: Literal["responses", "chat"]
    mode: Literal["json_schema", "json_object", "repair"]
    model: str
    headers: dict[str, str]
    client_options: dict[str, int]
    body_preview: dict[str, Any]


class RequestPreviewRead(BaseModel):
    preview_id: str
    expires_at: datetime
    draft_fingerprint: str
    request: ArkRequestRead


class RawArkRequestRead(BaseModel):
    method: Literal["POST"]
    url: str
    headers: dict[str, str]
    client_options: dict[str, int]
    body: dict[str, Any]


class RequestAttemptRead(BaseModel):
    sequence: int
    kind: Literal["inspection", "schema_fallback", "json_repair"]
    mode: Literal["json_schema", "json_object", "repair"]
    status: Literal["succeeded", "failed"]
    status_code: int | None
    error_code: str | None
    request: ArkRequestRead


class RequestPreviewSendRead(BaseModel):
    result: InspectionResult
    attempts: list[RequestAttemptRead]
```

- [ ] **Step 4: 注入存储并实现四个 API**

在 `main.py` 创建 `RequestPreviewStore`，保存到 `app.state.request_previews`；lifespan 启动 `purge_request_previews` 任务，并在关停时取消任务、等待取消完成、调用 `clear()`。在 `dependencies.py` 增加：

```python
def get_request_preview_store(request: Request) -> RequestPreviewStore:
    return request.app.state.request_previews
```

在 `templates.py` 用 `Form` 接收草稿，校验逻辑固定为：

```python
def _draft_from_form(raw: str) -> TemplateWrite:
    try:
        return TemplateWrite.model_validate_json(raw)
    except ValidationError as exc:
        raise ApiError(
            422,
            "invalid_template_draft",
            "模板草稿校验失败。",
            exc.errors(include_url=False),
        ) from exc
```

用同一个转换帮助函数生成初始请求和 attempt 响应，避免字段命名分叉：

```python
def _request_read(
    request: PreparedArkRequest,
    package: dict[str, Any],
) -> ArkRequestRead:
    return ArkRequestRead(
        method=request.method,
        url=request.url,
        protocol=request.protocol,
        mode=request.mode,
        model=str(request.body["model"]),
        headers=package["headers"],
        client_options=package["client_options"],
        body_preview=package["body"],
    )
```

`_preview_response` 对主请求调用 `_request_read`；`_send_response` 按 sequence 排序遍历 `item.attempts`，逐项调用 `_request_read` 并把 `trace.request.purpose` 映射到 `kind`。

准备接口必须先确认模板存在，使用安全原始文件名和 `TemporaryDirectory` 调用 `ImageIngestor`，再用 `SYSTEM_EVIDENCE_POLICY`、当前草稿的 `task_prompt` 和 `rules` 调用 `build_prompt` 与 `prepare_inspection_request`。离开临时目录前必须已把 JPEG 编进 `PreparedInspection`。

实现以下路由：

```text
POST   /api/templates/{template_id}/request-previews
GET    /api/templates/{template_id}/request-previews/{preview_id}/raw
POST   /api/templates/{template_id}/request-previews/{preview_id}/send
DELETE /api/templates/{template_id}/request-previews/{preview_id}
```

raw 支持可选 query 参数 `sequence` 并设置 `Cache-Control: no-store`。send 先用当前设置指纹 `claim_for_send`，再在线程中调用 `ark.inspector.inspect_prepared`，把 callback 连接到 `record_attempt`；成功调用 `finish(preview_id, outcome.result, None)`。Ark 错误映射为 502，并在 `ApiError.details.attempts` 中返回当前会话已发生的脱敏尝试，使前端即使收到非 2xx 响应也能展示失败请求；过期映射为 410，配置变化与重复发送映射为 409。不存在的模板必须沿用 `404 template_not_found`。

- [ ] **Step 5: 更新测试替身和旧单图测试**

给 `FakeInspectionService` 增加 `prepared_calls: list[PreparedInspection]` 与：

```python
def inspect_prepared(
    self,
    prepared: PreparedInspection,
    on_request: RequestTraceCallback | None = None,
) -> InspectionOutcome:
    self.prepared_calls.append(prepared)
    outcome = self.inspect(
        prepared.config,
        prepared.system_prompt,
        prepared.user_prompt,
        Path("prepared-image.jpg"),
    )
    if on_request is not None:
        on_request(
            ArkRequestTrace(
                sequence=1,
                request=prepared.primary,
                status="succeeded",
                status_code=200,
                error_code=None,
            )
        )
    return outcome
```

旧 `POST /api/templates/{id}/test` 保留，但内部改用 `prepare_inspection_request` 加 `inspect_prepared`；原测试继续断言不创建 `InspectionTask` 或 `TaskImage`。

- [ ] **Step 6: 运行 API 回归并提交**

```bash
backend/.venv/bin/pytest backend/tests/api/test_template_request_previews.py backend/tests/api/test_template_test_run.py backend/tests/api/test_templates.py -v
backend/.venv/bin/mypy backend/app
backend/.venv/bin/ruff check backend/app backend/tests/api/test_template_request_previews.py backend/tests/conftest.py
git add backend/app/main.py backend/app/dependencies.py backend/app/schemas.py backend/app/api/templates.py backend/tests/conftest.py backend/tests/api/test_template_request_previews.py backend/tests/api/test_template_test_run.py
git commit -m "feat: expose template request preview API"
```

Expected: pytest 全部 PASS，mypy 与 ruff 无错误。

### Task 5: 建立请求抽屉组件和前端 API 合约

**Files:**
- Modify: `frontend/src/api/types.ts:45-90`
- Modify: `frontend/src/api/client.ts:69-86`
- Create: `frontend/src/features/templates/RequestInspectorDrawer.tsx`
- Create: `frontend/src/features/templates/RequestInspectorDrawer.test.tsx`
- Modify: `frontend/src/styles.css:729-792`

- [ ] **Step 1: 写抽屉的失败组件测试**

创建 `RequestInspectorDrawer.test.tsx`。测试数据使用固定 URL、`json_schema`、1 KB JPEG 元数据和不含 Base64 的 `body_preview`。至少覆盖：

```tsx
test("shows endpoint and collapsed image without rendering base64", () => {
  renderDrawer();

  expect(screen.getByText("https://ark.example/v3/responses")).toBeInTheDocument();
  expect(screen.getByText("JSON Schema")).toBeInTheDocument();
  expect(screen.getByText(/JPEG · 1 KB · 64 × 64/)).toBeInTheDocument();
  expect(screen.queryByText(/data:image\/jpeg;base64/)).not.toBeInTheDocument();
});


test("requires confirmation before copying the complete request", async () => {
  const user = userEvent.setup();
  const onCopy = vi.fn();
  renderDrawer({ onCopy });

  await user.click(screen.getByRole("button", { name: "复制完整请求" }));
  expect(screen.getByText("完整请求包含图片数据")).toBeInTheDocument();
  expect(onCopy).not.toHaveBeenCalled();
  await user.click(screen.getByRole("button", { name: "确认复制" }));
  expect(onCopy).toHaveBeenCalledOnce();
});
```

另加测试：`stale=true` 时发送按钮禁用并显示“请求已过期”；发送成功后可切换“实际请求”和“结构化结果”；多次 attempt 按 sequence 排序。

- [ ] **Step 2: 运行测试并确认组件不存在**

```bash
cd frontend && npm test -- src/features/templates/RequestInspectorDrawer.test.tsx
```

Expected: FAIL，找不到 `RequestInspectorDrawer`。

- [ ] **Step 3: 定义 TypeScript 类型和客户端方法**

在 `frontend/src/api/types.ts` 增加：

```ts
export type ArkRequestMode = "json_schema" | "json_object" | "repair";

export interface TemplateDraftPayload {
  name: string;
  description: string;
  task_prompt: string;
  rules: TemplateRule[];
  model_params: TemplateModelParams;
}

export interface ArkRequestView {
  method: "POST";
  url: string;
  protocol: "responses" | "chat";
  mode: ArkRequestMode;
  model: string;
  headers: Record<string, string>;
  client_options: { timeout_seconds: number };
  body_preview: Record<string, unknown>;
}

export interface RequestPreview {
  preview_id: string;
  expires_at: string;
  draft_fingerprint: string;
  request: ArkRequestView;
}

export interface RawArkRequest {
  method: "POST";
  url: string;
  headers: Record<string, string>;
  client_options: { timeout_seconds: number };
  body: Record<string, unknown>;
}

export interface RequestAttempt {
  sequence: number;
  kind: "inspection" | "schema_fallback" | "json_repair";
  mode: ArkRequestMode;
  status: "succeeded" | "failed";
  status_code: number | null;
  error_code: string | null;
  request: ArkRequestView;
}

export interface RequestPreviewSendResult {
  result: InspectionResult;
  attempts: RequestAttempt[];
}
```

把 `TemplateDraftPayload` 定义在 `types.ts`，字段与后端 `TemplateWrite` 一致，并让 `TemplateDraft` 复用该类型。在 `ApiClient` 增加：

```ts
prepareTemplateRequest = (id: string, draft: TemplateDraftPayload, file: File) => {
  const body = new FormData();
  body.append("draft", JSON.stringify(draft));
  body.append("file", file);
  return this.request<RequestPreview>(`/api/templates/${id}/request-previews`, {
    method: "POST",
    body,
  });
};

getTemplateRequestRaw = (id: string, previewId: string, sequence?: number) => {
  const query = sequence === undefined ? "" : `?sequence=${sequence}`;
  return this.request<RawArkRequest>(
    `/api/templates/${id}/request-previews/${previewId}/raw${query}`,
  );
};

sendTemplateRequest = (id: string, previewId: string) =>
  this.request<RequestPreviewSendResult>(
    `/api/templates/${id}/request-previews/${previewId}/send`,
    { method: "POST" },
  );

deleteTemplateRequest = (id: string, previewId: string) =>
  this.request<void>(`/api/templates/${id}/request-previews/${previewId}`, {
    method: "DELETE",
  });
```

- [ ] **Step 4: 实现受控请求抽屉**

`RequestInspectorDrawer.tsx` 不直接调用 API，固定 props 为：

```tsx
interface RequestInspectorDrawerProps {
  preview: RequestPreview;
  attempts: RequestAttempt[];
  result: InspectionResult | null;
  imagePreviewUrl: string | null;
  imageName: string;
  stale: boolean;
  sending: boolean;
  copying: boolean;
  onClose: () => void;
  onRefresh: () => void;
  onSend: () => void;
  onCopy: (sequence?: number) => void;
}
```

组件内部只维护顶层标签、选中的 attempt sequence 和复制确认状态。使用 `JSON.stringify(selectedRequest.body_preview, null, 2)` 展示原始 JSON；递归定位 `collapsed: true` 图片对象并渲染摘要卡；用 `imagePreviewUrl` 和 `imageName` 保留现有测试图片预览；`result` 存在时复用 `ResultPanel`。标签使用 `role="tablist"`、`role="tab"` 和 `aria-selected`，抽屉使用 `role="complementary"` 与 `aria-label="方舟请求检查器"`。`sending=true` 时关闭、刷新和发送按钮均禁用，避免删除正在记录的会话。

- [ ] **Step 5: 增加抽屉样式并通过组件测试**

在 `styles.css` 增加：

```css
.request-inspector-drawer {
  display: flex;
  width: min(640px, 100%);
  height: 100%;
  flex-direction: column;
  overflow: hidden;
  background: var(--surface);
  border-left: 1px solid var(--line);
}

.request-inspector-body {
  flex: 1;
  padding: 24px;
  overflow: auto;
}

.request-code {
  padding: 16px;
  overflow: auto;
  color: #e8eadf;
  background: #252722;
  border-radius: 7px;
  font-family: "SF Mono", "JetBrains Mono", monospace;
  font-size: 11px;
  line-height: 1.65;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}

.request-inspector-footer {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
  padding: 16px 24px;
  background: var(--surface);
  border-top: 1px solid var(--line);
}
```

补充 metadata badge、attempt 列表、图片摘要、标签页和复制警告样式；在现有移动端 media query 中把抽屉宽度设为 `100%`。

Run:

```bash
cd frontend && npm test -- src/features/templates/RequestInspectorDrawer.test.tsx
```

Expected: PASS。

- [ ] **Step 6: 提交组件和 API 合约**

```bash
git add frontend/src/api/types.ts frontend/src/api/client.ts frontend/src/features/templates/RequestInspectorDrawer.tsx frontend/src/features/templates/RequestInspectorDrawer.test.tsx frontend/src/styles.css
git commit -m "feat: add Ark request inspector drawer"
```

### Task 6: 把未保存草稿接入模板页完整流程

**Files:**
- Modify: `frontend/src/features/templates/TemplateEditor.tsx:1-205`
- Modify: `frontend/src/features/templates/TemplatesPage.tsx:1-192`
- Modify: `frontend/src/features/templates/TemplatesPage.test.tsx:1-130`

- [ ] **Step 1: 写页面级失败测试和 MSW 合约**

在 `TemplatesPage.test.tsx` 增加四个 MSW handler，分别处理 `request-previews`、`raw`、`send` 和 `delete`。准备接口通过 `await request.formData()` 读取并记录 `draft`。增加：

```tsx
test("previews and sends the request from the unsaved draft", async () => {
  handlers();
  const user = userEvent.setup();
  renderTemplates();
  await user.click(await screen.findByRole("button", { name: "通用员工仪容仪表" }));
  await user.type(screen.getByLabelText("任务补充说明"), " UNSAVED_CHANGE");
  await user.upload(
    screen.getByLabelText("选择单张测试图片"),
    new File(["image"], "employee.png", { type: "image/png" }),
  );

  await user.click(screen.getByRole("button", { name: "查看请求" }));

  expect(receivedDraft?.task_prompt).toBe("逐项检查仪容仪表。 UNSAVED_CHANGE");
  expect(await screen.findByRole("complementary", { name: "方舟请求检查器" })).toBeVisible();
  expect(screen.getByText("https://ark.example/v3/responses")).toBeInTheDocument();

  await user.click(screen.getByRole("button", { name: "发送单图测试" }));
  expect(await screen.findByText("工牌缺失")).toBeInTheDocument();
  expect(screen.getByRole("tab", { name: "实际请求" })).toBeInTheDocument();
});
```

另加测试：预览后编辑规则会显示“请求已过期”并禁用发送；关闭抽屉调用 DELETE；后端返回 `preview_stale` 时保留抽屉并显示刷新按钮。

- [ ] **Step 2: 运行页面测试并确认新入口不存在**

```bash
cd frontend && npm test -- src/features/templates/TemplatesPage.test.tsx
```

Expected: FAIL，找不到“查看请求”。

- [ ] **Step 3: 修改编辑器入口**

将 `TemplateEditorProps` 中旧测试属性替换为：

```tsx
onRequestPreview: () => void;
requestPreparing: boolean;
requestPreviewEnabled: boolean;
```

图片选择控件之后渲染：

```tsx
<button
  className="button quiet-button"
  type="button"
  disabled={!requestPreviewEnabled || requestPreparing}
  onClick={onRequestPreview}
>
  <Eye size={17} /> {requestPreparing ? "正在准备..." : "查看请求"}
</button>
```

删除旧“运行单图测试”按钮。删除原“预览最终提示词”按钮和不完整 modal，避免继续把缺少文件名、Schema 和图片的内容称为最终提示词。

- [ ] **Step 4: 在页面管理准备、失效、发送、复制和清理**

`TemplatesPage` 增加：

```tsx
const [requestPreview, setRequestPreview] = useState<RequestPreview | null>(null);
const [requestAttempts, setRequestAttempts] = useState<RequestAttempt[]>([]);
const [preparedDraftJson, setPreparedDraftJson] = useState<string | null>(null);
const [preparedImage, setPreparedImage] = useState<File | null>(null);
const [testResult, setTestResult] = useState<InspectionResult | null>(null);

const currentDraftJson = draft ? JSON.stringify(draft) : null;
const requestStale =
  requestPreview !== null &&
  (preparedDraftJson !== currentDraftJson || preparedImage !== testImage);
```

准备 mutation 成功时保存预览、当前草稿 JSON 和 `File` 引用并打开抽屉。发送 mutation 调用 `sendTemplateRequest`，保存 result 与 attempts。复制 mutation 获取 raw 请求后执行：

```tsx
await navigator.clipboard.writeText(JSON.stringify(raw, null, 2));
```

关闭抽屉时先清空本地状态，再尽力调用 DELETE；DELETE 失败不能阻止关闭。切换模板时执行相同清理。所有错误沿用 `ApiError.message` 显示，不吞掉 `preview_stale`。发送返回 `ApiError` 时，如果 `error.details` 含 `attempts`，先写入 `requestAttempts` 再显示错误，保证失败调用也可检查。

- [ ] **Step 5: 运行页面测试、构建并提交**

```bash
cd frontend
npm test -- src/features/templates/RequestInspectorDrawer.test.tsx src/features/templates/TemplatesPage.test.tsx
npm run build
git add frontend/src/features/templates/TemplateEditor.tsx frontend/src/features/templates/TemplatesPage.tsx frontend/src/features/templates/TemplatesPage.test.tsx
git commit -m "feat: preview unsaved template requests"
```

Expected: Vitest 全部 PASS，TypeScript 与 Vite 构建成功。

### Task 7: 增加端到端覆盖并执行完整验证

**Files:**
- Modify: `backend/tests/e2e_server.py:28-54`
- Modify: `frontend/e2e/inspection-flow.spec.ts`

- [ ] **Step 1: 让端到端替身支持已准备请求**

给 `DeterministicInspectionService` 增加：

```python
def inspect_prepared(
    self,
    prepared: PreparedInspection,
    on_request: RequestTraceCallback | None = None,
) -> InspectionOutcome:
    if on_request is not None:
        on_request(
            ArkRequestTrace(
                sequence=1,
                request=prepared.primary,
                status="succeeded",
                status_code=200,
                error_code=None,
            )
        )
    return InspectionOutcome(
        result=InspectionResult(
            image_valid=True,
            verdict=Verdict.PASS,
            summary="测试图片可见项目通过",
            findings=[
                Finding(
                    rule_id="appearance",
                    rule_name="仪容仪表",
                    status=FindingStatus.PASS,
                    observation="可见区域无明显违规",
                    visual_evidence="人物上半身清晰可见",
                )
            ],
        ),
        usage=successful_usage(prepared.config.protocol),
    )
```

- [ ] **Step 2: 写浏览器端请求检查器测试**

在 `frontend/e2e/inspection-flow.spec.ts` 增加：

```ts
test("previews an unsaved template request before sending", async ({ page }) => {
  await page.goto("/settings");
  await page.getByLabel("模型 / Endpoint ID").fill("doubao-seed-test");
  await page.getByLabel("API Key").fill("E2E_REQUEST_SECRET");
  await page.getByRole("button", { name: "测试并保存" }).click();
  await expect(page.getByText("连接测试通过", { exact: false })).toBeVisible();

  await page.goto("/templates");
  await page.getByLabel("任务补充说明").fill("E2E_UNSAVED_PROMPT");
  await page.getByLabel("选择单张测试图片").setInputFiles("e2e/fixtures/one.png");
  await page.getByRole("button", { name: "查看请求" }).click();

  const drawer = page.getByRole("complementary", { name: "方舟请求检查器" });
  await expect(drawer).toContainText("/api/v3/responses");
  await expect(drawer).toContainText("E2E_UNSAVED_PROMPT");
  await expect(drawer).toContainText("JSON Schema");
  await expect(drawer).not.toContainText("E2E_REQUEST_SECRET");

  await drawer.getByRole("button", { name: "发送单图测试" }).click();
  await expect(drawer.getByText("测试图片可见项目通过")).toBeVisible();
  await drawer.getByRole("tab", { name: "实际请求" }).click();
  await expect(drawer.getByText("请求 1")).toBeVisible();
});
```

- [ ] **Step 3: 运行聚焦端到端测试**

```bash
cd frontend && npm run e2e -- --grep "previews an unsaved template request"
```

Expected: 1 test passed，页面和浏览器存储中均不出现 API Key。

- [ ] **Step 4: 运行完整后端验证**

```bash
backend/.venv/bin/ruff check backend/app backend/tests
backend/.venv/bin/mypy backend/app
backend/.venv/bin/pytest
```

Expected: ruff、mypy 和全部 pytest 测试通过。

- [ ] **Step 5: 运行完整前端验证**

```bash
cd frontend
npm test
npm run build
npm run e2e
```

Expected: Vitest、TypeScript/Vite 构建和 Playwright 全部通过。

- [ ] **Step 6: 检查安全边界和工作树**

```bash
rg -n "Authorization|api_key|image_url" backend/app/services/request_previews.py backend/app/api/templates.py frontend/src/features/templates/RequestInspectorDrawer.tsx
git diff --check
git status --short
```

Expected: 只有脱敏 Authorization、钥匙串读取和显式 Data URL 展示逻辑；没有打印或数据库持久化请求正文；`git diff --check` 无输出；用户原有 QR 图片仍保持未跟踪且未进入提交。

- [ ] **Step 7: 提交端到端覆盖**

```bash
git add backend/tests/e2e_server.py frontend/e2e/inspection-flow.spec.ts
git commit -m "test: cover Ark request inspector flow"
```

## 规格覆盖核对

- 模板页右侧 640 px 抽屉：Task 5、Task 6。
- 当前未保存草稿：Task 4、Task 6、Task 7。
- URL、协议、模型、JSON mode、请求正文：Task 1、Task 4、Task 5。
- Base64 默认折叠且可显式复制：Task 3、Task 4、Task 5。
- 页面展示对象与发送对象一致：Task 1、Task 3、Task 4。
- fallback 与 repair 的全部实际请求：Task 2、Task 4、Task 5。
- API Key 不泄漏、正文不落库、TTL 清理：Task 3、Task 4、Task 7。
- 正式任务语义与旧单图测试兼容：Task 1、Task 4、Task 7。
