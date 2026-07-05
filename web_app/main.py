import asyncio
import json
import re
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse

from config import Config
from web_app.jobs import (
    ArtifactNotFoundError,
    InvalidArtifactPathError,
    JobConflictError,
    JobManager,
    JobNotFoundError,
    TERMINAL_STATUSES,
)
from web_app.process_priority import set_process_priority
from web_app.path_utils import normalize_input_path
from web_app.runners.asr import run_asr_web_job
from web_app.runners.lada import run_lada_web_job
from web_app.runners.translate import build_resume_request_from_state, load_translation_state, run_translate_web_job
from web_app.schemas import (
    AsrJobRequest,
    CancelJobResponse,
    JobArtifact,
    JobInput,
    JobListResponse,
    JobRecord,
    LadaJobRequest,
    MockJobRequest,
    PathInspectRequest,
    PathInspectResponse,
    TranslateHandoffConfig,
    TranslateJobRequest,
    UploadResponse,
)
from web_app.settings import get_deepseek_api_key, get_runtime_paths, get_web_settings
from web_app.warmup import WarmupManager


settings = get_web_settings()
set_process_priority(settings.process_priority)
job_manager = JobManager(settings)
warmup_manager = WarmupManager(settings)

app = FastAPI(title="Qwen3-ASR Web Console", version="0.1.0")

_MEDIA_SUFFIXES = {
    ".3gp",
    ".aac",
    ".aif",
    ".aiff",
    ".alac",
    ".amr",
    ".avi",
    ".flac",
    ".m4a",
    ".m4v",
    ".mkv",
    ".mov",
    ".mp3",
    ".mp4",
    ".mpeg",
    ".mpg",
    ".ogg",
    ".opus",
    ".ts",
    ".wav",
    ".webm",
    ".wma",
    ".wmv",
}


def _cors_origins(value: str) -> list[str]:
    return [origin.strip() for origin in value.split(",") if origin.strip()]


app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins(settings.cors_origins),
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
async def startup_warmup() -> None:
    warmup_manager.start_background()


def _get_job_or_404(job_id: str):
    try:
        return job_manager.get_job(job_id)
    except JobNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Job not found") from exc


def _is_remote_input(input_file: str) -> bool:
    lowered = input_file.lower()
    return lowered.startswith("http://") or lowered.startswith("https://")


def _safe_upload_name(filename: str | None) -> str:
    original = Path(filename or "upload.bin").name
    stem = Path(original).stem or "upload"
    suffix = Path(original).suffix[:16]
    safe_stem = re.sub(r"[^A-Za-z0-9._@-]+", "_", stem).strip("._ ") or "upload"
    return f"{safe_stem[:80]}-{uuid4().hex[:8]}{suffix}"


def _get_job_artifact(job_id: str, artifact_name: str) -> JobArtifact:
    job = job_manager.get_job(job_id)
    for artifact in job.artifacts:
        if artifact.name == artifact_name:
            return artifact
    raise ArtifactNotFoundError(f"Artifact not found: {artifact_name}")


def _validate_translate_srt_source(path: Path, artifact_kind: str | None = None) -> None:
    if artifact_kind is not None and artifact_kind.lower() != "srt":
        raise HTTPException(status_code=400, detail="Source artifact must be an SRT subtitle")
    if path.suffix.lower() != ".srt":
        raise HTTPException(status_code=400, detail="Input subtitle file must use the .srt extension")
    try:
        size_bytes = path.stat().st_size
    except OSError as exc:
        raise HTTPException(status_code=400, detail=f"Input SRT file is not readable: {exc}") from exc
    max_bytes = settings.deepseek_max_srt_size_mb * 1024 * 1024
    if size_bytes > max_bytes:
        raise HTTPException(status_code=413, detail="Input SRT file exceeds DEEPSEEK_MAX_SRT_SIZE_MB")


def _validate_translate_srt_text(text: str | None) -> None:
    if not text:
        return
    max_bytes = settings.deepseek_max_srt_size_mb * 1024 * 1024
    if len(text.encode("utf-8")) > max_bytes:
        raise HTTPException(status_code=413, detail="Input SRT text exceeds DEEPSEEK_MAX_SRT_SIZE_MB")


