from pathlib import Path
import gc
import os
import platform
import time
from typing import Any

os.environ.setdefault("PYTORCH_CUDA_ALLOC_CONF", "expandable_segments:True")

import numpy as np
import torch
from qwen_asr import Qwen3ASRModel

from audio import WAV_SAMPLE_RATE
from config import DEFAULT_ASR_MODEL, Config


PROJECT_ROOT = Path(__file__).resolve().parent
DEFAULT_LOCAL_MODEL_DIR = PROJECT_ROOT / "models"
VALID_BACKENDS = {"auto", "vllm", "transformers"}


def _torch_dtype(dtype: str) -> torch.dtype:
    normalized = dtype.lower()
    if normalized in {"bf16", "bfloat16"}:
        return torch.bfloat16
    if normalized in {"fp16", "float16", "half"}:
        return torch.float16
    if normalized in {"fp32", "float32"}:
        return torch.float32
    raise ValueError(f"不支持的 dtype: {dtype}")


def select_backend(requested_backend: str) -> str:
    backend = requested_backend.lower()
    if backend not in VALID_BACKENDS:
        raise ValueError(f"不支持的 backend: {requested_backend}")
    if backend == "auto":
        return "transformers" if platform.system() == "Windows" else "vllm"
    return backend


def _looks_like_forced_aligner(model: str | None) -> bool:
    return bool(model and "forcedaligner" in model.replace("_", "").replace("-", "").lower())


def resolve_model_source(model: str | None) -> str | None:
    """优先复用本地预下载模型目录，避免重复从 HuggingFace 拉取。"""
    if not model:
        return None

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
    """初始化 ASR 模型。"""
    asr_model = config.model
    forced_aligner_model = config.forced_aligner_model
    selected_backend = select_backend(config.backend)

    if _looks_like_forced_aligner(asr_model):
        print(
            "[配置] ForcedAligner 是时间戳对齐模型，不能单独做 ASR；"
            f"已将 {asr_model} 作为对齐模型，并使用 {DEFAULT_ASR_MODEL} 作为 ASR 主模型。"
        )
        forced_aligner_model = asr_model
        asr_model = DEFAULT_ASR_MODEL

    model_source = resolve_model_source(asr_model)
    if model_source != asr_model:
        print(f"[GPU] 检测到本地预下载模型，优先使用: {model_source}")

    print(f"[GPU] 推理后端: {selected_backend}")

    common_kwargs: dict[str, Any] = dict(
        max_inference_batch_size=config.max_inference_batch_size,
        max_new_tokens=config.max_new_tokens,
    )

    if config.return_time_stamps and forced_aligner_model:
        aligner_source = resolve_model_source(forced_aligner_model)
        if aligner_source != forced_aligner_model:
            print(f"[GPU] 检测到本地预下载对齐模型，优先使用: {aligner_source}")
        common_kwargs["forced_aligner"] = aligner_source
        common_kwargs["forced_aligner_kwargs"] = dict(
            dtype=_torch_dtype(config.dtype),
            device_map=config.device_map,
        )

    if selected_backend == "transformers":
        return Qwen3ASRModel.from_pretrained(
            model_source,
            dtype=_torch_dtype(config.dtype),
            device_map=config.device_map,
            **common_kwargs,
        )

    try:
        return Qwen3ASRModel.LLM(
            model=model_source,
            gpu_memory_utilization=config.gpu_memory_utilization,
            **common_kwargs,
        )
    except ImportError as exc:
        if config.backend.lower() == "auto":
            print(f"[GPU] vLLM 后端不可用，回退到 transformers: {exc}")
            return Qwen3ASRModel.from_pretrained(
                model_source,
                dtype=_torch_dtype(config.dtype),
                device_map=config.device_map,
                **common_kwargs,
            )
        raise ImportError(
            "vLLM 后端在当前环境不可用。Windows 原生环境通常缺少 vllm._C，"
            "请改用 --backend transformers，或在 WSL/Linux/Docker 中运行 --backend vllm。"
        ) from exc


