from __future__ import annotations

import asyncio
import re
import time
from dataclasses import dataclass
from pathlib import Path

import httpx

from config import WebSettings
from web_app.jobs import CancelToken, JobReporter
from web_app.schemas import TranslateJobRequest
from web_app.settings import get_deepseek_api_key


@dataclass
class SubtitleBlock:
    number: str
    timing: str
    text: str


_SEGMENT_RE = re.compile(r"<SEG\s+(\d+)>\s*(.*?)\s*</SEG\s+\1>", re.DOTALL | re.IGNORECASE)


def _normalize_reasoning_effort(value: str) -> str:
    normalized = value.strip().lower()
    if normalized in {"max", "xhigh"}:
        return "max"
    return "high"


def _load_srt_text(request: TranslateJobRequest) -> str:
    if request.input_text and request.input_text.strip():
        return request.input_text
    if request.input_file:
        return Path(request.input_file).read_text(encoding="utf-8-sig", errors="replace")
    raise ValueError("翻译任务缺少 SRT 输入。")


def _parse_srt(text: str) -> list[SubtitleBlock]:
    normalized = text.replace("\r\n", "\n").replace("\r", "\n").lstrip("\ufeff").strip()
    if not normalized:
        raise ValueError("SRT 内容为空。")

    blocks: list[SubtitleBlock] = []
    for raw_block in re.split(r"\n{2,}", normalized):
        lines = raw_block.split("\n")
        if len(lines) < 2 or "-->" not in lines[1]:
            raise ValueError("输入内容不是有效的 SRT 字幕。")
        blocks.append(SubtitleBlock(number=lines[0].strip(), timing=lines[1].strip(), text="\n".join(lines[2:]).strip()))
    if not blocks:
        raise ValueError("未解析到 SRT 字幕块。")
    return blocks


def _chunk_blocks(blocks: list[SubtitleBlock], chunk_chars: int) -> list[list[tuple[int, SubtitleBlock]]]:
    chunks: list[list[tuple[int, SubtitleBlock]]] = []
    current: list[tuple[int, SubtitleBlock]] = []
    current_chars = 0
    for index, block in enumerate(blocks):
        block_chars = max(1, len(block.text))
        if current and current_chars + block_chars > chunk_chars:
            chunks.append(current)
            current = []
            current_chars = 0
        current.append((index, block))
        current_chars += block_chars
    if current:
        chunks.append(current)
    return chunks


def _format_segments(chunk: list[tuple[int, SubtitleBlock]]) -> str:
    return "\n\n".join(f"<SEG {index}>\n{block.text}\n</SEG {index}>" for index, block in chunk)


def _render_prompt(template: str, *, target_language: str, text: str) -> str:
    try:
        return template.format(target_language=target_language, text=text)
    except KeyError as exc:
        raise ValueError(f"prompt 模板缺少占位符或包含未转义花括号: {exc}") from exc


def _parse_translated_segments(content: str, expected_indexes: list[int]) -> dict[int, str]:
    translated = {int(match.group(1)): match.group(2).strip() for match in _SEGMENT_RE.finditer(content)}
    missing = [index for index in expected_indexes if index not in translated]
    if missing:
        raise RuntimeError(f"DeepSeek 返回缺少字幕段: {missing[:5]}")
    return {index: translated[index] for index in expected_indexes}


def _build_srt(blocks: list[SubtitleBlock], translations: dict[int, str]) -> str:
    output_blocks = []
    for index, block in enumerate(blocks):
        text = translations.get(index, block.text)
        output_blocks.append(f"{block.number}\n{block.timing}\n{text}".rstrip())
    return "\n\n".join(output_blocks) + "\n"


def _extract_content(payload: dict) -> str:
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices:
        raise RuntimeError("DeepSeek 响应缺少 choices。")
    message = choices[0].get("message") if isinstance(choices[0], dict) else None
    content = message.get("content") if isinstance(message, dict) else None
    if not isinstance(content, str) or not content.strip():
        raise RuntimeError("DeepSeek 响应缺少 message.content。")
    return content