def _find_translation_state_path(job_id: str) -> Path:
    job = job_manager.get_job(job_id)
    if job.type != "translate":
        raise HTTPException(status_code=400, detail="Only translate jobs can be resumed")
    output_dir = (job_manager.artifact_dir / job_id).resolve()
    candidates = sorted(output_dir.glob("*.translate_state.json"), key=lambda path: path.stat().st_mtime, reverse=True)
    if not candidates:
        raise HTTPException(status_code=404, detail="Translation checkpoint not found")
    return candidates[0]


def _lineage_for_child(parent: JobRecord) -> dict[str, str]:
    root_job_id = str(parent.metadata.get("pipeline_root_job_id") or parent.job_id)
    return {"pipeline_root_job_id": root_job_id, "parent_job_id": parent.job_id}


def _metadata_with_lineage(metadata: dict, lineage: dict[str, str] | None) -> dict:
    if not lineage:
        return metadata
    return {**metadata, **lineage}


def _apply_translate_runtime_defaults(request: TranslateJobRequest) -> TranslateJobRequest:
    runtime_defaults = {}
    if "model" not in request.model_fields_set:
        runtime_defaults["model"] = settings.deepseek_model
    if "reasoning_effort" not in request.model_fields_set:
        runtime_defaults["reasoning_effort"] = settings.deepseek_reasoning_effort
    if "max_tokens" not in request.model_fields_set:
        runtime_defaults["max_tokens"] = settings.deepseek_max_tokens
    if "chunk_chars" not in request.model_fields_set:
        runtime_defaults["chunk_chars"] = settings.deepseek_chunk_chars
    if "max_blocks_per_chunk" not in request.model_fields_set:
        runtime_defaults["max_blocks_per_chunk"] = settings.deepseek_max_blocks_per_chunk
    if "debug_io" not in request.model_fields_set:
        runtime_defaults["debug_io"] = settings.deepseek_debug_io
    if "target_language" not in request.model_fields_set:
        runtime_defaults["target_language"] = settings.deepseek_target_language
    if runtime_defaults:
        return TranslateJobRequest.model_validate({**request.model_dump(), **runtime_defaults})
    return request


def _translate_request_from_handoff(config: TranslateHandoffConfig) -> TranslateJobRequest:
    payload = {
        "artifact_name": (config.artifact_name or "subtitle").strip() or "subtitle",
        "target_language": config.target_language or settings.deepseek_target_language,
        "model": config.model or settings.deepseek_model,
        "reasoning_effort": config.reasoning_effort or settings.deepseek_reasoning_effort,
        "max_tokens": config.max_tokens or settings.deepseek_max_tokens,
        "chunk_chars": config.chunk_chars or settings.deepseek_chunk_chars,
        "max_blocks_per_chunk": config.max_blocks_per_chunk or settings.deepseek_max_blocks_per_chunk,
        "debug_io": settings.deepseek_debug_io if config.debug_io is None else config.debug_io,
        "prompt_template": config.prompt_template,
    }
    source_job_id = (config.source_job_id or "").strip()
    input_file = normalize_input_path(config.input_file or "")
    if source_job_id:
        payload["source_job_id"] = source_job_id
    elif input_file:
        payload["input_file"] = input_file
    else:
        raise HTTPException(status_code=422, detail="LADA 完成后翻译需要选择历史 ASR 字幕或填写 SRT 路径")
    return TranslateJobRequest.model_validate(payload)


