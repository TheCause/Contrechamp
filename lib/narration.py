"""Narrate an approved script chunk by chunk, and check what was said.

Lessons carried over from a channel's production method (sept. 2026):

- **The voice only speaks the approved text.** The script stage can be
  approved, then edited; nothing used to notice. The spoken text is
  fingerprinted and compared to the one in the approved ``script``
  checkpoint. A changed text is refused; a project without approval is
  reported ``not_checked``, never ``approved``.
- **Chunks of ~30 s, cut between sentences, never across a section.** A
  short chunk drifts (accent, tone): a short tail is merged into the previous
  chunk, and a chunk that stays short is flagged ``drift_risk`` for a
  listen.
- **Chunk ids are stable.** Regenerating one chunk must not renumber the
  others: the plan is saved, and audio is keyed by chunk id + text
  fingerprint, so only chunks whose text changed (or that are asked for) are
  synthesized again.
- **What was said is checked, per chunk.** Each chunk is transcribed and
  aligned on its text (lib.script_timing); a chunk heard below the fidelity
  threshold is ``suspect`` and the narration is ``revise``.
"""
from __future__ import annotations

import hashlib
import json
import re
import subprocess
import unicodedata
from pathlib import Path
from typing import Any, Callable

from lib import script_timing

DEFAULT_TARGET_WORDS = 75   # ~30 s of French narration
DEFAULT_MIN_WORDS = 35
DEFAULT_MIN_FIDELITY = 0.8
GAP_IN_SECTION_MS = 250
GAP_BETWEEN_SECTIONS_MS = 600


def _norm_text(text: str) -> str:
    text = unicodedata.normalize("NFC", text).replace("’", "'")
    return re.sub(r"\s+", " ", text).strip()


def spoken_sections(script: Any) -> list[tuple[str | None, str]]:
    if isinstance(script, str):
        return [(None, _norm_text(script))]
    return [
        (s.get("id"), _norm_text(s.get("text", "")))
        for s in (script or {}).get("sections", [])
        if s.get("text", "").strip()
    ]


