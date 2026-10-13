"""Source-level analysis: model declarations, locations, and v1 migration markers.

FastAPI's generated OpenAPI alone cannot tell two same-named models apart, so the
schema analyzer needs the Python source. This module builds that index with the
standard library ``ast`` and never imports user code.
"""

from __future__ import annotations

import ast
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field, replace
from pathlib import Path

_PYDANTIC_BASES = frozenset(
    {"BaseModel", "RootModel", "GenericModel", "SQLModel", "BaseSettings", "Document"}
)
_V1_DECORATORS = frozenset({"validator", "root_validator", "validate_arguments"})
_V1_CONFIG_KEYS = frozenset(
    {"orm_mode", "allow_population_by_field_name", "allow_mutation", "schema_extra", "fields"}
)
_SKIP_DIRS = frozenset(
    {".git", ".hg", ".svn", "__pycache__", ".venv", "venv", "env", ".tox", ".mypy_cache",
     ".ruff_cache", ".pytest_cache", "node_modules", "build", "dist", ".eggs", "site-packages"}
)


@dataclass(frozen=True, slots=True)
class ModelDeclaration:
    name: str
    qualname: str
    module: str
    file: str
    line: int
    is_pydantic: bool = False
    bases: tuple[str, ...] = ()

    def location(self) -> tuple[str, int, str]:
        return self.file, self.line, self.module


@dataclass(frozen=True, slots=True)
class MigrationMarker:
    """A construct that indicates Pydantic v1-era code."""

    kind: str
    file: str
    line: int
    detail: str


@dataclass(slots=True)
class SourceIndex:
    declarations: list[ModelDeclaration] = field(default_factory=list)
    markers: list[MigrationMarker] = field(default_factory=list)

    @classmethod
    def build(cls, root: Path, *, extra_roots: Iterable[Path] = ()) -> SourceIndex:
        """Factory: parse every Python file under ``root`` (and extra roots)."""
        index = cls()
        seen: set[Path] = set()
        for base in (root, *extra_roots):
            for path in _iter_python_files(base):
                if path in seen:
                    continue
                seen.add(path)
                index._parse(path, root)
        index._resolve_inheritance()
        return index

    def _parse(self, path: Path, root: Path) -> None:
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except (SyntaxError, UnicodeDecodeError, OSError):
            return
        module = _module_name(path, root)
        rel = _relative(path, root)
        aliases = _pydantic_aliases(tree)
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                self._collect_class(node, module, rel, aliases)
            elif isinstance(node, ast.Assign | ast.AnnAssign):
                self._collect_v1_config(node, rel)
        for marker in _scan_v1(tree, rel):
            self.markers.append(marker)

    def _collect_class(
        self, node: ast.ClassDef, module: str, rel: str, aliases: frozenset[str]
    ) -> None:
        bases = tuple(_base_name(b) for b in node.bases)
        bases = tuple(b for b in bases if b)
        is_pydantic = (
            any(b.split(".")[-1] in _PYDANTIC_BASES for b in bases)
            or any(b in aliases for b in bases)
            or "BaseModel" in bases
        )
        has_config = any(
            isinstance(stmt, ast.ClassDef) and stmt.name == "Config" for stmt in node.body
        ) or any(
            isinstance(stmt, ast.Assign)
            and any(getattr(t, "id", None) == "model_config" for t in stmt.targets)
            for stmt in node.body
        )
        qualname = f"{module}.{node.name}"
        self.declarations.append(
            ModelDeclaration(
                name=node.name,
                qualname=qualname,
                module=module,
                file=rel,
                line=node.lineno,
                is_pydantic=is_pydantic or has_config,
                bases=bases,
            )
        )

    def _resolve_inheritance(self) -> None:
        """Mark classes that inherit (transitively) from a locally declared model.

        Cross-module bases cannot be resolved without importing user code, so a
        subclass of an imported model is intentionally left unflagged.
        """
        lookup: dict[tuple[str, str], int] = {
            (decl.module, decl.name): index for index, decl in enumerate(self.declarations)
        }
        changed = True
        while changed:
            changed = False
            for index, decl in enumerate(self.declarations):
                if decl.is_pydantic:
                    continue
                for base in decl.bases:
                    target = lookup.get((decl.module, base.split(".")[-1]))
                    if target is not None and self.declarations[target].is_pydantic:
                        self.declarations[index] = replace(decl, is_pydantic=True)
                        changed = True
                        break

    def _collect_v1_config(self, node: ast.Assign | ast.AnnAssign, rel: str) -> None:
        for target in ast.walk(node):
            name = getattr(target, "id", None)
            if name in _V1_CONFIG_KEYS:
                self.markers.append(
                    MigrationMarker("config_key", rel, node.lineno, name)
                )

    def by_name(self, name: str) -> list[ModelDeclaration]:
        return [d for d in self.declarations if d.name == name]

    def by_qualname(self, qualname: str) -> ModelDeclaration | None:
        return next((d for d in self.declarations if d.qualname == qualname), None)

    def pydantic_names(self) -> dict[str, list[ModelDeclaration]]:
        grouped: dict[str, list[ModelDeclaration]] = {}
        for decl in self.declarations:
            if decl.is_pydantic:
                grouped.setdefault(decl.name, []).append(decl)
        return grouped


def _iter_python_files(base: Path) -> Iterator[Path]:
    if base.is_file() and base.suffix == ".py":
        yield base
        return
    if not base.is_dir():
        return
    for child in sorted(base.iterdir()):
        if child.name in _SKIP_DIRS or (child.name.startswith(".") and child.is_dir()):
            continue
        if child.is_dir():
            yield from _iter_python_files(child)
        elif child.suffix == ".py":
            yield child


def _pydantic_aliases(tree: ast.AST) -> frozenset[str]:
    """Names bound to a Pydantic base class via ``from pydantic import X as Y``."""
    aliases: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module and node.module.split(".")[0] == "pydantic":
            for alias in node.names:
                if alias.name in _PYDANTIC_BASES:
                    aliases.add(alias.asname or alias.name)
    return frozenset(aliases)


def _base_name(node: ast.expr) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    if isinstance(node, ast.Subscript):
        return _base_name(node.value)
    return ""


def _module_name(path: Path, root: Path) -> str:
    try:
        rel = path.relative_to(root)
    except ValueError:
        return path.stem
    parts = list(rel.parts)
    if parts[-1] == "__init__.py":
        dir_parts = parts[:-1]
        file_stem = ""
    else:
        dir_parts = parts[:-1]
        file_stem = parts[-1][:-3]
    module_dirs = _package_dirs(dir_parts, root)
    module = ".".join([*module_dirs, file_stem]) if file_stem else ".".join(module_dirs)
    return module or path.stem


def _package_dirs(dir_parts: list[str], root: Path) -> list[str]:
    for i in range(len(dir_parts)):
        directory = root.joinpath(*dir_parts[: i + 1])
        if (directory / "__init__.py").exists():
            return dir_parts[i:]
    return dir_parts


def _relative(path: Path, root: Path) -> str:
    try:
        return str(path.relative_to(root))
    except ValueError:
        return str(path)


def _scan_v1(tree: ast.AST, rel: str) -> Iterator[MigrationMarker]:
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            for decorator in node.decorator_list:
                name = _base_name(decorator.func if isinstance(decorator, ast.Call) else decorator)
                if name in _V1_DECORATORS:
                    yield MigrationMarker("decorator", rel, node.lineno, name)


__all__ = ["MigrationMarker", "ModelDeclaration", "SourceIndex"]
