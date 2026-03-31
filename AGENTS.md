# Qwen3-ASR 长视频转录工具 — 项目规划

## 项目目标

构建一个本地化的长视频/音频 ASR 转录管线，核心流程：

```
长视频 → ffmpeg pipe 提取音频(16kHz mono, 直接读入内存)
       → Silero-VAD 语音活动检测 & 智能切片  ← CPU，与 GPU 模型加载并行
       → Qwen3-ASR-1.7B (vLLM backend) 批量推理
       → 结果聚合 & 输出(纯文本 / SRT 字幕）
```

## 目标硬件

- **GPU**: RTX 4070 Super 12GB VRAM
- **RAM**: 32GB
- **显存预算**：1.7B bf16 权重 ≈ 3.4GB + vLLM 引擎开销 ≈ 1GB + KV cache ≈ 4GB → **总计 ~8.4GB**，12GB 充裕
- **默认参数**：`gpu_memory_utilization=0.7`、`max_inference_batch_size=32`

## 技术选型

| 组件 | 方案 | 说明 |
|------|------|------|
| 音频提取 | ffmpeg subprocess pipe | 从任意视频/音频格式提取 16kHz mono PCM，直接 pipe 到内存（不写中间文件） |
| VAD 切片 | silero-vad (CPU) | 轻量 CPU 模型，检测语音段边界 |
| ASR 推理 | qwen-asr[vllm] | `Qwen3ASRModel.LLM(...)` vLLM backend，单卡批量推理 |
| 模型权重 | HuggingFace / ModelScope 自动下载 | 首次加载时自动拉取，也支持预下载到本地路径 |
| 配置管理 | config.py | 所有可调参数集中管理，CLI 参数可覆盖 |
| 输出格式 | TXT + SRT | 聚合转录文本 + 可选 SRT 字幕（基于 VAD 段落级时间戳） |

## 架构设计

### 1. 配置模块 (`config.py`)

集中管理所有可调参数，使用 dataclass：

```python
@dataclass
class Config:
    # 输入输出
    input_file: str = ""
    output_dir: str = "./output"
    save_srt: bool = False

    # 模型
    model: str = "Qwen/Qwen3-ASR-1.7B"
    language: str | None = None       # None = 自动检测

    # vLLM 引擎
    gpu_memory_utilization: float = 0.7
    max_inference_batch_size: int = 32
    max_new_tokens: int = 4096

    # VAD 切片
    segment_duration: int = 120       # 目标切片长度（秒）
    max_segment_duration: int = 180   # 切片上限（秒）
```

### 2. 音频预处理模块 (`audio.py`)

- `load_audio(file_path) -> np.ndarray`：
  - 先尝试 librosa 加载（快速路径）
  - 失败则 fallback 到 ffmpeg subprocess pipe（输出到 stdout，不写中间文件）
  - 统一返回 16kHz mono float32 ndarray

### 3. VAD 切片模块 (`vad.py`)

参考 `qwen3_asr_toolkit/audio_tools.py` 的 `process_vad()` 逻辑：

- 使用 `silero_vad.get_speech_timestamps()` 检测语音段
- 以 `segment_duration`（默认 120s）为目标切分长度
- 在 VAD 检测到的静音边界处切分，避免切断语句
- 若切片超过 `max_segment_duration`（默认 180s），均匀再分
- VAD 失败时 fallback 到固定时长均匀切分
- 返回 `list[(start_sample, end_sample, wav_segment)]`

### 4. ASR 推理模块 (`transcribe.py`)

核心：**单卡 vLLM 批量推理，最大化吞吐**

```python
model = Qwen3ASRModel.LLM(
    model=config.model,
    gpu_memory_utilization=config.gpu_memory_utilization,  # 0.7 for 12GB
    max_inference_batch_size=config.max_inference_batch_size,  # 32 for 12GB
    max_new_tokens=config.max_new_tokens,
)
results = model.transcribe(
    audio=[(seg, 16000) for seg in segments],
    language=config.language,
)
```

**并发优化要点：**
- vLLM 内部调度器天然支持 continuous batching，无需手动线程池
- `max_inference_batch_size=32` 为 12GB 显存保守默认值，可按实际调大
- `gpu_memory_utilization=0.7` → 12GB × 0.7 = 8.4GB，预留 3.6GB 给系统/显示
- 音频切片越短（120s），batch 内样本越多，GPU 利用率越高
- 代码必须包裹在 `if __name__ == '__main__':` 内（vLLM spawn 要求）

### 5. 结果聚合模块 (`output.py`)

- 按切片原始顺序拼接转录文本
- 基于 VAD 切片时间偏移生成全局时间戳
- 输出纯文本 `.txt`
- 可选输出 SRT 字幕 `.srt`（段落级，每个 VAD 切片一条字幕）

### 6. CLI 入口 (`main.py`)

