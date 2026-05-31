import os
import subprocess
from urllib.parse import urlparse

import librosa
import numpy as np

WAV_SAMPLE_RATE = 16000
VIDEO_EXTENSIONS = {
    ".3gp",
    ".avi",
    ".flv",
    ".m4v",
    ".mkv",
    ".mov",
    ".mp4",
    ".mpeg",
    ".mpg",
    ".ts",
    ".webm",
    ".wmv",
}


def _is_ffmpeg_only_source(file_path: str) -> bool:
    scheme = urlparse(file_path).scheme.lower()
    return len(scheme) > 1 and scheme not in {"file"}


def _should_prefer_ffmpeg(file_path: str) -> bool:
    if _is_ffmpeg_only_source(file_path):
        return True

    parsed = urlparse(file_path)
    path = parsed.path if parsed.scheme.lower() == "file" else file_path
    return os.path.splitext(path)[1].lower() in VIDEO_EXTENSIONS


def _load_audio_with_ffmpeg(file_path: str) -> np.ndarray:
    command = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-i",
        file_path,
        "-vn",
        "-ar",
        str(WAV_SAMPLE_RATE),
        "-ac",
        "1",
        "-c:a",
        "pcm_s16le",
        "-f",
        "s16le",
        "-",
    ]
    process = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    stdout_data, stderr_data = process.communicate()

    if process.returncode != 0:
        raise RuntimeError(
            f"ffmpeg 处理失败: {stderr_data.decode('utf-8', errors='ignore')}"
        )

    return np.frombuffer(stdout_data, dtype=np.int16).astype(np.float32) / 32768.0


def load_audio(file_path: str) -> np.ndarray:
    """加载音频文件为 16kHz mono float32 ndarray。

    视频/远程输入优先使用 ffmpeg pipe，音频文件优先使用 librosa。
    """
    if _should_prefer_ffmpeg(file_path):
        return _load_audio_with_ffmpeg(file_path)

    try:
        wav_data, _ = librosa.load(file_path, sr=WAV_SAMPLE_RATE, mono=True)
        return wav_data.astype(np.float32, copy=False)
    except Exception:
        return _load_audio_with_ffmpeg(file_path)
