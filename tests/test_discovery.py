"""Project discovery: root, package manager, source roots, app candidates."""

from __future__ import annotations

from pathlib import Path

import pytest

from contractwatch.discovery import ProjectLayout, find_project_root
from contractwatch.errors import AppDetectionError, ProjectNotFoundError


def test_find_root_via_pyproject(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n", encoding="utf-8")
    nested = tmp_path / "a" / "b"
    nested.mkdir(parents=True)
    assert find_project_root(nested) == tmp_path


def test_find_root_missing(tmp_path: Path) -> None:
    with pytest.raises(ProjectNotFoundError):
        find_project_root(tmp_path)


@pytest.mark.parametrize(
    ("lockfile", "manager"),
    [("uv.lock", "uv"), ("poetry.lock", "poetry"), ("Pipfile.lock", "pipenv"), ("requirements.txt", "pip")],
)
def test_package_manager_detection(tmp_path: Path, lockfile: str, manager: str) -> None:
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n", encoding="utf-8")
    (tmp_path / lockfile).write_text("", encoding="utf-8")
    assert ProjectLayout.discover(tmp_path).package_manager == manager


def test_app_candidates_found_by_ast(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n", encoding="utf-8")
    (tmp_path / "app.py").write_text("from fastapi import FastAPI\napp = FastAPI()\n", encoding="utf-8")
    layout = ProjectLayout.discover(tmp_path)
    assert "app:app" in layout.app_candidates


def test_annotated_app_assignment(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n", encoding="utf-8")
    (tmp_path / "main.py").write_text(
        "from fastapi import FastAPI\napplication: FastAPI = FastAPI()\n", encoding="utf-8"
    )
    assert "main:application" in ProjectLayout.discover(tmp_path).app_candidates


def test_src_layout_adds_source_root(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n", encoding="utf-8")
    pkg = tmp_path / "src" / "myapp"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "main.py").write_text("from fastapi import FastAPI\napp = FastAPI()\n", encoding="utf-8")
    layout = ProjectLayout.discover(tmp_path)
    assert tmp_path / "src" in layout.source_roots
    assert "myapp.main:app" in layout.app_candidates


def test_resolve_app_uses_configured(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n", encoding="utf-8")
    layout = ProjectLayout.discover(tmp_path)
    assert layout.resolve_app("cfg:app", None) == "cfg:app"
    assert layout.resolve_app(None, "cli:app") == "cli:app"


def test_resolve_app_single_candidate(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n", encoding="utf-8")
    (tmp_path / "app.py").write_text("from fastapi import FastAPI\napp = FastAPI()\n", encoding="utf-8")
    assert ProjectLayout.discover(tmp_path).resolve_app(None, None) == "app:app"


def test_resolve_app_none_found(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n", encoding="utf-8")
    with pytest.raises(AppDetectionError):
        ProjectLayout.discover(tmp_path).resolve_app(None, None)


def test_resolve_app_multiple_candidates_requires_choice(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n", encoding="utf-8")
    (tmp_path / "a.py").write_text("from fastapi import FastAPI\napp = FastAPI()\n", encoding="utf-8")
    (tmp_path / "b.py").write_text("from fastapi import FastAPI\napp = FastAPI()\n", encoding="utf-8")
    with pytest.raises(AppDetectionError):
        ProjectLayout.discover(tmp_path).resolve_app(None, None)


def test_preferred_names_rank_first(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n", encoding="utf-8")
    (tmp_path / "app.py").write_text(
        "from fastapi import FastAPI\nserver = FastAPI()\napp = FastAPI()\n", encoding="utf-8"
    )
    candidates = ProjectLayout.discover(tmp_path).app_candidates
    assert "app:app" in candidates
    assert candidates.index("app:app") < candidates.index("app:server")
