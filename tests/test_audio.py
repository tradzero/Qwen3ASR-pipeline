import unittest
from unittest.mock import Mock, patch

import numpy as np

import audio


class LoadAudioTests(unittest.TestCase):
    def test_video_file_prefers_ffmpeg(self):
        expected = np.array([0.0], dtype=np.float32)

        with patch.object(audio, "_load_audio_with_ffmpeg", return_value=expected) as load_ffmpeg:
            result = audio.load_audio("sample.mp4")

        self.assertIs(result, expected)
        load_ffmpeg.assert_called_once_with("sample.mp4")

    def test_ffmpeg_pipe_uses_s16_normalization(self):
        pcm = np.array([0, 16384, -32768], dtype=np.int16).tobytes()
        fake_process = Mock()
        fake_process.communicate.return_value = (pcm, b"")
        fake_process.returncode = 0

        with patch.object(audio.subprocess, "Popen", return_value=fake_process) as popen:
            result = audio._load_audio_with_ffmpeg("sample.mp4")

        command = popen.call_args.args[0]
        self.assertIn("pcm_s16le", command)
        self.assertIn("s16le", command)
        np.testing.assert_allclose(
            result,
            np.array([0.0, 0.5, -1.0], dtype=np.float32),
        )


if __name__ == "__main__":
    unittest.main()
