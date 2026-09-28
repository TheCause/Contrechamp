"""`extract` checked against the real ffmpeg output, not the command shape.

The sibling test replaces `run_command` and inspects the argv. This one runs
ffmpeg on a synthetic source and reads the result back with ffprobe, so it
also catches a non-PCM codec slipping into a .wav, or an output file that was
never written. Skipped when ffmpeg/ffprobe are absent.
"""

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from tools.audio.audio_mixer import AudioMixer  # noqa: E402

pytestmark = pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="ffmpeg/ffprobe not installed",
)


def _make_source(path: Path, rate: int, channels: int) -> None:
    layout = "stereo" if channels == 2 else "mono"
    subprocess.run(
        [
            "ffmpeg", "-y", "-loglevel", "error",
            "-f", "lavfi", "-i", f"sine=frequency=440:sample_rate={rate}:duration=1",
            "-af", f"aformat=channel_layouts={layout}",
            "-c:a", "aac",
            str(path),
        ],
        check=True,
    )


def _probe_audio(path: Path) -> dict:
    out = subprocess.run(
        [
            "ffprobe", "-v", "error", "-select_streams", "a:0",
            "-show_entries", "stream=codec_name,sample_rate,channels",
            "-of", "json", str(path),
        ],
        check=True, capture_output=True, text=True,
    ).stdout
    return json.loads(out)["streams"][0]


def _extract(tmp_path: Path, source: Path, out_name: str) -> Path:
    out = tmp_path / out_name
    result = AudioMixer().execute({
        "operation": "extract",
        "input_path": str(source),
        "output_path": str(out),
    })
    assert result.success, result.error
    assert out.exists() and out.stat().st_size > 0, f"no audio written to {out}"
    return out


def test_wav_extract_keeps_48k_stereo_as_pcm(tmp_path) -> None:
    source = tmp_path / "clip.m4a"
    _make_source(source, 48000, 2)
    stream = _probe_audio(_extract(tmp_path, source, "out.wav"))
    assert stream["codec_name"].startswith("pcm_"), stream
    assert int(stream["sample_rate"]) == 48000, stream
    assert int(stream["channels"]) == 2, stream


def test_m4a_extract_actually_produces_a_file(tmp_path) -> None:
    source = tmp_path / "clip.m4a"
    _make_source(source, 48000, 2)
    stream = _probe_audio(_extract(tmp_path, source, "out.m4a"))
    assert int(stream["sample_rate"]) == 48000, stream
    assert int(stream["channels"]) == 2, stream


def test_speech_source_stays_16k_mono(tmp_path) -> None:
    """Healthy case: a source already at 16 kHz mono comes out unchanged."""
    source = tmp_path / "speech.m4a"
    _make_source(source, 16000, 1)
    stream = _probe_audio(_extract(tmp_path, source, "out.wav"))
    assert stream["codec_name"].startswith("pcm_"), stream
    assert int(stream["sample_rate"]) == 16000, stream
    assert int(stream["channels"]) == 1, stream
