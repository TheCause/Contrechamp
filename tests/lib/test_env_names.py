"""CONTRECHAMP_* settings, and the OPENMONTAGE_* names they replace.

The legacy names keep working so existing .env files and shell setups do not
break; the new name wins when both are set.
"""

from __future__ import annotations

import pytest

from lib import budget, env_names
from lib.config_model import BudgetMode


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    for name in ("BUDGET_MODE", "BUDGET_DISABLED", "APPROVE_TOOLS", "PROJECTS_DIR"):
        for var in env_names.names(name):
            monkeypatch.delenv(var, raising=False)


def test_new_name_is_read(monkeypatch):
    monkeypatch.setenv("CONTRECHAMP_PROJECTS_DIR", "/new")
    assert env_names.get("PROJECTS_DIR") == "/new"


def test_legacy_name_still_works(monkeypatch):
    monkeypatch.setenv("OPENMONTAGE_PROJECTS_DIR", "/old")
    assert env_names.get("PROJECTS_DIR") == "/old"


def test_new_name_wins_over_legacy(monkeypatch):
    monkeypatch.setenv("OPENMONTAGE_PROJECTS_DIR", "/old")
    monkeypatch.setenv("CONTRECHAMP_PROJECTS_DIR", "/new")
    assert env_names.get("PROJECTS_DIR") == "/new"


def test_blank_counts_as_unset(monkeypatch):
    monkeypatch.setenv("CONTRECHAMP_PROJECTS_DIR", "  ")
    monkeypatch.setenv("OPENMONTAGE_PROJECTS_DIR", "/old")
    assert env_names.get("PROJECTS_DIR") == "/old"
    assert env_names.get("BUDGET_MODE", "warn") == "warn"


def test_budget_honours_the_new_mode_name(monkeypatch):
    monkeypatch.setenv("CONTRECHAMP_BUDGET_MODE", "observe")
    assert budget._budget_config()["mode"] is BudgetMode.OBSERVE


def test_budget_kill_switch_under_either_name(monkeypatch):
    monkeypatch.setenv("CONTRECHAMP_BUDGET_DISABLED", "1")
    assert budget.budget_enabled() is False
    monkeypatch.delenv("CONTRECHAMP_BUDGET_DISABLED")
    monkeypatch.setenv("OPENMONTAGE_BUDGET_DISABLED", "1")
    assert budget.budget_enabled() is False


def test_overrides_are_reported_under_the_name_actually_set(monkeypatch):
    monkeypatch.setenv("CONTRECHAMP_APPROVE_TOOLS", "*")
    monkeypatch.setenv("OPENMONTAGE_BUDGET_MODE", "observe")
    assert set(budget.active_overrides()) == {"CONTRECHAMP_APPROVE_TOOLS", "OPENMONTAGE_BUDGET_MODE"}
