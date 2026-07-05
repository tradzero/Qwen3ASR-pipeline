import asyncio
import json
import os
import time
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from config import WebSettings
from web_app.lada_paths import lada_output_candidates
from web_app.schemas import JobArtifact, JobEvent, JobInput, JobProgress, JobRecord, JobStatus, JobType
from web_app.settings import ensure_runtime_dirs, get_runtime_paths


TERMINAL_STATUSES: set[JobStatus] = {"succeeded", "failed", "canceled", "interrupted"}
ACTIVE_STATUSES: set[JobStatus] = {"queued", "running"}
Runner = Callable[["JobReporter", "CancelToken"], Awaitable[None]]
CompletionCallback = Callable[[JobRecord], Awaitable[None]]


def _job_lane(job_type: JobType) -> str:
    return "translate" if job_type == "translate" else "local"


def _job_conflict_message(requested_type: JobType, active_job: JobRecord) -> str:
    if _job_lane(requested_type) == "translate":
        return f"已有翻译任务正在运行 ({active_job.job_id})，请等待翻译完成后再启动新的翻译任务。"
    return (
        f"已有本地资源任务正在运行 ({active_job.type}: {active_job.job_id})，"
        "ASR/LADA 等本地任务同一时刻只允许一个；翻译任务可以并行运行。"
    )


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
        self._runners: dict[str, Runner] = {}
        self._started_job_ids: set[str] = set()
        self._subscribers: dict[str, set[asyncio.Queue[JobEvent]]] = {}
        self._completion_callback: CompletionCallback | None = None
        self._lock = asyncio.Lock()
        self._load_history()

    def set_completion_callback(self, callback: CompletionCallback | None) -> None:
        self._completion_callback = callback

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
        tmp_path = self.history_path.with_name(f"{self.history_path.name}.{os.getpid()}.{uuid4().hex}.tmp")
        with open(tmp_path, "w", encoding="utf-8") as file:
            json.dump(payload, file, ensure_ascii=False, indent=2)
        try:
            for attempt in range(6):
                try:
                    os.replace(tmp_path, self.history_path)
                    return
                except PermissionError:
                    if attempt == 5:
                        raise
                    time.sleep(0.08 * (attempt + 1))
        finally:
            if tmp_path.exists():
                tmp_path.unlink(missing_ok=True)

    def list_jobs(self) -> list[JobRecord]:
        return sorted(self._jobs.values(), key=lambda job: job.created_at, reverse=True)

    def get_job(self, job_id: str) -> JobRecord:
        job = self._jobs.get(job_id)
        if job is None:
            raise JobNotFoundError(job_id)
        return job

    def has_active_job(self) -> bool:
        return any(job.status in ACTIVE_STATUSES for job in self._jobs.values())

    def _conflicting_active_job(self, job_type: JobType) -> JobRecord | None:
        requested_lane = _job_lane(job_type)
        for job in self._jobs.values():
            if _job_lane(job.type) == requested_lane and (job.status in ACTIVE_STATUSES or job.job_id in self._started_job_ids):
                return job
        return None

    def _lane_has_running_or_starting(self, lane: str) -> bool:
        for job in self._jobs.values():
            if _job_lane(job.type) != lane:
                continue
            if job.status == "running" or job.job_id in self._started_job_ids:
                return True
        return False

    async def create_job(
        self,
        job_type: JobType,
        job_input: JobInput | None = None,
        metadata: dict | None = None,
        *,
        allow_queue: bool = False,
    ) -> JobRecord:
        async with self._lock:
            conflict = self._conflicting_active_job(job_type)
            if conflict is not None and not allow_queue:
                raise JobConflictError(_job_conflict_message(job_type, conflict))
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
            message = "任务已创建。"
            if conflict is not None:
                message = f"任务已创建，等待通道空闲后自动运行；当前占用任务: {conflict.job_id}。"
            await self._publish(record, "status", message=message)
            return record

    async def submit_job(self, job_id: str, runner: Runner) -> None:
        async with self._lock:
            self.get_job(job_id)
            self._runners[job_id] = runner
        await self._maybe_start_next(_job_lane(self.get_job(job_id).type))

    async def _maybe_start_next(self, lane: str) -> None:
        async with self._lock:
            if self._lane_has_running_or_starting(lane):
                return
            candidates = sorted(
                (
                    job
                    for job in self._jobs.values()
                    if job.status == "queued"
                    and _job_lane(job.type) == lane
                    and job.job_id in self._runners
                    and job.job_id not in self._started_job_ids
                ),
                key=lambda job: job.created_at,
            )
            if not candidates:
                return
            job = candidates[0]
            runner = self._runners[job.job_id]
            self._started_job_ids.add(job.job_id)
            asyncio.create_task(self.run_job(job.job_id, runner))

    async def run_job(self, job_id: str, runner: Runner) -> None:
        lane = _job_lane(self.get_job(job_id).type)
        reporter = JobReporter(self, job_id)
        token = self._tokens[job_id]
        try:
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
                if self._completion_callback is not None:
                    try:
                        await self._completion_callback(self.get_job(job_id))
                    except Exception as exc:
                        await self.record_handoff_failure(job_id, str(exc))
                current_job = self.get_job(job_id)
                if current_job.status in TERMINAL_STATUSES:
                    return
                if current_job.progress.done == 0 and current_job.progress.total == 0 and current_job.progress.percent == 0.0:
                    progress = JobProgress(percent=100.0, done=1, total=1)
                else:
                    progress = current_job.progress.model_copy(update={"percent": 100.0})
                await self.update_job(
                    job_id,
                    status="succeeded",
                    stage="succeeded",
                    progress=progress,
                    event="status",
                    message="任务已完成。",
                )
        finally:
            async with self._lock:
                self._started_job_ids.discard(job_id)
            await self._maybe_start_next(lane)

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

    async def record_handoff(self, parent_job_id: str, child_job: JobRecord) -> JobRecord:
        async with self._lock:
            parent = self.get_job(parent_job_id)
            parent.metadata["handoff_status"] = "created"
            parent.metadata["handoff_child_job_id"] = child_job.job_id
            parent.metadata["handoff_child_job_type"] = child_job.type
            parent.updated_at = utc_now()
            self._persist_history()
            await self._publish(
                parent,
                "handoff",
                message=f"已自动转交到 {child_job.type} 任务: {child_job.job_id}",
                child_job_id=child_job.job_id,
                child_job_type=child_job.type,
            )
            return parent

    async def record_handoff_failure(self, parent_job_id: str, error: str) -> JobRecord:
        async with self._lock:
            parent = self.get_job(parent_job_id)
            parent.metadata["handoff_status"] = "failed"
            parent.metadata["handoff_error"] = error
            parent.logs.append(f"[{utc_now()}] 自动转交失败: {error}")
            parent.updated_at = utc_now()
            self._persist_history()
            await self._publish(parent, "log", message=f"自动转交失败: {error}")
            return parent

    def _validate_artifact_path(self, job_id: str, path: str) -> Path:
        artifact_path = Path(path).expanduser().resolve()
        job = self.get_job(job_id)
        allowed_roots = [(self.artifact_dir / job_id).resolve()]
        if job.type == "lada":
            input_file = job.metadata.get("input_file")
            if not isinstance(input_file, str):
                input_file = job.input.path
            allowed_roots.extend(candidate.resolve() for candidate in lada_output_candidates(self.settings, input_file, job_id))
        for allowed_root in allowed_roots:
            try:
                artifact_path.relative_to(allowed_root)
                return artifact_path
            except ValueError:
                continue
        raise InvalidArtifactPathError(
            "Artifact path must be inside a job output directory: "
            + ", ".join(str(root) for root in allowed_roots)
        )

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
        child_job_id: str | None = None,
        child_job_type: JobType | None = None,
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
            child_job_id=child_job_id,
            child_job_type=child_job_type,
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
