"""Pre-commit integration.

Adds ContractWatch to an existing ``.pre-commit-config.yaml`` without touching
other hooks, or creates a minimal config on opt-in. Malformed or ambiguous
configurations are left untouched and reported. Repeated calls are idempotent.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from .errors import HookError
from .fs import atomic_write_text
from .git import CommandRunner, SubprocessRunner

PRECOMMIT_FILE = ".pre-commit-config.yaml"
HOOK_ID = "contractwatch"
_HOOK_ENTRY = "contractwatch check"


@dataclass(frozen=True, slots=True)
class HookChange:
    changed: bool
    created: bool
    detail: str


class PreCommitManager:
    def __init__(self, root: Path, runner: CommandRunner | None = None) -> None:
        self.root = root
        self.path = root / PRECOMMIT_FILE
        self._runner = runner or SubprocessRunner("pre-commit")

    def has_hook(self) -> bool:
        if not self.path.exists():
            return False
        document = self._load(allow_missing=True)
        return any(hook.get("id") == HOOK_ID for hook in _iter_hooks(document))

    def ensure_hook(self) -> HookChange:
        """Add the hook if absent. Raises :class:`HookError` on malformed config."""
        if self.path.exists():
            document = self._load(allow_missing=False)
            if any(hook.get("id") == HOOK_ID for hook in _iter_hooks(document)):
                return HookChange(False, False, "ContractWatch hook already present.")
            document.setdefault("repos", []).append(_local_repo())
            self._write(document)
            return HookChange(True, False, "Added ContractWatch hook to existing configuration.")
        self._write({"repos": [_local_repo()]})
        return HookChange(True, True, "Created .pre-commit-config.yaml with the ContractWatch hook.")

    def install(self) -> None:
        """Install Git hooks via ``pre-commit install``."""
        result = self._runner.run(["install"], self.root)
        if not result.ok:
            detail = result.stderr or result.stdout or "unknown error"
            raise HookError(
                f"`pre-commit install` failed: {detail}",
                hint="Ensure pre-commit is installed and this is a Git repository.",
                code="CW403",
            )

    def _load(self, *, allow_missing: bool) -> dict[str, Any]:
        if not self.path.exists():
            return {} if allow_missing else {"repos": []}
        try:
            document = yaml.safe_load(self.path.read_text(encoding="utf-8"))
        except (yaml.YAMLError, OSError) as exc:
            raise HookError(
                f"{PRECOMMIT_FILE} is malformed and was left unchanged: {exc}",
                hint="Fix the YAML manually, then re-run `contractwatch init`.",
                code="CW403",
            ) from exc
        if document is None:
            return {"repos": []}
        if not isinstance(document, dict):
            raise HookError(
                f"{PRECOMMIT_FILE} is not a YAML mapping and was left unchanged.",
                hint="Fix the structure manually, then re-run `contractwatch init`.",
                code="CW403",
            )
        return document

    def _write(self, document: dict[str, Any]) -> None:
        try:
            text = yaml.safe_dump(document, sort_keys=False, default_flow_style=False)
        except yaml.YAMLError as exc:  # pragma: no cover - defensive
            raise HookError(f"Could not serialise pre-commit config: {exc}") from exc
        atomic_write_text(self.path, text, mode=0o644)


def _local_repo() -> dict[str, Any]:
    return {
        "repo": "local",
        "hooks": [
            {
                "id": HOOK_ID,
                "name": "ContractWatch",
                "description": "Block commits that break the API contract.",
                "entry": _HOOK_ENTRY,
                "language": "system",
                "pass_filenames": False,
                "always_run": True,
            }
        ],
    }


def _iter_hooks(document: dict[str, Any]) -> Iterator[dict[str, Any]]:
    for repo in document.get("repos", []) or []:
        if isinstance(repo, dict):
            for hook in repo.get("hooks", []) or []:
                if isinstance(hook, dict):
                    yield hook


__all__ = ["HOOK_ID", "PRECOMMIT_FILE", "HookChange", "PreCommitManager"]
