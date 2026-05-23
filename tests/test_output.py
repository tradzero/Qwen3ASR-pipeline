import tempfile
import unittest
from pathlib import Path

from output import save_srt


class SaveSrtTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()