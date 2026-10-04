"""HTTP API for the remote rendering worker."""

from __future__ import annotations

import hashlib
import hmac
import os
import platform
import re
import shutil
import subprocess
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated

from fastapi import FastAPI, File, Header, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
import psutil

from server.core import JobManager, RenderSettings


DATA_DIR = Path(os.environ.get("WORKER_DATA_DIR", "worker_data")).resolve()
MAX_UPLOAD_BYTES = int(os.environ.get("WORKER_MAX_UPLOAD_BYTES", str(10 * 1024**3)))
WORKER_TOKEN = os.environ.get("WORKER_TOKEN", "")
manager: JobManager | None = None


@asynccontextmanager
async def lifespan(_: FastAPI):
    global manager
    manager = JobManager(DATA_DIR, workers=max(1, int(os.environ.get("WORKER_CONCURRENCY", "1"))))
    yield
    manager.close()
    manager = None


app = FastAPI(title="Distributed Render Worker", version="1.0.0", lifespan=lifespan)


def check_token(token: str | None) -> None:
    if not WORKER_TOKEN:
        raise HTTPException(status_code=503, detail="Worker token is not configured")
    if not hmac.compare_digest(token or "", WORKER_TOKEN):
        raise HTTPException(status_code=401, detail="Invalid worker token")


def get_manager() -> JobManager:
    if manager is None:
        raise HTTPException(status_code=503, detail="Worker is starting")
    return manager


def job_response(job) -> dict:
    return {
        "job_id": job.job_id,
        "state": job.state,
        "progress": round(job.progress, 1),
        "message": job.message,
        "input_name": job.input_name,
        "error": job.error,
        "log": job.log,
    }


def sample_worker_metrics() -> dict:
    metrics = {
        "cpu_percent": psutil.cpu_percent(interval=None),
        "memory_percent": psutil.virtual_memory().percent,
        "logical_cpus": psutil.cpu_count(),
        "memory_total_bytes": psutil.virtual_memory().total,
        "platform": platform.platform(),
        "gpu_utilization_percent": None,
        "gpu_memory_used_mb": None,
        "gpu_memory_total_mb": None,
    }
    nvidia_smi = shutil.which("nvidia-smi")
    if nvidia_smi:
        try:
            result = subprocess.run(
                [nvidia_smi, "--query-gpu=utilization.gpu,memory.used,memory.total", "--format=csv,noheader,nounits"],
                capture_output=True,
                text=True,
                timeout=2,
                check=True,
            )
            values = [value.strip() for value in result.stdout.splitlines()[0].split(",")]
            metrics.update({
                "gpu_utilization_percent": float(values[0]),
                "gpu_memory_used_mb": float(values[1]),
                "gpu_memory_total_mb": float(values[2]),
            })
        except (OSError, subprocess.SubprocessError, ValueError, IndexError):
            pass
    return metrics


@app.get("/api/v1/health")
def health() -> dict:
    ffmpeg = JobManager.ffmpeg_path()
    return {
        "status": "ok" if ffmpeg else "degraded",
        "service": "distributed-render-worker",
        "ffmpeg_available": bool(ffmpeg),
        "time": time.time(),
    }


@app.get("/api/v1/ping")
def ping(client_time: float | None = None) -> dict:
    now = time.time()
    return {"status": "ok", "server_time": now, "client_time": client_time, "round_trip_ready": True}


@app.get("/api/v1/capabilities")
def capabilities(x_worker_token: Annotated[str | None, Header()] = None) -> dict:
    check_token(x_worker_token)
    ffmpeg = JobManager.ffmpeg_path()
    nvenc = False
    if ffmpeg:
        import subprocess

        try:
            result = subprocess.run([ffmpeg, "-hide_banner", "-encoders"], capture_output=True, text=True, timeout=10)
            nvenc = "h264_nvenc" in result.stdout
        except (OSError, subprocess.SubprocessError):
            pass
    return {
        "ffmpeg_available": bool(ffmpeg),
        "nvenc_available": nvenc,
        "max_upload_bytes": MAX_UPLOAD_BYTES,
        "system": sample_worker_metrics(),
    }


