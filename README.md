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

## Web 控制台规划

本项目计划增加本地 Web 控制台，使用 React + Vite 前端和 FastAPI 后端，支持网页上传/路径载入、ASR 任务进度展示、LADA 去码任务进度展示，以及手动 DeepSeek 翻译。详细阶段、审查点和验证清单见 [docs/web-console-roadmap.md](docs/web-console-roadmap.md)。

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
| `WEB_UPLOAD_DIR` | `./uploads` | 浏览器上传文件目录 |
| `WEB_JOB_DIR` | `./jobs` | 任务历史和运行态目录 |
| `WEB_ARTIFACT_DIR` | `./output/web` | Web 任务通用产物目录 |
| `LADA_CLI_PATH` | `D:\lada\lada-cli.exe` | LADA CLI 可执行文件路径 |
| `DEEPSEEK_API_KEY_ENV` | `DEEPSEEK_API_KEY` | DeepSeek API key 所在环境变量名 |
| `DEEPSEEK_MODEL` | `deepseek-v4-flash` | DeepSeek 翻译模型 |

### 阶段 1 后端运行时

阶段 1 已建立 FastAPI 后端骨架和通用任务运行时，可用 mock job 验证任务状态、SSE 事件、取消和 artifact 访问。

```powershell
uvicorn web_app.main:app --host 127.0.0.1 --port 7860
```

当前可用接口：

- `GET /api/health`
- `GET /api/config/defaults`
- `GET /api/jobs`
- `GET /api/jobs/{job_id}`
- `POST /api/jobs/{job_id}/cancel`
- `GET /api/jobs/{job_id}/events`
- `GET /api/artifacts/{job_id}/{artifact_name}`
- `POST /api/jobs/mock`

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
    --batch-size 1 \
    --max-tokens 2048 \
    --segment-duration 60 \
    --max-segment 90 \
    --cache-dir ./cache \
    --language Chinese \
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
| `--language` | 自动检测 | 指定语言（如 `Chinese`、`English`） |
| `--backend` | `auto` | 推理后端：`auto`、`transformers`、`vllm`；Windows `auto` 使用 transformers，WSL/Linux `auto` 使用 vLLM |
| `--device-map` | `cuda:0` | transformers 后端和 ForcedAligner 的设备映射 |
| `--dtype` | `bfloat16` | transformers 后端和 ForcedAligner 的 dtype，可选 `bfloat16`、`float16`、`float32` |
| `--gpu-mem` | `0.5` | vLLM 后端 GPU 显存利用率；transformers 后端不使用 |
| `--batch-size` | `1` | 最大推理批大小；12GB Windows transformers 后端建议保持 1 |
| `--max-tokens` | `2048` | 最大生成 token 数 |
| `--segment-duration` | `60` | VAD 目标切片长度（秒） |
| `--max-segment` | `90` | VAD 切片上限（秒） |
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
    save_srt: bool = True
    backend: str = "auto"
    device_map: str = "cuda:0"
    dtype: str = "bfloat16"
    gpu_memory_utilization: float = 0.5   # 仅 vLLM 后端使用
    max_inference_batch_size: int = 1     # 12GB Windows transformers 后端推荐 1
    max_new_tokens: int = 2048
    segment_duration: int = 60            # VAD 目标切片长度
    max_segment_duration: int = 90        # VAD 切片上限
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
