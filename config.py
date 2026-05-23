from dataclasses import dataclass


DEFAULT_ASR_MODEL = "Qwen/Qwen3-ASR-1.7B"
DEFAULT_FORCED_ALIGNER_MODEL = "Qwen/Qwen3-ForcedAligner-0.6B"


@dataclass
class Config:
    # 输入输出
    input_file: str = ""
    output_dir: str = "./output"
    save_srt: bool = True

    # 模型
    model: str = DEFAULT_ASR_MODEL
    forced_aligner_model: str | None = DEFAULT_FORCED_ALIGNER_MODEL
    return_time_stamps: bool = True
    language: str | None = None  # None = 自动检测
    backend: str = "auto"  # auto / vllm / transformers
    device_map: str = "cuda:0"
    dtype: str = "bfloat16"

    # 推理引擎
    gpu_memory_utilization: float = 0.5
    max_inference_batch_size: int = 1
    max_new_tokens: int = 2048

    # VAD 切片
    segment_duration: int = 60  # 目标切片长度（秒）
    max_segment_duration: int = 90  # 切片上限（秒）

    # 预处理缓存
    use_cache: bool = True
    cache_dir: str = "./cache"
    refresh_cache: bool = False

    # SRT 聚合
    srt_max_chars: int = 42
    srt_max_duration: float = 6.0
