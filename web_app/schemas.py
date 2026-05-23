from typing import Any, Literal

from pydantic import BaseModel, Field


JobType = Literal["mock", "asr", "lada", "translate"]
JobStatus = Literal["queued", "running", "succeeded", "failed", "canceled", "interrupted"]
JobEventType = Literal["progress", "log", "artifact", "status", "error"]


class JobProgress(BaseModel):
    percent: float = 0.0
    done: int = 0
    total: int = 0
    elapsed_seconds: float = 0.0
    eta_seconds: float | None = None


class JobInput(BaseModel):
    source_kind: str = "system"
    path: str | None = None


class JobArtifact(BaseModel):
    name: str
    kind: str
    path: str


class JobEvent(BaseModel):
    event: JobEventType
    job_id: str
    stage: str | None = None
    status: JobStatus | None = None
    message: str | None = None
    progress: JobProgress | None = None
    artifact: JobArtifact | None = None
    error: str | None = None
    timestamp: str


class JobRecord(BaseModel):
    job_id: str
    type: JobType
    status: JobStatus
    stage: str
    progress: JobProgress = Field(default_factory=JobProgress)
    input: JobInput = Field(default_factory=JobInput)
    artifacts: list[JobArtifact] = Field(default_factory=list)
    logs: list[str] = Field(default_factory=list)
    error: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    log_path: str | None = None
    created_at: str
    updated_at: str


class MockJobRequest(BaseModel):
    duration_seconds: float = Field(default=3.0, ge=0.1, le=120.0)
    steps: int = Field(default=6, ge=1, le=200)


class JobListResponse(BaseModel):
    jobs: list[JobRecord]


class CancelJobResponse(BaseModel):
    job: JobRecord