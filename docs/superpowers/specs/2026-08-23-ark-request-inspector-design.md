# 方舟请求检查器设计

日期：2026-08-23  
状态：已确认

## 1. 背景

模板页目前只能预览系统提示词和部分用户提示词。真正发送给火山方舟的请求还会包含完整 URL、模型、协议、图片 Data URL、结构化输出配置及超时等字段，因此当前“最终提示词预览”不能回答以下问题：

- 图片是否通过 Responses API 直接发送；
- 最终请求使用哪个 URL、模型和协议；
- 是否启用 `json_schema` 或降级到 `json_object`；
- 当前未保存的任务说明和规则最终如何进入请求；
- Schema 降级或 JSON 修复时实际又发送了哪些请求。

另外，当前单图测试接口只读取数据库中的当前模板版本，不会使用页面尚未保存的草稿。请求检查器需要同时解决预览与单图测试语义不一致的问题。

## 2. 目标

1. 在模板编辑页选择单张测试图片后，可查看基于当前未保存草稿生成的方舟请求。
2. 展示请求方法、完整 URL、协议、模型、JSON 输出模式和请求正文。
3. 默认折叠大体积 Base64，只显示图片格式、尺寸、字节数和 SHA-256；用户可显式复制完整请求。
4. 从预览中发送的请求必须与页面展示的请求为同一个已准备对象，不能再次独立拼装。
5. 发送完成后展示全部实际尝试，包括 `json_schema` 主请求、`json_object` 降级请求和纯文本 JSON 修复请求。
6. 不展示、返回、缓存或记录 API Key。
7. 不把请求正文或图片 Base64 持久化到数据库。

## 3. 非目标

- 本期不在正式任务图片详情页增加请求检查器。
- 不建设通用 HTTP 抓包、代理或网络调试器。
- 不展示 TLS 层或 Ark SDK 自动添加的原始传输头。
- 不改变正式任务的重试、判定和报告语义。
- 不顺带修复模板 `temperature`、`max_output_tokens` 当前未进入 Ark 请求的问题；检查器应忠实显示实际请求中是否存在这些字段。

## 4. “实际请求”的定义

本功能展示的是应用交给 Ark SDK 的提供方请求：

- HTTP 方法；
- 根据 `base_url` 和协议解析出的最终接口 URL；
- 经脱敏的必要请求头；
- Ark SDK 将序列化的完整 JSON 请求正文；
- `timeout` 等不进入 HTTP 正文的客户端选项。

它不是 TLS 抓包结果，也不承诺展示 SDK 内部自动补充的非业务字段。`Authorization` 由 SDK 在发送阶段添加，界面只显示 `Bearer ••••••`，完整请求复制内容中也不包含真实凭据。

Responses API 的 URL 为：

```text
{base_url 去除末尾斜杠}/responses
```

Chat Completions 的 URL 为：

```text
{base_url 去除末尾斜杠}/chat/completions
```

## 5. 页面与交互设计

### 5.1 放置位置

采用模板编辑页右侧抽屉。它复用并扩展现有单图测试结果抽屉，避免同时出现两个右侧浮层。

模板编辑器底部保留图片选择控件，并在选择有效图片后启用“查看请求”。点击后打开约 640 px 宽的抽屉。抽屉打开时模板编辑内容仍在背景中可见，便于对照规则和请求正文。

### 5.2 抽屉内容

抽屉顶部显示：

- `POST`；
- 完整接口 URL；
- 模型；
- `Responses` 或 `Chat Completions`；
- `json_schema`、`json_object` 或 `repair` 状态；
- “基于未保存草稿”提示。

请求区域提供两个标签：

1. **可读视图**：按 URL、提示词、图片、输出格式等区块展示。
2. **原始 JSON**：展示与 Ark SDK 调用参数一致的 JSON 结构。

图片字段默认渲染为摘要，例如：

```text
JPEG · 1.8 MB · 2048 × 1365 · SHA-256 6f…a2
```

摘要可展开查看 Data URL 的开头和结尾，但不直接渲染全部 Base64。点击“复制完整请求”时再获取完整正文并写入剪贴板。

抽屉底部提供“发送单图测试”。发送成功后新增“结构化结果”标签并自动切换过去；“实际请求”标签继续保留，供用户回看每次尝试。

### 5.3 失效提示

生成预览后，以下任一条件变化都会把预览标记为过期并禁用发送：

- 任务补充说明、规则或其他草稿字段发生变化；
- 用户重新选择图片；
- 方舟模型设置发生变化；
- 预览超过 10 分钟。

用户需要点击“刷新请求”生成新预览。过期预览不会被静默发送。