async def _create_asr_job_record(
    request: AsrJobRequest,
    *,
    allow_queue: bool = False,
    lineage: dict[str, str] | None = None,
) -> JobRecord:
    if warmup_manager.is_blocking():
        raise HTTPException(status_code=409, detail="ASR/VAD 模型预热中，请等待预热完成后再启动任务")

    input_file = normalize_input_path(request.input_file)
    if not input_file:
        raise HTTPException(status_code=422, detail="input_file is required")
    lada_handoff = request.handoff.lada
    if lada_handoff.enabled:
        if _is_remote_input(input_file):
            raise HTTPException(status_code=422, detail="ASR 自动转交 LADA 需要本机或 UNC/NAS 路径，远程 URL 不支持 LADA")
        if lada_handoff.translate.enabled and not request.save_srt:
            raise HTTPException(status_code=422, detail="LADA 后翻译需要 ASR 输出 SRT，请开启 SRT")
    if not _is_remote_input(input_file) and not Path(input_file).expanduser().is_file():
        raise HTTPException(status_code=400, detail="Input file does not exist")

    request = request.model_copy(update={"input_file": input_file})
    metadata = _metadata_with_lineage(request.model_dump(mode="json"), lineage)
    try:
        job = await job_manager.create_job(
            "asr",
            JobInput(source_kind="url" if _is_remote_input(input_file) else "path", path=input_file),
            metadata=metadata,
            allow_queue=allow_queue,
        )
    except JobConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    config = request.to_config(output_dir=str(job_manager.artifact_dir / job.job_id))
    runtime_paths = get_runtime_paths(settings)
    config.cache_dir = str(runtime_paths["asr_cache_dir"])
    config.device_map = settings.asr_device_map
    config.dtype = settings.asr_dtype

    async def runner(reporter, cancel_token) -> None:
        await run_asr_web_job(config, reporter, cancel_token)

    await job_manager.submit_job(job.job_id, runner)
    return job.model_copy(deep=True)


async def _create_lada_job_record(
    request: LadaJobRequest,
    *,
    allow_queue: bool = False,
    lineage: dict[str, str] | None = None,
) -> JobRecord:
    input_file = normalize_input_path(request.input_file)
    if not input_file:
        raise HTTPException(status_code=422, detail="input_file is required")
    if _is_remote_input(input_file):
        raise HTTPException(status_code=400, detail="LADA 任务目前只支持本机文件路径或上传文件")
    input_path = Path(input_file).expanduser()
    if not input_path.is_file():
        raise HTTPException(status_code=400, detail="Input file does not exist")

    translate_handoff = request.handoff.translate
    if translate_handoff.enabled:
        source_job_id = (translate_handoff.source_job_id or "").strip()
        source_file = normalize_input_path(translate_handoff.input_file or "")
        if not source_job_id and not source_file:
            raise HTTPException(status_code=422, detail="LADA 完成后翻译需要选择历史 ASR 字幕或填写 SRT 路径")
        translate_handoff = translate_handoff.model_copy(update={"input_file": source_file or None})
        request = request.model_copy(update={"handoff": request.handoff.model_copy(update={"translate": translate_handoff})})

    request = request.model_copy(update={"input_file": str(input_path.resolve())})
    metadata = _metadata_with_lineage(request.model_dump(mode="json"), lineage)
    try:
        job = await job_manager.create_job(
            "lada",
            JobInput(source_kind="path", path=str(input_path.resolve())),
            metadata=metadata,
            allow_queue=allow_queue,
        )
    except JobConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    async def runner(reporter, cancel_token) -> None:
        await run_lada_web_job(request, settings, reporter, cancel_token)

    await job_manager.submit_job(job.job_id, runner)
    return job.model_copy(deep=True)


