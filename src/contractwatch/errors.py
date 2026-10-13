"""Typed, user-facing errors and their exit-code mapping.

Every failure ContractWatch can anticipate is modelled here so the CLI never
leaks a raw traceback and the process always exits with a meaningful code.
"""

from __future__ import annotations

from typing import ClassVar

from .diagnostics import ExitCode


class ContractWatchError(Exception):
    """Base class for anticipated, renderable failures."""

    exit_code: ClassVar[ExitCode] = ExitCode.ERROR

    def __init__(
        self, message: str, *, hint: str | None = None, code: str | None = None
    ) -> None:
        super().__init__(message)
        self.message = message
        self.hint = hint
        self.code = code

    def render(self) -> str:
        return f"{self.message}\n  hint: {self.hint}" if self.hint else self.message


class ConfigError(ContractWatchError):
    """Configuration is missing, malformed, or contradictory."""


class InvalidConfigurationError(ConfigError):
    """Configuration parsed but failed schema validation."""


class ProjectNotFoundError(ConfigError):
    """No project root could be discovered from the working directory."""


class AppDetectionError(ContractWatchError):
    """The FastAPI application target could not be determined."""


class AppLoadError(ContractWatchError):
    """Importing the application failed."""

    def __init__(
        self,
        message: str,
        *,
        target: str | None = None,
        hint: str | None = None,
        code: str | None = None,
    ) -> None:
        super().__init__(message, hint=hint, code=code)
        self.target = target


class PydanticCompatibilityError(AppLoadError):
    """The application mixes Pydantic v1 and v2 semantics."""


class OpenAPIGenerationError(AppLoadError):
    """The application imported but its OpenAPI schema could not be generated."""


class GitError(ContractWatchError):
    """A Git operation failed."""


class BaselineError(ContractWatchError):
    """A baseline could not be read, written, or is inconsistent."""


class HookError(ContractWatchError):
    """A pre-commit configuration could not be safely modified."""


class ReportError(ContractWatchError):
    """A report could not be written or read."""


class CacheError(ContractWatchError):
    """The analysis cache could not be read or written."""


__all__ = [
    "AppDetectionError",
    "AppLoadError",
    "BaselineError",
    "CacheError",
    "ConfigError",
    "ContractWatchError",
    "GitError",
    "HookError",
    "InvalidConfigurationError",
    "OpenAPIGenerationError",
    "ProjectNotFoundError",
    "PydanticCompatibilityError",
    "ReportError",
]
