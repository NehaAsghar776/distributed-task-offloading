# CSC-334 Distributed Render Worker

A client/server prototype for offloading video transcodes from a laptop to a worker computer. The worker accepts a streamed upload, checks its SHA-256 digest, queues an FFmpeg job, exposes progress/log state, and serves a verified MP4 when rendering finishes. The default remote codec is NVIDIA NVENC (`h264_nvenc`); `libx264`/`libx265` are available for CPU-only development and testing.

> **Hardware note:** NVENC requires an NVIDIA GPU, a compatible NVIDIA driver, and an FFmpeg build compiled with NVENC. This project reports capability availability; it does not emulate GPU acceleration. The CPU codec is not evidence of GPU execution.

## Repository Contents

- `client/`: Tkinter desktop interface.
- `server/`: authenticated FastAPI worker and bounded FFmpeg job executor.
- `tests/`: focused tests for job state and configuration validation.
- `benchmarks/`: repeatable local-versus-remote measurement script.
- `reports/`: methodology and results template.
- `screenshots/`, `demos/`: reserved for genuine run evidence.

## Requirements

- Python 3.10 or newer on client and worker.
- FFmpeg on both machines; FFprobe is recommended for progress percentages.
- NVIDIA GPU and NVENC-capable FFmpeg on the worker for the GPU path.
- Both computers reachable on the same LAN/Wi-Fi subnet, or a properly secured routed network.

Install Python dependencies from the repository root on both machines:

```powershell
py -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

On Linux/macOS, activate with `source .venv/bin/activate` instead. If Windows blocks activation, use `\.venv\Scripts\python.exe` directly.

Install FFmpeg separately using the operating system's trusted package manager or official FFmpeg distribution. Verify `ffmpeg -version`, `ffprobe -version`, and (on the worker) `ffmpeg -hide_banner -encoders`.

## Start the Worker

From the repository root on the worker computer, create a long random shared token locally and keep it out of source control and screenshots. For example, in PowerShell:

```powershell
$token = python -c "import secrets; print(secrets.token_urlsafe(32))"
python server/run_worker.py --host 0.0.0.0 --port 8000 --token $token --data-dir worker_data
```

The launcher refuses tokens shorter than 16 characters. The API requires the token on all job, capabilities, and output routes. Health and ping intentionally remain unauthenticated for reachability checks. Uploaded inputs and rendered outputs are stored under `worker_data/`; do not commit that directory. Stop the service with `Ctrl+C`.

Check the worker from a browser at `http://WORKER_IP:8000/docs` or request `/api/v1/health`. The interactive API page does not carry the token automatically, so protected operations from there require the `X-Worker-Token` header. `/api/v1/capabilities` reports FFmpeg/NVENC availability.

## Network Setup

Use the router's DHCP reservation feature where possible; it avoids address collisions while keeping each device's network configuration manageable. A direct Ethernet connection or a dedicated private Wi-Fi network also works. Do not expose this prototype to the public internet: authentication is a shared token over plain HTTP, not TLS.

Example private IPv4 plan (choose addresses appropriate for your subnet):

| Device | Address | Mask | Gateway |
| --- | --- | --- | --- |
| Worker | `192.168.1.10` | `255.255.255.0` | router address |
| Client | `192.168.1.11` | `255.255.255.0` | router address |

On Windows, open **Settings → Network & internet → Advanced network settings → More network adapter options**, open the adapter's **Properties → Internet Protocol Version 4**, and enter the chosen address/mask/gateway/DNS. On Linux, configure the address using NetworkManager or `nmcli`; on macOS, use **System Settings → Network → Details → TCP/IP**. Use a subnet that does not conflict with the router or VPN. Restore DHCP after a direct-link experiment if required.

Allow inbound TCP port `8000` only on the worker's Private network profile (Windows Defender Firewall), or the equivalent narrowly scoped firewall rule on Linux. Confirm reachability from the client with `Test-NetConnection WORKER_IP -Port 8000` in PowerShell or `nc -vz WORKER_IP 8000`. ICMP ping can be disabled by a firewall; the application ping endpoint is an HTTP round trip and is the authoritative check here.

