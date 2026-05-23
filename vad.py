import threading

import numpy as np
from silero_vad import get_speech_timestamps, load_silero_vad

from audio import WAV_SAMPLE_RATE


_VAD_MODEL = None
_VAD_MODEL_LOCK = threading.Lock()


def get_vad_model():
    global _VAD_MODEL
    with _VAD_MODEL_LOCK:
        if _VAD_MODEL is None:
            _VAD_MODEL = load_silero_vad()
        return _VAD_MODEL


def warmup_vad() -> None:
    get_vad_model()


def process_vad(
    wav: np.ndarray,
    segment_duration: int = 120,
    max_segment_duration: int = 180,
) -> list[tuple[int, int, np.ndarray]]:
    """使用 Silero-VAD 对音频进行智能切片。

    在 VAD 检测到的静音边界处切分，避免切断语句。
    若切片超过 max_segment_duration 则均匀再分。
    VAD 失败时 fallback 到固定时长均匀切分。

    返回 list[(start_sample, end_sample, wav_segment)]。
    """
    try:
        vad_model = get_vad_model()
        speech_timestamps = get_speech_timestamps(
            wav,
            vad_model,
            sampling_rate=WAV_SAMPLE_RATE,
            return_seconds=False,
            min_speech_duration_ms=1500,
            min_silence_duration_ms=500,
        )

        if not speech_timestamps:
            raise ValueError("VAD 未检测到语音段")

        # 收集所有可能的切分点（语音段起始位置 + 首尾）
        potential_splits = {0, len(wav)}
        for ts in speech_timestamps:
            potential_splits.add(ts["start"])
        sorted_splits = sorted(potential_splits)

        # 按目标时长选取最近的切分点
        segment_samples = segment_duration * WAV_SAMPLE_RATE
        final_splits = {0, len(wav)}
        target = segment_samples
        while target < len(wav):
            closest = min(sorted_splits, key=lambda p: abs(p - target))
            final_splits.add(closest)
            target += segment_samples
        ordered_splits = sorted(final_splits)

        # 确保每段不超过 max_segment_duration
        max_samples = max_segment_duration * WAV_SAMPLE_RATE
        refined = [0]
        for i in range(1, len(ordered_splits)):
            start = ordered_splits[i - 1]
            end = ordered_splits[i]
            length = end - start

            if length <= max_samples:
                refined.append(end)
            else:
                n = int(np.ceil(length / max_samples))
                sub_len = length / n
                for j in range(1, n):
                    refined.append(int(start + j * sub_len))
                refined.append(end)

        # 切出片段
        segments = []
        for i in range(len(refined) - 1):
            s, e = int(refined[i]), int(refined[i + 1])
            segments.append((s, e, wav[s:e]))
        return segments

    except Exception:
        # fallback: 固定时长均匀切分
        max_samples = max_segment_duration * WAV_SAMPLE_RATE
        segments = []
        for start in range(0, len(wav), max_samples):
            end = min(start + max_samples, len(wav))
            seg = wav[start:end]
            if len(seg) > 0:
                segments.append((start, end, seg))
        return segments
