import tempfile
import unittest
import asyncio
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


async def _wait_for_status(manager: JobManager, job_id: str, status: str) -> None:
    for _ in range(80):
        if manager.get_job(job_id).status == status:
            return
        await asyncio.sleep(0.025)
    raise AssertionError(f"Job {job_id} did not reach status {status}")


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

    async def test_queued_handoff_waits_for_lane_and_runs_after_parent_finishes(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            manager = JobManager(_settings_for(temp_dir))
            parent_started = asyncio.Event()
            release_parent = asyncio.Event()
            child_finished = asyncio.Event()

            parent = await manager.create_job("asr")

            async def parent_runner(reporter, cancel_token):
                parent_started.set()
                await release_parent.wait()

            await manager.submit_job(parent.job_id, parent_runner)
            await asyncio.wait_for(parent_started.wait(), timeout=2)

            child = await manager.create_job("lada", allow_queue=True)

            async def child_runner(reporter, cancel_token):
                child_finished.set()

            await manager.submit_job(child.job_id, child_runner)
            self.assertEqual(manager.get_job(child.job_id).status, "queued")

            release_parent.set()
            await asyncio.wait_for(child_finished.wait(), timeout=2)
            await _wait_for_status(manager, parent.job_id, "succeeded")
            await _wait_for_status(manager, child.job_id, "succeeded")

    async def test_completion_callback_can_record_handoff_child(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            manager = JobManager(_settings_for(temp_dir))
            child_finished = asyncio.Event()

            async def on_complete(parent):
                child = await manager.create_job(
                    "lada",
                    metadata={"parent_job_id": parent.job_id},
                    allow_queue=True,
                )

                async def child_runner(reporter, cancel_token):
                    child_finished.set()

                await manager.submit_job(child.job_id, child_runner)
                await manager.record_handoff(parent.job_id, child)

            manager.set_completion_callback(on_complete)
            parent = await manager.create_job("asr", metadata={"handoff": {"lada": {"enabled": True}}})
            queue = manager.subscribe(parent.job_id)

            async def parent_runner(reporter, cancel_token):
                return None

            await manager.submit_job(parent.job_id, parent_runner)
            events = []
            for _ in range(5):
                event = await asyncio.wait_for(queue.get(), timeout=2)
                events.append(event)
                if event.status == "succeeded":
                    break
            await asyncio.wait_for(child_finished.wait(), timeout=2)
            parent_record = manager.get_job(parent.job_id)
            event_names = [event.event for event in events]
            succeeded_index = next(index for index, event in enumerate(events) if event.status == "succeeded")

            self.assertEqual(parent_record.metadata["handoff_status"], "created")
            self.assertEqual(parent_record.metadata["handoff_child_job_type"], "lada")
            self.assertIn("handoff", event_names)
            self.assertLess(event_names.index("handoff"), succeeded_index)


if __name__ == "__main__":
    unittest.main()
