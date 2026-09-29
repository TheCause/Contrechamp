"""Backlot must exit on SIGTERM even while a board keeps its live stream open.

Seen on a real machine: with a browser tab subscribed to /api/library/events,
the server released its port on SIGTERM but the process never exited, because
uvicorn waited forever for the SSE connection to close.
"""

from __future__ import annotations

import http.client
import os
import signal
import socket
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]

pytestmark = pytest.mark.skipif(os.name == "nt", reason="POSIX signals")


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _wait_healthy(port: int, proc: subprocess.Popen) -> None:
    deadline = time.time() + 20
    while time.time() < deadline:
        if proc.poll() is not None:
            raise AssertionError(f"server exited early: {proc.stderr.read()}")
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/health", timeout=1):
                return
        except OSError:
            time.sleep(0.2)
    raise AssertionError("server did not come up")


def test_sigterm_exits_while_a_live_stream_is_open(tmp_path):
    (tmp_path / "projects" / "essai").mkdir(parents=True)
    port = _free_port()
    env = {**os.environ, "CONTRECHAMP_PROJECTS_DIR": str(tmp_path / "projects")}
    proc = subprocess.Popen([sys.executable, "-m", "backlot", "serve", "--port", str(port)],
                            cwd=ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
    try:
        _wait_healthy(port, proc)
        stream = urllib.request.urlopen(f"http://127.0.0.1:{port}/api/library/events", timeout=30)
        assert b"hello" in stream.readline()  # the board is subscribed

        def drain() -> None:
            try:
                stream.read()
            except (OSError, http.client.HTTPException):
                pass  # the server closing the stream on shutdown is the expected end

        threading.Thread(target=drain, daemon=True).start()

        proc.send_signal(signal.SIGTERM)
        start = time.time()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            pytest.fail("still running 10 s after SIGTERM with a live stream open")
        assert time.time() - start < 10
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()