async def _create_translate_job_record(
    request: TranslateJobRequest,
    *,
    allow_queue: bool = False,
    lineage: dict[str, str] | None = None,
) -> JobRecord:
    if not get_deepseek_api_key(settings):
        raise HTTPException(status_code=400, detail=f"DeepSeek API key 未配置，请设置 {settings.deepseek_api_key_env} 或 API_KEY")

    request = _apply_translate_runtime_defaults(request)
    source_kind = "text"
    source_path: str | None = None
    source_job_id = (request.source_job_id or "").strip() or None
    artifact_name = (request.artifact_name or "subtitle").strip() or "subtitle"
    runner_request = request

    if source_job_id:
        try:
            artifact = _get_job_artifact(source_job_id, artifact_name)
            artifact_path = job_manager.get_artifact_path(source_job_id, artifact_name)
        except (ArtifactNotFoundError, InvalidArtifactPathError, JobNotFoundError) as exc:
            raise HTTPException(status_code=404, detail="Source artifact not found") from exc
        _validate_translate_srt_source(artifact_path, artifact.kind)
        source_kind = "artifact"
        source_path = str(artifact_path)
        runner_request = request.model_copy(
            update={"input_text": None, "input_file": source_path, "source_job_id": None, "artifact_name": artifact_name}
        )
    elif request.input_file:
        input_file = normalize_input_path(request.input_file)
        input_path = Path(input_file).expanduser()
        if not input_path.is_file():
            raise HTTPException(status_code=400, detail="Input SRT file does not exist")
        input_path = input_path.resolve()
        _validate_translate_srt_source(input_path)
        source_kind = "path"
        source_path = str(input_path)
        runner_request = request.model_copy(update={"input_file": source_path})
    else:
        _validate_translate_srt_text(request.input_text)

    metadata = _metadata_with_lineage(
        {
            "source_kind": source_kind,
            "source_job_id": source_job_id,
            "artifact_name": artifact_name if source_kind == "artifact" else None,
            "input_file": source_path if source_kind in {"artifact", "path"} else None,
            "input_chars": len(request.input_text or "") if source_kind == "text" else None,
            "target_language": request.target_language,
            "model": request.model,
            "reasoning_effort": request.reasoning_effort,
            "max_tokens": request.max_tokens,
            "chunk_chars": request.chunk_chars,
            "max_blocks_per_chunk": request.max_blocks_per_chunk,
            "debug_io": request.debug_io,
            "custom_prompt": bool((request.prompt_template or "").strip()),
        },
        lineage,
    )
    try:
        job = await job_manager.create_job(
            "translate",
            JobInput(source_kind=source_kind, path=source_path),
            metadata=metadata,
            allow_queue=allow_queue,
        )
    except JobConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    async def runner(reporter, cancel_token) -> None:
        await run_translate_web_job(runner_request, settings, reporter, cancel_token)

    await job_manager.submit_job(job.job_id, runner)
    return job.model_copy(deep=True)


async def _handle_job_success(job: JobRecord) -> None:
    if job.metadata.get("handoff_child_job_id") or job.metadata.get("handoff_status") == "failed":
        return

    handoff = job.metadata.get("handoff")
    if not isinstance(handoff, dict):
        return

    if job.type == "asr":
        lada_config = handoff.get("lada") if isinstance(handoff.get("lada"), dict) else {}
        if not lada_config.get("enabled"):
            return
        translate_config = lada_config.get("translate") if isinstance(lada_config.get("translate"), dict) else {}
        if translate_config.get("enabled"):
            translate_config = {
                **translate_config,
                "source_job_id": job.job_id,
                "input_file": None,
                "artifact_name": translate_config.get("artifact_name") or "subtitle",
            }
        child_request = LadaJobRequest.model_validate(
            {
                "input_file": job.metadata.get("input_file") or job.input.path,
                "encoding_preset": lada_config.get("encoding_preset"),
                "device": lada_config.get("device"),
                "fp16": lada_config.get("fp16"),
                "max_clip_length": lada_config.get("max_clip_length"),
                "handoff": {"translate": translate_config},
            }
        )
        child = await _create_lada_job_record(child_request, allow_queue=True, lineage=_lineage_for_child(job))
        await job_manager.record_handoff(job.job_id, child)
        return

    if job.type == "lada":
        translate_config = handoff.get("translate") if isinstance(handoff.get("translate"), dict) else {}
        if not translate_config.get("enabled"):
            return
        child_request = _translate_request_from_handoff(TranslateHandoffConfig.model_validate(translate_config))
        child = await _create_translate_job_record(child_request, allow_queue=True, lineage=_lineage_for_child(job))
        await job_manager.record_handoff(job.job_id, child)


job_manager.set_completion_callback(_handle_job_success)


@app.get("/api/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/warmup")
async def warmup_status() -> dict:
    return warmup_manager.snapshot()


@app.get("/api/config/defaults")
async def config_defaults() -> dict:
    return {
        "asr": asdict(Config()),
        "web": asdict(settings),
        "warmup": warmup_manager.snapshot(),
        "runtime_paths": {name: str(path) for name, path in get_runtime_paths(settings).items()},
    }


