"""End-to-end tests through the real subprocess loader and child interpreter."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from contractwatch.errors import AppLoadError
from contractwatch.loader import SubprocessLoader

pytestmark = pytest.mark.integration

GOOD_APP = """
from fastapi import FastAPI
from pydantic import BaseModel


class Item(BaseModel):
    id: int
    name: str


app = FastAPI()


@app.get("/items", response_model=list[Item])
def list_items() -> list[Item]:
    return [Item(id=1, name="a")]
"""

CRASHING_APP = """
raise RuntimeError("boom at import time")
"""


def _project(tmp_path: Path, body: str) -> Path:
    (tmp_path / "svc_app.py").write_text(body, encoding="utf-8")
    return tmp_path


def test_subprocess_loader_returns_contract(tmp_path: Path) -> None:
    root = _project(tmp_path, GOOD_APP)
    loaded = SubprocessLoader().load("svc_app:app", root=root, sys_paths=[str(root)])
    assert "/items" in loaded.openapi["paths"]
    assert loaded.pydantic_version.startswith("2")
    assert loaded.exposure


def test_subprocess_loader_reports_crash_as_app_load_error(tmp_path: Path) -> None:
    root = _project(tmp_path, CRASHING_APP)
    with pytest.raises(AppLoadError) as info:
        SubprocessLoader().load("svc_app:app", root=root, sys_paths=[str(root)])
    assert "boom" in str(info.value)


def test_bootstrap_emits_json_for_missing_target() -> None:
    completed = subprocess.run(
        [sys.executable, "-m", "contractwatch._bootstrap"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0
    assert '"ok": false' in completed.stdout


def test_bootstrap_is_isolated_from_parent(tmp_path: Path) -> None:
    """A crashing app must not take down the parent interpreter."""
    root = _project(tmp_path, CRASHING_APP)
    completed = subprocess.run(
        [sys.executable, "-m", "contractwatch._bootstrap", "svc_app:app", str(root)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0
    assert '"ok": false' in completed.stdout