```
python main.py -i video.mp4 [--model Qwen/Qwen3-ASR-1.7B] [--gpu-mem 0.7] \
    [--batch-size 32] [--segment-duration 120] [--max-segment 180] \
    [--language auto] [--srt] [--output-dir ./output]
```

### 7. 流水线并行策略

```
Thread 1 (CPU): ffmpeg 提取音频 → silero-vad 切片
Thread 2 (GPU): vLLM 引擎初始化 & 模型加载
                ↓ 两者完成后 ↓
           vLLM 批量推理所有切片
```

- silero-vad 是纯 CPU PyTorch 模型，处理极快
- vLLM 初始化期间大量时间花在磁盘 I/O 和 CUDA 操作上，释放 GIL
- 两者互不阻塞，可用 `threading` 并行执行

## 文件结构

```
qwenasr/
├── AGENTS.md           # 本文件 — 项目规划
├── README.md           # 使用说明 & 配置文档
├── requirements.txt    # 依赖列表
├── config.py           # 集中配置（dataclass）
├── main.py             # CLI 入口
├── audio.py            # ffmpeg pipe 音频提取 & 加载
├── vad.py              # Silero-VAD 切片
├── transcribe.py       # Qwen3-ASR vLLM 推理
└── output.py           # 结果聚合 & SRT 生成
```

## 依赖

```
qwen-asr[vllm]          # Qwen3-ASR + vLLM backend
silero-vad              # VAD 模型（CPU）
librosa                 # 音频加载（快速路径）
soundfile               # 音频读写
numpy                   # 数组操作
```

系统依赖：`ffmpeg`

## 模型权重获取

`qwen-asr` 包会在首次调用 `Qwen3ASRModel.LLM(model="Qwen/Qwen3-ASR-1.7B")` 时自动从 HuggingFace 下载权重。

如需预下载或国内加速：
```bash
# HuggingFace
hf download Qwen/Qwen3-ASR-1.7B --local-dir ./models/Qwen3-ASR-1.7B

# ModelScope（国内推荐）
modelscope download --model Qwen/Qwen3-ASR-1.7B --local_dir ./models/Qwen3-ASR-1.7B
```

预下载后在 `config.py` 中将 `model` 指向本地路径即可。

## 开发任务清单

| # | 任务 | 状态 |
|---|------|------|
| 1 | 创建 `requirements.txt` | ✅ 完成 |
| 2 | 实现 `config.py` — 集中配置管理 | ✅ 完成 |
| 3 | 实现 `audio.py` — ffmpeg pipe 音频提取 & 加载 | ✅ 完成 |
| 4 | 实现 `vad.py` — Silero-VAD 切片逻辑 | ✅ 完成 |
| 5 | 实现 `transcribe.py` — vLLM 批量推理 | ✅ 完成 |
| 6 | 实现 `output.py` — 文本聚合 & SRT 生成 | ✅ 完成 |
| 7 | 实现 `main.py` — CLI 入口 & 流水线编排 | ✅ 完成 |
| 8 | 撰写 `README.md` — 使用说明 & 配置文档 | ✅ 完成 |
| 9 | 端到端测试 & 调优 | 待开始 |
| 10 | 服务器模式（持久化推理服务） | 待开始 |

## 后续优化：服务器模式（持久化推理服务）

**动机**：当前每次运行都要加载模型到 GPU（权重加载 + CUDA Graph 编译），启动开销大。如果需要频繁/批量转录，应改用持久化服务器模式，模型常驻显存。

### 方案设计

qwen-asr 自带 `qwen-asr-serve` 命令（本质是 `vllm serve` 的封装），启动后暴露 OpenAI 兼容 API。

**启动服务器**：
```bash
qwen-asr-serve ./models/Qwen3-ASR-1.7B \
    --gpu-memory-utilization 0.7 \
    --host 127.0.0.1 --port 8000
```

**客户端调用**：
```python
from qwen_asr import Qwen3ASRModel

model = Qwen3ASRModel.OpenAI(
    base_url="http://127.0.0.1:8000/v1",
    model="Qwen/Qwen3-ASR-1.7B",
)
results = model.transcribe(audio=[(wav, 16000)], language=None)
```

### 改造要点

- `transcribe.py` 新增 `init_model_client(config)` 函数，返回 OpenAI 模式的 model 对象
- `config.py` 新增 `server_url: str | None = None` 参数
- `main.py` CLI 新增 `--server-url` 参数，传入时跳过本地模型加载，直接连接远程服务
- CPU 流水线（音频加载 + VAD）不受影响，无需改动
- 服务器启动可以写一个 `serve.sh` 脚本简化操作

### 注意事项

- 服务器模式下 `max_inference_batch_size` 由服务端控制，客户端无需设置
- `enforce_eager=True` 可传给 `qwen-asr-serve` 以加速服务器首次启动
- 作为临时加速手段，也可以在当前离线模式中加 `enforce_eager=True` 跳过 CUDA Graph 编译，减少约 30-60 秒启动时间
