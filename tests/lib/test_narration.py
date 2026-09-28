"""Narration by chunks: approved text only, stable chunk ids, per-chunk check."""
from __future__ import annotations

import json
import math
import struct
import sys
import wave
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from lib import narration as nar  # noqa: E402


def _sentence(i, n=10):
    return " ".join(f"mot{i}x{k}" for k in range(n - 1)) + f" fin{i}."


def _script(n_sentences=(11, 2), words=10):
    return {"sections": [
        {"id": f"s{j}", "text": " ".join(_sentence(f"{j}_{i}", words) for i in range(n))}
        for j, n in enumerate(n_sentences)
    ]}


def _tone(path: Path, seconds=0.2, rate=24000):
    with wave.open(str(path), "w") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(rate)
        w.writeframes(b"".join(struct.pack("<h", int(3000 * math.sin(i / 8))) for i in range(int(rate * seconds))))


class FakeVoice:
    def __init__(self):
        self.calls = []

    def __call__(self, text, path):
        self.calls.append(text)
        _tone(path)


def _hear_exactly(text_by_path):
    def transcribe(path):
        return [{"word": w, "start": i * 0.3, "end": i * 0.3 + 0.25}
                for i, w in enumerate(text_by_path(path).split())]
    return transcribe


def _approve(project, script):
    project.mkdir(parents=True, exist_ok=True)
    (project / "checkpoint_script.json").write_text(json.dumps({
        "stage": "script", "status": "completed", "human_approved": True,
        "artifacts": {"script": script}}), encoding="utf-8")


# --- chunk plan --------------------------------------------------------------


def test_chunks_cut_between_sentences_within_a_section():
    chunks = nar.plan_chunks(_script((11, 2)))
    assert [c["section"] for c in chunks] == ["s0", "s0", "s1"]
    assert all(c["text"].endswith(".") for c in chunks)
    assert chunks[0]["words"] <= 75


def test_a_short_tail_joins_the_previous_chunk():
    # 8 sentences of 10 words: 70 + 10 -> the 10-word tail is merged, not left alone
    chunks = nar.plan_chunks(_script((8,)))
    assert [c["words"] for c in chunks] == [80]


def test_a_chunk_that_stays_short_is_flagged_for_drift():
    chunks = nar.plan_chunks(_script((11, 2)))
    assert [c["drift_risk"] for c in chunks] == [False, False, True]


# --- approval ---------------------------------------------------------------


def test_voice_refuses_a_text_changed_since_approval(tmp_path):
    script = _script((2,))
    _approve(tmp_path / "proj", script)
    edited = json.loads(json.dumps(script))
    edited["sections"][0]["text"] = edited["sections"][0]["text"].replace("fin0_1.", "ajouté.")
    voice = FakeVoice()
    r = nar.narrate(edited, tmp_path / "out", voice, project_dir=tmp_path / "proj")
    assert r["status"] == "refused" and r["approval"]["status"] == "changed_since_approval"
    assert "ajouté." in r["approval"]["reason"] and voice.calls == []


def test_approved_text_is_spoken(tmp_path):
    script = _script((2,))
    _approve(tmp_path / "proj", script)
    r = nar.narrate(script, tmp_path / "out", FakeVoice(), project_dir=tmp_path / "proj")
    assert r["approval"]["status"] == "approved" and Path(r["narration"]).exists()


def test_unapproved_project_is_refused_and_no_project_is_not_checked(tmp_path):
    script = _script((2,))
    (tmp_path / "proj").mkdir()
    r = nar.narrate(script, tmp_path / "o1", FakeVoice(), project_dir=tmp_path / "proj")
    assert r["status"] == "refused"
    r = nar.narrate(script, tmp_path / "o2", FakeVoice())
    assert r["approval"]["status"] == "not_checked" and r["status"] == "not_checked"


# --- stable ids and partial regeneration -------------------------------------


def test_regenerating_one_chunk_touches_only_that_chunk(tmp_path):
    script = _script((11, 2))
    voice = FakeVoice()
    nar.narrate(script, tmp_path, voice)
    assert len(voice.calls) == 3
    voice.calls.clear()
    r = nar.narrate(script, tmp_path, voice, only=["c02"])
    assert r["plan"] == "reused" and len(voice.calls) == 1
    assert voice.calls[0] == r["chunks"][1]["text"]


def test_editing_one_chunk_keeps_ids_and_resynthesizes_only_it(tmp_path):
    script = _script((11, 2))
    voice = FakeVoice()
    nar.narrate(script, tmp_path, voice)
    voice.calls.clear()
    edited = json.loads(json.dumps(script))
    edited["sections"][1]["text"] = edited["sections"][1]["text"].replace("fin1_0.", "autre.")
    r = nar.narrate(edited, tmp_path, voice)
    assert r["plan"] == "updated" and [c["id"] for c in r["chunks"]] == ["c01", "c02", "c03"]
    assert voice.calls == [r["chunks"][2]["text"]]


def test_a_new_cut_is_never_renumbered_silently(tmp_path):
    nar.narrate(_script((11, 2)), tmp_path, FakeVoice())
    with pytest.raises(ValueError, match="replan=True"):
        nar.narrate(_script((11, 2, 3)), tmp_path, FakeVoice())
    r = nar.narrate(_script((11, 2, 3)), tmp_path, FakeVoice(), replan=True)
    assert r["plan"] == "replanned" and len(r["chunks"]) == 4


# --- per-chunk check ----------------------------------------------------------


def test_a_chunk_heard_wrong_is_suspect(tmp_path):
    script = _script((11, 2))
    chunks = nar.plan_chunks(script)
    by_id = {c["text_fingerprint"]: c["text"] for c in chunks}

    def heard(path):
        text = by_id[path.stem.split("_", 1)[1]]
        if path.stem.startswith("c02"):
            text = " ".join(text.split()[:20])  # the voice stopped early
        return text

    r = nar.narrate(script, tmp_path, FakeVoice(), _hear_exactly(heard))
    assert r["status"] == "revise" and r["suspect"] == ["c02"]


def test_chunks_heard_right_pass(tmp_path):
    script = _script((11, 2))
    by_id = {c["text_fingerprint"]: c["text"] for c in nar.plan_chunks(script)}
    r = nar.narrate(script, tmp_path, FakeVoice(),
                    _hear_exactly(lambda p: by_id[p.stem.split("_", 1)[1]]))
    assert r["status"] == "pass" and r["suspect"] == []
