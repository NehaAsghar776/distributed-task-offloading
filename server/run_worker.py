"""Start the rendering worker with an explicit authentication token."""

from __future__ import annotations

import argparse
import os
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the CSC-334 remote render worker")
    parser.add_argument("--host", default="0.0.0.0", help="Bind address (default: all interfaces)")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--token", required=True, help="Shared secret used by client and worker")
    parser.add_argument("--data-dir", default="worker_data")
    parser.add_argument("--workers", type=int, default=1)
    args = parser.parse_args()
    if len(args.token) < 16:
        parser.error("--token must be at least 16 characters")
    os.environ["WORKER_TOKEN"] = args.token
    os.environ["WORKER_DATA_DIR"] = args.data_dir
    os.environ["WORKER_CONCURRENCY"] = str(max(1, args.workers))

    import uvicorn

    project_root = Path(__file__).resolve().parent.parent
    uvicorn.run("server.api:app", app_dir=str(project_root), host=args.host, port=args.port, reload=False)


if __name__ == "__main__":
    main()