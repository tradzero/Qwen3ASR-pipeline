from dataclasses import dataclass


@dataclass
class Config:
    # 输入输出
    input_file: str = ""
    output_dir: str = "./output"
    save_srt: bool = False

    # 模型
    model: str = "Qwen/Qwen3-ASR-1.7B"
    language: str | None = None  # None = 自动检测

    # vLLM 引擎
    gpu_memory_utilization: float = 0.7
    max_inference_batch_size: int = 32
    max_new_tokens: int = 4096

    # VAD 切片
    segment_duration: int = 120  # 目标切片长度（秒）
    max_segment_duration: int = 180  # 切片上限（秒）
