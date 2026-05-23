import asyncio
import json
import os
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from config import WebSettings
from web_app.schemas import JobArtifact, JobEvent, JobInput, JobProgress, JobRecord, JobStatus, JobType
from web_app.settings import ensure_runtime_dirs, get_runtime_paths


TERMINAL_STATUSES: set[JobStatus] = {"succeeded", "failed", "canceled", "interrupted"}
ACTIVE_STATUSES: set[JobStatus] = {"queued", "running"}
Runner = Callable[["JobReporter", "CancelToken"], Awaitable[None]]


class JobConflictError(RuntimeError):
    pass


class JobNotFoundError(KeyError):
    pass


class ArtifactNotFoundError(KeyError):
    pass


class InvalidArtifactPathError(ValueError):
    pass


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


class CancelToken:
    def __init__(self) -> None:
        self._event = asyncio.Event()

    def cancel(self) -> None:
        self._event.set()

    @property
    def is_canceled(self) -> bool:
        return self._event.is_set()

    async def wait(self) -> None:
        await self._event.wait()


class JobReporter:
    def __init__(self, manager: "JobManager", job_id: str) -> None:
        self._manager = manager
        self.job_id = job_id

    async def stage(self, stage: str, message: str | None = None) -> JobRecord:
        return await self._manager.update_job(self.job_id, stage=stage, event="status", message=message)

    async def log(self, message: str) -> JobRecord:
        return await self._manager.append_log(self.job_id, message)

    async def progress(
        self,
        *,
        done: int,
        total: int,
        percent: float | None = None,
        elapsed_seconds: float = 0.0,
        eta_seconds: float | None = None,
        message: str | None = None,
    ) -> JobRecord:
        active_percent = percent if percent is not None else (done / total * 100 if total else 0.0)
        progress = JobProgress(
            percent=round(max(0.0, min(100.0, active_percent)), 2),
            done=done,
            total=total,
            elapsed_seconds=round(max(0.0, elapsed_seconds), 2),
            eta_seconds=eta_seconds,
        )
        return await self._manager.update_job(
            self.job_id,
            progress=progress,
            event="progress",
            message=message,
        )

    async def artifact(self, *, name: str, kind: str, path: str | Path) -> JobRecord:
        artifact = JobArtifact(name=name, kind=kind, path=str(Path(path).resolve()))
        return await self._manager.add_artifact(self.job_id, artifact)


