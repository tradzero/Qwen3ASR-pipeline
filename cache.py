import hashlib
import json
import os
import re
from pathlib import Path
from urllib.parse import unquote, urlparse

import numpy as np

from audio import WAV_SAMPLE_RATE
from config import Config


CACHE_VERSION = 1
VAD_CACHE_VERSION = "silero-vad:min_speech_1500:min_silence_500:v1"


def _is_remote_source(input_file: str) -> bool:
    scheme = urlparse(input_file).scheme.lower()
    return len(scheme) > 1 and scheme != "file"


def _input_stem(input_file: str) -> str:
    parsed = urlparse(input_file)
    if _is_remote_source(input_file):
        name = os.path.basename(unquote(parsed.path.rstrip("/")))
    else:
        name = os.path.basename(input_file)
    stem = os.path.splitext(name)[0] or "input"
    stem = re.sub(r"[^A-Za-z0-9._@-]+", "_", stem).strip("._ ")
    return (stem or "input")[:80]


def _normalized_input(input_file: str) -> str:
    if _is_remote_source(input_file):
        return input_file

    local_path = _local_input_path(input_file)
    return os.path.normcase(os.path.abspath(os.path.expanduser(local_path)))


def _local_input_path(input_file: str) -> str:
    parsed = urlparse(input_file)
    if parsed.scheme.lower() != "file":
        return input_file

    local_path = unquote(parsed.path)
    if re.match(r"^/[A-Za-z]:/", local_path):
        return local_path[1:]
    return local_path


def _input_file_stat(input_file: str) -> dict[str, int | None]:
    if _is_remote_source(input_file):
        return {"size": None, "mtime_ns": None}

    try:
        stat = os.stat(_local_input_path(input_file))
    except OSError:
        return {"size": None, "mtime_ns": None}
    return {"size": int(stat.st_size), "mtime_ns": int(stat.st_mtime_ns)}


def _cache_metadata(config: Config) -> dict[str, object]:
    file_stat = _input_file_stat(config.input_file)
    return {
        "cache_version": CACHE_VERSION,
        "vad_cache_version": VAD_CACHE_VERSION,
        "input": _normalized_input(config.input_file),
        "input_size": file_stat["size"],
        "input_mtime_ns": file_stat["mtime_ns"],
        "sample_rate": WAV_SAMPLE_RATE,
        "segment_duration": config.segment_duration,
        "max_segment_duration": config.max_segment_duration,
    }


def get_cache_dir(config: Config) -> Path:
    metadata = _cache_metadata(config)
    digest = hashlib.sha256(json.dumps(metadata, sort_keys=True).encode("utf-8")).hexdigest()[:16]
    return Path(config.cache_dir).expanduser() / f"{_input_stem(config.input_file)}-{digest}"


def clear_preprocess_cache(config: Config) -> None:
    cache_dir = get_cache_dir(config)
    if not cache_dir.exists():
        return
    for path in cache_dir.iterdir():
        if path.is_file():
            path.unlink()
    cache_dir.rmdir()


def load_preprocess_cache(config: Config) -> list[tuple[int, int, np.ndarray]] | None:
    cache_dir = get_cache_dir(config)
    metadata_path = cache_dir / "metadata.json"
    segments_path = cache_dir / "segments.json"
    audio_path = cache_dir / "audio.npy"
    if not metadata_path.is_file() or not segments_path.is_file() or not audio_path.is_file():
        return None

    try:
        with open(metadata_path, "r", encoding="utf-8") as file:
            cached_metadata = json.load(file)
        if cached_metadata != _cache_metadata(config):
            return None

        with open(segments_path, "r", encoding="utf-8") as file:
            ranges = json.load(file)
        wav = np.load(audio_path)
        return [(int(start), int(end), wav[int(start):int(end)]) for start, end in ranges]
    except (OSError, ValueError, json.JSONDecodeError):
        return None


def save_preprocess_cache(
    config: Config,
    wav: np.ndarray,
    segments: list[tuple[int, int, np.ndarray]],
) -> Path:
    cache_dir = get_cache_dir(config)
    cache_dir.mkdir(parents=True, exist_ok=True)

    audio_path = cache_dir / "audio.npy"
    segments_path = cache_dir / "segments.json"
    metadata_path = cache_dir / "metadata.json"

    audio_tmp = cache_dir / "audio.npy.tmp"
    segments_tmp = cache_dir / "segments.json.tmp"
    metadata_tmp = cache_dir / "metadata.json.tmp"

    with open(audio_tmp, "wb") as file:
        np.save(file, np.asarray(wav, dtype=np.float32))
    with open(segments_tmp, "w", encoding="utf-8") as file:
        json.dump([(int(start), int(end)) for start, end, _ in segments], file)
    with open(metadata_tmp, "w", encoding="utf-8") as file:
        json.dump(_cache_metadata(config), file, ensure_ascii=False, indent=2, sort_keys=True)

    os.replace(audio_tmp, audio_path)
    os.replace(segments_tmp, segments_path)
    os.replace(metadata_tmp, metadata_path)
    return cache_dir