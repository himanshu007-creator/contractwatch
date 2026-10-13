"""Source indexing: declarations, module naming, and Pydantic v1 markers."""

from __future__ import annotations

from pathlib import Path

from contractwatch.source import SourceIndex

MODELS = """
from pydantic import BaseModel


class Item(BaseModel):
    id: int


class Helper:
    pass
"""

V1_STYLE = """
from pydantic import BaseModel, validator


class Item(BaseModel):
    id: int

    @validator("id")
    def check(cls, v):
        return v

    class Config:
        orm_mode = True
"""


def _build(tmp_path: Path, files: dict[str, str]) -> SourceIndex:
    for relative, content in files.items():
        target = tmp_path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    return SourceIndex.build(tmp_path)


def test_collects_pydantic_models(tmp_path: Path) -> None:
    index = _build(tmp_path, {"app.py": MODELS})
    assert index.by_qualname("app.Item") is not None
    assert index.by_qualname("app.Helper") is not None
    assert index.by_qualname("app.Helper").is_pydantic is False  # type: ignore[union-attr]


def test_package_module_naming(tmp_path: Path) -> None:
    index = _build(tmp_path, {"pkg/__init__.py": "", "pkg/models.py": MODELS})
    assert index.by_qualname("pkg.models.Item") is not None


def test_pydantic_names_groups_repeats(tmp_path: Path) -> None:
    index = _build(tmp_path, {"a.py": MODELS, "b.py": MODELS})
    grouped = index.pydantic_names()
    assert len(grouped["Item"]) == 2


def test_v1_decorator_marker(tmp_path: Path) -> None:
    index = _build(tmp_path, {"app.py": V1_STYLE})
    kinds = {(m.kind, m.detail) for m in index.markers}
    assert ("decorator", "validator") in kinds


def test_v1_config_key_marker(tmp_path: Path) -> None:
    index = _build(tmp_path, {"app.py": V1_STYLE})
    assert any(m.kind == "config_key" and m.detail == "orm_mode" for m in index.markers)


def test_syntax_errors_are_skipped(tmp_path: Path) -> None:
    index = _build(tmp_path, {"bad.py": "def broken(:\n", "good.py": MODELS})
    assert index.by_qualname("good.Item") is not None


def test_skips_virtualenvs(tmp_path: Path) -> None:
    index = _build(tmp_path, {".venv/lib/x.py": MODELS, "app.py": MODELS})
    assert all("x.py" not in d.file for d in index.declarations)


def test_by_name_returns_all(tmp_path: Path) -> None:
    index = _build(tmp_path, {"a.py": MODELS, "b.py": MODELS})
    assert len(index.by_name("Item")) == 2