@app.post("/api/uploads", response_model=UploadResponse)
async def upload_file(file: UploadFile = File(...)) -> UploadResponse:
    upload_dir = get_runtime_paths(settings)["upload_dir"]
    upload_dir.mkdir(parents=True, exist_ok=True)
    date_dir = upload_dir / datetime.now(UTC).strftime("%Y%m%d")
    date_dir.mkdir(parents=True, exist_ok=True)
    output_path = date_dir / _safe_upload_name(file.filename)
    max_bytes = settings.max_upload_size_mb * 1024 * 1024
    size_bytes = 0

    try:
        with open(output_path, "wb") as output:
            while chunk := await file.read(1024 * 1024):
                size_bytes += len(chunk)
                if size_bytes > max_bytes:
                    output.close()
                    output_path.unlink(missing_ok=True)
                    raise HTTPException(status_code=413, detail="Uploaded file exceeds WEB_MAX_UPLOAD_SIZE_MB")
                output.write(chunk)
    except HTTPException:
        raise
    except OSError as exc:
        output_path.unlink(missing_ok=True)
        raise HTTPException(status_code=507, detail=f"Failed to save upload: {exc}") from exc
    finally:
        await file.close()

    return UploadResponse(path=str(output_path.resolve()), filename=Path(file.filename or output_path.name).name, size_bytes=size_bytes)


@app.post("/api/paths/inspect", response_model=PathInspectResponse)
async def inspect_path(request: PathInspectRequest) -> PathInspectResponse:
    normalized = normalize_input_path(request.path)
    if _is_remote_input(normalized):
        return PathInspectResponse(path=normalized, exists=False, is_file=False, error="远程 URL 无法在本机预检查")

    path = Path(normalized).expanduser()
    exists = path.exists()
    is_file = path.is_file()
    suffix = path.suffix.lower() if exists else None
    error = None
    if not exists:
        error = "路径不存在"
    elif not is_file:
        error = "路径不是文件"
    elif request.kind == "srt" and suffix != ".srt":
        error = "字幕文件必须使用 .srt 扩展名"
    elif request.kind == "media" and suffix not in _MEDIA_SUFFIXES:
        error = "媒体文件扩展名不支持"
    return PathInspectResponse(path=str(path.resolve()) if exists else normalized, exists=exists, is_file=is_file, suffix=suffix, error=error)


@app.get("/api/jobs", response_model=JobListResponse)
async def list_jobs() -> JobListResponse:
    return JobListResponse(jobs=job_manager.list_jobs())


@app.get("/api/jobs/{job_id}")
async def get_job(job_id: str):
    return _get_job_or_404(job_id)


@app.post("/api/jobs/{job_id}/cancel", response_model=CancelJobResponse)
async def cancel_job(job_id: str) -> CancelJobResponse:
    _get_job_or_404(job_id)
    return CancelJobResponse(job=await job_manager.request_cancel(job_id))


@app.post("/api/jobs/mock")
async def create_mock_job(request: MockJobRequest):
    try:
        job = await job_manager.create_job(
            "mock",
            JobInput(source_kind="system", path=None),
            metadata=request.model_dump(mode="json"),
        )
    except JobConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    response_job = job.model_copy(deep=True)

    async def runner(reporter, cancel_token) -> None:
        await reporter.stage("mock_running", "Mock 任务开始。")
        await reporter.log("Mock 任务用于验证阶段 1 的状态流、SSE 和取消语义。")
        step_duration = request.duration_seconds / request.steps
        started = asyncio.get_running_loop().time()
        for step in range(1, request.steps + 1):
            if cancel_token.is_canceled:
                await reporter.log("Mock 任务检测到取消请求，准备退出。")
                return
            await asyncio.sleep(step_duration)
            elapsed = asyncio.get_running_loop().time() - started
            remaining = max(0.0, request.duration_seconds - elapsed)
            await reporter.progress(
                done=step,
                total=request.steps,
                elapsed_seconds=elapsed,
                eta_seconds=round(remaining, 2),
                message=f"Mock 进度 {step}/{request.steps}",
            )
        artifact_dir = job_manager.artifact_dir / job.job_id
        artifact_dir.mkdir(parents=True, exist_ok=True)
        artifact_path = artifact_dir / "mock.txt"
        artifact_path.write_text("mock job completed\n", encoding="utf-8")
        await reporter.artifact(name="mock-output", kind="txt", path=artifact_path)

    await job_manager.submit_job(job.job_id, runner)
    return response_job


