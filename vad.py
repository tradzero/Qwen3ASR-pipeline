import threading

import numpy as np
from silero_vad import get_speech_timestamps, load_silero_vad

from audio import WAV_SAMPLE_RATE


_VAD_MODEL = None
_VAD_MODEL_LOCK = threading.Lock()
_PRIMARY_VAD_PARAMS = {
    "threshold": 0.5,
    "min_speech_duration_ms": 500,
    "min_silence_duration_ms": 500,
    "speech_pad_ms": 300,
}
_LOOSE_VAD_PARAMS = {
    "threshold": 0.35,
    "min_speech_duration_ms": 250,
    "min_silence_duration_ms": 300,
    "speech_pad_ms": 300,
}
_SPLIT_MARGIN_MS = 300
_MIN_SPLIT_SILENCE_MS = 200
_ENERGY_SEARCH_RADIUS_MS = 5000
_ENERGY_FRAME_MS = 120
_ENERGY_HOP_MS = 50


def get_vad_model():
    global _VAD_MODEL
    with _VAD_MODEL_LOCK:
        if _VAD_MODEL is None:
            _VAD_MODEL = load_silero_vad()
        return _VAD_MODEL


def warmup_vad() -> None:
    get_vad_model()


def _seconds_to_samples(seconds: float | int) -> int:
    return max(1, int(round(float(seconds) * WAV_SAMPLE_RATE)))


def _milliseconds_to_samples(milliseconds: float | int) -> int:
    return max(1, int(round(float(milliseconds) * WAV_SAMPLE_RATE / 1000)))


def _normalize_speech_ranges(
    speech_timestamps: list[dict[str, int]],
    total_samples: int,
) -> list[tuple[int, int]]:
    ranges = []
    for ts in speech_timestamps:
        start = max(0, min(total_samples, int(ts["start"])))
        end = max(0, min(total_samples, int(ts["end"])))
        if end > start:
            ranges.append((start, end))

    ranges.sort()
    merged: list[tuple[int, int]] = []
    for start, end in ranges:
        if not merged or start > merged[-1][1]:
            merged.append((start, end))
        else:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
    return merged


def _speech_coverage(speech_ranges: list[tuple[int, int]], total_samples: int) -> float:
    if total_samples <= 0:
        return 0.0
    speech_samples = sum(end - start for start, end in speech_ranges)
    return speech_samples / total_samples


def _silence_ranges_from_speech(
    speech_ranges: list[tuple[int, int]],
    total_samples: int,
) -> list[tuple[int, int]]:
    silence_ranges = []
    cursor = 0
    for start, end in speech_ranges:
        if start > cursor:
            silence_ranges.append((cursor, start))
        cursor = max(cursor, end)
    if cursor < total_samples:
        silence_ranges.append((cursor, total_samples))
    return silence_ranges


