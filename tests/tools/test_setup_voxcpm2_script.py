"""scripts/setup_voxcpm2.sh refuses an unsupported Python before installing anything."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "setup_voxcpm2.sh"


def _fake_bin(tmp_path: Path) -> Path:
    """A PATH holding only what the script needs, and Pythons that report 3.13."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "dirname").symlink_to(shutil.which("dirname"))
    for name in ("python3.12", "python3.11", "python3.10"):
        exe = bin_dir / name
        exe.write_text("#!/bin/sh\nexit 1\n")  # the version check fails, as on 3.13
        exe.chmod(0o755)
    return bin_dir


def test_script_is_valid_bash():
    assert subprocess.run(["bash", "-n", str(SCRIPT)]).returncode == 0


def test_no_supported_python_is_a_clear_refusal(tmp_path):
    venv = tmp_path / "venv-voxcpm"
    proc = subprocess.run(
        [shutil.which("bash"), str(SCRIPT)],
        env={"PATH": str(_fake_bin(tmp_path)), "VOXCPM_VENV": str(venv), "HOME": str(tmp_path)},
        capture_output=True, text=True, timeout=30,
    )
    assert proc.returncode != 0
    assert "3.10, 3.11 or 3.12" in proc.stderr
    assert not venv.exists()


def test_make_target_runs_the_script():
    assert "setup-voxcpm2:\n\t@bash scripts/setup_voxcpm2.sh" in (ROOT / "Makefile").read_text()


def test_the_engine_venv_is_ignored_even_as_a_symlink(tmp_path):
    # a symlink to an engine installed elsewhere is not a directory for git
    link = ROOT / ".venv-voxcpm"
    created = False
    if not link.exists() and not link.is_symlink():
        link.symlink_to(tmp_path)
        created = True
    try:
        proc = subprocess.run(["git", "check-ignore", "-q", ".venv-voxcpm"], cwd=ROOT)
        assert proc.returncode == 0
    finally:
        if created:
            link.unlink()
