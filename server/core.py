"""Threaded FFmpeg job execution and status tracking."""

from __future__ import annotations

import json
import shutil
import subprocess
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class RenderSettings:
    width: int = 1280
    height: int = 720
    bitrate: str = "5M"
    preset: str = "p4"
    codec: str = "h264_nvenc"


@dataclass
class Job:
    job_id: str
    state: str = "queued"
    progress: float = 0.0
    message: str = "Queued"
    input_name: str = ""
    output_path: str = ""
    error: str = ""
    log: list[str] = field(default_factory=list)


class JobManager:
    """Runs a bounded number of FFmpeg jobs and keeps in-memory status."""

    def __init__(self, data_dir: Path, workers: int = 1) -> None:
        self.data_dir = data_dir
        self.input_dir = data_dir / "inputs"
        self.output_dir = data_dir / "outputs"
        self.input_dir.mkdir(parents=True, exist_ok=True)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self._executor = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="render")
        self._jobs: dict[str, Job] = {}
        self._lock = threading.Lock()

    @staticmethod
    def ffmpeg_path() -> str | None:
        return shutil.which("ffmpeg")

    def create_job(self, input_path: Path, input_name: str, settings: RenderSettings) -> Job:
        job_id = uuid.uuid4().hex
        output_path = self.output_dir / f"{job_id}.mp4"
        job = Job(job_id=job_id, input_name=input_name, output_path=str(output_path))
        with self._lock:
            self._jobs[job_id] = job
        self._executor.submit(self._render, job_id, input_path, output_path, settings)
        return self._snapshot(job)

    def get_job(self, job_id: str) -> Job | None:
        with self._lock:
            job = self._jobs.get(job_id)
            return self._snapshot(job) if job else None

    def list_jobs(self) -> list[Job]:
        with self._lock:
            return [self._snapshot(job) for job in self._jobs.values()]

    @staticmethod
    def _snapshot(job: Job) -> Job:
        return Job(**{**asdict(job), "log": list(job.log)})

    def _update(self, job_id: str, **values: Any) -> None:
        with self._lock:
            job = self._jobs[job_id]
            for key, value in values.items():
                setattr(job, key, value)

    def _append_log(self, job_id: str, message: str) -> None:
        with self._lock:
            job = self._jobs[job_id]
            job.log.append(message)
            job.log = job.log[-200:]

    @staticmethod
    def _duration_seconds(input_path: Path) -> float:
        ffprobe = shutil.which("ffprobe")
        if not ffprobe:
            return 0.0
        try:
            result = subprocess.run(
                [ffprobe, "-v", "error", "-show_entries", "format=duration", "-of", "json", str(input_path)],
                capture_output=True,
                text=True,
                timeout=15,
                check=True,
            )
            return float(json.loads(result.stdout)["format"]["duration"])
        except (OSError, subprocess.SubprocessError, ValueError, KeyError, json.JSONDecodeError):
            return 0.0

    def _render(self, job_id: str, input_path: Path, output_path: Path, settings: RenderSettings) -> None:
        ffmpeg = self.ffmpeg_path()
        if not ffmpeg:
            self._update(job_id, state="failed", message="FFmpeg is not installed", error="Install FFmpeg and ensure it is on PATH.")
            input_path.unlink(missing_ok=True)
            return

        duration = self._duration_seconds(input_path)
        preset = settings.preset
        if settings.codec in ("libx264", "libx265") and preset.startswith("p"):
            preset = "medium"
        elif settings.codec in ("h264_nvenc", "hevc_nvenc") and not preset.startswith("p"):
            preset = "p4"
        command = [
            ffmpeg, "-y", "-hide_banner", "-i", str(input_path),
            "-vf", f"scale={settings.width}:{settings.height}:force_original_aspect_ratio=decrease,pad={settings.width}:{settings.height}:(ow-iw)/2:(oh-ih)/2",
            "-c:v", settings.codec, "-b:v", settings.bitrate, "-preset", preset,
            "-c:a", "aac", "-progress", "pipe:1", "-nostats", str(output_path),
        ]
        self._update(job_id, state="running", message="Rendering")
        self._append_log(job_id, "Starting: " + " ".join(command))
        try:
            process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
            assert process.stdout is not None
            for raw_line in process.stdout:
                line = raw_line.strip()
                if line:
                    self._append_log(job_id, line)
                if line.startswith("out_time_ms=") and duration > 0:
                    try:
                        seconds = int(line.partition("=")[2]) / 1_000_000
                        self._update(job_id, progress=min(99.9, seconds / duration * 100), message="Rendering")
                    except ValueError:
                        pass
            return_code = process.wait()
            if return_code != 0:
                raise RuntimeError(f"FFmpeg exited with code {return_code}")
            if not output_path.is_file() or output_path.stat().st_size == 0:
                raise RuntimeError("FFmpeg completed without producing an output file")
            self._update(job_id, state="completed", progress=100.0, message="Completed")
        except (OSError, RuntimeError) as exc:
            output_path.unlink(missing_ok=True)
            self._update(job_id, state="failed", message="Render failed", error=str(exc))
            self._append_log(job_id, f"ERROR: {exc}")
        finally:
            input_path.unlink(missing_ok=True)

    def close(self) -> None:
        self._executor.shutdown(wait=False, cancel_futures=True)