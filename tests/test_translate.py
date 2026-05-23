import unittest

from pydantic import ValidationError

from web_app.runners.translate import _build_translation_payload, _translation_output_stem
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
        self.assertFalse({"temperature", "top_p", "presence_penalty", "frequency_penalty"} & payload.keys())

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


if __name__ == "__main__":
    unittest.main()