import tempfile
import unittest
from pathlib import Path

from output import clean_asr_text, save_srt, save_txt


class SaveSrtTests(unittest.TestCase):
    def test_clean_asr_text_collapses_long_char_repeats(self):
        self.assertEqual(clean_asr_text("あ" * 21), "あ")

    def test_clean_asr_text_keeps_char_repeats_at_threshold(self):
        self.assertEqual(clean_asr_text("あ" * 20), "あ" * 20)

    def test_clean_asr_text_collapses_long_pattern_repeats(self):
        self.assertEqual(clean_asr_text("やめて" * 21), "やめて")

    def test_clean_asr_text_keeps_pattern_repeats_below_threshold(self):
        self.assertEqual(clean_asr_text("やめて" * 19), "やめて" * 19)

    def test_clean_asr_text_uses_configurable_threshold(self):
        self.assertEqual(clean_asr_text("abcabcabc", repeat_threshold=3), "abc")

    def test_save_txt_cleans_repeated_text(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            output_path = Path(tmp_dir) / "clean.txt"

            save_txt(["hello", "やめて" * 21], str(output_path))

            self.assertEqual(output_path.read_text(encoding="utf-8"), "hello\nやめて")

    def test_missing_segment_timestamps_fall_back_to_vad_timing(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            output_path = Path(tmp_dir) / "mixed.srt"

            save_srt(
                ["hello", "world"],
                [(0, 16000), (16000, 32000)],
                str(output_path),
                time_stamps=[[{"text": "hello", "start_time": 0.0, "end_time": 1.0}], None],
            )

            self.assertEqual(
                output_path.read_text(encoding="utf-8"),
                "1\n"
                "00:00:00,000 --> 00:00:01,000\n"
                "hello\n\n"
                "2\n"
                "00:00:01,000 --> 00:00:02,000\n"
                "world\n\n",
            )

    def test_save_srt_cleans_fallback_vad_text(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            output_path = Path(tmp_dir) / "clean.srt"

            save_srt(
                ["やめて" * 21],
                [(0, 16000)],
                str(output_path),
            )

            self.assertEqual(
                output_path.read_text(encoding="utf-8"),
                "1\n"
                "00:00:00,000 --> 00:00:01,000\n"
                "やめて\n\n",
            )

    def test_save_srt_cleans_timestamp_text(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            output_path = Path(tmp_dir) / "clean_timestamp.srt"

            save_srt(
                [""],
                [(0, 16000)],
                str(output_path),
                time_stamps=[[{"text": "やめて" * 21, "start_time": 0.0, "end_time": 1.0}]],
            )

            self.assertEqual(
                output_path.read_text(encoding="utf-8"),
                "1\n"
                "00:00:00,000 --> 00:00:01,000\n"
                "やめて\n\n",
            )


if __name__ == "__main__":
    unittest.main()
