import tempfile
import unittest
from pathlib import Path

from config import WebSettings
from web_app.jobs import JobConflictError, JobManager


def _settings_for(temp_dir: str) -> WebSettings:
    root = Path(temp_dir)
    return WebSettings(
        upload_dir=str(root / "uploads"),
        job_dir=str(root / "jobs"),
        artifact_dir=str(root / "artifacts"),
        asr_cache_dir=str(root / "cache"),
        lada_output_dir=str(root / "lada"),
    )


class JobManagerConcurrencyTests(unittest.IsolatedAsyncioTestCase):
    async def test_translate_can_run_alongside_local_resource_job(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            manager = JobManager(_settings_for(temp_dir))

            asr_job = await manager.create_job("asr")
            translate_job = await manager.create_job("translate")

        self.assertEqual(asr_job.type, "asr")
        self.assertEqual(translate_job.type, "translate")

    async def test_local_resource_job_can_run_alongside_translate(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            manager = JobManager(_settings_for(temp_dir))

            translate_job = await manager.create_job("translate")
            asr_job = await manager.create_job("asr")

        self.assertEqual(translate_job.type, "translate")
        self.assertEqual(asr_job.type, "asr")

    async def test_local_resource_jobs_still_conflict_with_each_other(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            manager = JobManager(_settings_for(temp_dir))

            await manager.create_job("asr")
            with self.assertRaises(JobConflictError):
                await manager.create_job("lada")

    async def test_translate_jobs_conflict_with_each_other(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            manager = JobManager(_settings_for(temp_dir))

            await manager.create_job("translate")
            with self.assertRaises(JobConflictError):
                await manager.create_job("translate")


if __name__ == "__main__":
    unittest.main()
