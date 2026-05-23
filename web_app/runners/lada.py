from __future__ import annotations

import asyncio
import os
import re
import time
from pathlib import Path

from config import WebSettings
from web_app.jobs import CancelToken, JobReporter
from web_app.schemas import LadaJobRequest
from web_app.settings import get_runtime_paths


_PROGRESS_PATTERNS = [
    re.compile(r"Processing video:\s*(\d+(?:\.\d+)?)%"),
    re.compile(r"正在处理视频[:：]\s*(\d+(?:\.\d+)?)%"),
]
_VIDEO_SUFFIXES = {".mp4", ".mkv", ".mov", ".avi", ".webm"}


def _command(settings: WebSettings, request: LadaJobRequest, output_dir: Path) -> list[str]:
    command = [settings.lada_cli_path, "--input", request.input_file, "--output", str(output_dir)]
    encoding_preset = request.encoding_preset or settings.lada_encoding_preset
    device = request.device or settings.lada_device
    fp16 = request.fp16 if request.fp16 is not None else settings.lada_fp16
    max_clip_length = request.max_clip_length or settings.lada_max_clip_length

    if encoding_preset:
        command.extend(["--encoding-preset", encoding_preset])
    if device:
        command.extend(["--device", device])
    if fp16 is not None:
        command.append("--fp16" if fp16 else "--no-fp16")
    if max_clip_length:
        command.extend(["--max-clip-length", str(max_clip_length)])
    return command


def _parse_progress(text: str) -> float | None:
    for pattern in _PROGRESS_PATTERNS:
        match = pattern.search(text)
        if match is not None:
            return max(0.0, min(100.0, float(match.group(1))))
    return None


def _find_outputs(output_dir: Path) -> list[Path]:
    if not output_dir.is_dir():
        return []
    return sorted(
        (path for path in output_dir.rglob("*") if path.is_file() and path.suffix.lower() in _VIDEO_SUFFIXES),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )


async def _read_stream(stream: asyncio.StreamReader, reporter: JobReporter, started: float, prefix: str) -> None:
    buffer = ""
    while chunk := await stream.read(4096):
        buffer += chunk.decode("utf-8", errors="replace")
        parts = re.split(r"[\r\n]+", buffer)
        buffer = parts.pop() if parts else ""
        for part in parts:
            message = part.strip()
            if not message:
                continue
            await reporter.log(f"[{prefix}] {message}")
            percent = _parse_progress(message)
            if percent is not None:
                await reporter.progress(
                    done=round(percent),
                    total=100,
                    percent=percent,
                    elapsed_seconds=time.monotonic() - started,
                    message=message,
                )
    if buffer.strip():
        message = buffer.strip()
        await reporter.log(f"[{prefix}] {message}")
        percent = _parse_progress(message)
        if percent is not None:
            await reporter.progress(
                done=round(percent),
                total=100,
                percent=percent,
                elapsed_seconds=time.monotonic() - started,
                message=message,
            )


async def _terminate_process(process: asyncio.subprocess.Process, reporter: JobReporter) -> None:
    if process.returncode is not None:
        return
    await reporter.log("取消请求已收到，正在终止 LADA 进程。")
    process.terminate()
    try:
        await asyncio.wait_for(process.wait(), timeout=10)
    except asyncio.TimeoutError:
        await reporter.log("LADA 进程未在 10 秒内退出，执行强制结束。")
        process.kill()
        await process.wait()


async def run_lada_web_job(request: LadaJobRequest, settings: WebSettings, reporter: JobReporter, cancel_token: CancelToken) -> None:
    cli_path = Path(settings.lada_cli_path)
    if not cli_path.is_file():
        raise FileNotFoundError(f"LADA CLI 不存在，请检查 LADA_CLI_PATH: {settings.lada_cli_path}")

    input_path = Path(request.input_file).expanduser()
    if not input_path.is_file():
        raise FileNotFoundError(f"LADA 输入文件不存在: {request.input_file}")

    output_dir = get_runtime_paths(settings)["lada_output_dir"] / reporter.job_id
    output_dir.mkdir(parents=True, exist_ok=True)
    command = _command(settings, request, output_dir)
    env = os.environ.copy()
    env.setdefault("PYTHONUTF8", "1")

    await reporter.stage("lada_starting", "LADA 任务准备开始。")
    await reporter.log("命令: " + " ".join(f'"{item}"' if " " in item else item for item in command))
    started = time.monotonic()
    process = await asyncio.create_subprocess_exec(
        *command,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=str(cli_path.parent),
        env=env,
    )
    await reporter.stage("lada_running", "LADA 进程已启动。")

    stdout_task = asyncio.create_task(_read_stream(process.stdout, reporter, started, "stdout"))
    stderr_task = asyncio.create_task(_read_stream(process.stderr, reporter, started, "stderr"))
    wait_task = asyncio.create_task(process.wait())
    cancel_task = asyncio.create_task(cancel_token.wait())

    done, _ = await asyncio.wait({wait_task, cancel_task}, return_when=asyncio.FIRST_COMPLETED)
    if cancel_task in done and cancel_token.is_canceled:
        await _terminate_process(process, reporter)
        await asyncio.gather(stdout_task, stderr_task, return_exceptions=True)
        return

    return_code = await wait_task
    cancel_task.cancel()
    await asyncio.gather(stdout_task, stderr_task, return_exceptions=True)
    elapsed = time.monotonic() - started

    if return_code != 0:
        raise RuntimeError(f"LADA 进程退出码 {return_code}")

    outputs = _find_outputs(output_dir)
    if not outputs:
        raise RuntimeError(f"LADA 退出成功，但未在输出目录找到视频产物: {output_dir}")

    await reporter.progress(done=100, total=100, percent=100.0, elapsed_seconds=elapsed, message="LADA 处理完成。")
    for index, output in enumerate(outputs, start=1):
        name = "restored-video" if index == 1 else f"restored-video-{index}"
        await reporter.artifact(name=name, kind=output.suffix.lstrip(".") or "video", path=output)