@app.post("/api/jobs/asr")
async def create_asr_job(request: AsrJobRequest):
    return await _create_asr_job_record(request)


@app.post("/api/jobs/lada")
async def create_lada_job(request: LadaJobRequest):
    return await _create_lada_job_record(request)


@app.post("/api/jobs/translate")
async def create_translate_job(request: TranslateJobRequest):
    return await _create_translate_job_record(request)


@app.post("/api/jobs/translate/{job_id}/resume")
async def resume_translate_job(job_id: str):
    if not get_deepseek_api_key(settings):
        raise HTTPException(status_code=400, detail=f"DeepSeek API key 未配置，请设置 {settings.deepseek_api_key_env} 或 API_KEY")

    previous_job = _get_job_or_404(job_id)
    if previous_job.type != "translate":
        raise HTTPException(status_code=400, detail="Only translate jobs can be resumed")
    if previous_job.status == "succeeded":
        raise HTTPException(status_code=400, detail="Succeeded translate jobs do not need resume")

    state_path = _find_translation_state_path(job_id)
    try:
        state = load_translation_state(state_path)
        runner_request = build_resume_request_from_state(state)
    except (OSError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=f"Translation checkpoint is not resumable: {exc}") from exc

    metadata = {
        "source_kind": "resume",
        "resume_from_job_id": job_id,
        "resume_state_path": str(state_path),
        "target_language": runner_request.target_language,
        "model": runner_request.model,
        "reasoning_effort": runner_request.reasoning_effort,
        "max_tokens": runner_request.max_tokens,
        "chunk_chars": runner_request.chunk_chars,
        "max_blocks_per_chunk": runner_request.max_blocks_per_chunk,
        "debug_io": runner_request.debug_io,
        "custom_prompt": bool((runner_request.prompt_template or "").strip()),
    }
    try:
        job = await job_manager.create_job(
            "translate",
            JobInput(source_kind="resume", path=str(state_path)),
            metadata=metadata,
        )
    except JobConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    response_job = job.model_copy(deep=True)

    async def runner(reporter, cancel_token) -> None:
        await run_translate_web_job(runner_request, settings, reporter, cancel_token, resume_state_path=state_path)

    await job_manager.submit_job(job.job_id, runner)
    return response_job


@app.get("/api/jobs/{job_id}/events")
async def job_events(job_id: str):
    _get_job_or_404(job_id)
    queue = job_manager.subscribe(job_id)

    async def event_stream():
        try:
            job = job_manager.get_job(job_id)
            initial = {
                "event": "status",
                "job_id": job.job_id,
                "stage": job.stage,
                "status": job.status,
                "progress": job.progress.model_dump(mode="json"),
                "timestamp": job.updated_at,
            }
            yield f"event: status\ndata: {json.dumps(initial, ensure_ascii=False)}\n\n"
            if job.status in TERMINAL_STATUSES:
                return
            while True:
                event = await queue.get()
                yield (
                    f"event: {event.event}\n"
                    f"data: {json.dumps(event.model_dump(mode='json'), ensure_ascii=False)}\n\n"
                )
                if event.status in TERMINAL_STATUSES:
                    while True:
                        try:
                            trailing_event = await asyncio.wait_for(queue.get(), timeout=1.0)
                        except asyncio.TimeoutError:
                            break
                        yield (
                            f"event: {trailing_event.event}\n"
                            f"data: {json.dumps(trailing_event.model_dump(mode='json'), ensure_ascii=False)}\n\n"
                        )
                    break
        finally:
            job_manager.unsubscribe(job_id, queue)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/api/artifacts/{job_id}/{artifact_name}")
async def get_artifact(job_id: str, artifact_name: str):
    _get_job_or_404(job_id)
    try:
        artifact_path = job_manager.get_artifact_path(job_id, artifact_name)
    except (ArtifactNotFoundError, InvalidArtifactPathError) as exc:
        raise HTTPException(status_code=404, detail="Artifact not found") from exc
    return FileResponse(artifact_path)
