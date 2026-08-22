# Seed VLM 巡检台

这是一个本机单用户工具：将 1–100 张图片按“一图一请求”发送给火山方舟 Seed 视觉模型，保存结构化结果和可见证据，并以用户身份生成包含图片的飞书文档。

## 功能

- 在界面配置火山方舟 Base URL、Responses / Chat Completions 协议、模型或 Endpoint ID 和 API Key。
- 编辑带版本的巡检模板，使用单张图片先验证提示词。
- 批量上传、暂停、继续、取消与单图重试；服务重启后不会错配图片和结果。
- 同屏查看批量画廊、单图大图、判定、置信度与逐条视觉证据。
- 通过飞书 OAuth 生成图文 Docx；失败的导出可续传，不会重复创建文档。

## 运行要求

- macOS（需要可用的系统钥匙串）
- Python 3.11 或更高版本
- Node.js 18 或更高版本
- 可访问火山方舟和飞书开放平台的网络

## 首次安装

```bash
python3 -m venv backend/.venv
backend/.venv/bin/python -m pip install --upgrade pip
backend/.venv/bin/python -m pip install -e 'backend[dev]'
npm --prefix frontend install
cp .env.example .env
```

如需运行端到端测试，再安装 Chromium：

```bash
npm --prefix frontend exec playwright install chromium
```

## 启动

开发模式同时启动 FastAPI 和 Vite：

```bash
./scripts/dev.sh
```

本地一体化运行会构建前端、执行数据库迁移，然后仅监听 `127.0.0.1:8000`：

```bash
./scripts/start.sh
```

默认地址是 [http://127.0.0.1:8000](http://127.0.0.1:8000)。不要将该服务监听到公网网卡；它是本地单用户工具，不包含多用户认证。

## 配置火山方舟

1. 在火山方舟控制台创建或选择支持图像输入的 Seed 模型 Endpoint。
2. 打开巡检台的“设置”，填写：
   - Base URL：默认 `https://ark.cn-beijing.volces.com/api/v3`；
   - 协议：优先选择 `Responses API`，仅在 Endpoint 要求时改用 `Chat Completions`；
   - 模型 / Endpoint ID：填控制台中可调用的 ID；
   - API Key：仅在此输入，不要写入 `.env`。
3. 选择并发数、超时和重试次数，点击“测试并保存”。界面显示“连接测试通过”后再创建任务。

API Key 只保存在操作系统钥匙串中。前端、SQLite、日志和 API 响应只能看到“是否已配置”。

## 配置飞书报告

1. 在飞书开放平台创建企业自建应用，记录 App ID 和 App Secret。
2. 在安全设置中添加 OAuth 回调地址：

   ```text
   http://127.0.0.1:8000/api/auth/feishu/callback
   ```

3. 开通用户 OAuth 所需的 `offline_access`，以及云文档创建/编辑、云空间文件访问和图片上传权限（对应 `docx:document` 与 `drive:drive` 范围），发布应用版本。
4. 如需将报告放入指定文件夹，先向应用/当前用户授予该文件夹的编辑权限，并在设置页填入文件夹 Token。
5. 在“设置”填入 App ID、App Secret 和文件夹 Token，点击“连接飞书”，在新窗口完成用户授权。

App Secret、access token 和 refresh token 都只保存在系统钥匙串。

## 巡检原则与任务恢复

模型只能根据图片中清晰可见的事实判定。未入镜、遮挡、分辨率不足或无法确认的项目必须标记为“未入镜/无法判断”，不能把没看到当成违规。这个边界用于减少误判，结果仍需人工复核。

- “暂停”会停止领取新图；已发出的请求可以继续到终态。
- 应用异常退出后，原“运行中”任务会在下次启动时恢复为“已暂停”，中断的图片重新排队；由用户点击“继续任务”。
- 单张图片只有在“执行失败”时才能重试，新结果会替代当前结果并保留尝试记录。
- 全部图片成功后会自动生成飞书报告。存在执行失败项时，任务停在“等待确认”，只有用户点击“带失败项生成报告”后才会导出。
- 报告中断后会保留已创建的文档 ID 和操作游标；重试会在同一文档续传。

## 本地数据与删除

默认数据目录是 `~/.vlm-inspector`：

- `app.db`：任务、提示词版本、结果和报告进度；
- `images/`：原图、模型推理副本和报告缩略图；
- `logs/`：本地运行日志（不应包含密钥）。

当前 MVP 没有任务删除按钮。退出程序后删除整个数据目录会清空本地任务和图片，但不会清除系统钥匙串。“断开飞书”会删除 OAuth token；若要完全清理，还需在 macOS“钥匙串访问”中删除本应用的火山方舟和飞书凭据。

## 故障排查

- 方舟 `401/403`：检查 API Key、Endpoint ID、模型开通状态和账号权限；修正后重试失败图片。
- 方舟 `429`：应用会按退避策略重试；持续限流时请降低并发数或联系平台提升配额。
- “不支持的协议”或返回结构不符：在设置中切换 Responses / Chat Completions，并先运行单图测试。
- “系统钥匙串不可用”：确认当前 macOS 会话已解锁钥匙串，且运行程序的用户有权读写。
- 飞书要求重新授权：回到“设置”重新点击“连接飞书”。
- 飞书权限错误：检查应用版本已发布、Docx / Drive 权限已开通，以及目标文件夹对当前用户可编辑。
- 飞书图片/媒体限制：确认文件大小、当日配额和上传频率；修复后从历史页重试报告。

## 验证与手工烟测

```bash
backend/.venv/bin/ruff check backend/app backend/tests
backend/.venv/bin/mypy backend/app
backend/.venv/bin/pytest backend/tests -v
npm --prefix frontend test
npm --prefix frontend run build
npm --prefix frontend run e2e
```

手工烟测建议：

1. 复制 3–10 张本地样图到临时目录，不要把用户原图加入 Git。
2. 在“设置”通过方舟连接测试，在“提示词模板”先跑一张图。
3. 新建批量任务，确认画廊和单图证据同时可见，状态过滤与计数一致。
4. 运行中暂停并重启应用，确认任务保持已暂停，继续后每张图只对应自己的结果。
5. 打开飞书报告，核对总数、四类计数、缩略图和每张图的证据。
