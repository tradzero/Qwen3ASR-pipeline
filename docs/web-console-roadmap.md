# Web 控制台分阶段落地计划

本文把本地 Web 控制台拆成可逐阶段执行、审查和验证的工作包。目标是在不破坏现有 CLI 的前提下，为项目增加一个本地网页端，支持 ASR、LADA 去码和 DeepSeek 翻译三个独立任务。

## 已定决策

- 前端技术栈：React + Vite。
- 后端技术栈：FastAPI + SSE；第一版只需要服务端向浏览器推送任务事件，后续再按需升级 WebSocket。
- 视频载入：支持浏览器上传文件，也支持输入本机路径、盘符路径或 UNC/NAS 路径。
- 任务关系：ASR、LADA、翻译第一版是三个独立任务，不做强制流水线编排。
- 翻译方式：手动触发 DeepSeek 翻译 ASR 文本；prompt 放在 `config.py`；API key 使用环境变量。
- 任务能力：第一版同一时刻只运行一个重任务，支持取消任务和保存任务历史。
- LADA 集成：默认调用 `D:\lada\lada-cli.exe`。
- 安全边界：默认只绑定 `127.0.0.1`，不做多用户认证，不主动扫描媒体库。
- 配置边界：现有 ASR `Config` 继续服务 CLI 和 ASR 参数；Web、LADA、DeepSeek、上传目录和任务目录放入单独的 `WebSettings`，避免把服务端运行配置混进 CLI。

## 非目标

- 不在第一版实现多用户权限、远程公网访问或账号体系。
- 不在第一版实现 GPU 多任务并发调度。
- 不在第一版实现 LADA -> ASR -> 翻译的一键流水线。
- 不在第一版实现翻译版 SRT；先输出翻译文本。
- 不把 DeepSeek API key 写入任务历史、日志、异常堆栈或前端本地存储。

## 推荐目录结构

```text
qwen3asr-pipeline/
├── web_app/
│   ├── __init__.py
│   ├── main.py                # FastAPI app 入口
│   ├── schemas.py             # API 请求/响应模型
│   ├── settings.py            # Web/LADA/DeepSeek 配置装配
│   ├── jobs.py                # JobManager、状态、历史和事件广播
│   └── runners/
│       ├── __init__.py
│       ├── asr.py             # ASR runner，复用现有核心模块
│       ├── lada.py            # LADA subprocess runner
│       └── translate.py       # DeepSeek 翻译 runner
├── frontend/
│   ├── package.json
│   ├── index.html
│   └── src/
│       ├── api/
│       ├── components/
│       ├── pages/
│       └── styles/
├── uploads/                   # 浏览器上传文件，运行时生成
├── jobs/                      # 任务历史和运行态快照，运行时生成
└── docs/web-console-roadmap.md
```

## 任务与事件模型

每个任务统一维护以下状态，前端只消费这个模型，避免 ASR、LADA、翻译各写一套 UI 状态逻辑。

```json
{
  "job_id": "20260523-153000-asr-xxxx",
  "type": "asr | lada | translate",
  "status": "queued | running | succeeded | failed | canceled | interrupted",
  "stage": "cache_check | model_loading | transcribing | exporting | ...",
  "progress": {
    "percent": 0,
    "done": 0,
    "total": 0,
    "elapsed_seconds": 0,
    "eta_seconds": null
  },
  "input": {
    "source_kind": "upload | path",
    "path": "..."
  },
  "artifacts": [
    {"name": "transcript", "kind": "txt", "path": "output/video.txt"}
  ],
  "logs": [],
  "error": null,
  "created_at": "...",
  "updated_at": "..."
}
```

事件建议使用同一格式推送：

```json
{
  "event": "progress | log | artifact | status | error",
  "job_id": "...",
  "stage": "transcribing",
  "message": "[ASR] batch 完成",
  "progress": {"done": 4, "total": 12, "percent": 33.3}
}
```

