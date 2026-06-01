import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx
from pydantic import ValidationError

from web_app.runners.translate import (
    MissingSegmentsError,
    SubtitleBlock,
    _build_context_messages,
    _build_srt,
    _build_translation_payload,
    _chunk_blocks,
    _extract_stream_deltas,
    _parse_translated_segments,
    _request_translation,
    _stream_data_from_line,
    _translate_chunk_with_missing_retry,
    _translation_output_stem,
    _write_partial,
    build_resume_request_from_state,
    run_translate_web_job,
)
from config import WebSettings
from web_app.jobs import CancelToken
from web_app.schemas import TranslateJobRequest


SRT_TEXT = "1\n00:00:00,000 --> 00:00:01,000\nhello\n"


class DeepSeekTranslateTests(unittest.TestCase):
    def request(self, **updates):
        return TranslateJobRequest(input_text=SRT_TEXT, **updates)

    def test_thinking_payload_omits_unsupported_sampling_parameters(self):
        request = self.request(
            model="deepseek-v4-pro",
            reasoning_effort="xhigh",
            max_tokens=384_000,
            chunk_chars=1_000_000,
        )

        payload = _build_translation_payload(request=request, prompt="Translate this subtitle")

        self.assertEqual(payload["model"], "deepseek-v4-pro")
        self.assertEqual(payload["thinking"], {"type": "enabled"})
        self.assertEqual(payload["reasoning_effort"], "max")
        self.assertEqual(payload["max_tokens"], 384_000)
        self.assertIs(payload["stream"], True)
        self.assertFalse({"temperature", "top_p", "presence_penalty", "frequency_penalty"} & payload.keys())

    def test_translation_payload_carries_bounded_prior_context_messages(self):
        context_messages = [{"role": "user", "content": "previous source"}, {"role": "assistant", "content": "previous translation"}]

        payload = _build_translation_payload(request=self.request(), prompt="current", context_messages=context_messages)

        self.assertEqual(payload["messages"], [*context_messages, {"role": "user", "content": "current"}])
        self.assertNotIn("reasoning_content", payload["messages"][1])

    def test_context_messages_use_recent_translated_subtitles_without_reasoning(self):
        blocks = [
            SubtitleBlock(number="1", timing="00:00:00,000 --> 00:00:01,000", text="hello"),
            SubtitleBlock(number="2", timing="00:00:01,000 --> 00:00:02,000", text="world"),
        ]

        messages = _build_context_messages(blocks, {0: "你好", 1: "世界"}, max_chars=200)

        self.assertEqual([message["role"] for message in messages], ["user", "assistant"])
        self.assertIn("hello", messages[0]["content"])
        self.assertIn("世界", messages[0]["content"])
        self.assertNotIn("reasoning_content", messages[0])

    def test_context_messages_clip_oversized_single_entry(self):
        blocks = [SubtitleBlock(number="1", timing="00:00:00,000 --> 00:00:01,000", text="a" * 200)]

        messages = _build_context_messages(blocks, {0: "b" * 200}, max_chars=80)

        context_prefix = "以下是已完成的相邻字幕翻译，仅用于保持术语、人名、称谓和语气一致；不要重新输出这些段落。\n\n"
        self.assertLessEqual(len(messages[0]["content"]) - len(context_prefix), 80)

    def test_max_tokens_is_capped_to_deepseek_v4_output_limit(self):
        with self.assertRaises(ValidationError):
            self.request(max_tokens=384_001)

    def test_chunk_chars_allows_deepseek_v4_context_budget(self):
        self.assertEqual(self.request(chunk_chars=1_000_000).chunk_chars, 1_000_000)

    def test_translation_output_stem_uses_input_srt_filename(self):
        request = TranslateJobRequest(input_file=r"D:\media\movie.name.srt")

        self.assertEqual(_translation_output_stem(request), "movie.name")

    def test_translation_output_stem_falls_back_for_pasted_text(self):
        self.assertEqual(_translation_output_stem(self.request()), "translated")

    def test_chunk_blocks_respects_max_blocks_per_chunk(self):
        blocks = [
            SubtitleBlock(number=str(index + 1), timing="00:00:00,000 --> 00:00:01,000", text="短句")
            for index in range(5)
        ]

        chunks = _chunk_blocks(blocks, chunk_chars=1000, max_blocks_per_chunk=2)

        self.assertEqual([[index for index, _ in chunk] for chunk in chunks], [[0, 1], [2, 3], [4]])

    def test_parse_translated_segments_keeps_partial_before_missing_error(self):
        with self.assertRaises(MissingSegmentsError) as context:
            _parse_translated_segments("<SEG 0>你好</SEG 0>\n<SEG 2>世界</SEG 2>", [0, 1, 2])

        self.assertEqual(context.exception.missing, [1])
        self.assertEqual(context.exception.translated, {0: "你好", 2: "世界"})

    def test_build_resume_request_from_state_restores_translation_parameters(self):
        blocks = [
            SubtitleBlock(number="1", timing="00:00:00,000 --> 00:00:01,000", text="hello"),
            SubtitleBlock(number="2", timing="00:00:01,000 --> 00:00:02,000", text="world"),
        ]
        state = {
            "version": 1,
            "blocks": [
                {"number": block.number, "timing": block.timing, "text": block.text}
                for block in blocks
            ],
            "request": {
                "target_language": "简体中文",
                "model": "deepseek-v4-pro",
                "reasoning_effort": "max",
                "max_tokens": 1234,
                "chunk_chars": 5000,
                "max_blocks_per_chunk": 12,
                "prompt_template": "翻译为{target_language}\n{text}",
            },
        }

        request = build_resume_request_from_state(state)

        self.assertEqual(request.max_blocks_per_chunk, 12)
        self.assertEqual(request.prompt_template, "翻译为{target_language}\n{text}")
        self.assertEqual(request.input_text, _build_srt(blocks, {}))

    def test_stream_delta_extracts_content_and_ignores_reasoning_text_body(self):
        content, reasoning = _extract_stream_deltas(
            {"choices": [{"delta": {"reasoning_content": "analysis", "content": "你好"}}]}
        )

        self.assertEqual(content, "你好")
        self.assertEqual(reasoning, "analysis")

    def test_stream_line_accepts_sse_data_and_raw_json(self):
        self.assertEqual(_stream_data_from_line('data: {"choices": []}'), '{"choices": []}')
        self.assertEqual(_stream_data_from_line('{"choices": []}'), '{"choices": []}')
        self.assertIsNone(_stream_data_from_line(": keep-alive"))


