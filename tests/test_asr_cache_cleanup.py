import tempfile
import unittest
from pathlib import Path

import numpy as np

from cache import get_cache_dir, save_preprocess_cache
from config import Config
from web_app.runners.asr import _clear_successful_preprocess_cache


class FakeReporter:
    def __init__(self):
        self.logs = []

    async def log(self, message: str):
        self.logs.append(message)


class AsrCacheCleanupTests(unittest.IsolatedAsyncioTestCase):
    async def test_successful_web_asr_clears_current_preprocess_cache(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            input_path = Path(tmp_dir) / "sample.wav"
            input_path.write_bytes(b"not a real wav")
            config = Config(input_file=str(input_path), cache_dir=str(Path(tmp_dir) / "cache"), use_cache=True)
            wav = np.zeros(16, dtype=np.float32)
            save_preprocess_cache(config, wav, [(0, 16, wav)])
            cache_dir = get_cache_dir(config)
            reporter = FakeReporter()

            await _clear_successful_preprocess_cache(config, reporter)

            self.assertFalse(cache_dir.exists())
            self.assertTrue(any("已清理预处理缓存" in message for message in reporter.logs))

    async def test_disabled_cache_is_left_untouched(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            config = Config(input_file="sample.wav", cache_dir=tmp_dir, use_cache=False)
            marker = Path(tmp_dir) / "marker.txt"
            marker.write_text("keep", encoding="utf-8")
            reporter = FakeReporter()

            await _clear_successful_preprocess_cache(config, reporter)

            self.assertTrue(marker.exists())
            self.assertEqual(reporter.logs, [])


if __name__ == "__main__":
    unittest.main()