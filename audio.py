import io
import subprocess

import librosa
import numpy as np
import soundfile as sf

WAV_SAMPLE_RATE = 16000


def load_audio(file_path: str) -> np.ndarray:
    """加载音频文件为 16kHz mono float32 ndarray。

    快速路径使用 librosa，失败则 fallback 到 ffmpeg pipe。
    """
    try:
        wav_data, _ = librosa.load(file_path, sr=WAV_SAMPLE_RATE, mono=True)
        return wav_data
    except Exception:
        pass

    command = [
        "ffmpeg",
        "-i", file_path,
        "-ar", str(WAV_SAMPLE_RATE),
        "-ac", "1",
        "-c:a", "pcm_s16le",
        "-f", "wav",
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

    with io.BytesIO(stdout_data) as buf:
        wav_data, _ = sf.read(buf, dtype="float32")

    return wav_data
