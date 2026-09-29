#!/usr/bin/env bash
# Install the official VoxCPM2 engine (openbmb, Apache-2.0) in its own venv.
#
# voxcpm needs Python 3.10-3.12, while Contrechamp's own venv may be newer, so
# the engine lives in a separate venv: .venv-voxcpm at the repository root.
# voxcpm2_tts finds it there without any variable. Remove it with
# `rm -rf .venv-voxcpm`.
#
#   make setup-voxcpm2                      # install, weights fetched on first use
#   VOXCPM2_DOWNLOAD=1 make setup-voxcpm2   # also fetch the weights now (~4.6 GB)
#
# Tested on macOS (Apple Silicon, MPS). Linux with CUDA should work the same
# way but has not been tried.
set -euo pipefail

cd "$(dirname "$0")/.."
VENV="${VOXCPM_VENV:-.venv-voxcpm}"
VOXCPM_VERSION="${VOXCPM_VERSION:-2.0.3}"

supported() {  # 0 if the interpreter is Python 3.10-3.12
    "$1" -c 'import sys; raise SystemExit(0 if (3, 10) <= sys.version_info[:2] <= (3, 12) else 1)' 2>/dev/null
}

if [ -x "$VENV/bin/python" ] && supported "$VENV/bin/python"; then
    echo "==> Using existing $VENV"
else
    if [ -e "$VENV" ]; then
        echo "ERROR: $VENV exists but does not hold Python 3.10-3.12; remove it and run again." >&2
        exit 1
    fi
    if command -v uv >/dev/null 2>&1; then
        echo "==> Creating $VENV with uv (Python 3.12)"
        uv venv --python 3.12 --seed "$VENV"
    else
        base=""
        for candidate in python3.12 python3.11 python3.10; do
            if command -v "$candidate" >/dev/null 2>&1 && supported "$(command -v "$candidate")"; then
                base="$(command -v "$candidate")"
                break
            fi
        done
        if [ -z "$base" ]; then
            echo "ERROR: VoxCPM2 needs Python 3.10, 3.11 or 3.12 (not 3.13 or newer)." >&2
            echo "Install uv (https://docs.astral.sh/uv/) or one of those Python versions, then run again." >&2
            exit 1
        fi
        echo "==> Creating $VENV with $base"
        "$base" -m venv "$VENV"
    fi
fi

PY="$VENV/bin/python"
echo "==> Installing voxcpm $VOXCPM_VERSION (pulls PyTorch, a few GB)"
"$PY" -m pip install --quiet --upgrade pip
"$PY" -m pip install --quiet "voxcpm==$VOXCPM_VERSION" soundfile

echo "==> Checking the engine"
"$PY" - <<'EOF'
import torch
import voxcpm  # noqa: F401

if torch.backends.mps.is_available():
    device = "mps (Apple GPU)"
elif torch.cuda.is_available():
    device = "cuda (NVIDIA GPU)"
else:
    device = "cpu only: works, but slowly"
print(f"    voxcpm imported, torch {torch.__version__}, device: {device}")
EOF

if [ "${VOXCPM2_DOWNLOAD:-0}" = "1" ]; then
    echo "==> Downloading openbmb/VoxCPM2 weights (~4.6 GB)"
    "$PY" -c "from huggingface_hub import snapshot_download; print('    ' + snapshot_download('openbmb/VoxCPM2'))"
else
    echo "==> Weights not downloaded: fetched on first use (~4.6 GB), or run with VOXCPM2_DOWNLOAD=1"
fi

if [ "$VENV" = ".venv-voxcpm" ]; then
    found="voxcpm2_tts now finds $VENV without any variable."
else
    found="Custom location: set VOXCPM2_PYTHON=$(cd "$VENV" && pwd)/bin/python (e.g. in .env)."
fi
cat <<EOF

Done. $found
  Licence: VoxCPM2 code and weights are Apache-2.0 (openbmb/VoxCPM2).
  Named voices: voices/<name>.wav (+ .txt transcript, + .json defaults), gitignored.
  Default narration voice for this machine: CONTRECHAMP_NARRATION_VOICE=<name> in .env
EOF
