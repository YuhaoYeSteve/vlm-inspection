# Token 用量可观测性设计规格

- 日期：2026-08-23
- 状态：待用户书面复核
- 适用分支：`feat/vlm-inspection-mvp`
- 依赖系统：Seed VLM 巡检本地 Web 应用

## 1. 背景

巡检系统已经为每张图片独立调用火山方舟，并在 `inference_attempts.usage_json` 中预留了模型用量字段，但当前 Ark 适配器只返回结构化巡检结果，丢弃了 SDK Response 中的 `usage`；Runner 创建 attempt 时也没有写入 usage。

本功能补齐从方舟响应到 SQLite、任务 API、任务工作台、历史记录和飞书报告的完整 Token 用量链路。目标是让用户知道整个任务和每张图片实际返回了多少输入、输出、缓存命中和推理 Token，同时明确展示无法获取用量的调用，避免把未知误报为零。

## 2. 已确认范围

1. 每张图片的用量包含与该图片相关的所有模型调用：初始检查、自动重试、人工重跑、原生 Schema 降级和 JSON 修复。
2. 展示任务级、图片级和逐调用三级明细。
3. 本地任务页和历史记录展示 Token；飞书报告加入任务级 Token 汇总，不加入逐调用长表。
4. 只展示 Token，不估算人民币费用。
5. 每图用量是该图关联的完整图文请求用量，包含图片、系统提示词和用户提示词；不声称是图片内容单独消耗的 Token。
6. API 未返回 usage 的请求标记为未知，不按零计算。
7. 首版复用现有 `inference_attempts.usage_json`，不新增用量明细表。

## 3. 非目标

- 不计算或展示费用。
- 不拆分图片 Token 与文本 Token；火山方舟当前 usage 不提供这一维度。
- 不建设跨任务成本预算、告警、配额管理或财务对账。
- 不从方舟控制台反查或补录本地未收到的 usage。
- 不把缓存命中或推理 Token 额外加到总 Token 中。
- 不在飞书报告中输出每次调用的完整明细表。

## 4. 火山方舟返回能力

### 4.1 Responses API

官方完成响应和项目当前安装的 `volcengine-python-sdk` 5.0.45 均提供以下结构：

```json
{
  "usage": {
    "input_tokens": 58,
    "input_tokens_details": {
      "cached_tokens": 0
    },
    "output_tokens": 1647,
    "output_tokens_details": {
      "reasoning_tokens": 1273
    },
    "total_tokens": 1705
  }
}
```

Responses SDK 还允许可选的音频与工具用量字段。巡检任务不使用音频或内置工具，因此首版不把它们做成界面指标，但会在 `raw_usage` 中原样保留，以便未来兼容。

### 4.2 Chat Completions API

应用仍支持 Chat 协议。它使用另一组字段名：

- `prompt_tokens` → `input_tokens`
- `completion_tokens` → `output_tokens`
- `total_tokens` → `total_tokens`
- `prompt_tokens_details.cached_tokens` → `cached_input_tokens`
- `completion_tokens_details.reasoning_tokens` → `reasoning_tokens`

两个协议在进入业务层之前统一为同一内部结构。

### 4.3 字段关系

- `total_tokens = input_tokens + output_tokens`
- `cached_input_tokens` 是 `input_tokens` 的子集。
- `reasoning_tokens` 是 `output_tokens` 的子集。
- `uncached_input_tokens = input_tokens - cached_input_tokens`，这是本地派生值，不是额外用量。

因此任务总量只累加 `total_tokens`。缓存命中、未缓存输入和推理 Token 用于解释构成，不能再次加入总量。

### 4.4 官方依据