def fingerprint(script: Any) -> str:
    """sha256 of the spoken text only (section ids and texts, normalized)."""
    payload = json.dumps(spoken_sections(script), ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def approval_status(project_dir: Path | None, script: Any) -> dict:
    """Is ``script`` the text approved at the ``script`` stage of this project?"""
    if project_dir is None:
        return {"status": "not_checked", "reason": "no project_dir given"}
    path = Path(project_dir) / "checkpoint_script.json"
    if not path.exists():
        return {"status": "not_checked", "reason": f"no script checkpoint at {path}"}
    cp = json.loads(path.read_text(encoding="utf-8"))
    if not (cp.get("status") == "completed" and cp.get("human_approved")):
        return {"status": "not_approved", "reason": f"script stage is {cp.get('status')!r}, not approved"}
    approved = (cp.get("artifacts") or {}).get("script")
    if not approved:
        return {"status": "not_checked", "reason": "approved checkpoint carries no script artifact"}
    if fingerprint(approved) == fingerprint(script):
        return {"status": "approved", "fingerprint": fingerprint(script)}
    return {
        "status": "changed_since_approval",
        "reason": _first_difference(spoken_sections(approved), spoken_sections(script)),
    }


def _first_difference(a: list, b: list) -> str:
    for (ida, ta), (idb, tb) in zip(a, b):
        if (ida, ta) != (idb, tb):
            sa, sb = ta.split(), tb.split()
            k = next((i for i, (x, y) in enumerate(zip(sa, sb)) if x != y), min(len(sa), len(sb)))
            return (f"section {idb!r} differs from the approved text near: "
                    f"approved «{' '.join(sa[k:k + 8])}» / now «{' '.join(sb[k:k + 8])}»")
    return f"approved text has {len(a)} sections, current has {len(b)}"


def _sentences(text: str) -> list[str]:
    out, cur = [], []
    for tok in text.split():
        cur.append(tok)
        if script_timing._ends_sentence(tok):
            out.append(" ".join(cur))
            cur = []
    if cur:
        out.append(" ".join(cur))
    return out


def plan_chunks(
    script: Any, *, target_words: int = DEFAULT_TARGET_WORDS, min_words: int = DEFAULT_MIN_WORDS
) -> list[dict]:
    """Group sentences into chunks of about ``target_words``, within each section."""
    chunks: list[dict] = []
    for section, text in spoken_sections(script):
        cur: list[str] = []
        section_chunks: list[list[str]] = []
        for sentence in _sentences(text):
            n = len(sentence.split())
            if cur and sum(len(s.split()) for s in cur) + n > target_words:
                section_chunks.append(cur)
                cur = []
            cur.append(sentence)
        if cur:
            section_chunks.append(cur)
        # a short tail joins the previous chunk of the same section
        if len(section_chunks) > 1 and sum(len(s.split()) for s in section_chunks[-1]) < min_words:
            tail = section_chunks.pop()
            section_chunks[-1].extend(tail)
        for sentences in section_chunks:
            text_c = " ".join(sentences)
            words = len(text_c.split())
            chunks.append({
                "section": section,
                "text": text_c,
                "words": words,
                "drift_risk": words < min_words,
                "text_fingerprint": hashlib.sha256(text_c.encode("utf-8")).hexdigest()[:12],
            })
    for i, c in enumerate(chunks, 1):
        c["id"] = f"c{i:02d}"
    return chunks


def load_or_plan(plan_path: Path, script: Any, *, replan: bool = False, **kw) -> tuple[list[dict], str]:
    """Reuse the saved plan when the script still maps onto it; never renumber silently.

    Returns (chunks, how) where how is "new", "reused" or "replanned".
    """
    fresh = plan_chunks(script, **kw)
    if plan_path.exists() and not replan:
        saved = json.loads(plan_path.read_text(encoding="utf-8"))
        if [c["text"] for c in saved["chunks"]] == [c["text"] for c in fresh]:
            return saved["chunks"], "reused"
        if len(saved["chunks"]) == len(fresh) and all(
            a["section"] == b["section"] for a, b in zip(saved["chunks"], fresh)
        ):
            # same cut, some texts edited: ids are positional so they stay; the
            # edited chunks get new fingerprints and are synthesized again
            how = "updated"
        else:
            raise ValueError(
                f"The script no longer maps onto the saved chunk plan {plan_path} "
                "(chunk count or sections changed). Pass replan=True to renumber the chunks."
            )
    else:
        how = "replanned" if plan_path.exists() else "new"
    plan_path.parent.mkdir(parents=True, exist_ok=True)
    plan_path.write_text(json.dumps({"chunks": fresh}, ensure_ascii=False, indent=2), encoding="utf-8")
    return fresh, how


def chunk_audio_path(out_dir: Path, chunk: dict) -> Path:
    return out_dir / f"{chunk['id']}_{chunk['text_fingerprint']}.wav"


def narrate(
    script: Any,
    out_dir: Path,
    synthesize: Callable[[str, Path], None],
    transcribe: Callable[[Path], list[dict]] | None = None,
    *,
    project_dir: Path | None = None,
    require_approval: bool = True,
    only: list[str] | None = None,
    replan: bool = False,
    min_fidelity: float = DEFAULT_MIN_FIDELITY,
    target_words: int = DEFAULT_TARGET_WORDS,
    min_words: int = DEFAULT_MIN_WORDS,
) -> dict:
    """Synthesize the chunks that need it, check each one, join them into narration.wav."""
    approval = approval_status(project_dir, script)
    if approval["status"] in ("changed_since_approval", "not_approved") or (
        require_approval and project_dir is not None and approval["status"] != "approved"
    ):
        return {"status": "refused", "approval": approval, "chunks": []}

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    chunks, how = load_or_plan(
        out_dir / "narration_plan.json", script, replan=replan,
        target_words=target_words, min_words=min_words,
    )
    wanted = set(only or [])
    unknown = wanted - {c["id"] for c in chunks}
    if unknown:
        raise ValueError(f"Unknown chunk ids {sorted(unknown)}; plan has {[c['id'] for c in chunks]}")

    for c in chunks:
        path = chunk_audio_path(out_dir, c)
        c["audio"] = str(path)
        c["synthesized"] = False
        if not path.exists() or c["id"] in wanted:
            synthesize(c["text"], path)
            c["synthesized"] = True
        if transcribe is None:
            c["check"] = "not_checked"
            continue
        timing = script_timing.time_script(c["text"], transcribe(path))
        c["fidelity"] = timing["report"]["fidelity"]
        c["gaps"] = timing["report"]["gaps"]
        c["check"] = "suspect" if c["fidelity"] < min_fidelity or timing["report"]["status"] != "pass" else "ok"

    narration = out_dir / "narration.wav"
    _join(chunks, narration)

    if transcribe is None:
        status = "not_checked"
    elif any(c["check"] == "suspect" for c in chunks):
        status = "revise"
    else:
        status = "pass"
    return {
        "status": status,
        "approval": approval,
        "plan": how,
        "narration": str(narration),
        "chunks": chunks,
        "drift_risk": [c["id"] for c in chunks if c["drift_risk"]],
        "suspect": [c["id"] for c in chunks if c.get("check") == "suspect"],
    }


def _join(chunks: list[dict], out: Path, sample_rate: int = 48000) -> None:
    """Concatenate chunk audio with short silences (longer between sections)."""
    inputs, filters, labels = [], [], []
    for i, c in enumerate(chunks):
        inputs += ["-i", c["audio"]]
        filters.append(f"[{i}:a]aresample={sample_rate},aformat=channel_layouts=mono[a{i}]")
        labels.append(f"[a{i}]")
        if i < len(chunks) - 1:
            gap = GAP_BETWEEN_SECTIONS_MS if chunks[i + 1]["section"] != c["section"] else GAP_IN_SECTION_MS
            filters.append(
                f"aevalsrc=0:d={gap / 1000}:s={sample_rate},aformat=channel_layouts=mono[s{i}]")
            labels.append(f"[s{i}]")
    graph = ";".join(filters) + ";" + "".join(labels) + f"concat=n={len(labels)}:v=0:a=1[out]"
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", *inputs, "-filter_complex", graph, "-map", "[out]",
         "-c:a", "pcm_s16le", str(out)],
        check=True, capture_output=True, text=True,
    )
