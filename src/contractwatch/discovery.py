"""Project discovery: root, package manager, Git, source roots, FastAPI targets.

Everything here is read-only and offline. The FastAPI target is found by parsing
source with ``ast`` (never by importing), so discovery is fast and side-effect-free.
"""

from __future__ import annotations

import ast
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

from .config import CONFIG_FILENAME, PYPROJECT_FILENAME
from .errors import AppDetectionError, ProjectNotFoundError
from .git import GitRepo
from .source import _SKIP_DIRS, _module_name

_ROOT_MARKERS = (".git", PYPROJECT_FILENAME, CONFIG_FILENAME)
_PRECOMMIT = ".pre-commit-config.yaml"


@dataclass(slots=True)
class ProjectLayout:
    root: Path
    package_manager: str
    lockfile: str | None
    git: GitRepo | None
    has_precommit_config: bool
    source_roots: tuple[Path, ...] = ()
    app_candidates: tuple[str, ...] = ()
    config_file: str | None = None

    @classmethod
    def discover(cls, start: Path, *, configured_app: str | None = None) -> ProjectLayout:
        root = find_project_root(start)
        package_manager, lockfile = _package_manager(root)
        git = GitRepo.discover(root)
        source_roots = _source_roots(root)
        candidates = _app_candidates(root, source_roots, configured_app)
        return cls(
            root=root,
            package_manager=package_manager,
            lockfile=lockfile,
            git=git,
            has_precommit_config=(root / _PRECOMMIT).exists(),
            source_roots=source_roots,
            app_candidates=candidates,
            config_file=_config_file(root),
        )

    @property
    def sys_paths(self) -> list[str]:
        return [str(path) for path in self.source_roots]

    def resolve_app(self, configured_app: str | None, chosen: str | None) -> str:
        """Pick the single application target or raise a clear, fixable error."""
        target = chosen or configured_app
        if target:
            return target
        candidates = self.app_candidates
        if len(candidates) == 1:
            return candidates[0]
        if not candidates:
            raise AppDetectionError(
                "No FastAPI application was found.",
                hint=(
                    "Create a FastAPI() instance and set 'app' in contractwatch.toml, "
                    "or pass --app package.module:app."
                ),
            )
        listed = ", ".join(candidates)
        raise AppDetectionError(
            f"Multiple FastAPI applications were found: {listed}.",
            hint="Select one with --app package.module:app (or set 'app' in contractwatch.toml).",
        )


def find_project_root(start: Path) -> Path:
    current = start.resolve()
    for candidate in (current, *current.parents):
        if any((candidate / marker).exists() for marker in _ROOT_MARKERS):
            return candidate
    raise ProjectNotFoundError(
        f"No project root found from {current}.",
        hint="Run ContractWatch from inside a project containing pyproject.toml or .git.",
    )


def _package_manager(root: Path) -> tuple[str, str | None]:
    if (root / "uv.lock").exists():
        return "uv", "uv.lock"
    if (root / "poetry.lock").exists():
        return "poetry", "poetry.lock"
    if (root / "Pipfile.lock").exists():
        return "pipenv", "Pipfile.lock"
    if (root / "requirements.txt").exists():
        return "pip", "requirements.txt"
    return "pip", None


def _config_file(root: Path) -> str | None:
    if (root / CONFIG_FILENAME).exists():
        return CONFIG_FILENAME
    if (root / PYPROJECT_FILENAME).exists():
        return PYPROJECT_FILENAME
    return None


def _source_roots(root: Path) -> tuple[Path, ...]:
    roots = [root]
    src = root / "src"
    is_package_src = src.is_dir() and any(
        (child / "__init__.py").exists() for child in src.iterdir() if child.is_dir()
    )
    if is_package_src:
        roots.insert(0, src)
    return tuple(roots)


def _app_candidates(root: Path, source_roots: tuple[Path, ...], configured_app: str | None) -> tuple[str, ...]:
    if configured_app:
        return (configured_app,)
    found: list[str] = []
    for source_root in source_roots:
        for path in _iter_python_files(source_root):
            found.extend(_candidates_in_file(path, root))
    return _dedupe(found)


def _candidates_in_file(path: Path, root: Path) -> list[str]:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (SyntaxError, UnicodeDecodeError, OSError):
        return []
    module = _module_name(path, root)
    candidates: list[str] = []
    for node in tree.body:
        if isinstance(node, ast.Assign) and _is_fastapi_call(node.value):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    candidates.append(f"{module}:{target.id}")
        elif (
            isinstance(node, ast.AnnAssign)
            and _is_fastapi_call(node.value)
            and isinstance(node.target, ast.Name)
        ):
            candidates.append(f"{module}:{node.target.id}")
    return candidates


def _is_fastapi_call(node: ast.expr | None) -> bool:
    if not isinstance(node, ast.Call):
        return False
    func = node.func
    name = func.id if isinstance(func, ast.Name) else (func.attr if isinstance(func, ast.Attribute) else "")
    return name == "FastAPI"


def _iter_python_files(base: Path) -> Iterator[Path]:
    if not base.is_dir():
        return
    for child in sorted(base.iterdir()):
        if child.name in _SKIP_DIRS or (child.name.startswith(".") and child.is_dir()):
            continue
        if child.is_dir():
            yield from _iter_python_files(child)
        elif child.suffix == ".py":
            yield child


def _dedupe(items: list[str]) -> tuple[str, ...]:
    seen: dict[str, None] = {}
    for item in items:
        seen.setdefault(item, None)
    ranked = sorted(seen, key=lambda t: (not t.endswith((":app", ":api", ":application")), t))
    return tuple(ranked)


__all__ = ["ProjectLayout", "find_project_root"]
