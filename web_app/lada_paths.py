from __future__ import annotations

from pathlib import Path

from config import WebSettings
from web_app.settings import get_runtime_paths


def preferred_lada_output_dir(input_path: Path, job_id: str) -> Path:
    return input_path.parent / f"{input_path.stem}-lada-{job_id}"


def fallback_lada_output_dir(settings: WebSettings, job_id: str) -> Path:
    return get_runtime_paths(settings)["lada_output_dir"] / job_id


def prepare_lada_output_dir(settings: WebSettings, input_path: Path, job_id: str) -> tuple[Path, OSError | None]:
    preferred = preferred_lada_output_dir(input_path, job_id)
    try:
        preferred.mkdir(parents=True, exist_ok=True)
        return preferred, None
    except OSError as exc:
        fallback = fallback_lada_output_dir(settings, job_id)
        fallback.mkdir(parents=True, exist_ok=True)
        return fallback, exc


def lada_output_candidates(settings: WebSettings, input_file: str | None, job_id: str) -> list[Path]:
    candidates: list[Path] = []
    if input_file:
        input_path = Path(input_file).expanduser()
        if input_path.name:
            candidates.append(preferred_lada_output_dir(input_path, job_id))
    candidates.append(fallback_lada_output_dir(settings, job_id))

    unique: list[Path] = []
    seen: set[str] = set()
    for candidate in candidates:
        resolved = str(candidate.resolve())
        if resolved in seen:
            continue
        seen.add(resolved)
        unique.append(candidate)
    return unique