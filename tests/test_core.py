from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from server.core import JobManager, RenderSettings


class JobManagerTests(unittest.TestCase):
    def test_job_is_registered_with_unique_output_and_snapshot(self) -> None:
        with TemporaryDirectory() as temporary:
            manager = JobManager(Path(temporary))
            try:
                input_path = Path(temporary) / "source.mp4"
                input_path.write_bytes(b"input")
                manager._executor.submit = lambda *_args, **_kwargs: None
                first = manager.create_job(input_path, "source.mp4", RenderSettings())
                second = manager.create_job(input_path, "source.mp4", RenderSettings())

                self.assertNotEqual(first.job_id, second.job_id)
                self.assertNotEqual(first.output_path, second.output_path)
                first.progress = 55
                snapshot = manager.get_job(first.job_id)
                self.assertIsNotNone(snapshot)
                self.assertEqual(snapshot.progress, 0)
                self.assertEqual(len(manager.list_jobs()), 2)
            finally:
                manager.close()

    def test_render_settings_defaults_use_nvenc(self) -> None:
        settings = RenderSettings()

        self.assertEqual(settings.codec, "h264_nvenc")
        self.assertEqual((settings.width, settings.height), (1280, 720))


if __name__ == "__main__":
    unittest.main()