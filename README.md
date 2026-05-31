# Qwen3-ASR 长视频转录工具

基于 [Qwen3-ASR-1.7B](https://github.com/QwenLM/Qwen3-ASR) + [Qwen3-ForcedAligner-0.6B](https://huggingface.co/Qwen/Qwen3-ForcedAligner-0.6B) 的本地长视频/音频 ASR 转录工具。

## 功能

- 支持任意视频/音频格式（通过 ffmpeg）
- Silero-VAD 智能切片，在静音处切分，避免切断语句
- Windows 原生环境默认使用 transformers 后端；WSL/Linux/Docker 可切换 vLLM 后端最大化吞吐
- 音频提取和 VAD 切片支持本地缓存，重复转录同一视频时可跳过 NAS 读取和 VAD
- 输出纯文本 `.txt` + 默认 SRT 字幕 `.srt`
- SRT 优先使用 ForcedAligner 词/字级时间戳，失败时回退到 VAD 段落级时间
- ASR 主模型和 ForcedAligner 模型均支持 HuggingFace ID / 本地路径 / Windows UNC 路径

## Web 控制台

本项目的本地 Web 控制台使用 React + Vite 前端和 FastAPI 后端，当前已支持本机/UNC 路径载入、启动预热、ASR 任务进度展示、LADA 去码任务进度展示、DeepSeek SRT 字幕翻译、任务历史和产物下载。详细阶段、审查点和验证清单见 [docs/web-console-roadmap.md](docs/web-console-roadmap.md)。

### Web 快速启动

```powershell
pip install -r requirements-web.txt
npm --prefix frontend install
if (-not (Test-Path .env)) { Copy-Item .env.example .env }
# 编辑 .env，填入 API_KEY；不要提交 .env
.\scripts\start-web.ps1
# 修改配置或代码后重启后端/前端
.\scripts\restart-web.ps1
```

默认后端监听 `http://127.0.0.1:7860`，前端监听 `http://127.0.0.1:5173`。后端启动时会静默加载项目根目录 `.env`，不会打印 API key；已有进程环境变量优先于 `.env`。本工具默认只适合本机使用，不建议把 `WEB_HOST` 改成 `0.0.0.0`。局域网访问会暴露本机路径、上传文件和任务执行能力，需自行承担风险。

Web 页面以路径输入为主，适合服务端和浏览器都在本机运行的场景；ASR/LADA 输入可填写 `D:\media\video.mp4` 或 `\\NAS\media\video.mp4`。标准浏览器不会向网页暴露真实绝对路径，所以文件选择控件不能可靠替代本机/UNC 路径输入。

`start-web.ps1` 会在后端窗口中激活 conda 环境，默认环境名是 `qwen3-asr`。`restart-web.ps1` 会先停止占用后端/前端端口的监听进程，再用相同参数调用 `start-web.ps1`。如需覆盖：

```powershell
.\scripts\start-web.ps1 -CondaEnv qwen3-asr
.\scripts\start-web.ps1 -CondaHook "$HOME\miniconda3\shell\condabin\conda-hook.ps1"
.\scripts\restart-web.ps1 -CondaEnv qwen3-asr
.\scripts\restart-web.ps1 -ShutdownTimeoutSeconds 60
```

### 阶段 0 基线

阶段 0 已建立 Web 配置和前端空壳。后端服务入口、任务队列和真实任务 API 会在后续阶段接入。

```powershell
# Web 后端依赖
pip install -r requirements-web.txt

# 前端依赖和构建检查
npm --prefix frontend install
npm --prefix frontend run build
```

Web 运行配置使用单独的 `WebSettings`，不会改变 CLI 的 ASR `Config` 默认行为。常用环境变量：

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `WEB_HOST` | `127.0.0.1` | 后续 FastAPI 服务监听地址 |
| `WEB_PORT` | `7860` | 后续 FastAPI 服务端口 |
| `WEB_CORS_ORIGINS` | `http://127.0.0.1:5173,http://localhost:5173` | 允许访问后端的前端 Origin；`start-web.ps1` 会按 `-FrontendPort` 自动设置 |
| `WEB_WARMUP_ON_STARTUP` | `true` | 后端启动后预热 VAD/ASR，前端等待预热完成后显示任务页 |
| `WEB_WARMUP_VAD` | `true` | 启动时加载 Silero VAD |
| `WEB_WARMUP_ASR` | `true` | 启动时加载 ASR/ForcedAligner，并在任务中复用模型 |
| `WEB_PROCESS_PRIORITY` | `AboveNormal` | Windows 下后端 Python 进程和 LADA 子进程优先级；可设 `Idle` / `BelowNormal` / `Normal` / `AboveNormal` / `High` |
| `WEB_UPLOAD_DIR` | `./uploads` | 可选浏览器上传接口的保存目录；主界面默认使用本机/UNC 路径 |
| `WEB_JOB_DIR` | `./jobs` | 任务历史和运行态目录 |
| `WEB_ARTIFACT_DIR` | `./output/web` | Web 任务通用产物目录 |
| `WEB_ASR_CACHE_DIR` | `./cache` | Web ASR 预处理缓存目录；成功任务会清理当前输入缓存，失败/取消保留以便重试 |
| `LADA_CLI_PATH` | `D:\lada\lada-cli.exe` | LADA CLI 可执行文件路径 |
| `LADA_OUTPUT_DIR` | `./output/lada` | LADA 无法写入输入文件同目录时使用的备用输出根目录 |
| `LADA_ENCODING_PRESET` | 空 | LADA `--encoding-preset`；为空时使用 CLI 默认值 |
| `LADA_DEVICE` | 空 | LADA `--device`；为空时使用 CLI 默认值 |
| `LADA_FP16` | 空 | LADA `--fp16/--no-fp16`；为空时使用 CLI 默认值 |
| `LADA_MAX_CLIP_LENGTH` | 空 | LADA `--max-clip-length`；为空时使用 CLI 默认值 |
| `DEEPSEEK_API_KEY_ENV` | `DEEPSEEK_API_KEY` | DeepSeek API key 所在环境变量名 |
| `DEEPSEEK_MODEL` | `deepseek-v4-flash` | DeepSeek 翻译模型 |
| `MODEL` | 空 | DeepSeek 模型别名；未设置 `DEEPSEEK_MODEL` 时使用，例如 `deepseek-v4-pro` |
| `THINK_LEVEL` | 空 | DeepSeek 思考强度别名；未设置 `DEEPSEEK_REASONING_EFFORT` 时使用，例如 `max` |
| `API_KEY` | 空 | DeepSeek API key 兼容别名；不会写入日志或任务历史 |
| `DEEPSEEK_REASONING_EFFORT` | `high` | DeepSeek `reasoning_effort`，支持 `high` / `max` |
| `DEEPSEEK_MAX_TOKENS` | `384000` | DeepSeek v4 单次 completion 输出上限；校验上限为 384K tokens |
| `DEEPSEEK_CHUNK_CHARS` | `200000` | SRT 翻译分块字符预算；校验上限按 v4 1M context 设置 |
| `DEEPSEEK_CONTEXT_CHARS` | `12000` | 多分块翻译时携带的上一批字幕/译文参考字符预算；不回传 `reasoning_content` |
| `DEEPSEEK_MAX_SRT_SIZE_MB` | `20` | 翻译任务允许读取的 SRT 文件或粘贴文本大小上限 |

### 阶段 1 后端运行时

阶段 1 已建立 FastAPI 后端骨架和通用任务运行时，可用 mock job 验证任务状态、SSE 事件、取消和 artifact 访问。

```powershell
uvicorn web_app.main:app --host 127.0.0.1 --port 7860
```

当前可用接口：

- `GET /api/health`
- `GET /api/warmup`
- `GET /api/config/defaults`
- `GET /api/jobs`
- `GET /api/jobs/{job_id}`
- `POST /api/jobs/{job_id}/cancel`
- `GET /api/jobs/{job_id}/events`
- `GET /api/artifacts/{job_id}/{artifact_name}`
- `POST /api/uploads`
- `POST /api/jobs/mock`
- `POST /api/jobs/asr`
- `POST /api/jobs/lada`
- `POST /api/jobs/translate`

### 阶段 4 LADA

LADA 页面会调用 `LADA_CLI_PATH` 指向的 `lada-cli.exe`，并优先把恢复后的视频写入输入视频同目录下的 `<输入文件名>-lada-<job_id>/`。如果该目录无法创建（例如 NAS 共享只读或权限不足），后端会回退到 `LADA_OUTPUT_DIR/<job_id>/`。后端启动子进程时会把工作目录设为 `lada-cli.exe` 所在目录，匹配 LADA 对 `./model_weights` 的默认查找方式。

已按本机 `D:\lada\lada-cli.exe --help` 核对的参数：`--input`、`--output`、`--encoding-preset`、`--device`、`--fp16/--no-fp16`、`--max-clip-length`。本机设备枚举显示 `cpu` 和 `cuda:0`，编码预设可用：`h264-cpu-uhq`、`h264-cpu-fast`、`h264-nvidia-gpu-fast`、`hevc-nvidia-gpu-balanced`、`hevc-nvidia-gpu-hq`、`hevc-nvidia-gpu-uhq`、`av1-cpu-uhq` 等。

```powershell
& "D:\lada\lada-cli.exe" --help
& "D:\lada\lada-cli.exe" --list-devices
& "D:\lada\lada-cli.exe" --list-encoding-presets
```

### 阶段 5 DeepSeek 字幕翻译

翻译页面读取 SRT 字幕并按源 SRT 文件名输出 `<源文件名>.srt`；粘贴文本没有源文件名时回退为 `translated.srt`。输入可以来自历史 ASR 任务的 `subtitle` 产物、本机 SRT 文件路径，或直接粘贴 SRT 内容。后端只翻译字幕正文，重建输出时保留原 SRT 序号和时间轴；失败或取消时会保留已完成分块的 `<源文件名>.partial.srt`。

DeepSeek API 使用 `POST https://api.deepseek.com/chat/completions`，请求体包含 `model`、`messages`、`thinking: {"type":"enabled"}`、`reasoning_effort`、`max_tokens`、`response_format` 和 `stream: true`。后端按 DeepSeek SSE 流式读取响应，并通过任务 SSE 定期刷新翻译日志，避免长分块时页面一直像在死等。DeepSeek v4 按 1M context / 384K 最大输出配置；thinking 模式不会发送 `temperature`、`top_p`、`presence_penalty` 或 `frequency_penalty`。`.env.example` 风格变量可直接映射：

```powershell
$env:API_KEY="<token>"
$env:MODEL="deepseek-v4-pro"
$env:THINK_LEVEL="max"
```

后端运行时会静默读取项目根目录 `.env`，但不会输出 API key，也不会把 API key 写入任务历史、日志或前端配置。

### Web API 和事件模型

任务统一使用 `JobRecord`：`status` 为 `queued`、`running`、`succeeded`、`failed`、`canceled` 或 `interrupted`；`progress` 包含 `percent`、`done`、`total`、`elapsed_seconds`、`eta_seconds`；`artifacts` 只登记任务目录内产物。

主要接口：

| 方法 | 路径 | 说明 |
|------|------|------|
| `GET` | `/api/health` | 后端健康检查 |
| `GET` | `/api/config/defaults` | 返回非敏感默认配置和运行时目录 |
| `POST` | `/api/uploads` | 可选上传接口，返回服务端保存路径 |
| `GET` | `/api/jobs` | 任务历史 |
| `GET` | `/api/jobs/{job_id}` | 任务详情 |
| `POST` | `/api/jobs/{job_id}/cancel` | 请求取消任务 |
| `GET` | `/api/jobs/{job_id}/events` | SSE 事件流：`status`、`progress`、`log`、`artifact`、`error` |
| `GET` | `/api/artifacts/{job_id}/{artifact_name}` | 下载或打开任务产物 |
| `POST` | `/api/jobs/asr` | 创建 ASR 任务 |
| `POST` | `/api/jobs/lada` | 创建 LADA 去码任务 |
| `POST` | `/api/jobs/translate` | 创建 DeepSeek SRT 翻译任务 |

运行时目录 `uploads/`、`jobs/`、`output/`、`cache/`、`models/` 和前端构建目录均已在 `.gitignore` 中排除。

## 前置条件

- Python 3.12+
- NVIDIA GPU（推荐 12GB+ VRAM）
- ffmpeg（系统级安装）

```bash
# macOS
brew install ffmpeg

# Ubuntu/Debian
sudo apt update && sudo apt install ffmpeg
```

## 安装

```bash
# 建议创建独立环境
conda create -n qwenasr python=3.12 -y
conda activate qwenasr

# Windows 原生环境：默认 transformers 后端
pip install -r requirements.txt

# WSL/Linux/Docker CUDA 环境：可选 vLLM 后端
pip install -r requirements-vllm.txt
```

## 快速开始

```bash
python main.py -i video.mp4
```

转录结果保存在 `./output/video.txt`，字幕时间轴保存在 `./output/video.srt`。

Windows 上的视频在 NAS 时，建议给路径加引号：

```powershell
python main.py -i "\\NAS\media\video.mp4"
python main.py -i "Z:\media\video.mp4"
python main.py -i "http://nas.local/media/video.mp4"
```

## 完整参数

```bash
python main.py -i video.mp4 \
    --model Qwen/Qwen3-ASR-1.7B \
    --aligner-model Qwen/Qwen3-ForcedAligner-0.6B \
    --gpu-mem 0.5 \
    --batch-size 4 \
    --max-tokens 1024 \
    --segment-duration 60 \
    --max-segment 120 \
    --cache-dir ./cache \
    --language Japanese \
    --backend auto \
    --device-map cuda:0 \
    --dtype bfloat16 \
    --timestamps \
    --srt-max-chars 42 \
    --srt-max-duration 6.0 \
    --output-dir ./output
```

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `-i / --input` | (必填) | 输入视频/音频文件路径 |
| `-o / --output-dir` | `./output` | 输出目录 |
| `--srt / --no-srt` | 开启 | 是否输出 SRT 字幕文件 |
| `--model` | `Qwen/Qwen3-ASR-1.7B` | ASR 主模型名称或本地路径；若存在 `./models/Qwen3-ASR-1.7B` 会自动优先使用 |
| `--aligner-model` | `Qwen/Qwen3-ForcedAligner-0.6B` | ForcedAligner 模型名称或本地路径；若存在 `./models/Qwen3-ForcedAligner-0.6B` 会自动优先使用 |
| `--timestamps / --no-timestamps` | 开启 | 是否启用 ForcedAligner 词/字级时间戳；关闭后 SRT 使用 VAD 段落级时间 |
| `--language` | `Japanese` | 指定语言（如 `Japanese`、`Chinese`、`English`）；传 `auto` 可自动检测 |
| `--backend` | `auto` | 推理后端：`auto`、`transformers`、`vllm`；Windows `auto` 使用 transformers，WSL/Linux `auto` 使用 vLLM |
| `--device-map` | `cuda:0` | transformers 后端和 ForcedAligner 的设备映射 |
| `--dtype` | `bfloat16` | transformers 后端和 ForcedAligner 的 dtype，可选 `bfloat16`、`float16`、`float32` |
| `--gpu-mem` | `0.5` | vLLM 后端 GPU 显存利用率；transformers 后端不使用 |
| `--batch-size` | `4` | 最大推理批大小；显存不足时可降为 1 |
| `--max-tokens` | `1024` | 最大生成 token 数 |
| `--segment-duration` | `60` | VAD 目标切片长度（秒） |
| `--max-segment` | `120` | VAD 切片上限（秒） |
| `--cache-dir` | `./cache` | 音频/VAD 预处理缓存目录 |
| `--no-cache` | 关闭 | 关闭音频/VAD 预处理缓存 |
| `--refresh-cache` | 关闭 | 忽略并重建当前输入的音频/VAD 缓存 |
| `--srt-max-chars` | `42` | 单条 SRT 字幕最大字符数 |
| `--srt-max-duration` | `6.0` | 单条 SRT 字幕最长秒数 |

## 配置说明

所有参数也可在 `config.py` 中修改默认值：

```python
@dataclass
class Config:
    model: str = "Qwen/Qwen3-ASR-1.7B"
    forced_aligner_model: str | None = "Qwen/Qwen3-ForcedAligner-0.6B"
    return_time_stamps: bool = True
    language: str | None = "Japanese"      # None = 自动检测
    save_srt: bool = True
    backend: str = "auto"
    device_map: str = "cuda:0"
    dtype: str = "bfloat16"
    gpu_memory_utilization: float = 0.5   # 仅 vLLM 后端使用
    max_inference_batch_size: int = 4     # 显存不足时可降为 1
    max_new_tokens: int = 1024
    segment_duration: int = 60            # VAD 目标切片长度
    max_segment_duration: int = 120       # VAD 切片上限
    use_cache: bool = True
    cache_dir: str = "./cache"
    ...
```

### 预处理缓存

默认会把 CPU 端预处理结果写入 `./cache/<输入名>-<hash>/`：

- `audio.npy`：ffmpeg/librosa 提取后的 16kHz mono float32 音频
- `segments.json`：VAD 切片边界
- `metadata.json`：输入路径、文件大小、修改时间、采样率和 VAD 参数

缓存命中条件包含输入文件大小/修改时间和 `--segment-duration`、`--max-segment`。NAS 上的视频重复转录时，命中缓存后会直接跳过 ffmpeg 音频提取和 Silero-VAD。

```powershell
# 强制重建当前输入的缓存
python main.py -i "\\NAS\media\video.mp4" --refresh-cache

# 临时关闭缓存
python main.py -i "\\NAS\media\video.mp4" --no-cache
```

缓存体积约为每小时音频 230 MB，长视频较多时可以定期清理 `./cache`。

### 显存调优建议

| GPU VRAM | `gpu_memory_utilization` | `max_inference_batch_size` |
|----------|--------------------------|---------------------------|
| 12 GB Windows transformers | 不使用 | 1 |
| 12 GB WSL/Linux vLLM | 0.5-0.7 | 16-32 |
| 16 GB | 0.7 | 64 |
| 24 GB | 0.8 | 128 |

`gpu_memory_utilization` 只作用于 vLLM 后端。Windows 原生默认 `--backend auto` 会选择 transformers 后端，因此显存不足时优先调小 `--batch-size`、`--segment-duration`、`--max-segment`，而不是调 `--gpu-mem`。

### 后端选择

Windows 原生环境下，`vllm` 可能可以安装成功，但运行 `from vllm import LLM` 时会因为缺少原生扩展 `vllm._C` 失败。默认 `--backend auto` 会在 Windows 上自动使用 transformers 后端，避免这个问题。

如果你在 WSL/Linux/Docker CUDA 环境中运行，并且已安装 vLLM 依赖，可以显式启用：

```bash
python main.py -i video.mp4 --backend vllm
```

## 模型权重

首次运行时自动从 HuggingFace 下载。如需预下载：

```bash
# HuggingFace
hf download Qwen/Qwen3-ASR-1.7B --local-dir ./models/Qwen3-ASR-1.7B
hf download Qwen/Qwen3-ForcedAligner-0.6B --local-dir ./models/Qwen3-ForcedAligner-0.6B

# ModelScope（国内推荐）
pip install -U modelscope
modelscope download --model Qwen/Qwen3-ASR-1.7B --local_dir ./models/Qwen3-ASR-1.7B
modelscope download --model Qwen/Qwen3-ForcedAligner-0.6B --local_dir ./models/Qwen3-ForcedAligner-0.6B
```

如果你把模型预下载到 `./models/Qwen3-ASR-1.7B` 和 `./models/Qwen3-ForcedAligner-0.6B`，程序会自动优先使用这些目录，直接运行即可：

```bash
python main.py -i video.mp4
```

也可以显式指定本地路径：

```bash
python main.py -i video.mp4 \
    --model ./models/Qwen3-ASR-1.7B \
    --aligner-model ./models/Qwen3-ForcedAligner-0.6B
```

注意：`Qwen3-ForcedAligner-0.6B` 是时间戳对齐模型，不是 ASR 主模型。需要时间轴时应通过 `--aligner-model` 配置它；如果误传给 `--model`，程序会自动把它当作对齐模型，并回退使用默认 ASR 主模型。

## 工作原理

```
Thread 1 (CPU): 预处理缓存命中；否则 ffmpeg 提取音频 → Silero-VAD 切片 → 写入缓存
Thread 2 (GPU): transformers/vLLM 引擎初始化 & 模型加载
                ↓ 两者完成后 ↓
         批量推理所有切片 + ForcedAligner 时间戳 → 输出 TXT / SRT
```

## License

Apache-2.0
