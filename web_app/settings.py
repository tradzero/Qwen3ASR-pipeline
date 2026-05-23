import os
from functools import lru_cache
from pathlib import Path

from config import WebSettings


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _env_str(name: str, default: str) -> str:
    value = os.environ.get(name)
    return value if value not in (None, "") else default


def _env_optional_str(name: str, default: str | None) -> str | None:
    value = os.environ.get(name)
    if value is None or value == "":
        return default
    return value


def _env_int(name: str, default: int) -> int:
    value = os.environ.get(name)
    if value is None or value == "":
        return default
    return int(value)


def _env_optional_int(name: str, default: int | None) -> int | None:
    value = os.environ.get(name)
    if value is None or value == "":
        return default
    return int(value)


def _env_float(name: str, default: float) -> float:
    value = os.environ.get(name)
    if value is None or value == "":
        return default
    return float(value)


def _env_optional_bool(name: str, default: bool | None) -> bool | None:
    value = os.environ.get(name)
    if value is None or value == "":
        return default
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"Invalid boolean value for {name}: {value}")


@lru_cache(maxsize=1)
def get_web_settings() -> WebSettings:
    defaults = WebSettings()
    return WebSettings(
        host=_env_str("WEB_HOST", defaults.host),
        port=_env_int("WEB_PORT", defaults.port),
        upload_dir=_env_str("WEB_UPLOAD_DIR", defaults.upload_dir),
        job_dir=_env_str("WEB_JOB_DIR", defaults.job_dir),
        artifact_dir=_env_str("WEB_ARTIFACT_DIR", defaults.artifact_dir),
        max_upload_size_mb=_env_int("WEB_MAX_UPLOAD_SIZE_MB", defaults.max_upload_size_mb),
        recent_log_lines=_env_int("WEB_RECENT_LOG_LINES", defaults.recent_log_lines),
        asr_cache_dir=_env_str("WEB_ASR_CACHE_DIR", defaults.asr_cache_dir),
        asr_device_map=_env_str("WEB_ASR_DEVICE_MAP", defaults.asr_device_map),
        asr_dtype=_env_str("WEB_ASR_DTYPE", defaults.asr_dtype),
        lada_cli_path=_env_str("LADA_CLI_PATH", defaults.lada_cli_path),
        lada_output_dir=_env_str("LADA_OUTPUT_DIR", defaults.lada_output_dir),
        lada_encoding_preset=_env_optional_str("LADA_ENCODING_PRESET", defaults.lada_encoding_preset),
        lada_device=_env_optional_str("LADA_DEVICE", defaults.lada_device),
        lada_fp16=_env_optional_bool("LADA_FP16", defaults.lada_fp16),
        lada_max_clip_length=_env_optional_int("LADA_MAX_CLIP_LENGTH", defaults.lada_max_clip_length),
        deepseek_api_base=_env_str("DEEPSEEK_API_BASE", defaults.deepseek_api_base),
        deepseek_chat_completion_path=_env_str(
            "DEEPSEEK_CHAT_COMPLETION_PATH",
            defaults.deepseek_chat_completion_path,
        ),
        deepseek_api_key_env=_env_str("DEEPSEEK_API_KEY_ENV", defaults.deepseek_api_key_env),
        deepseek_model=_env_str("DEEPSEEK_MODEL", defaults.deepseek_model),
        deepseek_temperature=_env_float("DEEPSEEK_TEMPERATURE", defaults.deepseek_temperature),
        deepseek_max_tokens=_env_int("DEEPSEEK_MAX_TOKENS", defaults.deepseek_max_tokens),
        deepseek_target_language=_env_str("DEEPSEEK_TARGET_LANGUAGE", defaults.deepseek_target_language),
        deepseek_chunk_chars=_env_int("DEEPSEEK_CHUNK_CHARS", defaults.deepseek_chunk_chars),
        deepseek_prompt_template=_env_str("DEEPSEEK_PROMPT_TEMPLATE", defaults.deepseek_prompt_template),
    )


def get_deepseek_api_key(settings: WebSettings | None = None) -> str | None:
    active_settings = settings or get_web_settings()
    value = os.environ.get(active_settings.deepseek_api_key_env)
    return value if value not in (None, "") else None


def _project_path(value: str) -> Path:
    path = Path(value).expanduser()
    if path.is_absolute():
        return path
    return PROJECT_ROOT / path


def get_runtime_paths(settings: WebSettings | None = None) -> dict[str, Path]:
    active_settings = settings or get_web_settings()
    return {
        "upload_dir": _project_path(active_settings.upload_dir),
        "job_dir": _project_path(active_settings.job_dir),
        "artifact_dir": _project_path(active_settings.artifact_dir),
        "asr_cache_dir": _project_path(active_settings.asr_cache_dir),
        "lada_output_dir": _project_path(active_settings.lada_output_dir),
    }


def ensure_runtime_dirs(settings: WebSettings | None = None) -> None:
    for path in get_runtime_paths(settings).values():
        path.mkdir(parents=True, exist_ok=True)