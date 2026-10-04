"""Measure local FFmpeg rendering against end-to-end remote offloading."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import platform
import subprocess
import tempfile
import time
from pathlib import Path

import psutil
import requests
from requests_toolbelt.multipart.encoder import MultipartEncoder


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def ffmpeg_command(ffmpeg: str, input_path: Path, output_path: Path, settings: dict, codec: str) -> list[str]:
    preset = settings["preset"] if "nvenc" in codec else "medium"
    return [
        ffmpeg, "-y", "-hide_banner", "-loglevel", "error", "-i", str(input_path),
        "-vf", f"scale={settings['width']}:{settings['height']}:force_original_aspect_ratio=decrease,pad={settings['width']}:{settings['height']}:(ow-iw)/2:(oh-ih)/2",
        "-c:v", codec, "-b:v", settings["bitrate"], "-preset", preset,
        "-c:a", "aac", str(output_path),
    ]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    parser.add_argument("--server", required=True, help="Worker base URL, for example http://192.168.1.1:8000")
    parser.add_argument("--token", required=True)
    parser.add_argument("--width", type=int, default=1280)
    parser.add_argument("--height", type=int, default=720)
    parser.add_argument("--bitrate", default="5M")
    parser.add_argument("--preset", default="p4")
    parser.add_argument("--remote-codec", default="h264_nvenc")
    parser.add_argument("--local-codec", default="libx264")
    parser.add_argument("--output", type=Path, default=Path("benchmarks/results/latest.json"))
    parser.add_argument("--ffmpeg", default="ffmpeg")
    args = parser.parse_args()
    if not args.input.is_file():
        parser.error(f"Input file does not exist: {args.input}")

    settings = {"width": args.width, "height": args.height, "bitrate": args.bitrate, "preset": args.preset, "codec": args.remote_codec}
    headers = {"X-Worker-Token": args.token}
    base_url = args.server.rstrip("/")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    report = {
        "input": str(args.input.resolve()),
        "input_bytes": args.input.stat().st_size,
        "input_sha256": sha256_file(args.input),
        "resolution": f"{args.width}x{args.height}",
        "host": {
            "os": platform.platform(),
            "processor": platform.processor() or platform.machine(),
            "logical_cpus": psutil.cpu_count(),
            "memory_bytes": psutil.virtual_memory().total,
        },
    }

    with tempfile.TemporaryDirectory(prefix="csc334-bench-") as temporary:
        local_output = Path(temporary) / "local.mp4"
        local_started = time.perf_counter()
        local_process = subprocess.run(ffmpeg_command(args.ffmpeg, args.input, local_output, settings, args.local_codec), check=False)
        report["local_seconds"] = time.perf_counter() - local_started
        if local_process.returncode != 0:
            raise SystemExit(f"Local FFmpeg failed with exit code {local_process.returncode}")

        worker_info = requests.get(base_url + "/api/v1/capabilities", headers=headers, timeout=(5, 15))
        worker_info.raise_for_status()
        report["worker_capabilities"] = worker_info.json()
        end_to_end_started = time.perf_counter()
        upload_started = time.perf_counter()
        with args.input.open("rb") as source:
            encoder = MultipartEncoder(fields={"file": (args.input.name, source, "application/octet-stream")})
            upload_headers = {
                **headers,
                "Content-Type": encoder.content_type,
                "X-Content-SHA256": report["input_sha256"],
                "X-Render-Settings": json.dumps(settings, separators=(",", ":")),
            }
            response = requests.post(base_url + "/api/v1/jobs", data=encoder, headers=upload_headers, timeout=(10, 3600))
        response.raise_for_status()
        report["upload_seconds"] = time.perf_counter() - upload_started
        job_id = response.json()["job_id"]
        remote_render_started = time.perf_counter()
        worker_samples = []
        while True:
            response = requests.get(f"{base_url}/api/v1/jobs/{job_id}", headers=headers, timeout=(5, 20))
            response.raise_for_status()
            job = response.json()
            if job.get("worker_metrics"):
                worker_samples.append(job["worker_metrics"])
            if job["state"] == "failed":
                raise SystemExit(f"Remote render failed: {job.get('error')}")
            if job["state"] == "completed":
                break
            time.sleep(0.5)
        report["remote_render_seconds"] = time.perf_counter() - remote_render_started
        report["worker_metric_samples"] = worker_samples
        if worker_samples:
            report["worker_resource_summary"] = {
                "sample_count": len(worker_samples),
                "max_cpu_percent": max(sample["cpu_percent"] for sample in worker_samples),
                "max_memory_percent": max(sample["memory_percent"] for sample in worker_samples),
                "max_gpu_utilization_percent": max(
                    (sample["gpu_utilization_percent"] for sample in worker_samples if sample["gpu_utilization_percent"] is not None),
                    default=None,
                ),
                "max_gpu_memory_used_mb": max(
                    (sample["gpu_memory_used_mb"] for sample in worker_samples if sample["gpu_memory_used_mb"] is not None),
                    default=None,
                ),
            }

        download_started = time.perf_counter()
        output_path = args.output.with_suffix(".mp4")
        output_digest = hashlib.sha256()
        with requests.get(f"{base_url}/api/v1/jobs/{job_id}/output", headers=headers, stream=True, timeout=(5, 60)) as response:
            response.raise_for_status()
            expected_digest = response.headers.get("X-Content-SHA256", "")
            with output_path.open("wb") as destination:
                for chunk in response.iter_content(chunk_size=1024 * 1024):
                    if chunk:
                        destination.write(chunk)
                        output_digest.update(chunk)
        if expected_digest and output_digest.hexdigest() != expected_digest:
            output_path.unlink(missing_ok=True)
            raise SystemExit("Downloaded remote output failed SHA-256 verification")
        report["download_seconds"] = time.perf_counter() - download_started
        report["remote_end_to_end_seconds"] = time.perf_counter() - end_to_end_started
        report["remote_output_bytes"] = output_path.stat().st_size
        report["remote_output"] = str(output_path.resolve())

    report["remote_overhead_seconds"] = report["remote_end_to_end_seconds"] - report["remote_render_seconds"]
    report["speedup_end_to_end"] = report["local_seconds"] / report["remote_end_to_end_seconds"]
    report["speedup_render_only"] = report["local_seconds"] / report["remote_render_seconds"]
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    csv_path = args.output.with_suffix(".csv")
    with csv_path.open("w", newline="", encoding="utf-8") as result_file:
        writer = csv.DictWriter(result_file, fieldnames=report.keys())
        writer.writeheader()
        writer.writerow(report)
    print(json.dumps({key: report[key] for key in ("local_seconds", "upload_seconds", "remote_render_seconds", "download_seconds", "remote_end_to_end_seconds", "speedup_end_to_end")}, indent=2))
    print(f"Saved: {args.output} and {csv_path}")


if __name__ == "__main__":
    main()