## Launch the Client

On the client computer, run:

```powershell
python -m client.client
```

Enter `http://WORKER_IP:8000`, paste the same token into **Worker token**, and choose **Ping worker**. Select a video, set output resolution/bitrate/codec/preset, then submit. The default codec is `h264_nvenc`; choose `libx264` if the worker has no supported NVIDIA GPU. The progress bar and worker log update while FFmpeg runs. A successful output is saved to the user's Downloads folder and checked against the worker's SHA-256 response header.

For headless environments, use the REST API directly. Upload metadata uses `X-Render-Settings` (compact JSON) and `X-Content-SHA256` headers; uploads are streamed and limited by `WORKER_MAX_UPLOAD_BYTES` (default 10 GiB).

## Tests

```powershell
python -m unittest discover -s tests -v
```

Tests cover job-state isolation and settings validation. A full media integration test requires FFmpeg; an NVENC integration test additionally requires an NVENC-capable worker. Never claim GPU results from a CPU codec run.

## Benchmarking

Run the benchmark from the repository root with the worker running and a representative input file:

```powershell
python benchmarks/benchmark.py .\sample.mp4 --server http://WORKER_IP:8000 --token $token --width 1280 --height 720 --output benchmarks/results/720p-small.json
```

Repeat across multiple input sizes and resolutions, keeping the same source, output settings, and network conditions for each local/remote pair. The script records local encode time, upload time, remote wall-clock render time, download time, end-to-end time, host specifications, worker capability report, output size, and calculated speedups. It writes JSON and CSV files. Results are hardware- and network-specific; do not use example numbers as measured results. Review [reports/performance.md](reports/performance.md) for interpretation and limitations.

The transfer/overhead figure is computed as remote end-to-end time minus the measured worker wait/render interval. It approximates upload plus download overhead; upload and download are also reported separately. Polling is included in the worker wait/render interval, and small orchestration timing boundaries are not isolated. Render-only speedup excludes transfer and is not the user's experienced end-to-end speedup.

## Evidence and GitHub Submission

Capture actual runs on the client/worker hardware and add screenshots to `screenshots/` and a short screen recording or GIF to `demos/`. Include at least the successful handshake, settings/job submission, progress/log updates, completed output, and a clearly labeled NVENC capability check. See each folder's README. No screenshots, videos, or benchmark measurements are pre-filled because they must reflect your real execution.

Before submission:

1. Create a **public** GitHub repository and push this project, excluding tokens, virtual environments, media inputs/outputs, and private data.
2. Add `githubprojectmin@gmail.com` as a collaborator in the repository's **Settings → Collaborators** and confirm the invite was sent.
3. Replace any personal-information placeholders in your own submission notes/form with your university-registered full name and registration number. This repository does not contain either value.
4. Add real screenshots, demonstration media, and measured benchmark results; verify they are visible from a logged-out browser.
5. Submit only the public repository URL in the official form. Do not submit passwords, access tokens, or private keys.

Publishing, inviting collaborators, and submitting the form require the student's authenticated GitHub/Google account and are intentionally not automated here.

## API Summary

- `GET /api/v1/health`: worker service and FFmpeg availability.
- `GET /api/v1/ping`: application-level round-trip check.
- `GET /api/v1/capabilities`: protected FFmpeg/NVENC and upload-limit report.
- `POST /api/v1/jobs`: protected multipart upload with SHA-256 and render settings.
- `GET /api/v1/jobs/{job_id}`: protected asynchronous status/progress/log.
- `GET /api/v1/jobs/{job_id}/output`: protected completed MP4 and SHA-256 header.

The queue and job history are in-memory and reset when the worker restarts. This is an assignment-scale prototype, not a durable production queue. Keep the worker on a trusted private network and restrict firewall access.