def _choose_silence_split(
    target: int,
    lower: int,
    upper: int,
    silence_ranges: list[tuple[int, int]],
    split_margin_samples: int,
    min_silence_samples: int,
) -> int | None:
    candidates = []
    for silence_start, silence_end in silence_ranges:
        start = max(silence_start, lower)
        end = min(silence_end, upper)
        if end <= start or end - start < min_silence_samples:
            continue

        margin = min(split_margin_samples, max(0, (end - start - 1) // 2))
        safe_start = start + margin
        safe_end = end - margin
        midpoint = (safe_start + safe_end) // 2
        candidates.append(midpoint)

        if safe_start <= target <= safe_end:
            candidates.append(target)
        elif target < safe_start:
            candidates.append(safe_start)
        else:
            candidates.append(safe_end)

    if not candidates:
        return None

    return min(candidates, key=lambda point: (abs(point - target), point))


def _refine_split_by_energy(
    wav: np.ndarray | None,
    target: int,
    lower: int,
    upper: int,
) -> int:
    if wav is None or len(wav) == 0 or upper <= lower:
        return target

    search_radius = _milliseconds_to_samples(_ENERGY_SEARCH_RADIUS_MS)
    frame_samples = _milliseconds_to_samples(_ENERGY_FRAME_MS)
    hop_samples = _milliseconds_to_samples(_ENERGY_HOP_MS)
    search_lower = max(lower, target - search_radius)
    search_upper = min(upper, target + search_radius, len(wav))
    if search_upper <= search_lower:
        return target

    half_frame = max(1, frame_samples // 2)
    best_point = target
    best_score: tuple[float, int] | None = None
    for point in range(search_lower, search_upper + 1, hop_samples):
        start = max(0, point - half_frame)
        end = min(len(wav), point + half_frame)
        if end <= start:
            continue

        frame = wav[start:end].astype(np.float32, copy=False)
        energy = float(np.mean(frame * frame))
        score = (energy, abs(point - target))
        if best_score is None or score < best_score:
            best_score = score
            best_point = point

    return int(best_point)


def _energy_split_points(
    total_samples: int,
    segment_samples: int,
    max_samples: int,
    wav: np.ndarray | None,
) -> list[int]:
    if total_samples <= 0:
        return []

    split_points = [0]
    current = 0
    min_segment_samples = max(1, min(segment_samples // 2, 10 * WAV_SAMPLE_RATE))
    while current + segment_samples < total_samples:
        target = current + segment_samples
        upper = min(current + max_samples, total_samples)
        lower = min(current + min_segment_samples, upper)
        split = _refine_split_by_energy(wav, target, lower, upper)
        if split <= current:
            split = min(target, total_samples)
        if split <= current:
            break
        split_points.append(split)
        current = split

    if split_points[-1] != total_samples:
        split_points.append(total_samples)
    return split_points


def _split_points_from_speech(
    total_samples: int,
    speech_ranges: list[tuple[int, int]],
    segment_samples: int,
    max_samples: int,
    wav: np.ndarray | None = None,
) -> list[int]:
    if total_samples <= 0:
        return []

    target_samples = max(1, min(segment_samples, max_samples))
    if not speech_ranges:
        return _energy_split_points(total_samples, target_samples, max_samples, wav)

    silence_ranges = _silence_ranges_from_speech(speech_ranges, total_samples)
    split_margin_samples = _milliseconds_to_samples(_SPLIT_MARGIN_MS)
    min_silence_samples = _milliseconds_to_samples(_MIN_SPLIT_SILENCE_MS)
    min_segment_samples = max(1, min(target_samples // 2, 10 * WAV_SAMPLE_RATE))

    split_points = [0]
    current = 0
    while current + target_samples < total_samples:
        target = current + target_samples
        upper = min(current + max_samples, total_samples)
        lower = min(current + min_segment_samples, upper)
        split = _choose_silence_split(
            target,
            lower,
            upper,
            silence_ranges,
            split_margin_samples,
            min_silence_samples,
        )
        if split is None:
            split = min(target, upper)
        split = _refine_split_by_energy(wav, split, lower, upper)
        if split <= current:
            split = min(current + target_samples, total_samples)
        if split <= current:
            break

        split_points.append(split)
        current = split

    if split_points[-1] != total_samples:
        split_points.append(total_samples)
    return split_points


def _segments_from_split_points(
    wav: np.ndarray,
    split_points: list[int],
) -> list[tuple[int, int, np.ndarray]]:
    segments = []
    for index in range(len(split_points) - 1):
        start, end = int(split_points[index]), int(split_points[index + 1])
        if end > start:
            segments.append((start, end, wav[start:end]))
    return segments


def _detect_speech_ranges(wav: np.ndarray, vad_model) -> list[tuple[int, int]]:
    primary_timestamps = get_speech_timestamps(
        wav,
        vad_model,
        sampling_rate=WAV_SAMPLE_RATE,
        return_seconds=False,
        **_PRIMARY_VAD_PARAMS,
    )
    primary_ranges = _normalize_speech_ranges(primary_timestamps, len(wav))
    if primary_ranges and _speech_coverage(primary_ranges, len(wav)) >= 0.15:
        return primary_ranges

    loose_timestamps = get_speech_timestamps(
        wav,
        vad_model,
        sampling_rate=WAV_SAMPLE_RATE,
        return_seconds=False,
        **_LOOSE_VAD_PARAMS,
    )
    loose_ranges = _normalize_speech_ranges(loose_timestamps, len(wav))
    if not loose_ranges:
        return primary_ranges

    return _normalize_speech_ranges(
        [{"start": start, "end": end} for start, end in primary_ranges + loose_ranges],
        len(wav),
    )


def process_vad(
    wav: np.ndarray,
    segment_duration: int = 120,
    max_segment_duration: int = 180,
) -> list[tuple[int, int, np.ndarray]]:
    """使用 Silero-VAD 对音频进行智能切片。

    在 VAD 检测到的静音区域切分，避免切在语音起点。
    VAD 不可靠或失败时 fallback 到目标时长均匀切分。

    返回 list[(start_sample, end_sample, wav_segment)]。
    """
    segment_samples = _seconds_to_samples(segment_duration)
    max_samples = _seconds_to_samples(max_segment_duration)

    try:
        vad_model = get_vad_model()
        speech_ranges = _detect_speech_ranges(wav, vad_model)
        split_points = _split_points_from_speech(
            len(wav),
            speech_ranges,
            segment_samples,
            max_samples,
            wav,
        )
        return _segments_from_split_points(wav, split_points)

    except Exception:
        split_points = _energy_split_points(
            len(wav),
            min(segment_samples, max_samples),
            max_samples,
            wav,
        )
        return _segments_from_split_points(wav, split_points)
