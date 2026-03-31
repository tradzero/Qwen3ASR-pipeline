import numpy as np
from qwen_asr import Qwen3ASRModel

from audio import WAV_SAMPLE_RATE
from config import Config


def init_model(config: Config) -> Qwen3ASRModel:
    """初始化 vLLM backend ASR 模型。"""
    return Qwen3ASRModel.LLM(
        model=config.model,
        gpu_memory_utilization=config.gpu_memory_utilization,
        max_inference_batch_size=config.max_inference_batch_size,
        max_new_tokens=config.max_new_tokens,
    )


def transcribe_segments(
    model: Qwen3ASRModel,
    segments: list[tuple[int, int, np.ndarray]],
    language: str | None = None,
) -> list[str]:
    """批量转录所有切片，返回有序文本列表。"""
    audio_inputs = [(seg, WAV_SAMPLE_RATE) for _, _, seg in segments]
    results = model.transcribe(audio=audio_inputs, language=language)
    return [r.text for r in results]
