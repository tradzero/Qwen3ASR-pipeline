import os
from typing import Any

from audio import WAV_SAMPLE_RATE


def clean_asr_text(
    text: str,
    repeat_threshold: int = 20,
    max_pattern_chars: int = 20,
) -> str:
    """压缩 ASR 偶发的超长重复字符或短语。"""
    if repeat_threshold <= 1 or not text:
        return text

    text = _collapse_char_repeats(text, repeat_threshold)
    return _collapse_pattern_repeats(text, repeat_threshold, max_pattern_chars)


def _collapse_char_repeats(text: str, repeat_threshold: int) -> str:
    parts: list[str] = []
    index = 0
    while index < len(text):
        char = text[index]
        end = index + 1
        while end < len(text) and text[end] == char:
            end += 1

        run_length = end - index
        if run_length > repeat_threshold:
            parts.append(char)
        else:
            parts.append(text[index:end])
        index = end

    return "".join(parts)


def _count_repeated_pattern(text: str, start: int, pattern: str) -> int:
    count = 0
    pattern_length = len(pattern)
    index = start
    while index + pattern_length <= len(text) and text[index : index + pattern_length] == pattern:
        count += 1
        index += pattern_length
    return count


def _collapse_pattern_repeats(
    text: str,
    repeat_threshold: int,
    max_pattern_chars: int,
) -> str:
    if len(text) < repeat_threshold * 2:
        return text

    parts: list[str] = []
    index = 0
    max_pattern_chars = max(1, max_pattern_chars)
    while index < len(text):
        found_repeat = False
        remaining = len(text) - index
        for pattern_length in range(1, min(max_pattern_chars, remaining // repeat_threshold) + 1):
            pattern = text[index : index + pattern_length]
            repeat_count = _count_repeated_pattern(text, index, pattern)
            if repeat_count < repeat_threshold:
                continue

            parts.append(pattern)
            index += repeat_count * pattern_length
            found_repeat = True
            break

        if not found_repeat:
            parts.append(text[index])
            index += 1

    return "".join(parts)


def save_txt(texts: list[str], output_path: str) -> None:
    """将转录文本按顺序拼接并保存为 .txt 文件。"""
    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write("\n".join(clean_asr_text(text) for text in texts))


def _format_srt_time(seconds: float) -> str:
    """将秒数格式化为 SRT 时间戳 HH:MM:SS,mmm。"""
    seconds = max(0.0, seconds)
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    ms = int(round((seconds % 1) * 1000))
    if ms == 1000:
        s += 1
        ms = 0
    if s == 60:
        m += 1
        s = 0
    if m == 60:
        h += 1
        m = 0
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def _timestamp_value(item: Any, name: str) -> Any:
    if isinstance(item, dict):
        return item.get(name)
    return getattr(item, name, None)


def _needs_space(left: str, right: str) -> bool:
    if not left or not right or left.isspace() or right.isspace():
        return False
    if right in ",.!?;:%)]}>，。！？；：、）】》":
        return False
    if left in "([{<（【《":
        return False
    if left in ",.!?;:" and right.isascii() and right.isalnum():
        return True
    return left.isascii() and right.isascii() and left.isalnum() and right.isalnum()


def _join_caption(parts: list[str]) -> str:
    text = ""
    for part in parts:
        clean = str(part).strip()
        if not clean:
            continue
        if text and _needs_space(text[-1], clean[0]):
            text += " "
        text += clean
    return text.strip()


def _timestamp_entries_for_segment(
    time_stamps: list[Any],
    segment_offset: float,
    max_caption_chars: int,
    max_caption_duration: float,
) -> list[tuple[float, float, str]]:
    entries: list[tuple[float, float, str]] = []
    current_parts: list[str] = []
    current_start: float | None = None
    current_end: float | None = None

    def flush() -> None:
        nonlocal current_parts, current_start, current_end
        text = clean_asr_text(_join_caption(current_parts))
        if text and current_start is not None and current_end is not None:
            entries.append((current_start, current_end, text))
        current_parts = []
        current_start = None
        current_end = None

    for item in time_stamps:
        text = _timestamp_value(item, "text")
        start_time = _timestamp_value(item, "start_time")
        end_time = _timestamp_value(item, "end_time")
        if text is None or start_time is None or end_time is None:
            continue

        text = str(text).strip()
        if not text:
            continue

        start = float(start_time) + segment_offset
        end = float(end_time) + segment_offset
        if end <= start:
            continue

        if current_start is None:
            current_start = start

        candidate = _join_caption(current_parts + [text])
        too_long = len(candidate) > max_caption_chars
        too_slow = end - current_start > max_caption_duration
        if current_parts and (too_long or too_slow):
            flush()
            current_start = start

        current_parts.append(text)
        current_end = end

    flush()
    return entries


def _write_srt_entries(entries: list[tuple[float, float, str]], output_path: str) -> None:
    with open(output_path, "w", encoding="utf-8") as f:
        for idx, (start_s, end_s, text) in enumerate(entries, start=1):
            f.write(f"{idx}\n")
            f.write(f"{_format_srt_time(start_s)} --> {_format_srt_time(end_s)}\n")
            f.write(f"{text}\n\n")


def save_srt(
    texts: list[str],
    segments: list[tuple[int, int]],
    output_path: str,
    time_stamps: list[list[Any] | None] | None = None,
    max_caption_chars: int = 42,
    max_caption_duration: float = 6.0,
) -> None:
    """优先根据 ForcedAligner 时间戳生成 SRT，失败时回退到 VAD 切片时间。

    segments: list[(start_sample, end_sample)]
    """
    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)

    entries: list[tuple[float, float, str]] = []
    for index, (text, (start_sample, end_sample)) in enumerate(zip(texts, segments)):
        segment_entries: list[tuple[float, float, str]] = []
        if time_stamps and index < len(time_stamps) and time_stamps[index]:
            segment_entries = _timestamp_entries_for_segment(
                time_stamps[index] or [],
                start_sample / WAV_SAMPLE_RATE,
                max_caption_chars,
                max_caption_duration,
            )

        if segment_entries:
            entries.extend(segment_entries)
            continue

        text = clean_asr_text(text).strip()
        if not text:
            continue
        entries.append((start_sample / WAV_SAMPLE_RATE, end_sample / WAV_SAMPLE_RATE, text))

    _write_srt_entries(entries, output_path)
