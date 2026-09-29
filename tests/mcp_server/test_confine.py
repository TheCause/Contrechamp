"""Paths from the MCP client stay inside their project."""

from __future__ import annotations

import os

import pytest

from openmontage_mcp.confine import ConfinementError, confine_inputs, confine_path, project_dir


@pytest.mark.parametrize("bad_id", ["../tools", "..", "/etc", "a/b", "Proj", "", "-x", "x" * 65])
def test_project_id_must_be_a_plain_slug(projects, bad_id):
    with pytest.raises(ConfinementError):
        project_dir(bad_id)


@pytest.mark.parametrize("escape", [
    "../other/x.png", "../../.env", "/etc/passwd", "~/.ssh/id_rsa", "file:///etc/passwd",
    "https://../../x", "C:/x", "project.json", "cost_log.json", "history/x", ".",
])
def test_paths_that_leave_the_project_are_refused(projects, escape):
    (projects / "p").mkdir()
    with pytest.raises(ConfinementError):
        confine_path("p", escape)


def test_a_symlink_pointing_out_is_an_escape(projects, tmp_path):
    root = projects / "p"
    root.mkdir()
    (tmp_path / "secret.txt").write_text("s")
    os.symlink(tmp_path / "secret.txt", root / "link.txt")
    with pytest.raises(ConfinementError):
        confine_path("p", "link.txt")


def test_a_path_hidden_deep_in_the_inputs_is_caught(projects):
    (projects / "p").mkdir()
    inputs = {"edit_decisions": {"cuts": [{"source": "/Users/someone/private.mp4"}]}}
    with pytest.raises(ConfinementError):
        confine_inputs("p", inputs)
    with pytest.raises(ConfinementError):
        confine_inputs("p", {"prompt_notes": "../../.env"})


def test_healthy_inputs_are_kept_or_anchored_in_the_project(projects):
    """Silent side: relative paths land in the project, non-paths are untouched."""
    root = (projects / "p")
    root.mkdir()
    out = confine_inputs("p", {
        "output_path": "renders/final.mp4",
        "reference_image_paths": ["assets/images/a.png"],
        "source": "youtube",
        "sources": ["pexels", "archive_org"],
        "video_url": "https://example.com/v.mp4",
        "prompt": "a cat, 16/9 framing",
        "nested": {"file": str(root.resolve() / "assets" / "b.png")},
    })
    assert out["output_path"] == str(root.resolve() / "renders" / "final.mp4")
    assert out["reference_image_paths"] == [str(root.resolve() / "assets" / "images" / "a.png")]
    assert out["source"] == "youtube"
    assert out["sources"] == ["pexels", "archive_org"]
    assert out["video_url"] == "https://example.com/v.mp4"
    assert out["prompt"] == "a cat, 16/9 framing"
    assert out["nested"]["file"] == str(root.resolve() / "assets" / "b.png")
