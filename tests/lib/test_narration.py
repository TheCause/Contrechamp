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


def _tone(path: Path, seconds=0.2, rate=24000, pitch=8):
    with wave.open(str(path), "w") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(rate)
        w.writeframes(b"".join(struct.pack("<h", int(3000 * math.sin(i / pitch))) for i in range(int(rate * seconds))))


class FakeVoice:
    def __init__(self):
        self.calls = []

    def __call__(self, text, path, attempt=0):
        self.calls.append(text)
        self.attempts = getattr(self, "attempts", []) + [attempt]
        _tone(path, pitch=8 + attempt)   # a new take sounds different


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
    assert r["status"] == "refused" and "project_dir" in r["approval"]["reason"]
    r = nar.narrate(script, tmp_path / "o3", FakeVoice(), require_approval=False)
    assert r["approval"]["status"] == "not_checked" and r["approval_waived"] is True


# --- stable ids and partial regeneration -------------------------------------


def test_regenerating_one_chunk_touches_only_that_chunk(tmp_path):
    script = _script((11, 2))
    voice = FakeVoice()
    nar.narrate(script, tmp_path, voice, require_approval=False)
    assert len(voice.calls) == 3
    voice.calls.clear()
    r = nar.narrate(script, tmp_path, voice, only=["c02"], require_approval=False)
    assert r["plan"] == "reused" and len(voice.calls) == 1
    assert voice.calls[0] == r["chunks"][1]["text"]


def test_editing_one_chunk_keeps_ids_and_resynthesizes_only_it(tmp_path):
    script = _script((11, 2))
    voice = FakeVoice()
    nar.narrate(script, tmp_path, voice, require_approval=False)
    voice.calls.clear()
    edited = json.loads(json.dumps(script))
    edited["sections"][1]["text"] = edited["sections"][1]["text"].replace("fin1_0.", "autre.")
    r = nar.narrate(edited, tmp_path, voice, require_approval=False)
    assert r["plan"] == "updated" and [c["id"] for c in r["chunks"]] == ["c01", "c02", "c03"]
    assert voice.calls == [r["chunks"][2]["text"]]


def test_a_new_cut_is_never_renumbered_silently(tmp_path):
    nar.narrate(_script((11, 2)), tmp_path, FakeVoice(), require_approval=False)
    with pytest.raises(ValueError, match="replan=True"):
        nar.narrate(_script((11, 2, 3)), tmp_path, FakeVoice(), require_approval=False)
    r = nar.narrate(_script((11, 2, 3)), tmp_path, FakeVoice(), replan=True, require_approval=False)
    assert r["plan"] == "replanned" and len(r["chunks"]) == 4


# --- per-chunk check ----------------------------------------------------------


def test_a_chunk_heard_wrong_is_suspect(tmp_path):
    script = _script((11, 2))
    chunks = nar.plan_chunks(script)
    by_id = {c["text_fingerprint"]: c["text"] for c in chunks}

    def heard(path):
        text = by_id[path.stem.split("_")[1]]
        if path.stem.startswith("c02"):
            text = " ".join(text.split()[:20])  # the voice stopped early
        return text

    r = nar.narrate(script, tmp_path, FakeVoice(), _hear_exactly(heard), require_approval=False)
    assert r["status"] == "revise" and r["suspect"] == ["c02"]


def test_chunks_heard_right_pass(tmp_path):
    script = _script((11, 2))
    by_id = {c["text_fingerprint"]: c["text"] for c in nar.plan_chunks(script)}
    r = nar.narrate(script, tmp_path, FakeVoice(),
                    _hear_exactly(lambda p: by_id[p.stem.split("_")[1]]), require_approval=False)
    assert r["status"] == "pass" and r["suspect"] == []



# --- adversarial review (28 Sept) ------------------------------------------------


def test_changing_the_voice_resynthesizes_instead_of_reusing_audio(tmp_path):
    script = _script((2,))
    voice = FakeVoice()
    nar.narrate(script, tmp_path, voice, require_approval=False, voice_key="voiceA")
    voice.calls.clear()
    nar.narrate(script, tmp_path, voice, require_approval=False, voice_key="voiceA")
    assert voice.calls == []
    nar.narrate(script, tmp_path, voice, require_approval=False, voice_key="voiceB")
    assert len(voice.calls) == 1


def test_an_empty_approved_script_approves_nothing(tmp_path):
    _approve(tmp_path / "proj", {"sections": []})
    r = nar.narrate(_script((2,)), tmp_path / "o", FakeVoice(), project_dir=tmp_path / "proj")
    assert r["status"] == "refused"
    r = nar.narrate({"sections": []}, tmp_path / "o2", FakeVoice(), require_approval=False)
    assert r["status"] == "refused"


def test_regenerating_a_chunk_asks_the_voice_for_a_new_take(tmp_path):
    script = _script((2,))
    voice = FakeVoice()
    nar.narrate(script, tmp_path, voice, require_approval=False)
    nar.narrate(script, tmp_path, voice, only=["c01"], require_approval=False)
    nar.narrate(script, tmp_path, voice, only=["c01"], require_approval=False)
    assert voice.attempts == [0, 1, 2]


def test_unchanged_chunks_are_not_transcribed_again(tmp_path):
    script = _script((11, 2))
    by_id = {c["text_fingerprint"]: c["text"] for c in nar.plan_chunks(script)}
    heard_paths = []

    def transcribe(path):
        heard_paths.append(path.name)
        return _hear_exactly(lambda p: by_id[p.stem.split("_")[1]])(path)

    nar.narrate(script, tmp_path, FakeVoice(), transcribe, require_approval=False)
    heard_paths.clear()
    nar.narrate(script, tmp_path, FakeVoice(), transcribe, only=["c02"], require_approval=False)
    assert [p.split("_")[0] for p in heard_paths] == ["c02"]


def test_a_voice_that_loops_makes_the_chunk_suspect(tmp_path):
    script = _script((2,))
    text = nar.plan_chunks(script)[0]["text"]
    looped = lambda p: " ".join(text.split()[:10] + ["et", "puis"] * 5 + text.split()[10:])
    r = nar.narrate(script, tmp_path, FakeVoice(), _hear_exactly(looped), require_approval=False)
    assert r["suspect"] == ["c01"]


def test_a_failed_join_is_a_clear_error(tmp_path):
    script = _script((2,))

    def broken(text, path, attempt=0):
        path.write_bytes(b"not audio")

    with pytest.raises(RuntimeError, match="could not join"):
        nar.narrate(script, tmp_path, broken, require_approval=False)


def test_two_empty_scripts_never_approve_each_other(tmp_path):
    _approve(tmp_path / "proj", {"sections": []})
    assert nar.approval_status(tmp_path / "proj", {"text": "anything"})["status"] != "approved"
