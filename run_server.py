#!/usr/bin/env python3
"""Run Max Pro Cloud Licensing Server locally."""
import os
import sys
from pathlib import Path

# Add project root to sys.path
ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR))

# Configure stdout/stderr for Unicode safety on Windows
try:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import uvicorn

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    host = os.environ.get("HOST", "0.0.0.0")
    print(f"Starting Max Pro Cloud Licensing Server on http://{host}:{port}")
    print(f"Admin Dashboard: http://localhost:{port}/admin")
    uvicorn.run("cloud_server.app:app", host=host, port=port, reload=False)
