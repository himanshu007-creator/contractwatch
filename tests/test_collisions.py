"""Collision matrix, including the safety property that repeated names alone never block."""

from __future__ import annotations

from pathlib import Path

from contractwatch.collisions import CollisionContext, Exposure, analyze_collisions
from contractwatch.source import SourceIndex

MODEL_ITEM = "from pydantic import BaseModel\n\n\nclass Item(BaseModel):\n    id: int\n"


def _index(tmp_path: Path, files: dict[str, str]) -> SourceIndex:
    for relative, content in files.items():
        target = tmp_path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    return SourceIndex.build(tmp_path)


def _codes(findings) -> set[str]:
    return {f.code for f in findings}


def test_repeated_name_alone_is_never_a_confirmed_collision(tmp_path: Path) -> None:
    index = _index(tmp_path, {"pkg/__init__.py": "", "pkg/a.py": MODEL_ITEM, "pkg/b.py": MODEL_ITEM})
    exposure = Exposure.from_records(
        [
            {"route": "GET /a", "response_models": ["pkg.a.Item"], "response_components": ["pkg__a__Item"]},
            {"route": "GET /b", "response_models": ["pkg.b.Item"], "response_components": ["pkg__b__Item"]},
        ]
    )
    findings = analyze_collisions(CollisionContext(index, exposure))
    assert "CW201" not in _codes(findings)
    assert not any(f.blocking for f in findings)


def test_cw201_confirmed_when_two_models_share_a_component(tmp_path: Path) -> None:
    index = _index(tmp_path, {"pkg/__init__.py": "", "pkg/a.py": MODEL_ITEM, "pkg/b.py": MODEL_ITEM})
    exposure = Exposure.from_records(
        [
            {"route": "GET /a", "response_models": ["pkg.a.Item"], "response_components": ["Item"]},
            {"route": "GET /b", "response_models": ["pkg.b.Item"], "response_components": ["Item"]},
        ]
    )
    findings = analyze_collisions(CollisionContext(index, exposure))
    confirmed = [f for f in findings if f.code == "CW201"]
    assert len(confirmed) == 1
    assert confirmed[0].blocking
    assert {loc.file for loc in confirmed[0].locations} == {"pkg/a.py", "pkg/b.py"}


def test_cw202_ambiguous_when_names_repeat_and_are_exposed(tmp_path: Path) -> None:
    index = _index(tmp_path, {"pkg/__init__.py": "", "pkg/a.py": MODEL_ITEM, "pkg/b.py": MODEL_ITEM})
    exposure = Exposure.from_records(
        [
            {"route": "GET /a", "response_models": ["pkg.a.Item"], "response_components": ["pkg__a__Item"]},
            {"route": "GET /b", "response_models": ["pkg.b.Item"], "response_components": ["pkg__b__Item"]},
        ]
    )
    findings = analyze_collisions(CollisionContext(index, exposure))
    ambiguous = [f for f in findings if f.code == "CW202"]
    assert len(ambiguous) == 1
    assert not ambiguous[0].blocking


def test_cw203_informational_when_not_exposed(tmp_path: Path) -> None:
    index = _index(tmp_path, {"pkg/__init__.py": "", "pkg/a.py": MODEL_ITEM, "pkg/b.py": MODEL_ITEM})
    findings = analyze_collisions(CollisionContext(index, Exposure()))
    informational = [f for f in findings if f.code == "CW203"]
    assert len(informational) == 1
    assert informational[0].severity.value == "info"


def test_single_declaration_is_silent(tmp_path: Path) -> None:
    index = _index(tmp_path, {"app.py": MODEL_ITEM})
    exposure = Exposure.from_records(
        [{"route": "GET /a", "response_models": ["app.Item"], "response_components": ["Item"]}]
    )
    assert _codes(analyze_collisions(CollisionContext(index, exposure))) == set()


def test_body_models_are_linked_from_request_components(tmp_path: Path) -> None:
    index = _index(tmp_path, {"pkg/__init__.py": "", "pkg/a.py": MODEL_ITEM, "pkg/b.py": MODEL_ITEM})
    exposure = Exposure.from_records(
        [
            {"route": "POST /a", "body_models": ["pkg.a.Item"], "request_components": ["ItemIn"]},
            {"route": "POST /b", "body_models": ["pkg.b.Item"], "request_components": ["ItemIn"]},
        ]
    )
    findings = analyze_collisions(CollisionContext(index, exposure))
    assert "CW201" in _codes(findings)


# --- PRD 19.2 collision test matrix ----------------------------------------- #


