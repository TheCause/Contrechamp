"""`python -m backlot serve` stays on localhost unless a host is asked for."""

from __future__ import annotations

import sys
import types

import pytest

from backlot import __main__ as cli


@pytest.fixture
def served(monkeypatch):
    seen: dict = {}
    fake = types.SimpleNamespace(run=lambda app, **kw: seen.update(kw))
    monkeypatch.setitem(sys.modules, "uvicorn", fake)
    monkeypatch.delenv("BACKLOT_HOST", raising=False)
    return seen


def test_default_is_localhost_only(served):
    assert cli.main(["serve", "--port", "4999"]) == 0
    assert served["host"] == "127.0.0.1" and served["port"] == 4999


def test_host_option_opens_it_to_the_network(served, capsys):
    cli.main(["serve", "--host", "0.0.0.0"])
    assert served["host"] == "0.0.0.0"
    assert "no authentication" in capsys.readouterr().err


def test_host_from_the_environment(served, monkeypatch):
    monkeypatch.setenv("BACKLOT_HOST", "0.0.0.0")
    cli.main(["serve"])
    assert served["host"] == "0.0.0.0"


def test_localhost_serving_prints_no_warning(served, capsys):
    cli.main(["serve"])
    assert "no authentication" not in capsys.readouterr().err
