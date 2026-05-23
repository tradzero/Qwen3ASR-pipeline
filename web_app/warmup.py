from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Literal

from config import Config, WebSettings
from web_app.settings import get_runtime_paths


WarmupStatus = Literal["disabled", "pending", "running", "ready", "failed"]


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


class WarmupManager:
    def __init__(self, settings: WebSettings) -> None:
        self.settings = settings
        self.status: WarmupStatus = "pending" if settings.warmup_on_startup else "disabled"
        self.stage = "pending" if settings.warmup_on_startup else "disabled"
        self.message = "等待模型预热。" if settings.warmup_on_startup else "启动预热已关闭。"
        self.error: str | None = None
        self.started_at: str | None = None
        self.finished_at: str | None = None
        self._task: asyncio.Task | None = None

    def start_background(self) -> None:
        if not self.settings.warmup_on_startup or self._task is not None:
            return
        self._task = asyncio.create_task(self._run())

    def is_blocking(self) -> bool:
        return self.status in {"pending", "running"}

    def snapshot(self) -> dict:
        return {
            "status": self.status,
            "stage": self.stage,
            "message": self.message,
            "error": self.error,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "settings": {
                "warmup_on_startup": self.settings.warmup_on_startup,
                "warmup_vad": self.settings.warmup_vad,
                "warmup_asr": self.settings.warmup_asr,
            },
        }

    async def _run(self) -> None:
        self.status = "running"
        self.started_at = _utc_now()
        try:
            if self.settings.warmup_vad:
                self.stage = "vad"
                self.message = "正在加载 Silero VAD..."
                await asyncio.to_thread(self._warmup_vad)

            if self.settings.warmup_asr:
                self.stage = "asr"
                self.message = "正在加载 ASR/ForcedAligner 模型..."
                await asyncio.to_thread(self._warmup_asr)

            self.status = "ready"
            self.stage = "ready"
            self.message = "预热完成。"
        except Exception as exc:
            self.status = "failed"
            self.stage = "failed"
            self.error = str(exc)
            self.message = "预热失败。"
        finally:
            self.finished_at = _utc_now()

    @staticmethod
    def _warmup_vad() -> None:
        from vad import warmup_vad

        warmup_vad()

    def _warmup_asr(self) -> None:
        from transcribe import init_model

        runtime_paths = get_runtime_paths(self.settings)
        config = Config()
        config.output_dir = str(runtime_paths["artifact_dir"])
        config.cache_dir = str(runtime_paths["asr_cache_dir"])
        config.device_map = self.settings.asr_device_map
        config.dtype = self.settings.asr_dtype
        init_model(config)