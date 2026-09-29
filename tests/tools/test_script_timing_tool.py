from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from tools.subtitle.script_timing import ScriptTiming  # noqa: E402


def _transcription(tmp_path, words):
    p = tmp_path / "transcription.json"
    p.write_text(json.dumps({"segments": [{"words": words}]}), encoding="utf-8")
    return p


def test_tool_reads_a_transcriber_file_and_writes_the_timing(tmp_path):
    said = ["Bonjour", "à", "tous", "voici", "Clode", "qui", "répond"]
    words = [{"word": f" {w}", "start": i * 0.5, "end": i * 0.5 + 0.4} for i, w in enumerate(said)]
    out = tmp_path / "timing.json"
    r = ScriptTiming().execute({
        "text": "Bonjour à tous, voici Claude qui répond.",
        "transcription_path": str(_transcription(tmp_path, words)),
        "output_path": str(out),
    })
    assert r.success and r.data["status"] == "pass"
    saved = json.loads(out.read_text(encoding="utf-8"))
    assert [c["word"] for c in saved["captions"]] == ["Bonjour", "à", "tous,", "voici", "Claude", "qui", "répond."]


def test_tool_fails_when_nothing_was_heard(tmp_path):
    r = ScriptTiming().execute({"text": "Bonjour Claude.", "word_timestamps": []})
    assert not r.success and r.data["status"] == "fail"


def test_tool_refuses_without_a_transcription():
    r = ScriptTiming().execute({"text": "Bonjour."})
    assert not r.success and "word_timestamps" in r.error
