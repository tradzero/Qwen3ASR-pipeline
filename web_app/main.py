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
from web_app.runners.asr import run_asr_web_job
from web_app.schemas import (
    AsrJobRequest,
    CancelJobResponse,
    JobInput,
    JobListResponse,
    MockJobRequest,
    UploadResponse,
)
from web_app.settings import get_runtime_paths, get_web_settings


settings = get_web_settings()
job_manager = JobManager(settings)

app = FastAPI(title="Qwen3-ASR Web Console", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://127.0.0.1:5173", "http://localhost:5173"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


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


@app.get("/api/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/config/defaults")
async def config_defaults() -> dict:
    return {
        "asr": asdict(Config()),
        "web": asdict(settings),
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

    asyncio.create_task(job_manager.run_job(job.job_id, runner))
    return response_job


@app.post("/api/jobs/asr")
async def create_asr_job(request: AsrJobRequest):
    input_file = request.input_file.strip()
    if not input_file:
        raise HTTPException(status_code=422, detail="input_file is required")
    if not _is_remote_input(input_file) and not Path(input_file).expanduser().is_file():
        raise HTTPException(status_code=400, detail="Input file does not exist")

    request = request.model_copy(update={"input_file": input_file})
    try:
        job = await job_manager.create_job(
            "asr",
            JobInput(source_kind="url" if _is_remote_input(input_file) else "path", path=input_file),
            metadata=request.model_dump(mode="json"),
        )
    except JobConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    response_job = job.model_copy(deep=True)
    config = request.to_config(output_dir=str(job_manager.artifact_dir / job.job_id))
    runtime_paths = get_runtime_paths(settings)
    config.cache_dir = str(runtime_paths["asr_cache_dir"])
    config.device_map = settings.asr_device_map
    config.dtype = settings.asr_dtype

    async def runner(reporter, cancel_token) -> None:
        await run_asr_web_job(config, reporter, cancel_token)

    asyncio.create_task(job_manager.run_job(job.job_id, runner))
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