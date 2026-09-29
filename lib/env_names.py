"""Contrechamp environment variables, and the OPENMONTAGE_* names they replace.

Every setting is read as ``CONTRECHAMP_<NAME>`` first, then as the legacy
``OPENMONTAGE_<NAME>``, so existing .env files and shell setups keep working.
A variable set to a blank value counts as unset.

Code that must refuse a setting (the MCP server refusing a disabled budget
gate) checks ``set_names()``, which covers both spellings; code that must
force a value uses ``force()``, which writes both.
"""

from __future__ import annotations

import os
from typing import Optional

PREFIX = "CONTRECHAMP_"
LEGACY_PREFIX = "OPENMONTAGE_"


def names(name: str) -> tuple[str, str]:
    """The current and the legacy variable name for a setting."""
    return PREFIX + name, LEGACY_PREFIX + name


def get(name: str, default: Optional[str] = None) -> Optional[str]:
    """The setting's value; the current name wins over the legacy one."""
    for var in names(name):
        value = os.environ.get(var)
        if value is not None and value.strip():
            return value
    return default


def set_names(name: str) -> list[str]:
    """The variable names, current or legacy, set to a non-blank value."""
    return [var for var in names(name) if os.environ.get(var, "").strip()]


def force(name: str, value: str) -> None:
    """Set the setting under both names, so neither spelling can undo it."""
    for var in names(name):
        os.environ[var] = value