- [火山方舟 Responses 流式响应](https://www.volcengine.com/docs/82379/1599499?lang=zh)
- [火山方舟迁移至 Responses API](https://www.volcengine.com/docs/82379/1585128?lang=zh)
- [火山方舟上下文缓存（Responses API）](https://www.volcengine.com/docs/82379/1602228?lang=zh)

## 5. 架构与组件边界

### 5.1 用量领域模块

新增一个纯 Python 用量模块，负责：

- 将 Responses 与 Chat 的 SDK usage 转为统一字段。
- 校验非负数、子集关系和总量关系。
- 生成单次高层 attempt 的 trace 与 totals。
- 将多个 attempts 汇总为图片级和任务级 summary。
- 判定 `complete`、`partial`、`unavailable` 等展示状态。

该模块不访问数据库、不调用 Ark，也不依赖 FastAPI，因此可用固定 JSON 做完整单元测试。

### 5.2 Ark 适配器

Ark 适配器负责围绕每次真正调用 SDK 的位置创建调用记录。调用用途限定为：

- `inspection`：原始图片巡检请求。
- `schema_fallback`：原生 JSON Schema 不支持后发起的 JSON Object 请求。
- `json_repair`：模型正文无法通过 Schema 校验后的纯文本 JSON 修复请求。

适配器在解析模型正文之前提取 usage。因此即使正文不是合法 JSON，初始调用的 Token 仍能记录。

成功时，适配器返回巡检结果和完整 attempt usage trace。失败时，现有异常携带已经收集到的 trace，使 Runner 仍可保存失败前已经完成的调用，以及最后一次 usage 未返回的调用。

### 5.3 Runner 与仓储

Runner 保持当前“每次 retry 一条 `InferenceAttempt`”的边界：

- 成功 attempt 写入状态、耗时和 usage trace。
- 失败 attempt 同样写入 usage trace。
- 自动重试和人工重跑创建新的 attempt；旧 attempts 永久保留。
- 图片和任务汇总读取全部历史 attempts，满足“全部计入”的要求。

`TaskRepository.record_attempt()` 现有 `usage` 参数继续作为唯一写入口。首版不需要 Alembic 迁移。

### 5.4 API 聚合层

后端从 attempt traces 计算 Token summary，不允许前端自行汇总。任务详情、历史列表和飞书报告调用同一个聚合函数，避免三个出口产生不同数字。

列表查询需要批量读取 attempts，避免按图片或任务逐条查询造成 N+1。

## 6. `usage_json` 数据契约

每条 `InferenceAttempt.usage_json` 使用带版本号的结构：

```json
{
  "schema_version": 1,
  "protocol": "responses",
  "calls": [
    {
      "sequence": 1,
      "purpose": "inspection",
      "status": "succeeded",
      "usage_available": true,
      "usage_error": null,
      "input_tokens": 712,
      "cached_input_tokens": 420,
      "output_tokens": 108,
      "reasoning_tokens": 0,
      "total_tokens": 820,
      "raw_usage": {
        "input_tokens": 712,
        "input_tokens_details": {"cached_tokens": 420},
        "output_tokens": 108,
        "output_tokens_details": {"reasoning_tokens": 0},
        "total_tokens": 820
      }
    },
    {
      "sequence": 2,
      "purpose": "json_repair",
      "status": "failed",
      "usage_available": false,
      "usage_error": "not_returned",
      "input_tokens": null,
      "cached_input_tokens": null,
      "output_tokens": null,
      "reasoning_tokens": null,
      "total_tokens": null,
      "raw_usage": null
    }
  ],
  "totals": {
    "known_call_count": 1,
    "unknown_call_count": 1,
    "input_tokens": 712,
    "cached_input_tokens": 420,
    "output_tokens": 108,
    "reasoning_tokens": 0,
    "total_tokens": 820
  }
}
```

`uncached_input_tokens` 不落库，由 `input_tokens - cached_input_tokens` 派生。`raw_usage` 只保存 usage 对象，不保存完整模型响应、提示词、图片 Base64 或鉴权信息。

## 7. 校验与异常语义

一个 usage 只有同时满足以下条件才进入已知总量：

1. 所需字段存在且能解析为整数。
2. 所有 Token 值大于或等于零。
3. `cached_input_tokens <= input_tokens`。
4. `reasoning_tokens <= output_tokens`。
5. `total_tokens == input_tokens + output_tokens`。

不满足时保留 `raw_usage`，将该调用标记为 `usage_available=false` 和 `usage_error=invalid_usage`。Token 遥测异常不能使原本成功的巡检结果失败。

SDK 抛错且没有可用 Response 时，调用记录为 `usage_error=not_returned`。这类调用可能在服务端产生了用量，也可能在模型执行前被拒绝；本地不能可靠判断，因此只显示未知。

## 8. 汇总状态

API summary 包含：

```text
input_tokens
cached_input_tokens
uncached_input_tokens
output_tokens
reasoning_tokens
total_tokens
known_call_count
unknown_call_count
availability
```

`availability` 枚举：

- `not_started`：尚无模型调用。
- `complete`：所有新格式调用都有有效 usage。
- `partial`：既有已知用量，也有未知调用。
- `unavailable`：存在调用，但全部 usage 未知。
- `untracked`：历史 attempt 的 `usage_json` 为空，说明当时尚未采集。

图片 summary 汇总该图片的全部 attempts；任务 summary 汇总任务内全部图片。`partial` 状态下界面显示“已知总 Token”，并同时显示未知调用数量；`unavailable` 和 `untracked` 不显示伪造的零总量。

## 9. API 合同

后端新增两个公开结构：

- `TokenUsageSummary`：上述汇总字段与 availability。
- `TokenUsageCall`：图片 ID、attempt 编号、调用 sequence、purpose、protocol、status、usage_error 和可空的 Token 字段。

现有响应扩展如下：

- `TaskSummary.token_usage`：供任务头部和历史记录使用。
- `TaskImageRead.token_usage`：供单图摘要使用。
- `TaskImageRead.token_calls`：供单图折叠明细使用。

`raw_usage` 不返回前端；它只用于本地追溯与未来兼容。

## 10. 前端展示

采用已确认的“摘要 + 渐进展开”布局。

### 10.1 任务页

现有图片结果统计下方增加独立 Token 区域：

- 总 Token；若存在未知调用则改为“已知总 Token”。
- 输入 Token。
- 缓存命中 Token 和占输入比例。
- 未缓存输入 Token。
- 输出 Token。
- 推理 Token，并明确标注它属于输出。
- 已知与未知调用数量。

任务未开始时显示“尚无调用”；老任务显示“未采集”。

### 10.2 单图详情

在巡检结果下方加入可折叠 Token 卡片：

- 默认显示本图汇总。
- 展开后按 attempt 编号和调用 sequence 排列。
- 每行显示用途、状态、输入、缓存、输出、推理和总 Token。
- usage 缺失的行显示“方舟未返回 usage”，不展示零。

缩略图网格不增加 Token 数字，避免在主要的结果筛选区域制造视觉噪声。

### 10.3 历史记录

历史任务行显示任务总 Token 或相应的 `已知`、`未采集`、`不可用`状态。完整逐次明细仍进入任务详情查看。

## 11. 飞书报告

报告的任务信息与巡检统计之后增加“模型 Token 用量”章节，包含：

- 总 Token 或已知总 Token。
- 输入、缓存命中、未缓存输入。
- 输出及其中推理 Token。
- 已知调用数和未知调用数。

报告直接使用与任务 API 相同的 summary，不单独实现一套统计逻辑。若存在未知调用，报告附注“总量不包含未返回 usage 的调用”。历史任务无用量数据时写“该任务运行时尚未采集 Token 用量”。

## 12. 向后兼容与安全

- 现有 `usage_json` 可为空，不需要回填或数据库迁移。
- 老任务的空值解释为 `untracked`，不是零。
- 新版读取器按 `schema_version` 解析；未知未来版本返回不可用状态并保留原始数据。
- 只持久化数字、协议、调用用途和 usage 原始对象，不保存提示词、模型正文、图片数据或密钥。
- 日志中只记录 usage 解析错误类型，不输出完整第三方响应。

## 13. 测试策略

### 13.1 用量领域单元测试

- 使用官方 Responses 示例验证输入、输出、总量、缓存和推理字段。
- 验证 Chat 字段映射到同一内部结构。
- 验证缓存和推理是子集且不会重复计入总量。
- 验证缺字段、负数、缓存大于输入、推理大于输出和总量不一致。
- 验证 `complete`、`partial`、`unavailable`、`untracked` 和 `not_started`。

### 13.2 Ark 与 Runner 测试

- 一次正常检查产生一条调用明细。
- JSON 修复保存初始检查和修复两条 usage。
- Schema 降级保存失败的原生 Schema 调用和成功的 fallback 调用。
- 429/5xx 自动重试跨多个 attempts 累加。
- 人工重跑保留旧 attempts 并全部计入。
- SDK usage 为 `None` 时不影响巡检结果，并产生未知调用。
- 适配器在后续调用失败时仍将此前收集的 usage 交给 Runner 持久化。

### 13.3 API、前端与报告测试

- Task API 的图片汇总与任务汇总等于 attempt traces 的确定性求和结果。
- 前端完整、部分、不可用、未采集和未开始状态文案正确。
- 单图折叠表显示所有 attempts 和调用用途。
- 缓存命中比例以缓存除以输入计算，输入为零时不显示错误比例。
- 飞书报告 Token 汇总与 Task API 完全一致。
- 端到端固定夹具覆盖一张正常图、一张 JSON 修复图和一张 usage 未知失败图。

## 14. 验收标准

1. Responses 和 Chat 两种协议都能记录并展示标准化用量。
2. 每张图的全部自动重试、人工重跑、Schema 降级和 JSON 修复调用都出现在明细中。
3. 任务总量等于所有有效调用总量之和，且缓存与推理 Token 不被重复相加。
4. API 未返回或返回无效 usage 时，巡检仍按原逻辑完成或失败，用量明确显示未知。
5. 有未知调用时不把已知合计描述为完整总量。
6. 旧任务显示未采集，而不是零。
7. 历史记录、任务详情与飞书报告的任务级数字一致。
8. 原始 usage 可在 SQLite 中追溯，但模型内容、图片和密钥不因本功能新增落库或日志暴露。
