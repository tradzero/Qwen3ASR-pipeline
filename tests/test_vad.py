import unittest

import numpy as np

from audio import WAV_SAMPLE_RATE
from vad import _split_points_from_speech


def samples(seconds: float) -> int:
    return int(round(seconds * WAV_SAMPLE_RATE))


class VadSplitPointTests(unittest.TestCase):
    def test_split_uses_silence_before_next_speech_not_speech_start(self):
        total_samples = samples(103.45)
        speech_ranges = [
            (samples(44.5), samples(48.8)),
            (samples(59.5), samples(63.3)),
        ]

        split_points = _split_points_from_speech(
            total_samples,
            speech_ranges,
            samples(60),
            samples(120),
        )

        self.assertEqual(split_points[0], 0)
        self.assertEqual(split_points[-1], total_samples)
        self.assertEqual(len(split_points), 3)
        split_seconds = split_points[1] / WAV_SAMPLE_RATE
        self.assertGreater(split_seconds, 48.8)
        self.assertLess(split_seconds, 59.5)

    def test_no_speech_fallback_uses_target_duration_not_max_duration(self):
        split_points = _split_points_from_speech(
            samples(258),
            [],
            samples(60),
            samples(120),
        )

        self.assertEqual(
            [round(point / WAV_SAMPLE_RATE) for point in split_points],
            [0, 60, 120, 180, 240, 258],
        )

    def test_target_duration_is_capped_by_max_duration(self):
        split_points = _split_points_from_speech(
            samples(100),
            [],
            samples(120),
            samples(45),
        )

        self.assertEqual(
            [round(point / WAV_SAMPLE_RATE) for point in split_points],
            [0, 45, 90, 100],
        )

    def test_energy_refinement_avoids_missed_speech_near_target(self):
        wav = np.zeros(samples(100), dtype=np.float32)
        wav[samples(59.5) : samples(63.0)] = 0.5

        split_points = _split_points_from_speech(
            len(wav),
            [(samples(44.5), samples(48.8))],
            samples(60),
            samples(120),
            wav,
        )

        split_seconds = split_points[1] / WAV_SAMPLE_RATE
        self.assertLess(split_seconds, 59.5)
        self.assertGreater(split_seconds, 54.0)


if __name__ == "__main__":
    unittest.main()
