"""CustomTkinter desktop client for remote FFmpeg rendering (CSC-334)."""

from __future__ import annotations

import hashlib
import hmac
import json
import queue
import threading
import time
from pathlib import Path
from tkinter import filedialog, messagebox
from urllib.parse import quote

import customtkinter as ctk
import requests
from requests_toolbelt.multipart.encoder import MultipartEncoder, MultipartEncoderMonitor

MAX_POLL_FAILURES = 5  # kitni baar network error aaye to job fail mana jaye


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def raise_for_server_error(response: requests.Response) -> None:
    """Server ka asli error message dikhane ke liye."""
    if response.ok:
        return
    try:
        detail = response.json().get("detail", response.text)
    except ValueError:
        detail = response.text
    raise RuntimeError(f"Server error {response.status_code}: {detail}")


class RenderClient(ctk.CTk):
    def __init__(self) -> None:
        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("blue")
        super().__init__()
        self.title("Remote Render | CSC-334")
        self.geometry("860x720")
        self.minsize(740, 620)
        self.events: queue.Queue[tuple] = queue.Queue()
        self.busy = False
        self.output_dir = Path.home() / "Downloads"
        self._build_ui()
        self.after(100, self._drain_events)

    # ------------------------------------------------------------------ UI
    def _build_ui(self) -> None:
        heading = ctk.CTkFont(size=24, weight="bold")
        section = ctk.CTkFont(size=13, weight="bold")

        page = ctk.CTkFrame(self, fg_color="transparent")
        page.pack(fill="both", expand=True, padx=20, pady=16)
        ctk.CTkLabel(page, text="REMOTE RENDER", font=heading).pack(anchor="w")
        ctk.CTkLabel(page, text="CSC-334  /  distributed task offloading", text_color="gray").pack(anchor="w", pady=(0, 12))

        # --- Worker connection
        connection = ctk.CTkFrame(page)
        connection.pack(fill="x", pady=(0, 10))
        ctk.CTkLabel(connection, text="Worker connection", font=section).grid(row=0, column=0, columnspan=4, sticky="w", padx=12, pady=(10, 4))
        self.server_var = ctk.StringVar(value="http://127.0.0.1:8000")
        self.token_var = ctk.StringVar()
        ctk.CTkLabel(connection, text="Server URL").grid(row=1, column=0, sticky="w", padx=(12, 6), pady=6)
        ctk.CTkEntry(connection, textvariable=self.server_var).grid(row=1, column=1, sticky="ew", padx=(0, 12))
        ctk.CTkLabel(connection, text="Worker token").grid(row=1, column=2, sticky="w", padx=(0, 6))
        ctk.CTkEntry(connection, textvariable=self.token_var, show="*").grid(row=1, column=3, sticky="ew", padx=(0, 12))
        connection.columnconfigure(1, weight=1)
        connection.columnconfigure(3, weight=1)
        self.ping_button = ctk.CTkButton(connection, text="Ping worker", width=120, command=self._ping)
        self.ping_button.grid(row=2, column=1, sticky="w", pady=(4, 12))
        self.connection_status = ctk.StringVar(value="Not checked")
        ctk.CTkLabel(connection, textvariable=self.connection_status).grid(row=2, column=2, columnspan=2, sticky="w", pady=(4, 12))

        # --- Render job settings
        job = ctk.CTkFrame(page)
        job.pack(fill="x", pady=(0, 10))
        ctk.CTkLabel(job, text="Render job", font=section).grid(row=0, column=0, columnspan=5, sticky="w", padx=12, pady=(10, 4))

        self.file_var = ctk.StringVar()
        ctk.CTkLabel(job, text="Input video").grid(row=1, column=0, sticky="w", padx=(12, 6), pady=6)
        ctk.CTkEntry(job, textvariable=self.file_var).grid(row=1, column=1, columnspan=3, sticky="ew", padx=(0, 8))
        ctk.CTkButton(job, text="Browse...", width=100, command=self._browse).grid(row=1, column=4, padx=(0, 12))

        self.resolution_var = ctk.StringVar(value="1280x720")
        ctk.CTkLabel(job, text="Resolution").grid(row=2, column=0, sticky="w", padx=(12, 6), pady=6)
        ctk.CTkComboBox(job, variable=self.resolution_var, values=["640x360", "1280x720", "1920x1080", "2560x1440"], state="readonly", width=150).grid(row=2, column=1, sticky="w")
        self.bitrate_var = ctk.StringVar(value="5M")
        ctk.CTkLabel(job, text="Bitrate").grid(row=2, column=2, sticky="e", padx=(0, 6))
        ctk.CTkEntry(job, textvariable=self.bitrate_var, width=100).grid(row=2, column=3, sticky="w")

        self.codec_var = ctk.StringVar(value="h264_nvenc")
        ctk.CTkLabel(job, text="Codec").grid(row=3, column=0, sticky="w", padx=(12, 6), pady=(6, 12))
        ctk.CTkComboBox(job, variable=self.codec_var, values=["h264_nvenc", "hevc_nvenc", "libx264", "libx265"], state="readonly", width=150).grid(row=3, column=1, sticky="w", pady=(6, 12))
        self.preset_var = ctk.StringVar(value="p4")
        ctk.CTkLabel(job, text="Preset").grid(row=3, column=2, sticky="e", padx=(0, 6), pady=(6, 12))
        ctk.CTkComboBox(job, variable=self.preset_var, values=["p1", "p2", "p3", "p4", "p5", "p6", "p7", "ultrafast", "fast", "medium", "slow"], state="readonly", width=110).grid(row=3, column=3, sticky="w", pady=(6, 12))
        job.columnconfigure(1, weight=1)
        job.columnconfigure(3, weight=1)

        # --- Submit + progress
        controls = ctk.CTkFrame(page, fg_color="transparent")
        controls.pack(fill="x", pady=(0, 10))
        self.submit_button = ctk.CTkButton(controls, text="Submit render", height=38, command=self._submit)
        self.submit_button.pack(side="left")
        self.progress = ctk.CTkProgressBar(controls)
        self.progress.set(0)
        self.progress.pack(side="left", fill="x", expand=True, padx=14)
        self.progress_text = ctk.StringVar(value="Ready")
        ctk.CTkLabel(controls, textvariable=self.progress_text, width=190, anchor="w").pack(side="right")

        # --- Live log terminal
        ctk.CTkLabel(page, text="Worker log", font=section).pack(anchor="w")
        self.log = ctk.CTkTextbox(page, state="disabled", wrap="word", font=("Consolas", 12))
        self.log.pack(fill="both", expand=True, pady=(4, 0))

    def _log(self, message: str) -> None:
        self.log.configure(state="normal")
        self.log.insert("end", message.rstrip() + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    def _set_progress(self, percent: float) -> None:
        self.progress.set(max(0.0, min(100.0, percent)) / 100)

    def _browse(self) -> None:
        path = filedialog.askopenfilename(filetypes=(("Video files", "*.mp4 *.mov *.mkv *.avi *.webm"), ("All files", "*.*")))
        if path:
            self.file_var.set(path)

    def _headers(self) -> dict[str, str]:
        token = self.token_var.get().strip()
        return {"X-Worker-Token": token} if token else {}

    # ---------------------------------------------------------------- ping
    def _ping(self) -> None:
        if self.busy:
            return
        self.connection_status.set("Checking...")
        threading.Thread(
            target=self._ping_worker,
            args=(self.server_var.get().strip().rstrip("/"), self._headers()),
            daemon=True,
        ).start()

    def _ping_worker(self, base_url: str, headers: dict[str, str]) -> None:
        try:
            started = time.perf_counter()
            response = requests.get(base_url + "/api/v1/ping", params={"client_time": time.time()}, headers=headers, timeout=(4, 8))
            latency = (time.perf_counter() - started) * 1000
            raise_for_server_error(response)
            health = requests.get(base_url + "/api/v1/health", headers=headers, timeout=(4, 8))
            raise_for_server_error(health)
            ffmpeg = "ready" if health.json().get("ffmpeg_available") else "missing"
            self.events.put(("ping", f"Online | {latency:.1f} ms | FFmpeg {ffmpeg}"))
        except (requests.RequestException, RuntimeError, ValueError) as exc:
            self.events.put(("ping", f"Unavailable: {exc}"))

    # -------------------------------------------------------------- submit
    def _submit(self) -> None:
        if self.busy:
            return
        path = Path(self.file_var.get()).expanduser()
        if not path.is_file():
            messagebox.showerror("Input required", "Choose an existing video file first.")
            return
        try:
            width, height = (int(value) for value in self.resolution_var.get().split("x"))
            bitrate = self.bitrate_var.get().strip()
            if not bitrate[:-1].isdigit() or bitrate[-1:].lower() not in ("k", "m"):
                raise ValueError("Use a bitrate such as 2500k or 5M")
        except ValueError as exc:
            messagebox.showerror("Invalid settings", str(exc))
            return
        settings = {"width": width, "height": height, "bitrate": bitrate, "preset": self.preset_var.get(), "codec": self.codec_var.get()}
        self.busy = True
        self.submit_button.configure(state="disabled")
        self._set_progress(0)
        self.progress_text.set("Preparing upload")
        self._log(f"Selected {path.name} ({path.stat().st_size:,} bytes)")
        threading.Thread(
            target=self._submit_worker,
            args=(path, settings, self.server_var.get().strip().rstrip("/"), self._headers()),
            daemon=True,
        ).start()

    def _submit_worker(self, path: Path, settings: dict, base_url: str, headers: dict[str, str]) -> None:
        output_path: Path | None = None
        try:
            self.events.put(("log", "Calculating SHA-256..."))
            checksum = file_sha256(path)

            # ---- 1. Upload (live progress ke sath)
            self.events.put(("log", f"Uploading {path.name} ..."))
            upload_started = time.perf_counter()
            last_percent = -1
            with path.open("rb") as source:
                encoder = MultipartEncoder(fields={"file": (path.name, source, "application/octet-stream")})

                def on_upload(monitor: MultipartEncoderMonitor) -> None:
                    nonlocal last_percent
                    percent = monitor.bytes_read * 100 / monitor.len
                    if int(percent) != last_percent:
                        last_percent = int(percent)
                        self.events.put(("progress", percent, f"Uploading | {percent:.0f}%"))

                monitor = MultipartEncoderMonitor(encoder, on_upload)
                upload_headers = {
                    **headers,
                    "Content-Type": encoder.content_type,
                    "X-Content-SHA256": checksum,
                    "X-Render-Settings": json.dumps(settings, separators=(",", ":")),
                }
                response = requests.post(base_url + "/api/v1/jobs", data=monitor, headers=upload_headers, timeout=(10, 3600))
            raise_for_server_error(response)
            upload_time = time.perf_counter() - upload_started
            job_id = response.json()["job_id"]
            self.events.put(("log", f"Upload done ({upload_time:.2f} s). Accepted job {job_id}; waiting for worker progress."))

            # ---- 2. Render progress polling (network drop par retry)
            render_started = time.perf_counter()
            previous_log: list[str] = []
            failures = 0
            while True:
                try:
                    response = requests.get(f"{base_url}/api/v1/jobs/{quote(job_id)}", headers=headers, timeout=(5, 20))
                except (requests.ConnectionError, requests.Timeout):
                    failures += 1
                    if failures >= MAX_POLL_FAILURES:
                        raise RuntimeError("Lost connection to the worker (retries khatam)")
                    self.events.put(("log", f"Network issue, retrying ({failures}/{MAX_POLL_FAILURES}) ..."))
                    time.sleep(2)
                    continue
                failures = 0
                raise_for_server_error(response)
                job = response.json()
                self.events.put(("progress", job["progress"], f"{job['state'].title()} | {job['progress']:.1f}%"))
                current_log = job.get("log", [])
                new_entries = current_log[len(previous_log):] if current_log[:len(previous_log)] == previous_log else current_log
                for entry in new_entries:
                    self.events.put(("log", entry))
                previous_log = current_log
                if job["state"] == "failed":
                    raise RuntimeError(job.get("error") or "Worker render failed")
                if job["state"] == "completed":
                    break
                time.sleep(0.7)
            render_time = time.perf_counter() - render_started

            # ---- 3. Download + checksum verify
            self.events.put(("log", "Downloading rendered output ..."))
            download_started = time.perf_counter()
            self.output_dir.mkdir(parents=True, exist_ok=True)
            output_path = self.output_dir / f"{path.stem}-remote-render.mp4"
            response = requests.get(f"{base_url}/api/v1/jobs/{quote(job_id)}/output", headers=headers, stream=True, timeout=(5, 60))
            raise_for_server_error(response)
            expected_digest = response.headers.get("X-Content-SHA256", "")
            if not expected_digest:
                response.close()
                raise RuntimeError("Worker did not provide an output checksum")
            total_bytes = int(response.headers.get("Content-Length", 0))
            received = 0
            last_percent = -1
            digest = hashlib.sha256()
            with response, output_path.open("wb") as destination:
                for chunk in response.iter_content(chunk_size=1024 * 1024):
                    if chunk:
                        destination.write(chunk)
                        digest.update(chunk)
                        received += len(chunk)
                        if total_bytes and int(received * 100 / total_bytes) != last_percent:
                            last_percent = int(received * 100 / total_bytes)
                            self.events.put(("progress", last_percent, f"Downloading | {last_percent}%"))
            if not hmac.compare_digest(digest.hexdigest(), expected_digest):
                raise RuntimeError("Downloaded output checksum did not match")
            download_time = time.perf_counter() - download_started

            # ---- 4. Summary (benchmark report ke kaam aayega)
            total = upload_time + render_time + download_time
            self.events.put(("log", "-------- SUMMARY --------"))
            self.events.put(("log", f"Upload   : {upload_time:.2f} s"))
            self.events.put(("log", f"Render   : {render_time:.2f} s"))
            self.events.put(("log", f"Download : {download_time:.2f} s"))
            self.events.put(("log", f"Total    : {total:.2f} s"))
            self.events.put(("log", "-------------------------"))
            self.events.put(("done", f"Saved verified output: {output_path}"))
            return
        except requests.Timeout:
            error = "Network timeout - worker ne waqt par jawab nahi diya."
        except requests.ConnectionError:
            error = "Connection lost - worker tak nahi pahunch sakte. IP/cable check karo aur dekho server chal raha hai."
        except (requests.RequestException, OSError, RuntimeError, KeyError, ValueError) as exc:
            error = str(exc)
        if output_path is not None:
            output_path.unlink(missing_ok=True)
        self.events.put(("error", error))

    # ---------------------------------------------------------- UI events
    def _drain_events(self) -> None:
        try:
            while True:
                event = self.events.get_nowait()
                if event[0] == "ping":
                    self.connection_status.set(event[1])
                    self._log("Ping: " + event[1])
                elif event[0] == "log":
                    self._log(event[1])
                elif event[0] == "progress":
                    self._set_progress(event[1])
                    self.progress_text.set(event[2])
                elif event[0] == "done":
                    self._log(event[1])
                    self._set_progress(100)
                    self.progress_text.set("Complete")
                    self.busy = False
                    self.submit_button.configure(state="normal")
                    messagebox.showinfo("Render complete", event[1])
                elif event[0] == "error":
                    self._log("ERROR: " + event[1])
                    self.progress_text.set("Failed")
                    self.busy = False
                    self.submit_button.configure(state="normal")
                    messagebox.showerror("Render failed", event[1])
        except queue.Empty:
            pass
        self.after(100, self._drain_events)


def main() -> None:
    app = RenderClient()
    app.mainloop()


if __name__ == "__main__":
    main()