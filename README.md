# Qwen3-ASR 长视频转录工具

基于 [Qwen3-ASR-1.7B](https://github.com/QwenLM/Qwen3-ASR) + vLLM backend 的本地长视频/音频 ASR 转录工具。

## 功能

- 支持任意视频/音频格式（通过 ffmpeg）
- Silero-VAD 智能切片，在静音处切分，避免切断语句
- vLLM batch 推理，最大化单卡吞吐
- 输出纯文本 `.txt` + 可选 SRT 字幕 `.srt`
- 模型权重自动下载 / 预下载目录自动复用

## Web 控制台规划

本项目计划增加本地 Web 控制台，使用 React + Vite 前端和 FastAPI 后端，支持网页上传/路径载入、ASR 任务进度展示、LADA 去码任务进度展示，以及手动 DeepSeek 翻译。详细阶段、审查点和验证清单见 [docs/web-console-roadmap.md](docs/web-console-roadmap.md)。

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

# 安装依赖
pip install -r requirements.txt
```

## 快速开始

```bash
python main.py -i video.mp4
```

转录结果保存在 `./output/video.txt`。

## 完整参数

```bash
python main.py -i video.mp4 \
    --model Qwen/Qwen3-ASR-1.7B \
    --gpu-mem 0.7 \
    --batch-size 32 \
    --max-tokens 4096 \
    --segment-duration 120 \
    --max-segment 180 \
    --language Chinese \
    --srt \
    --output-dir ./output
```

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `-i / --input` | (必填) | 输入视频/音频文件路径 |
| `-o / --output-dir` | `./output` | 输出目录 |
| `--srt` | 关闭 | 同时输出 SRT 字幕文件 |
| `--model` | `Qwen/Qwen3-ASR-1.7B` | ASR 模型名称或本地路径；若存在 `./models/Qwen3-ASR-1.7B` 会自动优先使用 |
| `--language` | 自动检测 | 指定语言（如 `Chinese`、`English`） |
| `--gpu-mem` | `0.7` | GPU 显存利用率 |
| `--batch-size` | `32` | 最大推理批大小 |
| `--max-tokens` | `4096` | 最大生成 token 数 |
| `--segment-duration` | `120` | VAD 目标切片长度（秒） |
| `--max-segment` | `180` | VAD 切片上限（秒） |

## 配置说明

所有参数也可在 `config.py` 中修改默认值：

```python
@dataclass
class Config:
    model: str = "Qwen/Qwen3-ASR-1.7B"
    gpu_memory_utilization: float = 0.7   # 12GB VRAM 推荐 0.7
    max_inference_batch_size: int = 32    # 12GB VRAM 推荐 32
    segment_duration: int = 120           # VAD 目标切片长度
    max_segment_duration: int = 180       # VAD 切片上限
    ...
```

### 显存调优建议

| GPU VRAM | `gpu_memory_utilization` | `max_inference_batch_size` |
|----------|--------------------------|---------------------------|
| 12 GB | 0.7 | 32 |
| 16 GB | 0.7 | 64 |
| 24 GB | 0.8 | 128 |

## 模型权重

首次运行时自动从 HuggingFace 下载。如需预下载：

```bash
# HuggingFace
hf download Qwen/Qwen3-ASR-1.7B --local-dir ./models/Qwen3-ASR-1.7B

# ModelScope（国内推荐）
pip install -U modelscope
modelscope download --model Qwen/Qwen3-ASR-1.7B --local_dir ./models/Qwen3-ASR-1.7B
```

如果你把模型预下载到 `./models/Qwen3-ASR-1.7B`，程序会自动优先使用该目录，直接运行即可：

```bash
python main.py -i video.mp4
```

也可以显式指定本地路径：

```bash
python main.py -i video.mp4 --model ./models/Qwen3-ASR-1.7B
```

## 工作原理

```
Thread 1 (CPU): ffmpeg 提取音频 → Silero-VAD 切片
Thread 2 (GPU): vLLM 引擎初始化 & 模型加载
                ↓ 两者完成后 ↓
           vLLM 批量推理所有切片 → 输出 TXT / SRT
```

## License

Apache-2.0
