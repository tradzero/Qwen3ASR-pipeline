from pathlib import Path

import numpy as np
from qwen_asr import Qwen3ASRModel

from audio import WAV_SAMPLE_RATE
from config import Config


PROJECT_ROOT = Path(__file__).resolve().parent
DEFAULT_LOCAL_MODEL_DIR = PROJECT_ROOT / "models"


def resolve_model_source(model: str) -> str:
    """优先复用本地预下载模型目录，避免重复从 HuggingFace 拉取。"""
    explicit_path = Path(model).expanduser()
    if model.startswith((".", "/", "~")) or explicit_path.exists():
        return str(explicit_path)

    if model.count("/") != 1:
        return model

    local_candidate = DEFAULT_LOCAL_MODEL_DIR / model.rsplit("/", 1)[-1]
    if local_candidate.is_dir():
        return str(local_candidate)

    return model


def init_model(config: Config) -> Qwen3ASRModel:
    """初始化 vLLM backend ASR 模型。"""
    model_source = resolve_model_source(config.model)
    if model_source != config.model:
        print(f"[GPU] 检测到本地预下载模型，优先使用: {model_source}")

    return Qwen3ASRModel.LLM(
        model=model_source,
        gpu_memory_utilization=config.gpu_memory_utilization,
        max_inference_batch_size=config.max_inference_batch_size,
        max_new_tokens=config.max_new_tokens,
    )


def transcribe_segments(
    model: Qwen3ASRModel,
    segments: list[tuple[int, int, np.ndarray]],
    language: str | None = None,
    batch_size: int = 0,
) -> list[str]:
    """批量转录所有切片，返回有序文本列表。"""
    audio_inputs = [(seg, WAV_SAMPLE_RATE) for _, _, seg in segments]
    total = len(audio_inputs)

    # 少量切片或未指定 batch_size 时一次性推理
    if batch_size <= 0 or total <= batch_size:
        print(f"[ASR] 推理中 ... (共 {total} 段)")
        results = model.transcribe(audio=audio_inputs, language=language)
        return [r.text for r in results]

    # 分批推理并输出进度
    all_texts: list[str] = []
    for i in range(0, total, batch_size):
        batch = audio_inputs[i : i + batch_size]
        end = min(i + batch_size, total)
        print(f"[ASR] 推理中 [{end}/{total}] ...")
        results = model.transcribe(audio=batch, language=language)
        all_texts.extend(r.text for r in results)
    return all_texts
