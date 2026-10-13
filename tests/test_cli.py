"""CLI behaviour via the Typer test runner against a real temporary project."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from typer.testing import CliRunner

from contractwatch.cli import app as cli_app

runner = CliRunner()


@pytest.fixture(autouse=True)
def _clean_modules() -> None:
    for name in ("app", "svc", "service"):
        sys.modules.pop(name, None)
    yield
    for name in ("app", "svc", "service"):
        sys.modules.pop(name, None)

APP = """
from fastapi import FastAPI
from pydantic import BaseModel


class Item(BaseModel):
    name: str
    price: float


app = FastAPI()


@app.get("/items/{item_id}", response_model=Item)
def get_item(item_id: int) -> Item:
    return Item(name="x", price=1.0)
"""

APP_BREAKING = APP.replace('    price: float\n', "")


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    (tmp_path / "pyproject.toml").write_text("[project]\nname='demo'\nversion='0.1.0'\n", encoding="utf-8")
    (tmp_path / "app.py").write_text(APP, encoding="utf-8")
    monkeypatch.setenv("CONTRACTWATCH_HOME", str(tmp_path / ".home"))
    return tmp_path


def _run(*args: str):
    return runner.invoke(cli_app, list(args), catch_exceptions=False)


def test_version() -> None:
    result = _run("--version")
    assert result.exit_code == 0
    assert "ContractWatch" in result.stdout


def test_help_lists_commands() -> None:
    result = _run("--help")
    assert "check" in result.stdout and "baseline" in result.stdout


def test_init_writes_baseline(project: Path) -> None:
    result = _run("init", "--cwd", str(project), "--no-install-hook")
    assert result.exit_code == 0
    assert (project / ".contractwatch" / "baseline.json").exists()


def test_check_without_baseline_exits_error(project: Path) -> None:
    result = _run("check", "--cwd", str(project))
    assert result.exit_code == 2


def test_check_clean_after_init(project: Path) -> None:
    _run("init", "--cwd", str(project), "--no-install-hook")
    result = _run("check", "--cwd", str(project))
    assert result.exit_code == 0


def test_check_blocks_breaking_change(project: Path) -> None:
    _run("init", "--cwd", str(project), "--no-install-hook")
    (project / "app.py").write_text(APP_BREAKING, encoding="utf-8")
    result = _run("check", "--cwd", str(project))
    assert result.exit_code == 1
    assert "CW102" in result.stdout


def test_check_json_is_parseable(project: Path) -> None:
    _run("init", "--cwd", str(project), "--no-install-hook")
    result = _run("check", "--cwd", str(project), "--json")
    payload = json.loads(result.stdout)
    assert payload["app"] == "app:app"
    assert payload["exit_code"] == 0


def test_inspect_json(project: Path) -> None:
    result = _run("inspect", "--cwd", str(project), "--json")
    payload = json.loads(result.stdout)
    assert "openapi" in payload
    assert payload["ok"] is True


def test_baseline_update_reaccepts(project: Path) -> None:
    _run("init", "--cwd", str(project), "--no-install-hook")
    (project / "app.py").write_text(APP_BREAKING, encoding="utf-8")
    assert _run("check", "--cwd", str(project)).exit_code == 1
    assert _run("baseline", "update", "--cwd", str(project)).exit_code == 0
    assert _run("check", "--cwd", str(project)).exit_code == 0


def test_report_latest_after_check(project: Path) -> None:
    _run("init", "--cwd", str(project), "--no-install-hook")
    _run("check", "--cwd", str(project))
    result = _run("report", "--latest", "--json")
    payload = json.loads(result.stdout)
    assert payload["app"] == "app:app"


def test_report_latest_text(project: Path) -> None:
    _run("init", "--cwd", str(project), "--no-install-hook")
    _run("check", "--cwd", str(project))
    result = _run("report", "--latest")
    assert result.exit_code == 0
    assert "ContractWatch" in result.stdout
    assert "Summary:" in result.stdout


def test_report_output_to_file(project: Path) -> None:
    _run("init", "--cwd", str(project), "--no-install-hook")
    _run("check", "--cwd", str(project))
    out = project / "out.txt"
    result = _run("report", "--output", str(out))
    assert result.exit_code == 0
    assert out.is_file()
    assert "ContractWatch" in out.read_text(encoding="utf-8")


def test_report_output_json_to_file(project: Path) -> None:
    _run("init", "--cwd", str(project), "--no-install-hook")
    _run("check", "--cwd", str(project))
    out = project / "out.json"
    result = _run("report", "--output", str(out), "--format", "json")
    assert result.exit_code == 0
    assert out.is_file()
    assert json.loads(out.read_text(encoding="utf-8"))["app"] == "app:app"


def test_report_list_lists_reports(project: Path) -> None:
    _run("init", "--cwd", str(project), "--no-install-hook")
    _run("check", "--cwd", str(project))
    result = _run("report", "--list")
    assert result.exit_code == 0
    names = result.stdout.splitlines()
    assert any(name.endswith(".json") for name in names)


def test_report_without_runs_exits(project: Path) -> None:
    assert _run("report", "--latest").exit_code == 2


def test_bad_baseline_action(project: Path) -> None:
    result = _run("baseline", "wrong", "--cwd", str(project))
    assert result.exit_code != 0