## 6. 后端设计

### 6.1 单一请求构造入口

`backend/app/services/prompts.py` 继续只负责生成 `PromptBundle(system, user)`，不承担 HTTP 或 Ark 协议职责。

`backend/app/services/ark.py` 增加统一的已准备请求模型，并成为预览和发送的唯一请求构造入口：

```python
@dataclass(frozen=True)
class PreparedArkRequest:
    method: str
    url: str
    protocol: Literal["responses", "chat"]
    mode: Literal["json_schema", "json_object", "repair"]
    body: dict[str, Any]
    timeout_seconds: int
    image_metadata: PreparedImageMetadata | None
```

主调用路径先生成 `PreparedArkRequest`，然后以其 `body` 调用对应 SDK 方法，并把 `timeout_seconds` 作为 SDK 客户端选项传入。预览接口也只读取这一对象，不能复制一份独立的请求拼装逻辑。

### 6.2 临时预览存储

新增 `backend/app/services/request_previews.py`，负责管理短期 `PreparedRequestSession`：

- 使用随机 UUID 作为 `preview_id`；
- 保存已序列化请求正文、脱敏元数据、配置快照和实际尝试记录；
- 不保存 API Key；
- 默认 TTL 为 10 分钟；
- 同一浏览器流程生成新预览时删除旧预览；
- 发送结束后保留到抽屉关闭，最长不超过 TTL；
- 进程退出后允许全部丢失，不写入 SQLite。

完整 Base64 只存在于该临时会话。图片完成预处理并编码进已准备请求后立即删除临时文件；关闭抽屉时前端显式请求删除会话，TTL 清理作为兜底。存储实现必须使用锁保护创建、读取、发送和清理操作，避免并发请求读到半清理状态。

### 6.3 使用当前草稿

准备请求接口接收 `TemplateWrite` 对应的完整草稿，而不是只接收模板 ID。后端按现有 Pydantic 规则重新校验：

- 模板名称和说明仅参与草稿校验，不进入模型请求；
- `task_prompt` 进入用户提示词的“任务说明”；
- `rules` 按当前页面顺序序列化；
- 固定证据政策仍由后端强制添加，前端不能覆盖；
- 图片提示词中的文件名使用用户上传的安全原始文件名，不使用临时 UUID 文件名。

### 6.4 API

#### 准备请求

```http
POST /api/templates/{template_id}/request-previews
Content-Type: multipart/form-data

file=<image>
draft=<TemplateWrite JSON>
```

示意返回（下例聚焦图片摘要，实际响应保留正文的全部其他字段）：

```json
{
  "preview_id": "uuid",
  "expires_at": "2026-08-23T12:10:00Z",
  "draft_fingerprint": "sha256",
  "request": {
    "method": "POST",
    "url": "https://ark.cn-beijing.volces.com/api/v3/responses",
    "protocol": "responses",
    "mode": "json_schema",
    "model": "ep-...",
    "headers": {
      "Content-Type": "application/json",
      "Authorization": "Bearer ••••••"
    },
    "client_options": {
      "timeout_seconds": 120
    },
    "body_preview": {
      "model": "ep-...",
      "input": [
        {
          "role": "user",
          "content": [
            {
              "type": "input_image",
              "image_url": {
                "collapsed": true,
                "mime_type": "image/jpeg",
                "byte_length": 1887436,
                "width": 2048,
                "height": 1365,
                "sha256": "6f…a2"
              }
            }
          ]
        }
      ]
    }
  }
}
```

`body_preview` 与真实正文结构相同，但以图片元数据对象替换 Data URL 字符串；实际响应中的其他字段不得省略。配置指纹覆盖会影响 URL、请求正文或客户端选项的方舟公开设置，不包含 API Key。

#### 获取完整请求

```http
GET /api/templates/{template_id}/request-previews/{preview_id}/raw
```

返回包含方法、URL、脱敏请求头、客户端选项和完整 JSON 正文的请求包，并设置 `Cache-Control: no-store`。正文中的 Data URL 不截断；真实 Authorization 始终不返回。

#### 发送已准备请求

```http
POST /api/templates/{template_id}/request-previews/{preview_id}/send
```

后端直接发送会话中的 `PreparedArkRequest`。返回结构化巡检结果和已脱敏的尝试摘要。发送前若模型设置快照已变化，则返回 `409 preview_stale`，而不是使用新设置重新拼装。

#### 删除预览

```http
DELETE /api/templates/{template_id}/request-previews/{preview_id}
```

关闭抽屉、替换图片或生成新预览时调用。

## 7. 实际尝试与降级

每次向 Ark 发出的请求都记录为当前临时会话中的 `RequestAttempt`：