class DeepSeekTranslateAsyncTests(unittest.IsolatedAsyncioTestCase):
    async def test_request_translation_reads_deepseek_sse_stream(self):
        def handler(request):
            payload = json.loads(request.content)
            self.assertIs(payload["stream"], True)
            self.assertEqual(request.headers["accept"], "text/event-stream")
            return httpx.Response(
                200,
                headers={"Content-Type": "text/event-stream"},
                content=(
                    'data: {"choices":[{"delta":{"reasoning_content":"thinking"}}]}\n\n'
                    'data: {"choices":[{"delta":{"content":"<SEG 0>你"}}]}\n\n'
                    'data: {"choices":[{"delta":{"content":"好</SEG 0>"}}]}\n\n'
                    "data: [DONE]\n\n"
                ).encode("utf-8"),
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            content = await _request_translation(
                client,
                url="https://api.deepseek.com/chat/completions",
                api_key="secret",
                request=TranslateJobRequest(input_text=SRT_TEXT),
                prompt="Translate",
            )

        self.assertEqual(content, "<SEG 0>你好</SEG 0>")

    async def test_missing_segment_retry_preserves_partial_translations(self):
        class FakeReporter:
            def __init__(self):
                self.logs = []

            async def log(self, message):
                self.logs.append(message)

        blocks = [
            SubtitleBlock(number="1", timing="00:00:00,000 --> 00:00:01,000", text="a"),
            SubtitleBlock(number="2", timing="00:00:01,000 --> 00:00:02,000", text="b"),
            SubtitleBlock(number="3", timing="00:00:02,000 --> 00:00:03,000", text="c"),
        ]
        chunks = [list(enumerate(blocks))]
        translations = {}
        reporter = FakeReporter()
        request = TranslateJobRequest(input_text=_build_srt(blocks, {}), max_blocks_per_chunk=3)

        with tempfile.TemporaryDirectory() as temp_dir:
            state_path = Path(temp_dir) / "translated.translate_state.json"
            with patch(
                "web_app.runners.translate._request_translation",
                new=AsyncMock(
                    side_effect=[
                        "<SEG 0>A</SEG 0>\n<SEG 2>C</SEG 2>",
                        "<SEG 1>B</SEG 1>",
                    ]
                ),
            ):
                async with httpx.AsyncClient() as client:
                    await _translate_chunk_with_missing_retry(
                        client,
                        url="https://api.deepseek.com/chat/completions",
                        api_key="secret",
                        request=request,
                        prompt_template="{text}",
                        blocks=blocks,
                        chunks=chunks,
                        translations=translations,
                        state_path=state_path,
                        output_stem="translated",
                        reporter=reporter,
                        cancel_token=CancelToken(),
                        chunk_index=1,
                        chunk_total=1,
                        chunk=chunks[0],
                        started=0.0,
                        settings=WebSettings(),
                    )

        self.assertEqual(translations, {0: "A", 1: "B", 2: "C"})
        self.assertTrue(any("缺少段 [1]" in message for message in reporter.logs))

    async def test_partial_srt_writes_only_contiguous_translated_prefix(self):
        class FakeReporter:
            def __init__(self):
                self.artifacts = []

            async def artifact(self, *, name, kind, path):
                self.artifacts.append((name, kind, Path(path)))

        blocks = [
            SubtitleBlock(number="1", timing="00:00:00,000 --> 00:00:01,000", text="source 1"),
            SubtitleBlock(number="2", timing="00:00:01,000 --> 00:00:02,000", text="source 2"),
            SubtitleBlock(number="3", timing="00:00:02,000 --> 00:00:03,000", text="source 3"),
        ]

        with tempfile.TemporaryDirectory() as temp_dir:
            partial_path = Path(temp_dir) / "movie.partial.srt"
            await _write_partial(
                FakeReporter(),
                partial_path,
                blocks,
                {0: "译文 1", 2: "译文 3"},
                register=True,
            )

            content = partial_path.read_text(encoding="utf-8")

        self.assertIn("译文 1", content)
        self.assertNotIn("source 2", content)
        self.assertNotIn("译文 3", content)

    async def test_run_translate_resume_skips_completed_chunks_and_writes_full_output(self):
        class FakeManager:
            def __init__(self, artifact_dir):
                self.artifact_dir = artifact_dir

        class FakeReporter:
            def __init__(self, artifact_dir):
                self.job_id = "resume-job"
                self._manager = FakeManager(artifact_dir)
                self.logs = []
                self.progress_events = []
                self.artifacts = []

            async def stage(self, stage, message=None):
                self.logs.append(f"{stage}: {message}")

            async def log(self, message):
                self.logs.append(message)

            async def progress(self, **kwargs):
                self.progress_events.append(kwargs)

            async def artifact(self, *, name, kind, path):
                self.artifacts.append((name, kind, Path(path)))

        blocks = [
            SubtitleBlock(number="1", timing="00:00:00,000 --> 00:00:01,000", text="a"),
            SubtitleBlock(number="2", timing="00:00:01,000 --> 00:00:02,000", text="b"),
            SubtitleBlock(number="3", timing="00:00:02,000 --> 00:00:03,000", text="c"),
        ]
        state = {
            "version": 1,
            "output_stem": "movie",
            "blocks": [
                {"number": block.number, "timing": block.timing, "text": block.text}
                for block in blocks
            ],
            "chunks": [[0, 1], [2]],
            "translations": {"0": "A", "1": "B"},
            "request": {
                "target_language": "简体中文",
                "model": "deepseek-v4-pro",
                "reasoning_effort": "max",
                "max_tokens": 1000,
                "chunk_chars": 5000,
                "max_blocks_per_chunk": 2,
                "prompt_template": "{text}",
            },
        }

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            old_job_dir = temp_path / "old-job"
            old_job_dir.mkdir()
            state_path = old_job_dir / "movie.translate_state.json"
            state_path.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
            request = build_resume_request_from_state(state)
            reporter = FakeReporter(temp_path)
            mocked_request = AsyncMock(return_value="<SEG 2>C</SEG 2>")

            with patch("web_app.runners.translate.get_deepseek_api_key", return_value="secret"):
                with patch("web_app.runners.translate._request_translation", new=mocked_request):
                    await run_translate_web_job(
                        request,
                        WebSettings(),
                        reporter,
                        CancelToken(),
                        resume_state_path=state_path,
                    )

            output_path = temp_path / "resume-job" / "movie.srt"
            output_content = output_path.read_text(encoding="utf-8")

        self.assertEqual(mocked_request.await_count, 1)
        self.assertIn("字幕翻译分块 1/2 已从 checkpoint 跳过。", [event["message"] for event in reporter.progress_events])
        self.assertIn("A", output_content)
        self.assertIn("B", output_content)
        self.assertIn("C", output_content)
        self.assertEqual(reporter.artifacts[0][0], "movie")


if __name__ == "__main__":
    unittest.main()