def transcribe_segments(
    model: Qwen3ASRModel,
    segments: list[tuple[int, int, np.ndarray]],
    language: str | None = None,
    batch_size: int = 0,
    return_time_stamps: bool = False,
) -> tuple[list[str], list[list[Any] | None]]:
    """批量转录所有切片，返回有序文本和模型时间戳。"""
    audio_inputs = [(seg, WAV_SAMPLE_RATE) for _, _, seg in segments]
    total = len(audio_inputs)
    t_start = time.time()

    def collect_outputs(results) -> tuple[list[str], list[list[Any] | None]]:
        texts = [getattr(r, "text", "") for r in results]
        time_stamps = [getattr(r, "time_stamps", None) for r in results]
        return texts, time_stamps

    def clear_cuda_cache() -> None:
        gc.collect()
        if torch.cuda.is_available():
            torch.cuda.empty_cache()

    def format_duration(seconds: float | None) -> str:
        if seconds is None:
            return "--:--"
        seconds = max(0, int(seconds))
        minutes, sec = divmod(seconds, 60)
        hours, minutes = divmod(minutes, 60)
        if hours:
            return f"{hours:d}:{minutes:02d}:{sec:02d}"
        return f"{minutes:02d}:{sec:02d}"

    def progress_text(done: int, total_count: int, current_batch_size: int) -> str:
        percent = done / total_count * 100 if total_count else 100.0
        elapsed = time.time() - t_start
        eta = None
        if done > 0 and done < total_count:
            eta = elapsed / done * (total_count - done)
        return (
            f"[ASR] 推理中 [{done}/{total_count}, {percent:.1f}%] "
            f"batch={current_batch_size}, elapsed={format_duration(elapsed)}, "
            f"eta={format_duration(eta)} ..."
        )

    def cuda_peak_text() -> str:
        if not torch.cuda.is_available():
            return ""
        peak_gb = torch.cuda.max_memory_allocated() / 1024**3
        reserved_gb = torch.cuda.memory_reserved() / 1024**3
        return f", CUDA peak={peak_gb:.1f}GiB, reserved={reserved_gb:.1f}GiB"

    def run_transcribe(batch_audio, progress: str):
        print(progress)
        if torch.cuda.is_available():
            torch.cuda.reset_peak_memory_stats()
        try:
            results = model.transcribe(
                audio=batch_audio,
                language=language,
                return_time_stamps=return_time_stamps,
            )
            print(f"[ASR] batch 完成{cuda_peak_text()}")
            return results
        except torch.cuda.OutOfMemoryError:
            print(f"[ASR] batch OOM{cuda_peak_text()}")
            clear_cuda_cache()
            if len(batch_audio) == 1:
                raise RuntimeError(
                    "CUDA 显存不足。请尝试加 --batch-size 1 --segment-duration 45 "
                    "--max-segment 60，或临时加 --no-timestamps 关闭 ForcedAligner 时间戳。"
                )
            print("[ASR] 当前 batch 显存不足，自动降到单段重试...")
            retry_results = []
            for retry_index, single_audio in enumerate(batch_audio, start=1):
                clear_cuda_cache()
                if torch.cuda.is_available():
                    torch.cuda.reset_peak_memory_stats()
                retry_results.extend(
                    model.transcribe(
                        audio=[single_audio],
                        language=language,
                        return_time_stamps=return_time_stamps,
                    )
                )
                print(f"[ASR] 单段重试完成 [{retry_index}/{len(batch_audio)}]{cuda_peak_text()}")
            return retry_results
        finally:
            clear_cuda_cache()

    # 少量切片或未指定 batch_size 时一次性推理
    if batch_size <= 0 or total <= batch_size:
        results = run_transcribe(audio_inputs, progress_text(total, total, len(audio_inputs)))
        return collect_outputs(results)

    # 分批推理并输出进度
    all_texts: list[str] = []
    all_time_stamps: list[list[Any] | None] = []
    for i in range(0, total, batch_size):
        batch = audio_inputs[i : i + batch_size]
        end = min(i + batch_size, total)
        results = run_transcribe(batch, progress_text(end, total, len(batch)))
        texts, time_stamps = collect_outputs(results)
        all_texts.extend(texts)
        all_time_stamps.extend(time_stamps)
    return all_texts, all_time_stamps