```python
@dataclass(frozen=True)
class RequestAttempt:
    sequence: int
    kind: Literal["primary", "schema_fallback", "json_repair"]
    request: PreparedArkRequest
    outcome: Literal["succeeded", "failed"]
    status_code: int | None
    error_code: str | None
```

抽屉按真实顺序显示：

```text
请求 1 · json_schema · 失败
请求 2 · json_object · 成功
请求 3 · JSON 修复 · 成功
```

只有实际发生的尝试才出现。Schema 不受支持时生成新的 `PreparedArkRequest(mode="json_object")`；模型返回无效 JSON 时生成不含图片的 `PreparedArkRequest(mode="repair")`。每次尝试的完整正文都可单独复制，但均不含 API Key。

## 8. 前端组件

新增 `frontend/src/features/templates/RequestInspectorDrawer.tsx`，职责包括：

- 请求准备、刷新和失效状态；
- 可读视图与原始 JSON；
- 图片摘要与 Base64 折叠；
- 完整请求复制；
- 发送状态、尝试列表和结构化结果。

`TemplatesPage.tsx` 负责把当前 `TemplateDraft` 和选中的 `File` 交给抽屉，并在草稿或图片变化时通知抽屉失效。现有结果展示继续复用 `ResultPanel`，但承载位置移动到统一抽屉中。

前端不得自行构造方舟 URL、Schema 或最终请求正文，避免与后端实际发送逻辑漂移。

## 9. 错误处理

| 场景 | HTTP / 状态 | 页面行为 |
|---|---|---|
| 草稿校验失败 | `422` | 定位并提示无效规则或字段 |
| 图片格式或大小无效 | `422` | 保留草稿，提示重新选图 |
| 方舟未配置 | `409 ark_not_configured` | 引导前往模型设置 |
| 草稿、图片或设置变化 | `409 preview_stale` | 禁止发送并显示“刷新请求” |
| 预览已过期 | `410 preview_expired` | 清空会话并要求重新准备 |
| Ark 请求失败 | 现有错误分类 | 保留已发生的请求尝试供排查 |
| JSON 修复仍失败 | `invalid_model_output` | 显示全部尝试并保留错误结果 |

失败不得触发前端自行重放完整请求。所有协议降级和格式修复继续由 Ark 服务控制。

## 10. 安全与隐私

- API Key 只从系统钥匙串读取并在发送时使用，不进入 `PreparedArkRequest`。
- 所有预览、原始请求和尝试接口设置 `Cache-Control: no-store`。
- 服务端日志不得输出请求正文、Data URL、图片字节或 Authorization。
- “复制完整请求”会明确提示内容包含完整图片数据。
- 临时会话只用于本机单用户流程，不提供跨进程恢复。
- 预览 ID 必须是不可预测 UUID，并校验其所属模板。

## 11. 测试策略

### 11.1 后端单元测试

- Responses `PreparedArkRequest.body` 与实际传给 `responses.create` 的正文参数完全相同，`timeout_seconds` 只作为客户端选项传入。
- Chat 请求同样使用统一对象。
- JSON Schema 降级和 JSON 修复分别生成独立尝试记录。
- API Key 不出现在准备对象、序列化结果或错误文本中。
- TTL 到期和显式删除会释放请求正文。

### 11.2 API 测试

- 未保存草稿中的任务说明、规则 ID、顺序均出现在预览正文。
- 模板名称和说明不进入模型提示词。
- 图片摘要与完整 Data URL 对应同一预处理图片。
- `raw` 返回完整正文并包含 `Cache-Control: no-store`。
- `send` 使用已准备对象，不重新调用请求构造器。
- 配置变化、过期预览和非法图片返回稳定错误码。

### 11.3 前端测试

- 未选图片时“查看请求”禁用。
- 选择图片后打开右侧抽屉并展示 URL、模型和输出模式。
- Base64 默认折叠，显式操作后才获取完整请求。
- 修改草稿或更换图片会使预览失效。
- 发送后仍可在请求与结构化结果间切换。
- 多次真实尝试按顺序显示并可分别查看。

## 12. 验收标准

1. 用户无需保存模板即可看到当前草稿对应的完整方舟请求。
2. 页面显示的主请求正文与交给 Ark SDK 序列化的正文对象完全相同；客户端选项单独、准确展示。
3. 用户可以确认图片是内联 Data URL，并确认输出模式是 `json_schema` 或实际降级模式。
4. 任意响应、日志和数据库记录中均不存在 API Key 或持久化 Base64。
5. Schema 降级和 JSON 修复发生时，用户可以看到每一次真实请求及其结果。
