# Qwen3-ASR Pipeline Agent Guide

本文件面向接手项目的 agent，用来快速理解当前实现、模块边界和验证入口。面向用户的安装、启动和参数说明放在 `README.md`；Web 分阶段历史和更细的路线记录放在 `docs/web-console-roadmap.md`。

## 当前项目状态

这是一个本地长视频/音频处理工具，核心能力已经从单纯 ASR 扩展为本地 Web 任务控制台：

- CLI：`main.py` 串起音频提取、VAD 切片、ASR 推理、TXT/SRT 输出。
- Web：`web_app/main.py` 提供 FastAPI API，`frontend/` 提供 React + Vite 控制台。
- ASR：Qwen3-ASR-1.7B，Windows `auto` 后端默认走 transformers，非 Windows `auto` 默认走 vLLM。
- 时间轴：默认启用 Qwen3-ForcedAligner-0.6B 生成更细 SRT；失败或关闭时回退到 VAD 段落时间。
- 翻译：DeepSeek chat completions，后端使用 `stream: true` 读取 DeepSeek SSE，并通过任务 SSE 推送日志/进度。
- LADA：Web 端通过外部 `lada-cli.exe` 子进程执行去码任务。

默认运行配置以 12GB 显存 Windows 本机为主要调优对象：`batch=4`、`max_new_tokens=1024`、`segment_duration=60`、`max_segment_duration=120`、默认识别语言为 `Japanese`。

项目使用 conda 管理 Python 环境；运行测试、脚本或后端前，先确认并使用当前项目对应的 conda 环境。

## 技术选型

| 领域 | 当前实现 | 主要文件 |
|------|----------|----------|
| 音频读取 | 视频/远程输入优先 ffmpeg pipe 输出 16kHz mono s16le；普通音频先试 librosa，失败再回退 ffmpeg | `audio.py` |
| VAD 切片 | Silero VAD，primary + loose 双阈值；优先静音边界，必要时用低能量点或均匀切分兜底 | `vad.py` |
| 预处理缓存 | 缓存 audio.npy、segments.json、metadata.json；缓存键包含输入 stat、采样率和 VAD 参数版本 | `cache.py` |
| ASR 推理 | `qwen_asr.Qwen3ASRModel`，支持 transformers/vLLM；模型对象进程内缓存；OOM 时 batch 自动降为单段重试 | `transcribe.py` |
| SRT/TXT 输出 | 清理 ASR 重复文本；SRT 优先 ForcedAligner 时间戳，回退 VAD 时间 | `output.py` |
| Web API | FastAPI、任务队列、上传、artifact 下载、任务 SSE | `web_app/main.py`, `web_app/jobs.py` |
| Web 前端 | React + Vite 单页控制台，ASR/LADA/翻译/历史页 | `frontend/src/` |
| DeepSeek 翻译 | SRT 分块、上下文携带、流式读取 DeepSeek SSE、失败/取消保留 partial SRT | `web_app/runners/translate.py` |
| LADA | 调用外部 CLI，输出目录优先输入文件同目录，失败回退 Web artifact 目录 | `web_app/runners/lada.py`, `web_app/lada_paths.py` |

## 目录和模块地图

```text
.
├── main.py                 # CLI 入口；也暴露 run_asr_job() 给 Web ASR runner 复用
├── config.py               # Config + WebSettings；不要把服务运行参数混进 Config
├── audio.py                # 16kHz mono float32 音频读取
├── vad.py                  # Silero VAD + 切点逻辑
├── cache.py                # 音频/VAD 预处理缓存
├── transcribe.py           # 模型选择、加载缓存、批量推理
├── output.py               # TXT/SRT 聚合和 ASR 文本清理
├── web_app/
│   ├── main.py             # FastAPI routes
│   ├── jobs.py             # JobManager、状态持久化、SSE 发布
│   ├── schemas.py          # Pydantic 请求/响应模型
│   ├── settings.py         # .env + 环境变量装配 WebSettings
│   ├── warmup.py           # Web 启动预热 VAD/ASR
│   └── runners/            # ASR、LADA、DeepSeek 翻译任务 runner
├── frontend/src/
│   ├── App.jsx             # 顶层路由、任务恢复、任务 SSE 订阅
│   ├── api/client.js       # 后端 API 和 EventSource 封装
│   ├── pages/              # ASR、LADA、翻译、历史页
│   └── components/         # Panel、JobDetail 等通用组件
├── tests/                  # 单元测试，覆盖音频、VAD、输出、翻译、缓存清理
├── scripts/                # Windows Web 启动/重启脚本
└── docs/                   # Web 控制台路线和阶段记录
```

