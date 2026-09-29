"""Keep every MCP call inside one project directory.

The MCP client is an agent, not the human. Whatever it sends — a project id,
an output path, a path buried in an edit decision list — is untrusted. This
module turns a project id into a directory under PROJECTS_DIR and refuses any
path that would leave it: "../", absolute paths elsewhere, `~`, URL schemes,
and symlinks pointing out.

Why so strict: a path is also a way to READ. `image_path="/Users/x/.ssh/id_rsa"`
handed to a paid image-to-video tool uploads that file to a provider. So input
paths are confined exactly like output paths.

Inside the project, the governance files are out of reach too: the project
marker, the checkpoints and their history, the cost ledger and the logs sit at
the project root, and a tool that could overwrite them could erase a gate or
reset the spend. Tool paths must go through a subdirectory (`assets/`,
`renders/`, `artifacts/`...), never `history/`.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from lib import paths

PROJECT_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")

# Keys whose string values are always paths, at any depth of the inputs.
_PATH_KEY_RE = re.compile(r"(^|_)(path|paths|dir|file|files)$")
# Keys that hold a path OR something else ("source": an asset id, "youtube",
# an enum value, a URL). Their value is a path only when it looks like one.
_MAYBE_PATH_KEYS = frozenset({"source", "src"})
_SCHEME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*:")
_WEB_URL_RE = re.compile(r"^https?://[^/\s]", re.IGNORECASE)
_PROTECTED_DIRS = frozenset({"history"})

# Inputs that hand the tool something other than data: an ffmpeg filter graph
# (can open any file: movie=, amovie=, sendcmd=), code to execute, raw command
# arguments, a whole ComfyUI graph, raw provider parameters, or a switch that
# waives an approval. Refused whatever their value, except where noted.
_FORBIDDEN_KEYS = frozenset({
    "custom_vf", "custom_af", "extra_args", "workflow_json", "extra_params",
})
_FORBIDDEN_TRUE = frozenset({"allow_unsafe_code"})
_FORBIDDEN_FALSE = frozenset({"require_approval"})


class ConfinementError(ValueError):
    """An input would reach outside the project directory."""


def projects_root() -> Path:
    return paths.PROJECTS_DIR.resolve()


def project_dir(project_id: str) -> Path:
    """Directory of a project; the id must be a plain kebab-case slug."""
    if not isinstance(project_id, str) or not PROJECT_ID_RE.match(project_id):
        raise ConfinementError(
            f"invalid project_id {project_id!r}: expected lowercase letters, "
            "digits and dashes (max 64), nothing else"
        )
    return projects_root() / project_id


def confine_path(project_id: str, value: str) -> str:
    """Resolve a path against the project directory and refuse any escape.

    Relative paths are relative to projects/<project_id>/. Absolute paths are
    accepted only when they already point inside it. The result must sit in a
    subdirectory of the project, or be one (see the module docstring).
    """
    root = project_dir(project_id)
    if not isinstance(value, str) or not value.strip():
        raise ConfinementError(f"empty path for project {project_id!r}")
    if _SCHEME_RE.match(value):
        raise ConfinementError(f"{value!r}: a URL or scheme is not a path")
    if value.startswith("~"):
        raise ConfinementError(f"path {value!r} uses '~'; paths are relative to the project")
    candidate = Path(value)
    if not candidate.is_absolute():
        candidate = root / candidate
    resolved = candidate.resolve()  # follows symlinks: a link out is an escape
    try:
        rel = resolved.relative_to(root)
    except ValueError:
        raise ConfinementError(
            f"path {value!r} resolves outside project {project_id!r}"
        ) from None
    parts = rel.parts
    # One level down is allowed only for a directory ("renders"), never a file
    # ("project.json", "cost_log.json", "checkpoint_x.json").
    top_level_dir = len(parts) == 1 and not resolved.is_file() and not resolved.suffix
    if not parts or parts[0] in _PROTECTED_DIRS or (len(parts) == 1 and not top_level_dir):
        raise ConfinementError(
            f"path {value!r} points at the project root or its governance files; "
            "use a subdirectory such as assets/, renders/ or artifacts/"
        )
    return str(resolved)


def _is_path(key: str, value: str, project_root: Path) -> bool:
    if _PATH_KEY_RE.search(key):
        return True  # a URL here is refused by confine_path, never waved through
    if _WEB_URL_RE.match(value):
        return False
    if value.startswith(("/", "~")) or "../" in value or value == ".." or _SCHEME_RE.match(value):
        return True
    if key in _MAYBE_PATH_KEYS and ("/" in value or value.startswith(".")):
        return True
    # A bare name that exists on disk ("config.yaml", ".env", "projects/other/x.png")
    # is read as a path by the tool, whatever its key.
    # Any existing file ("Makefile", "LICENSE"); a directory only when the value
    # is path-like, so an enum value naming a repository directory ("tools",
    # "docs") is not rewritten.
    if "\n" in value or len(value) > 1024:
        return False
    try:
        if Path(value).is_file() or (project_root / value).is_file():
            return True
        return ("." in value or "/" in value) and (Path(value).exists() or (project_root / value).exists())
    except (OSError, ValueError):
        return False


def _check_forbidden(key: str, value: Any) -> None:
    if key in _FORBIDDEN_KEYS and value not in (None, "", {}, []):
        raise ConfinementError(f"input {key!r} is not accepted through MCP")
    if key in _FORBIDDEN_TRUE and value:
        raise ConfinementError(f"input {key!r} must stay false through MCP")
    if key in _FORBIDDEN_FALSE and value is False:
        raise ConfinementError(f"input {key!r} cannot be switched off through MCP")


def confine_inputs(project_id: str, inputs: Any, _key: str = "") -> Any:
    """Return a copy of the inputs with every path confined to the project.

    A string is treated as a path when its key names one (`*_path`, `*_dir`,
    `file`...), when it looks like one whatever its key (absolute, `~`,
    `../`, a scheme other than http(s)), under `source`/`src` when it has a
    slash or a leading dot, and whenever it names something that exists on
    disk. Confined paths come back absolute, so the tool cannot re-resolve
    them against another directory. Inputs that smuggle code, filters or a
    waived approval are refused.
    """
    if isinstance(inputs, dict):
        out = {}
        for k, v in inputs.items():
            _check_forbidden(str(k).lower(), v)
            out[k] = confine_inputs(project_id, v, str(k))
        return out
    if isinstance(inputs, list):
        return [confine_inputs(project_id, v, _key) for v in inputs]
    if isinstance(inputs, str) and _is_path(_key.lower(), inputs, project_dir(project_id)):
        return confine_path(project_id, inputs)
    return inputs
