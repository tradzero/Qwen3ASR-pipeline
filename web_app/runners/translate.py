from __future__ import annotations

import asyncio
import json
import re
import time
from dataclasses import dataclass
from pathlib import Path, PureWindowsPath

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
Message = dict[str, str]


class TranslationCanceled(Exception):
    pass


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


def _extract_stream_deltas(payload: dict) -> tuple[str, str]:
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices:
        return "", ""
    choice = choices[0] if isinstance(choices[0], dict) else {}
    delta = choice.get("delta") if isinstance(choice, dict) else None
    if isinstance(delta, dict):
        content = delta.get("content")
        reasoning = delta.get("reasoning_content")
        return (content if isinstance(content, str) else "", reasoning if isinstance(reasoning, str) else "")

    message = choice.get("message") if isinstance(choice, dict) else None
    if isinstance(message, dict):
        content = message.get("content")
        return (content if isinstance(content, str) else "", "")
    return "", ""


def _stream_data_from_line(line: str) -> str | None:
    stripped = line.strip()
    if not stripped or stripped.startswith(":"):
        return None
    if stripped.startswith("data:"):
        return stripped[5:].strip()
    if stripped.startswith("{"):
        return stripped
    return None


def _build_translation_payload(
    *,
    request: TranslateJobRequest,
    prompt: str,
    context_messages: list[Message] | None = None,
    stream: bool = True,
) -> dict:
    reasoning_effort = _normalize_reasoning_effort(request.reasoning_effort)
    messages = [*(context_messages or []), {"role": "user", "content": prompt}]
    return {
        "model": request.model,
        "messages": messages,
        "thinking": {"type": "enabled"},
        "reasoning_effort": reasoning_effort,
        "max_tokens": request.max_tokens,
        "response_format": {"type": "text"},
        "stream": stream,
    }


def _translation_output_stem(request: TranslateJobRequest) -> str:
    if request.input_file:
        stem = PureWindowsPath(request.input_file).stem.strip()
        if stem:
            return stem
    return "translated"


def _clip_context_entry(entry: str, max_chars: int) -> str:
    if len(entry) <= max_chars:
        return entry
    marker = "\n..."
    if max_chars <= len(marker):
        return entry[:max_chars]
    return entry[: max_chars - len(marker)] + marker


def _build_context_messages(blocks: list[SubtitleBlock], translations: dict[int, str], max_chars: int) -> list[Message]:
    if max_chars <= 0 or not translations:
        return []

    entries: list[str] = []
    used_chars = 0
    for index in sorted(translations.keys(), reverse=True):
        block = blocks[index]
        entry = f"<SEG {index}>\n原文：\n{block.text}\n译文：\n{translations[index]}\n</SEG {index}>"
        entry_chars = len(entry)
        if entries and used_chars + entry_chars > max_chars:
            break
        if not entries and entry_chars > max_chars:
            entry = _clip_context_entry(entry, max_chars)
            entry_chars = len(entry)
        entries.append(entry)
        used_chars += entry_chars
        if used_chars >= max_chars:
            break

    if not entries:
        return []

    context = "\n\n".join(reversed(entries))
    return [
        {
            "role": "user",
            "content": "以下是已完成的相邻字幕翻译，仅用于保持术语、人名、称谓和语气一致；不要重新输出这些段落。\n\n" + context,
        },
        {"role": "assistant", "content": "收到。后续只输出当前请求中的 <SEG> 翻译。"},
    ]


