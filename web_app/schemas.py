from typing import Any, Literal

from pydantic import BaseModel, Field

from config import Config


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


_ASR_DEFAULTS = Config()


class AsrJobRequest(BaseModel):
    input_file: str = Field(min_length=1)
    model: str = _ASR_DEFAULTS.model
    language: str | None = _ASR_DEFAULTS.language
    backend: Literal["auto", "vllm", "transformers"] = _ASR_DEFAULTS.backend
    gpu_memory_utilization: float = Field(default=_ASR_DEFAULTS.gpu_memory_utilization, ge=0.1, le=1.0)
    max_inference_batch_size: int = Field(default=_ASR_DEFAULTS.max_inference_batch_size, ge=1, le=256)
    max_new_tokens: int = Field(default=_ASR_DEFAULTS.max_new_tokens, ge=1)
    segment_duration: int = Field(default=_ASR_DEFAULTS.segment_duration, ge=1)
    max_segment_duration: int = Field(default=_ASR_DEFAULTS.max_segment_duration, ge=1)
    save_srt: bool = _ASR_DEFAULTS.save_srt
    use_cache: bool = _ASR_DEFAULTS.use_cache
    refresh_cache: bool = _ASR_DEFAULTS.refresh_cache
    return_time_stamps: bool = _ASR_DEFAULTS.return_time_stamps
    forced_aligner_model: str | None = _ASR_DEFAULTS.forced_aligner_model
    srt_max_chars: int = Field(default=_ASR_DEFAULTS.srt_max_chars, ge=1)
    srt_max_duration: float = Field(default=_ASR_DEFAULTS.srt_max_duration, gt=0.0)

    def to_config(self, output_dir: str) -> Config:
        return Config(
            input_file=self.input_file,
            output_dir=output_dir,
            save_srt=self.save_srt,
            model=self.model,
            language=self.language,
            backend=self.backend,
            gpu_memory_utilization=self.gpu_memory_utilization,
            max_inference_batch_size=self.max_inference_batch_size,
            max_new_tokens=self.max_new_tokens,
            segment_duration=self.segment_duration,
            max_segment_duration=self.max_segment_duration,
            use_cache=self.use_cache,
            refresh_cache=self.refresh_cache,
            return_time_stamps=self.return_time_stamps,
            forced_aligner_model=self.forced_aligner_model,
            srt_max_chars=self.srt_max_chars,
            srt_max_duration=self.srt_max_duration,
        )


class JobListResponse(BaseModel):
    jobs: list[JobRecord]


class CancelJobResponse(BaseModel):
    job: JobRecord