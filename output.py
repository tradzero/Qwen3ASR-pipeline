import os

from audio import WAV_SAMPLE_RATE


def save_txt(texts: list[str], output_path: str) -> None:
    """将转录文本按顺序拼接并保存为 .txt 文件。"""
    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write("\n".join(texts))


def _format_srt_time(seconds: float) -> str:
    """将秒数格式化为 SRT 时间戳 HH:MM:SS,mmm。"""
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    ms = int((seconds % 1) * 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def save_srt(
    texts: list[str],
    segments: list[tuple[int, int]],
    output_path: str,
) -> None:
    """根据 VAD 切片时间偏移生成 SRT 字幕文件。

    segments: list[(start_sample, end_sample)]
    """
    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        idx = 1
        for text, (start_sample, end_sample) in zip(texts, segments):
            text = text.strip()
            if not text:
                continue
            start_s = start_sample / WAV_SAMPLE_RATE
            end_s = end_sample / WAV_SAMPLE_RATE
            f.write(f"{idx}\n")
            f.write(f"{_format_srt_time(start_s)} --> {_format_srt_time(end_s)}\n")
            f.write(f"{text}\n\n")
            idx += 1