async def _request_translation(
    client: httpx.AsyncClient,
    *,
    url: str,
    api_key: str,
    request: TranslateJobRequest,
    prompt: str,
    context_messages: list[Message] | None = None,
    reporter: JobReporter | None = None,
    cancel_token: CancelToken | None = None,
    chunk_index: int = 0,
    chunk_total: int = 0,
    started: float = 0.0,
) -> str:
    payload = _build_translation_payload(request=request, prompt=prompt, context_messages=context_messages)
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json", "Accept": "text/event-stream"}
    content_parts: list[str] = []
    content_chars = 0
    reasoning_chars = 0
    stream_events = 0
    last_reported_at = time.monotonic()

    async with client.stream("POST", url, headers=headers, json=payload) as response:
        if response.status_code >= 400:
            body = (await response.aread()).decode("utf-8", errors="replace")
            detail = body[:500].replace(api_key, "<redacted>")
            raise RuntimeError(f"DeepSeek 请求失败: HTTP {response.status_code} {detail}")

        if reporter is not None and chunk_index and chunk_total:
            await reporter.log(f"字幕翻译分块 {chunk_index}/{chunk_total} 已连接 DeepSeek stream。")

        async for line in response.aiter_lines():
            if cancel_token is not None and cancel_token.is_canceled:
                raise TranslationCanceled()

            data = _stream_data_from_line(line)
            if data is None:
                continue
            if data == "[DONE]":
                break

            try:
                chunk_payload = json.loads(data)
            except json.JSONDecodeError as exc:
                raise RuntimeError(f"DeepSeek stream JSON 解析失败: {data[:120]}") from exc

            content_delta, reasoning_delta = _extract_stream_deltas(chunk_payload)
            if content_delta:
                content_parts.append(content_delta)
                content_chars += len(content_delta)
            if reasoning_delta:
                reasoning_chars += len(reasoning_delta)
            stream_events += 1

            now = time.monotonic()
            if reporter is not None and chunk_index and chunk_total and now - last_reported_at >= 5.0:
                elapsed = now - started if started else 0.0
                await reporter.progress(
                    done=chunk_index - 1,
                    total=chunk_total,
                    elapsed_seconds=elapsed,
                    message=f"字幕翻译分块 {chunk_index}/{chunk_total} 流式接收中。",
                )
                await reporter.log(
                    f"字幕翻译分块 {chunk_index}/{chunk_total} 流式接收中: "
                    f"events={stream_events}, content_chars={content_chars}, reasoning_chars={reasoning_chars}。"
                )
                last_reported_at = now

    content = "".join(content_parts)
    if not content.strip():
        raise RuntimeError("DeepSeek stream 响应缺少 message.content。")
    return content


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
        await reporter.artifact(name=partial_path.stem, kind="srt", path=partial_path)


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
    output_stem = _translation_output_stem(request)
    partial_path = output_dir / f"{output_stem}.partial.srt"
    output_path = output_dir / f"{output_stem}.srt"
    url = settings.deepseek_api_base.rstrip("/") + settings.deepseek_chat_completion_path
    translations: dict[int, str] = {}
    started = time.monotonic()

    await reporter.stage("translate_running", "DeepSeek 字幕翻译开始。")
    await reporter.log(
        f"DeepSeek 字幕翻译: model={request.model}, reasoning_effort={_normalize_reasoning_effort(request.reasoning_effort)}, "
        f"max_tokens={request.max_tokens}, chunk_chars={request.chunk_chars}, blocks={len(blocks)}, chunks={len(chunks)}, target={request.target_language}"
    )

    timeout = httpx.Timeout(1800.0, connect=30.0)
    async with httpx.AsyncClient(timeout=timeout) as client:
        for chunk_index, chunk in enumerate(chunks, start=1):
            if cancel_token.is_canceled:
                await reporter.log("翻译任务检测到取消请求，正在保留已完成字幕分块。")
                await _write_partial(reporter, partial_path, blocks, translations, register=True)
                return

            segment_text = _format_segments(chunk)
            prompt = _render_prompt(prompt_template, target_language=request.target_language, text=segment_text)
            context_messages = _build_context_messages(blocks, translations, settings.deepseek_context_chars)
            expected_indexes = [index for index, _ in chunk]
            try:
                content = await _request_translation(
                    client,
                    url=url,
                    api_key=api_key,
                    request=request,
                    prompt=prompt,
                    context_messages=context_messages,
                    reporter=reporter,
                    cancel_token=cancel_token,
                    chunk_index=chunk_index,
                    chunk_total=len(chunks),
                    started=started,
                )
                translations.update(_parse_translated_segments(content, expected_indexes))
            except TranslationCanceled:
                await reporter.log("翻译任务检测到取消请求，正在保留已完成字幕分块。")
                await _write_partial(reporter, partial_path, blocks, translations, register=True)
                return
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
    await reporter.artifact(name=output_stem, kind="srt", path=output_path)