def test_matrix_4_case_distinct_names_are_independent(tmp_path: Path) -> None:
    upper = "from pydantic import BaseModel\n\n\nclass UserResponse(BaseModel):\n    id: int\n"
    lower = "from pydantic import BaseModel\n\n\nclass userResponse(BaseModel):\n    id: int\n"
    index = _index(tmp_path, {"pkg/__init__.py": "", "pkg/a.py": upper, "pkg/b.py": lower})
    exposure = Exposure.from_records(
        [
            {"route": "GET /a", "response_models": ["pkg.a.UserResponse"]},
            {"route": "GET /b", "response_models": ["pkg.b.userResponse"]},
        ]
    )
    # Names differ only by case: no collision, no warning.
    assert _codes(analyze_collisions(CollisionContext(index, exposure))) == set()


def test_matrix_5_nested_models_are_indexed_with_locations(tmp_path: Path) -> None:
    nested = (
        "from pydantic import BaseModel\n\n\n"
        "class Outer(BaseModel):\n"
        "    class Inner(BaseModel):\n"
        "        id: int\n"
    )
    index = _index(tmp_path, {"pkg/__init__.py": "", "pkg/a.py": nested, "pkg/b.py": nested})
    inner = index.by_name("Inner")
    assert len(inner) == 2
    assert {decl.file for decl in inner} == {"pkg/a.py", "pkg/b.py"}
    assert all(decl.line > 0 and decl.is_pydantic for decl in inner)
    exposure = Exposure.from_records(
        [
            {"route": "GET /a", "response_models": ["pkg.a.Inner"], "response_components": ["Outer__Inner"]},
            {"route": "GET /b", "response_models": ["pkg.b.Inner"], "response_components": ["Outer__Inner"]},
        ]
    )
    findings = analyze_collisions(CollisionContext(index, exposure))
    assert "CW201" in _codes(findings)


def test_matrix_6_model_reused_across_endpoints_is_silent(tmp_path: Path) -> None:
    index = _index(tmp_path, {"app.py": MODEL_ITEM})
    exposure = Exposure.from_records(
        [
            {"route": "GET /a", "response_models": ["app.Item"], "response_components": ["Item"]},
            {"route": "GET /b", "response_models": ["app.Item"], "response_components": ["Item"]},
        ]
    )
    assert _codes(analyze_collisions(CollisionContext(index, exposure))) == set()


def test_matrix_8_inherited_models_are_detected(tmp_path: Path) -> None:
    source = (
        "from pydantic import BaseModel\n\n\n"
        "class Base(BaseModel):\n    id: int\n\n\n"
        "class Item(Base):\n    name: str\n"
    )
    index = _index(tmp_path, {"app.py": source})
    assert index.by_name("Item")[0].is_pydantic


def test_matrix_8_aliased_base_models_are_detected(tmp_path: Path) -> None:
    source = "from pydantic import BaseModel as BM\n\n\nclass Item(BM):\n    id: int\n"
    index = _index(tmp_path, {"app.py": source})
    assert index.by_name("Item")[0].is_pydantic


def test_matrix_8_generic_models_are_detected(tmp_path: Path) -> None:
    source = (
        "from typing import Generic, TypeVar\n"
        "from pydantic import BaseModel\n\n"
        "T = TypeVar('T')\n\n\n"
        "class Page(BaseModel, Generic[T]):\n    items: list[T] = []\n"
    )
    index = _index(tmp_path, {"app.py": source})
    assert index.by_name("Page")[0].is_pydantic


def test_matrix_10_every_conflicting_declaration_is_located(tmp_path: Path) -> None:
    files = {f"pkg/{name}.py": MODEL_ITEM for name in ("a", "b", "c")}
    files["pkg/__init__.py"] = ""
    index = _index(tmp_path, files)
    exposure = Exposure.from_records(
        [
            {"route": "GET /a", "response_models": ["pkg.a.Item"], "response_components": ["pkg__a__Item"]},
            {"route": "GET /b", "response_models": ["pkg.b.Item"], "response_components": ["pkg__b__Item"]},
            {"route": "GET /c", "response_models": ["pkg.c.Item"], "response_components": ["pkg__c__Item"]},
        ]
    )
    ambiguous = [f for f in analyze_collisions(CollisionContext(index, exposure)) if f.code == "CW202"]
    assert len(ambiguous) == 1
    assert {loc.file for loc in ambiguous[0].locations} == {"pkg/a.py", "pkg/b.py", "pkg/c.py"}