运行时目录包括 `cache/`、`uploads/`、`jobs/`、`output/`、`models/`、`frontend/dist/`，这些目录不应作为源码改动提交。

## 关键数据流

CLI ASR：

```text
input media
  -> audio.load_audio()
  -> cache.load/save_preprocess_cache()
  -> vad.process_vad()
  -> transcribe.init_model()
  -> transcribe.transcribe_segments()
  -> output.save_txt()/save_srt()
```

Web 任务：

```text
frontend create job
  -> FastAPI /api/jobs/<type>
  -> JobManager.create_job()
  -> runner runs in asyncio task
  -> JobReporter emits status/progress/log/artifact
  -> /api/jobs/{job_id}/events streams SSE
  -> frontend refreshes active JobRecord
```

Web 当前同一时间只允许一个 active job。任务历史保存在 `jobs/history.json`；服务重启时未完成任务会标记为 `interrupted`。

## 配置边界

- `Config` 只描述 ASR 任务参数，CLI 和 Web ASR 请求都会转成它。
- `WebSettings` 描述 Web 服务、运行时目录、预热、LADA、DeepSeek 等服务级参数。
- `web_app/settings.py` 会静默读取项目根目录 `.env`，已有进程环境变量优先。
- DeepSeek API key 通过 `DEEPSEEK_API_KEY` 或 `API_KEY` 读取，不要写入日志、任务历史或前端状态。
- Web 普通 ASR 请求不能随意覆盖 `device_map`、`dtype`、缓存根目录等机器策略；这些由 `WebSettings` 控制。

## 开发注意事项

- 修改 ASR 默认值时，同步检查 `config.py`、`main.py` CLI help、`frontend/src/pages/AsrPage.jsx` fallback、`README.md`。
- 修改 VAD 逻辑时，如果缓存语义变了，需要更新 `cache.py` 中的 `VAD_CACHE_VERSION`。
- 修改输出清理时，同时验证 TXT 和 SRT；`clean_asr_text()` 当前用于两者。
- 修改 Web runner 时，优先通过 `JobReporter` 发状态、进度、日志和产物，不要绕过 `JobManager`。
- 修改 artifact 路径规则时，注意 `JobManager._validate_artifact_path()` 的允许根目录，避免暴露任意本机文件。
- 修改 DeepSeek 翻译时，保留 SRT 分段标签契约：模型只翻译 `<SEG n>` 内容，后端负责重建 SRT 时间轴。
- Windows 原生环境下 vLLM 通常不可用，`backend=auto` 会选 transformers；不要假设本机能跑 vLLM。

## 常用验证

后端/核心单测：

```powershell
& C:\Users\Zero\miniconda3\envs\qwen3-asr\python.exe -m unittest discover -s tests
```

基础编译检查：

```powershell
python -m py_compile config.py main.py web_app\runners\translate.py tests\test_translate.py
```

前端构建：

```powershell
npm --prefix frontend run build
```

Git 空白检查：

```powershell
git -c safe.directory=C:/Users/Zero/Qwen3ASR-pipeline diff --check
```

涉及 Web UI 的可视化或交互改动，启动后端/前端并用浏览器实际确认。若只改文档，通常不需要跑完整测试。