class JobManager:
    def __init__(self, settings: WebSettings) -> None:
        self.settings = settings
        ensure_runtime_dirs(settings)
        paths = get_runtime_paths(settings)
        self.job_dir = paths["job_dir"]
        self.artifact_dir = paths["artifact_dir"]
        self.logs_dir = self.job_dir / "logs"
        self.logs_dir.mkdir(parents=True, exist_ok=True)
        self.history_path = self.job_dir / "history.json"
        self._jobs: dict[str, JobRecord] = {}
        self._tokens: dict[str, CancelToken] = {}
        self._subscribers: dict[str, set[asyncio.Queue[JobEvent]]] = {}
        self._lock = asyncio.Lock()
        self._load_history()

    def _load_history(self) -> None:
        if not self.history_path.is_file():
            return
        try:
            with open(self.history_path, "r", encoding="utf-8") as file:
                payload = json.load(file)
        except (OSError, json.JSONDecodeError):
            return

        changed = False
        for item in payload if isinstance(payload, list) else []:
            try:
                record = JobRecord.model_validate(item)
            except ValueError:
                continue
            if record.status in ACTIVE_STATUSES:
                record.status = "interrupted"
                record.stage = "interrupted"
                record.error = "服务重启前任务未完成，已标记为 interrupted。"
                record.updated_at = utc_now()
                changed = True
            self._jobs[record.job_id] = record
        if changed:
            self._persist_history()

    def _persist_history(self) -> None:
        self.job_dir.mkdir(parents=True, exist_ok=True)
        payload = [job.model_dump(mode="json") for job in self.list_jobs()]
        tmp_path = self.history_path.with_suffix(".json.tmp")
        with open(tmp_path, "w", encoding="utf-8") as file:
            json.dump(payload, file, ensure_ascii=False, indent=2)
        os.replace(tmp_path, self.history_path)

    def list_jobs(self) -> list[JobRecord]:
        return sorted(self._jobs.values(), key=lambda job: job.created_at, reverse=True)

    def get_job(self, job_id: str) -> JobRecord:
        job = self._jobs.get(job_id)
        if job is None:
            raise JobNotFoundError(job_id)
        return job

    def has_active_job(self) -> bool:
        return any(job.status in ACTIVE_STATUSES for job in self._jobs.values())

    async def create_job(
        self,
        job_type: JobType,
        job_input: JobInput | None = None,
        metadata: dict | None = None,
    ) -> JobRecord:
        async with self._lock:
            if self.has_active_job():
                raise JobConflictError("已有任务正在运行，第一版 Web 控制台同一时刻只允许一个任务。")
            created_at = utc_now()
            job_id = f"{datetime.now(UTC).strftime('%Y%m%d-%H%M%S')}-{job_type}-{uuid4().hex[:8]}"
            log_path = self.logs_dir / f"{job_id}.log"
            record = JobRecord(
                job_id=job_id,
                type=job_type,
                status="queued",
                stage="queued",
                input=job_input or JobInput(),
                metadata=metadata or {},
                log_path=str(log_path),
                created_at=created_at,
                updated_at=created_at,
            )
            self._jobs[job_id] = record
            self._tokens[job_id] = CancelToken()
            self._persist_history()
            await self._publish(record, "status", message="任务已创建。")
            return record

    async def run_job(self, job_id: str, runner: Runner) -> None:
        reporter = JobReporter(self, job_id)
        token = self._tokens[job_id]
        await self.update_job(job_id, status="running", stage="running", event="status", message="任务开始运行。")
        try:
            await runner(reporter, token)
        except Exception as exc:
            await self.update_job(
                job_id,
                status="failed",
                stage="failed",
                error=str(exc),
                event="error",
                message=str(exc),
            )
            return
        current_job = self.get_job(job_id)
        if current_job.status in TERMINAL_STATUSES:
            return
        if token.is_canceled:
            await self.update_job(job_id, status="canceled", stage="canceled", event="status", message="任务已取消。")
        else:
            await self.update_job(
                job_id,
                status="succeeded",
                stage="succeeded",
                progress=JobProgress(percent=100.0, done=1, total=1),
                event="status",
                message="任务已完成。",
            )

    async def request_cancel(self, job_id: str) -> JobRecord:
        job = self.get_job(job_id)
        token = self._tokens.get(job_id)
        if job.status in TERMINAL_STATUSES:
            return job
        if token is not None:
            token.cancel()
        return await self.update_job(job_id, stage="cancel_requested", event="status", message="取消请求已发送。")

    async def update_job(
        self,
        job_id: str,
        *,
        status: JobStatus | None = None,
        stage: str | None = None,
        progress: JobProgress | None = None,
        error: str | None = None,
        event: str = "status",
        message: str | None = None,
    ) -> JobRecord:
        async with self._lock:
            job = self.get_job(job_id)
            if status is not None:
                job.status = status
            if stage is not None:
                job.stage = stage
            if progress is not None:
                job.progress = progress
            if error is not None:
                job.error = error
            job.updated_at = utc_now()
            self._persist_history()
            await self._publish(job, event, message=message, error=error)
            return job

    async def append_log(self, job_id: str, message: str) -> JobRecord:
        async with self._lock:
            job = self.get_job(job_id)
            timestamped = f"[{utc_now()}] {message}"
            job.logs.append(timestamped)
            if len(job.logs) > self.settings.recent_log_lines:
                job.logs = job.logs[-self.settings.recent_log_lines :]
            if job.log_path:
                with open(job.log_path, "a", encoding="utf-8") as file:
                    file.write(timestamped + "\n")
            job.updated_at = utc_now()
            self._persist_history()
            await self._publish(job, "log", message=message)
            return job

    async def add_artifact(self, job_id: str, artifact: JobArtifact) -> JobRecord:
        async with self._lock:
            job = self.get_job(job_id)
            artifact_path = self._validate_artifact_path(job_id, artifact.path)
            artifact = artifact.model_copy(update={"path": str(artifact_path)})
            job.artifacts = [item for item in job.artifacts if item.name != artifact.name]
            job.artifacts.append(artifact)
            job.updated_at = utc_now()
            self._persist_history()
            await self._publish(job, "artifact", message=f"产物已登记: {artifact.name}", artifact=artifact)
            return job

    def _validate_artifact_path(self, job_id: str, path: str) -> Path:
        artifact_path = Path(path).expanduser().resolve()
        allowed_root = (self.artifact_dir / job_id).resolve()
        try:
            artifact_path.relative_to(allowed_root)
        except ValueError as exc:
            raise InvalidArtifactPathError(
                f"Artifact path must be inside job artifact directory: {allowed_root}"
            ) from exc
        return artifact_path

    def get_artifact_path(self, job_id: str, artifact_name: str) -> Path:
        job = self.get_job(job_id)
        for artifact in job.artifacts:
            if artifact.name == artifact_name:
                path = self._validate_artifact_path(job_id, artifact.path)
                if not path.is_file():
                    break
                return path
        raise ArtifactNotFoundError(f"Artifact not found: {artifact_name}")

    def subscribe(self, job_id: str) -> asyncio.Queue[JobEvent]:
        self.get_job(job_id)
        queue: asyncio.Queue[JobEvent] = asyncio.Queue(maxsize=100)
        self._subscribers.setdefault(job_id, set()).add(queue)
        return queue

    def unsubscribe(self, job_id: str, queue: asyncio.Queue[JobEvent]) -> None:
        subscribers = self._subscribers.get(job_id)
        if subscribers is None:
            return
        subscribers.discard(queue)
        if not subscribers:
            self._subscribers.pop(job_id, None)

    async def _publish(
        self,
        job: JobRecord,
        event: str,
        *,
        message: str | None = None,
        artifact: JobArtifact | None = None,
        error: str | None = None,
    ) -> None:
        payload = JobEvent(
            event=event,
            job_id=job.job_id,
            stage=job.stage,
            status=job.status,
            message=message,
            progress=job.progress,
            artifact=artifact,
            error=error,
            timestamp=utc_now(),
        )
        for queue in list(self._subscribers.get(job.job_id, set())):
            try:
                queue.put_nowait(payload)
            except asyncio.QueueFull:
                if job.status not in TERMINAL_STATUSES:
                    continue
                try:
                    queue.get_nowait()
                except asyncio.QueueEmpty:
                    pass
                try:
                    queue.put_nowait(payload)
                except asyncio.QueueFull:
                    pass