async def _request_translation(
    client: httpx.AsyncClient,
    *,
    url: str,
    api_key: str,
    request: TranslateJobRequest,
    prompt: str,
) -> str:
    reasoning_effort = _normalize_reasoning_effort(request.reasoning_effort)
    payload = {
        "model": request.model,
        "messages": [{"role": "user", "content": prompt}],
        "thinking": {"type": "enabled"},
        "reasoning_effort": reasoning_effort,
        "max_tokens": request.max_tokens,
        "response_format": {"type": "text"},
        "stream": False,
        "temperature": request.temperature,
    }
    response = await client.post(
        url,
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json", "Accept": "application/json"},
        json=payload,
    )
    if response.status_code >= 400:
        detail = response.text[:500].replace(api_key, "<redacted>")
        raise RuntimeError(f"DeepSeek 请求失败: HTTP {response.status_code} {detail}")
    return _extract_content(response.json())


async def _write_partial(
    reporter: JobReporter,
    partial_path: Path,
    blocks: list[SubtitleBlock],
    translations: dict[int, str],
    *,
    register: bool = False,
) -> None:
    if not translations:
        return
    completed_blocks = blocks[: max(translations.keys()) + 1]
    partial_path.write_text(_build_srt(completed_blocks, translations), encoding="utf-8")
    if register:
        await reporter.artifact(name="translation-partial", kind="srt", path=partial_path)


async def run_translate_web_job(request: TranslateJobRequest, settings: WebSettings, reporter: JobReporter, cancel_token: CancelToken) -> None:
    api_key = get_deepseek_api_key(settings)
    if not api_key:
        raise RuntimeError(f"DeepSeek API key 未配置，请设置 {settings.deepseek_api_key_env} 或 API_KEY。")

    srt_text = _load_srt_text(request)
    blocks = _parse_srt(srt_text)
    chunks = _chunk_blocks(blocks, request.chunk_chars)
    prompt_template = request.prompt_template or settings.deepseek_prompt_template
    output_dir = reporter._manager.artifact_dir / reporter.job_id
    output_dir.mkdir(parents=True, exist_ok=True)
    partial_path = output_dir / "translation.partial.srt"
    output_path = output_dir / "translated.srt"
    url = settings.deepseek_api_base.rstrip("/") + settings.deepseek_chat_completion_path
    translations: dict[int, str] = {}
    started = time.monotonic()

    await reporter.stage("translate_running", "DeepSeek 字幕翻译开始。")
    await reporter.log(
        f"DeepSeek 字幕翻译: model={request.model}, reasoning_effort={_normalize_reasoning_effort(request.reasoning_effort)}, "
        f"blocks={len(blocks)}, chunks={len(chunks)}, target={request.target_language}"
    )

    timeout = httpx.Timeout(180.0, connect=30.0)
    async with httpx.AsyncClient(timeout=timeout) as client:
        for chunk_index, chunk in enumerate(chunks, start=1):
            if cancel_token.is_canceled:
                await reporter.log("翻译任务检测到取消请求，正在保留已完成字幕分块。")
                await _write_partial(reporter, partial_path, blocks, translations, register=True)
                return

            segment_text = _format_segments(chunk)
            prompt = _render_prompt(prompt_template, target_language=request.target_language, text=segment_text)
            expected_indexes = [index for index, _ in chunk]
            try:
                content = await _request_translation(client, url=url, api_key=api_key, request=request, prompt=prompt)
                translations.update(_parse_translated_segments(content, expected_indexes))
            except Exception:
                await _write_partial(reporter, partial_path, blocks, translations, register=True)
                raise

            await _write_partial(reporter, partial_path, blocks, translations)
            elapsed = time.monotonic() - started
            await reporter.progress(
                done=chunk_index,
                total=len(chunks),
                elapsed_seconds=elapsed,
                message=f"字幕翻译分块 {chunk_index}/{len(chunks)} 完成。",
            )
            await asyncio.sleep(0)

    output_path.write_text(_build_srt(blocks, translations), encoding="utf-8")
    elapsed = time.monotonic() - started
    await reporter.progress(done=len(chunks), total=len(chunks), percent=100.0, elapsed_seconds=elapsed, message="字幕翻译完成。")
    await reporter.artifact(name="translated-subtitle", kind="srt", path=output_path)
