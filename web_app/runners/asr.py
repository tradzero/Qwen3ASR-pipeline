from __future__ import annotations

import asyncio
from typing import Any

from config import Config
from web_app.jobs import CancelToken, JobReporter


class ThreadedReporterBridge:
    def __init__(self, loop: asyncio.AbstractEventLoop, reporter: JobReporter) -> None:
        self._loop = loop
        self._reporter = reporter

    def __call__(self, payload: dict[str, Any]) -> None:
        event = str(payload.get("event") or "log")
        stage = payload.get("stage")
        message = payload.get("message")

        if event == "progress":
            self._report_progress(payload)
            return
        if event == "artifact":
            self._report_artifact(payload)
            return
        if event == "status" and stage:
            self._await(self._reporter.stage(str(stage), str(message) if message else None))
            return
        if stage:
            self._await(self._reporter.stage(str(stage), str(message) if message else None))
        if message:
            self._await(self._reporter.log(str(message)))

    def _report_progress(self, payload: dict[str, Any]) -> None:
        progress = payload.get("progress") or {}
        message = payload.get("message")
        self._await(
            self._reporter.progress(
                done=int(progress.get("done") or 0),
                total=int(progress.get("total") or 0),
                percent=float(progress.get("percent") or 0.0),
                elapsed_seconds=float(progress.get("elapsed_seconds") or 0.0),
                eta_seconds=progress.get("eta_seconds"),
                message=str(message) if message else None,
            )
        )
        if message:
            self._await(self._reporter.log(str(message)))

    def _report_artifact(self, payload: dict[str, Any]) -> None:
        artifact = payload.get("artifact") or {}
        name = artifact.get("name")
        kind = artifact.get("kind")
        path = artifact.get("path")
        if not name or not kind or not path:
            return
        self._await(self._reporter.artifact(name=str(name), kind=str(kind), path=str(path)))

    def _await(self, coroutine) -> None:
        future = asyncio.run_coroutine_threadsafe(coroutine, self._loop)
        future.result()


async def run_asr_web_job(config: Config, reporter: JobReporter, cancel_token: CancelToken) -> None:
    from main import AsrJobCanceled, run_asr_job
    from transcribe import TranscriptionCanceled

    artifact_dir = reporter._manager.artifact_dir / reporter.job_id
    artifact_dir.mkdir(parents=True, exist_ok=True)
    config.output_dir = str(artifact_dir)

    await reporter.stage("asr_starting", "ASR 任务准备开始。")
    loop = asyncio.get_running_loop()
    bridge = ThreadedReporterBridge(loop, reporter)

    try:
        await asyncio.to_thread(
            run_asr_job,
            config,
            progress_callback=bridge,
            cancel_token=cancel_token,
            parallel_model_load=False,
        )
    except (AsrJobCanceled, TranscriptionCanceled):
        await reporter.log("ASR 任务检测到取消请求，准备退出。")