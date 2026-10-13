"""Git integration.

All Git access goes through :class:`CommandRunner` so tests never touch the
developer's real repository. Errors surface as :class:`GitError`.
"""

from __future__ import annotations

import subprocess
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from .errors import GitError


@dataclass(frozen=True, slots=True)
class CommandResult:
    returncode: int
    stdout: str
    stderr: str

    @property
    def ok(self) -> bool:
        return self.returncode == 0


class CommandRunner(Protocol):
    def run(self, args: Sequence[str], cwd: Path) -> CommandResult: ...


class SubprocessRunner:
    """Default runner. Never uses a shell, so arguments are passed verbatim."""

    def __init__(self, executable: str = "git") -> None:
        self._executable = executable

    def run(self, args: Sequence[str], cwd: Path) -> CommandResult:
        try:
            completed = subprocess.run(
                [self._executable, *args],
                cwd=cwd,
                capture_output=True,
                text=True,
                check=False,
                timeout=30,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise GitError(f"Failed to run git {' '.join(args)}: {exc}") from exc
        return CommandResult(completed.returncode, completed.stdout.strip(), completed.stderr.strip())


class GitRepo:
    def __init__(self, root: Path, runner: CommandRunner | None = None) -> None:
        self.root = root
        self._runner = runner or SubprocessRunner()

    @classmethod
    def discover(cls, start: Path, runner: CommandRunner | None = None) -> GitRepo | None:
        repo = cls(start, runner)
        result = repo._git("rev-parse", "--show-toplevel")
        if not result.ok or not result.stdout:
            return None
        return cls(Path(result.stdout), runner)

    def _git(self, *args: str) -> CommandResult:
        return self._runner.run(list(args), self.root)

    def revision(self, ref: str = "HEAD") -> str | None:
        result = self._git("rev-parse", "--verify", "--quiet", ref)
        return result.stdout or None

    def branch(self) -> str | None:
        result = self._git("rev-parse", "--abbrev-ref", "HEAD")
        return result.stdout or None

    def is_dirty(self) -> bool:
        result = self._git("status", "--porcelain")
        return bool(result.stdout)

    def staged_paths(self) -> list[str]:
        result = self._git("diff", "--cached", "--name-only", "--diff-filter=ACMR")
        if not result.ok:
            return []
        return [line for line in result.stdout.splitlines() if line]

    def changed_paths(self, base: str, head: str = "HEAD") -> list[str]:
        result = self._git("diff", "--name-only", base, head)
        if not result.ok:
            return []
        return [line for line in result.stdout.splitlines() if line]

    def read_file_at(self, ref: str, path: str) -> str | None:
        result = self._git("show", f"{ref}:{path}")
        return result.stdout if result.ok else None


__all__ = ["CommandResult", "CommandRunner", "GitRepo", "SubprocessRunner"]