class SettingsModel(BaseModel):
    width: int = Field(default=1280, ge=16, le=7680)
    height: int = Field(default=720, ge=16, le=4320)
    bitrate: str = Field(default="5M", pattern=r"^[1-9][0-9]{0,3}[kKmM]$")
    preset: str = Field(default="p4", pattern=r"^(p[1-7]|ultrafast|fast|medium|slow)$")
    codec: str = Field(default="h264_nvenc", pattern=r"^(h264_nvenc|hevc_nvenc|libx264|libx265)$")


@app.post("/api/v1/jobs", status_code=202)
async def submit_job(
    file: Annotated[UploadFile, File()],
    settings: Annotated[str, Header(alias="X-Render-Settings")],
    sha256: Annotated[str, Header(alias="X-Content-SHA256")],
    x_worker_token: Annotated[str | None, Header()] = None,
) -> dict:
    check_token(x_worker_token)
    if not re.fullmatch(r"[a-fA-F0-9]{64}", sha256):
        raise HTTPException(status_code=400, detail="A valid SHA-256 header is required")
    try:
        configuration = SettingsModel.model_validate_json(settings)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"Invalid render settings: {exc}") from exc

    job_manager = get_manager()
    safe_name = Path(file.filename or "input-video").name
    staging_path = job_manager.input_dir / f"upload-{time.time_ns()}.part"
    digest = hashlib.sha256()
    total_bytes = 0
    try:
        with staging_path.open("wb") as destination:
            while chunk := await file.read(1024 * 1024):
                total_bytes += len(chunk)
                if total_bytes > MAX_UPLOAD_BYTES:
                    raise HTTPException(status_code=413, detail="Upload exceeds worker size limit")
                digest.update(chunk)
                destination.write(chunk)
        if total_bytes == 0:
            raise HTTPException(status_code=400, detail="Uploaded file is empty")
        if not hmac.compare_digest(digest.hexdigest().lower(), sha256.lower()):
            raise HTTPException(status_code=422, detail="SHA-256 mismatch; upload discarded")
        input_path = job_manager.input_dir / f"{time.time_ns()}-{safe_name}"
        staging_path.replace(input_path)
        job = job_manager.create_job(
            input_path,
            safe_name,
            RenderSettings(**configuration.model_dump()),
        )
        return job_response(job)
    except HTTPException:
        staging_path.unlink(missing_ok=True)
        raise
    except OSError as exc:
        staging_path.unlink(missing_ok=True)
        raise HTTPException(status_code=500, detail="Unable to store uploaded file") from exc
    except Exception:
        staging_path.unlink(missing_ok=True)
        raise
    finally:
        await file.close()


@app.get("/api/v1/jobs")
def list_jobs(x_worker_token: Annotated[str | None, Header()] = None) -> list[dict]:
    check_token(x_worker_token)
    return [job_response(job) for job in get_manager().list_jobs()]


@app.get("/api/v1/jobs/{job_id}")
def job_status(job_id: str, x_worker_token: Annotated[str | None, Header()] = None) -> dict:
    check_token(x_worker_token)
    job = get_manager().get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Unknown job")
    response = job_response(job)
    if job.state in ("queued", "running"):
        response["worker_metrics"] = sample_worker_metrics()
    return response


@app.get("/api/v1/jobs/{job_id}/output")
def download_output(job_id: str, x_worker_token: Annotated[str | None, Header()] = None) -> FileResponse:
    check_token(x_worker_token)
    job = get_manager().get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Unknown job")
    if job.state != "completed":
        raise HTTPException(status_code=409, detail="Output is not ready")
    output = Path(job.output_path)
    if not output.is_file():
        raise HTTPException(status_code=404, detail="Output file is unavailable")
    digest = hashlib.sha256()
    with output.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return FileResponse(
        output,
        media_type="video/mp4",
        filename=f"{Path(job.input_name).stem}-rendered.mp4",
        headers={"X-Content-SHA256": digest.hexdigest()},
    )