## API 草案

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/health` | 服务健康检查 |
| GET | `/api/config/defaults` | 返回 ASR、LADA、翻译默认配置 |
| POST | `/api/uploads` | 上传视频或音频，返回服务端路径 |
| GET | `/api/jobs` | 任务历史列表 |
| GET | `/api/jobs/{job_id}` | 任务详情 |
| POST | `/api/jobs/{job_id}/cancel` | 请求取消任务 |
| GET | `/api/artifacts/{job_id}/{artifact_name}` | 下载或预览产物 |
| POST | `/api/jobs/asr` | 创建 ASR 任务 |
| POST | `/api/jobs/lada` | 创建 LADA 去码任务 |
| POST | `/api/jobs/translate` | 创建 DeepSeek 翻译任务 |
| SSE | `/api/jobs/{job_id}/events` | 实时事件流 |

第一版建议先实现 SSE。SSE 对单向任务进度足够，前端实现简单；后续如果要做更复杂的交互，再升级 WebSocket。

产物下载接口只能根据任务历史中登记过的 artifact 返回文件，不能把 URL 中的 `artifact_name` 拼成任意本机路径。输入本机路径只用于创建任务，不提供“任意路径下载”能力。

## 阶段 0：文档、依赖和配置基线

### 目标

建立 Web 控制台的配置、依赖和目录约定，但不改变现有 CLI 行为。

### 范围

- 新增或拆分 Web 后端依赖：`fastapi`、`uvicorn`、`pydantic`、`httpx`、`python-multipart`。
- 新增前端目录骨架，选择 React + Vite。
- 新增 `WebSettings`：上传目录、任务历史目录、任务产物目录、LADA CLI 路径、DeepSeek API base、DeepSeek model、API key env、翻译 prompt 模板。
- 保持现有 ASR `Config` 对 CLI 的兼容性；Web 层按请求把表单参数转换为 `Config`。
- 更新 `.gitignore`：忽略 `.env`、`cache/`、`uploads/`、`jobs/`、前端构建产物和依赖目录。

### 主要文件

- `requirements.txt` 或新增 `requirements-web.txt`
- `config.py`
- `web_app/settings.py`
- `.gitignore`
- `frontend/package.json`
- `README.md`

### 审查重点

- CLI 默认值没有被 Web 配置意外改变。
- `Config` 和 `WebSettings` 职责分离：ASR 参数属于 `Config`，服务运行参数属于 `WebSettings`。
- DeepSeek API key 只通过环境变量读取。
- Windows 路径默认值正确表达为 `D:\lada\lada-cli.exe`。
- 上传目录、任务历史目录和输出目录都在项目内有清晰默认值。
- `.env`、上传文件、缓存、任务历史和前端构建产物不会进入 git。

### 验证

```powershell
python -m compileall .
python -c "from config import Config; print(Config())"
npm --prefix frontend install
npm --prefix frontend run build
```

### 退出标准

- Python 能编译通过。
- 前端空壳能构建。
- README 中有 Web 模式依赖和环境变量说明。
- 运行 `python main.py ...` 的参数和默认行为不变。

## 阶段 1：后端任务运行时

### 目标

先做通用任务基础设施，让后续 ASR、LADA、翻译都接入同一个任务模型。

### 范围

- 新增 `JobManager`，管理任务创建、状态更新、日志、产物、取消请求和历史写入。
- 实现单任务锁：已有任务运行时，新任务返回明确错误，或后续阶段再改为排队。
- 实现 SSE 事件流。
- 实现任务历史 JSON 摘要持久化；完整日志写入每个任务自己的日志文件，内存和历史摘要中只保留最近若干行。
- 服务重启时将未完成任务标记为 `interrupted`。
- 实现取消 token，不在这一阶段接入具体重任务。

### 主要文件

- `web_app/main.py`
- `web_app/jobs.py`
- `web_app/schemas.py`
- `web_app/settings.py`

### 审查重点

- 状态流转是否单向清晰：`queued -> running -> succeeded/failed/canceled`。
- 取消语义是否明确：取消是请求，具体 runner 在安全边界检查并退出。
- 历史文件写入是否原子化，避免服务中断造成 JSON 损坏。
- 日志是否有长度上限，避免长任务撑爆内存或让历史 JSON 过大。
- artifact 下载是否只允许访问任务登记过的产物路径。

### 验证

```powershell
python -m compileall .
uvicorn web_app.main:app --host 127.0.0.1 --port 7860
```

手动检查：

- `GET /api/health` 返回正常。
- `GET /api/jobs` 返回空列表或历史列表。
- 创建一个 mock job 能看到状态变化和 SSE 事件。
- 取消 mock job 后状态为 `canceled`。
- 尝试访问未登记 artifact 时返回 404 或 403。

### 退出标准

- 不依赖 ASR 模型和 LADA，也能启动后端并演示任务状态流。
- 任务历史文件可持久化，并能在重启后读取。

## 阶段 2：ASR Web 适配

### 目标

把现有 CLI ASR 管线接入任务运行时，同时保留 CLI 可用。

### 范围

- 从 `main.py` 抽出可复用的 ASR runner，例如 `run_asr_job(config, reporter, cancel_token)`。
- `transcribe.py` 的 `transcribe_segments` 增加可选 `progress_callback` 和 `cancel_token`。
- CPU 阶段上报：缓存检查、缓存命中/未命中、音频加载、VAD、缓存保存。
- GPU 阶段上报：模型加载开始、模型加载完成、等待另一侧流水线。
- 推理阶段上报：batch 开始、batch 完成、OOM 降级、单段重试、CUDA 峰值。
- 输出阶段上报 TXT/SRT 产物路径。

### 主要文件

- `main.py`
- `transcribe.py`
- `web_app/runners/asr.py`
- `web_app/main.py`
- `web_app/schemas.py`

### 审查重点

- CLI 入口仍只在 `if __name__ == "__main__"` 下执行。
- vLLM/transformers 的 Windows 后端选择逻辑不被破坏。
- 取消不会在写输出文件时留下被当作成功产物的半文件。
- `model.transcribe` 正在执行时无法强杀是已知限制，文档和 UI 需要表达为“将在安全点取消”。

### 验证

```powershell
python -m compileall .
python main.py -i "<短测试视频或音频>"
uvicorn web_app.main:app --host 127.0.0.1 --port 7860
```

手动检查：

- 用 Web API 创建 ASR 任务，进度阶段依次出现。
- 命中预处理缓存时，Web 显示 cache hit。
- ASR 输出 TXT 和 SRT，并在任务详情中作为 artifacts 出现。
- 任务失败时错误信息进入 `error`，后端不会卡在 running。
- CLI 转录仍能生成原来的 TXT/SRT。

### 退出标准

- ASR 可以从网页任务 API 完成一次短视频转录。
- CLI 回归通过。
- 任务取消在 VAD 前后或 batch 间生效。

## 阶段 3：React 前端基础体验

### 目标

完成可操作的本地任务控制台骨架，让 ASR 任务可以从页面启动并查看进度。

### 范围

- React + Vite 项目初始化。
- 实现服务状态、任务 tabs、上传/路径输入、参数表单、任务详情、实时日志、历史列表。
- ASR 面板接入阶段 2 API。
- 上传文件显示浏览器上传进度，并展示服务端保存路径、文件大小和剩余磁盘不足时的错误。
- 前端读取 SSE，断线后能回退轮询任务详情。

### 主要文件

- `frontend/package.json`
- `frontend/src/api/*`
- `frontend/src/pages/AsrPage.*`
- `frontend/src/pages/HistoryPage.*`
- `frontend/src/components/*`

### 审查重点

- 页面第一屏就是实际控制台，不做营销页。
- 表单参数与后端默认配置一致。
- 路径输入和上传输入的状态互斥清晰。
- 任务运行时取消按钮、日志和产物区域状态明确。
- CSS 有稳定尺寸约束，日志区和进度条不会挤压布局。
- 大文件上传失败、后端断开、任务失败三种错误在 UI 上能区分。

### 验证

```powershell
npm --prefix frontend install
npm --prefix frontend run build
npm --prefix frontend run dev
uvicorn web_app.main:app --host 127.0.0.1 --port 7860
```

手动检查：

- 打开 Vite 页面，能看到后端健康状态。
- 上传小文件后返回 server path。
- 从页面启动 ASR，进度和日志实时更新。
- 刷新页面后历史任务仍可见。
- 任务完成后可下载 TXT/SRT。

### 退出标准

- 不使用命令行也能完成一次 ASR 流程。
- 前端生产构建通过。

## 阶段 4：LADA 去码任务

### 目标

把 `D:\lada\lada-cli.exe` 包装成可从网页启动、显示进度和取消的独立任务。

### 范围

- 新增 LADA runner，使用 `subprocess.Popen` 调用 CLI。
- 默认命令：`D:\lada\lada-cli.exe --input <path> --output <output>`。
- 支持可选参数：encoding preset、device、fp16/no-fp16、max clip length。
- 输出目录默认 `output/lada/<job_id>/`。
- 捕获 stdout/stderr，解析 tqdm 文本中的 `Processing video: NN%`。
- 解析不到百分比时，前端显示运行中、elapsed 和最新日志。
- 实现取消：先 terminate，超时后 kill。
- 记录 LADA 进程退出码；只有退出码为 0 且输出文件存在时才登记 artifact。
- 前端新增 LADA 面板和历史产物展示。

### 主要文件

- `web_app/runners/lada.py`
- `web_app/main.py`
- `web_app/schemas.py`
- `frontend/src/pages/LadaPage.*`
- `README.md`

### 审查重点

- 不删除或修改用户输入视频。
- subprocess 参数必须以列表形式传递，避免路径和引号问题。
- 取消后不会把半成品标记为成功产物。
- LADA 可执行文件不存在时，错误提示指向配置项。
- Windows 路径和 UNC 路径在 API 请求、日志、历史中保持可读。
- LADA 输出文件必须位于任务输出目录或显式配置的输出目录下，避免覆盖用户输入文件。

### 验证

```powershell
& "D:\lada\lada-cli.exe" --help
& "D:\lada\lada-cli.exe" --list-devices
python -m compileall .
npm --prefix frontend run build
```

手动检查：

- 从网页创建 LADA 任务。
- 日志能实时显示 LADA 输出。
- 如果输出中包含百分比，进度条同步更新。
- 取消任务后 subprocess 退出，任务状态为 `canceled`。
- 成功后 restored mp4 出现在 artifacts。

### 退出标准

- LADA 可以独立完成一个短视频处理任务。
- 取消和失败路径都有明确状态。

## 阶段 5：DeepSeek 手动翻译

### 目标

支持从 ASR 文本或粘贴文本手动触发 DeepSeek 翻译任务。

### 范围

- 新增 DeepSeek 翻译配置：API base、model、temperature、max tokens、API key env、prompt 模板。
- 新增 translate runner，使用 `httpx` 调用 `POST https://api.deepseek.com/chat/completions`。
- 支持输入来源：ASR artifact 路径、历史 job_id、用户粘贴文本。
- 长文本按段落或字符预算分块，请求完成后合并为 `translated.txt`。
- 前端新增翻译面板：选择历史 ASR 输出、目标语言、prompt 预览/编辑、运行翻译。
- 无 API key 时给出清晰错误，不发起请求。

### 主要文件

- `config.py`
- `web_app/runners/translate.py`
- `web_app/schemas.py`
- `frontend/src/pages/TranslatePage.*`
- `README.md`

### 审查重点

- API key 不进入日志、历史、异常堆栈或前端持久化。
- DeepSeek 请求体符合文档：`model`、`messages` 必填。
- 分块策略不会打乱段落顺序。
- 请求失败时已完成分块不丢失，错误信息可读。
- prompt 模板有默认值，也允许用户在 UI 中临时覆盖。
- 翻译输出文件只写入任务产物目录，不覆盖 ASR 原始文本。

### 验证

```powershell
$env:DEEPSEEK_API_KEY="<token>"
python -m compileall .
npm --prefix frontend run build
```

手动检查：

- 无 API key 时创建翻译任务会失败并提示配置环境变量。
- 使用短文本翻译成功，生成 `translated.txt`。
- 使用 ASR 输出文件作为输入时，任务历史能关联来源。
- 取消翻译任务时，在分块边界停止。

### 退出标准

- 翻译任务可手动运行，并能生成文本产物。
- Secret 处理通过审查。

## 阶段 6：收尾、文档和回归验证

### 目标

把 Web 控制台从功能可用整理到可长期维护。

### 范围

- README 增加完整 Web 模式安装、启动、环境变量、LADA 路径配置和常见问题。
- 增加一键启动脚本或明确的 PowerShell 启动步骤。
- 固化 API 和任务事件模型文档。
- 统一错误展示和日志截断策略。
- 确认输出目录、上传目录、历史目录都在 `.gitignore` 中。
- 根据实际体验优化前端布局和移动端可读性。
- 补充安全说明：本工具默认只适合本机使用；如需局域网访问，必须由用户自行承担路径暴露和任务执行风险。

### 主要文件

- `README.md`
- `.gitignore`
- `docs/web-console-roadmap.md`
- 可选：`scripts/start-web.ps1`

### 审查重点

- 文档中的命令在 Windows PowerShell 下可直接运行。
- 默认 host 是 `127.0.0.1`。
- 运行时生成目录不进入 git。
- CLI、Web ASR、LADA、翻译四条路径都有验证记录。

### 验证

```powershell
python -m compileall .
npm --prefix frontend run build
python main.py -i "<短测试视频或音频>"
uvicorn web_app.main:app --host 127.0.0.1 --port 7860
```

最终手动回归：

- Web 上传文件。
- Web 路径输入。
- ASR 成功、失败、取消。
- LADA 成功、失败、取消。
- DeepSeek 翻译成功、缺少 key、取消。
- 任务历史重启后仍可查看。
- 输出产物可下载或打开。

### 退出标准

- README 足够让新用户启动 Web 控制台。
- 所有阶段的验证项至少跑过一次。
- 已知限制被记录在文档中。

## 阶段总览

| 阶段 | 交付物 | 审查门槛 | 验证门槛 |
|------|--------|----------|----------|
| 0 | 依赖、配置、目录骨架 | CLI 默认行为不变，WebSettings 与 Config 分离 | Python 编译、前端空壳构建 |
| 1 | JobManager、API 骨架、SSE、历史 | 状态、取消和 artifact 访问语义清晰 | mock job 可运行、取消、持久化 |
| 2 | ASR Web runner | CLI 不回归，进度事件完整 | 短视频 Web ASR 成功 |
| 3 | React 控制台 | 参数和状态表达清晰 | 页面可启动 ASR 并展示产物 |
| 4 | LADA runner 和 UI | subprocess 安全、取消可靠 | 短视频 LADA 成功/取消 |
| 5 | DeepSeek 翻译 | secret 不泄漏、分块有序 | 短文本翻译成功 |
| 6 | 文档、脚本、回归 | 可维护、可复现 | 全流程回归通过 |

## 已知风险和处理策略

| 风险 | 影响 | 处理策略 |
|------|------|----------|
| ASR 单次 `model.transcribe` 不能被立即中断 | 取消可能延迟 | 第一版协作式取消；后续如有必要改成独立进程 |
| Windows 原生 vLLM 不稳定 | Web ASR 后端选择可能踩坑 | 保持 `backend=auto` 走 transformers，文档强调 WSL/Linux 再用 vLLM |
| LADA tqdm 输出可能被 stderr/控制字符打散 | 百分比解析不稳定 | 解析不到时展示日志和运行中状态，不阻断任务 |
| LADA 和 ASR 都吃 GPU | 并发会 OOM | 第一版单任务锁；后续再做队列和资源调度 |
| DeepSeek 长文本成本和限速 | 翻译失败或耗时长 | 分块、重试、错误可见；不默认自动翻译 |
| 本机路径通过 Web 暴露 | 安全风险 | 默认只监听 localhost，不做远程访问 |
| 任意 artifact 路径下载 | 可能泄漏本机文件 | 下载接口只允许返回任务登记过的产物 |
| 浏览器上传大视频 | 占用磁盘和内存 | 流式写入、限制大小、上传前检查剩余空间 |

## 后续可选增强

- Pipeline job：把 LADA、ASR、翻译串成可选流水线。
- SQLite 历史：替代 JSON，支持更多查询和大历史量。
- ASR 服务模式：模型常驻显存，减少频繁启动成本。
- 翻译版 SRT：保留原时间轴，输出 bilingual 或 translated SRT。
- LADA 参数探测：从 `--list-devices`、`--list-encoding-presets` 动态生成表单选项。