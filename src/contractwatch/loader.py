"""Loading the user's FastAPI application and generating its OpenAPI contract.

Two strategies share one payload builder:

* :class:`SubprocessLoader` (default) isolates a crashing or misbehaving app.
* :class:`InProcessLoader` is fast and used by tests.

Pydantic v1/v2 migration problems are detected and surfaced as actionable,
categorised errors rather than raw tracebacks.
"""

from __future__ import annotations

import importlib
import json
import subprocess
import sys
import traceback
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .diagnostics import Remediation
from .errors import AppLoadError, OpenAPIGenerationError, PydanticCompatibilityError

_METHODS_SKIP = frozenset({"HEAD", "OPTIONS"})


@dataclass(slots=True)
class LoadedApp:
    openapi: dict[str, Any]
    exposure: list[dict[str, Any]] = field(default_factory=list)
    pydantic_version: str = ""
    fastapi_version: str = ""


def build_payload(target: str, sys_paths: Sequence[str] = ()) -> dict[str, Any]:
    """Import ``module:attr`` and return the OpenAPI document plus route records.

    Never raises for anticipated problems: returns an ``ok: False`` envelope with
    a category so the caller can present a clear, fixable error.
    """
    for path in reversed(sys_paths):
        if path not in sys.path:
            sys.path.insert(0, path)
    try:
        module_name, _, attribute = target.partition(":")
        if not module_name or not attribute:
            raise AppLoadError(
                f"Application target '{target}' must look like 'package.module:app'.",
                target=target,
                hint="Set the target with --app or the 'app' key in contractwatch.toml.",
            )
        module = importlib.import_module(module_name)
        application = getattr(module, attribute, None)
        if application is None:
            raise AppLoadError(
                f"Module '{module_name}' has no attribute '{attribute}'.",
                target=target,
                hint="Check the FastAPI instance name (usually 'app').",
            )
        if not hasattr(application, "openapi"):
            raise AppLoadError(
                f"'{target}' is not a FastAPI application.",
                target=target,
                hint="Point --app at the object returned by FastAPI().",
            )
        document = _generate_openapi(application, target)
        exposure = _build_exposure(application, document)
        versions = _versions()
        return {"ok": True, "openapi": document, "exposure": exposure, **versions}
    except (AppLoadError, PydanticCompatibilityError, OpenAPIGenerationError) as exc:
        return _error_payload(exc, target)
    except BaseException as exc:
        return _error_payload(_categorise(exc, target), target)


def _generate_openapi(application: Any, target: str) -> dict[str, Any]:
    try:
        return dict(application.openapi())
    except BaseException as exc:
        raise _categorise(exc, target, phase="openapi") from exc


def _versions() -> dict[str, str]:
    versions = {"pydantic_version": "", "fastapi_version": ""}
    try:
        import pydantic

        versions["pydantic_version"] = getattr(pydantic, "VERSION", "")
    except Exception:
        pass
    try:
        import fastapi

        versions["fastapi_version"] = getattr(fastapi, "__version__", "")
    except Exception:
        pass
    return versions


def _error_payload(exc: BaseException, target: str) -> dict[str, Any]:
    error = exc if isinstance(exc, AppLoadError) else _categorise(exc, target)
    return {
        "ok": False,
        "category": type(error).__name__,
        "message": error.message,
        "hint": error.hint,
        "target": target,
        "traceback": _tail_traceback(exc),
        **_versions(),
    }


def _tail_traceback(exc: BaseException, lines: int = 12) -> str:
    return "".join(traceback.format_exception(type(exc), exc, exc.__traceback__)[-lines:])


def _categorise(exc: BaseException, target: str, phase: str = "import") -> AppLoadError:
    name = type(exc).__name__
    module = type(exc).__module__ or ""
    text = str(exc)
    version = _versions().get("pydantic_version", "")

    if "pydantic" in module or "pydantic" in text.lower() or name.startswith("Pydantic"):
        return PydanticCompatibilityError(
            f"Pydantic could not process the application: {name}: {text}",
            target=target,
            hint=(
                f"Installed Pydantic is {version or 'unknown'}. This usually means v1-era code "
                "on a v2 runtime (e.g. `@validator`/`class Config`) or a v1-only import "
                "(`from pydantic import validator`). Migrate to v2 or pin Pydantic to <2."
            ),
        )
    if phase == "openapi":
        return OpenAPIGenerationError(
            f"Generating the OpenAPI schema failed: {name}: {text}",
            target=target,
            hint="Fix the model definitions reported in the traceback and retry.",
        )
    return AppLoadError(
        f"Importing '{target}' failed: {name}: {text}",
        target=target,
        hint="Ensure the target is importable from the project root and dependencies are installed.",
    )


def _build_exposure(application: Any, document: dict[str, Any]) -> list[dict[str, Any]]:
    from fastapi.routing import APIRoute

    records: list[dict[str, Any]] = []
    paths = document.get("paths", {})
    for route in getattr(application, "routes", []):
        if not isinstance(route, APIRoute):
            continue
        methods = route.methods or set()
        for method in sorted(m for m in methods if m not in _METHODS_SKIP):
            operation = paths.get(route.path, {}).get(method.lower(), {})
            records.append(
                {
                    "route": f"{method} {route.path}",
                    "response_models": [_qualname(m) for m in _iter_models(route.response_model)],
                    "body_models": [
                        _qualname(m)
                        for param in getattr(route.dependant, "body_params", [])
                        for m in _iter_models(_annotation(param))
                    ],
                    "response_components": _response_components(operation),
                    "request_components": _request_components(operation),
                }
            )
    return records


def _annotation(field: Any) -> Any:
    info = getattr(field, "field_info", None)
    return getattr(info, "annotation", None) or getattr(field, "outer_type_", None)


def _iter_models(annotation: Any) -> Iterable[type]:
    if annotation is None:
        return
    from pydantic import BaseModel

    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        yield annotation
        return
    for arg in getattr(annotation, "__args__", ()):
        yield from _iter_models(arg)


def _qualname(model: Any) -> str:
    return f"{getattr(model, '__module__', '')}.{getattr(model, '__qualname__', model)}"


def _component_name(schema: Any) -> str | None:
    if isinstance(schema, dict):
        ref = schema.get("$ref")
        if isinstance(ref, str):
            return ref.rsplit("/", 1)[-1]
    return None


def _media_schema(container: Any) -> Any:
    if not isinstance(container, dict):
        return None
    content = container.get("content") or {}
    if not content:
        return None
    media = content.get("application/json") or next(iter(content.values()))
    return media.get("schema") if isinstance(media, dict) else None


def _response_components(operation: Any) -> list[str]:
    names: list[str] = []
    for response in (operation.get("responses") or {}).values():
        name = _component_name(_media_schema(response))
        if name:
            names.append(name)
    return names


def _request_components(operation: Any) -> list[str]:
    name = _component_name(_media_schema(operation.get("requestBody")))
    return [name] if name else []


class AppLoader:
    """Strategy base class for producing a :class:`LoadedApp`."""

    def load(self, target: str, *, root: Path, sys_paths: Sequence[str] = ()) -> LoadedApp:
        raise NotImplementedError

    @classmethod
    def for_environment(cls, *, prefer_subprocess: bool = True) -> AppLoader:
        """Factory: pick the isolation level appropriate to the environment."""
        return SubprocessLoader() if prefer_subprocess else InProcessLoader()

    def _envelope_to_app(self, envelope: dict[str, Any], target: str) -> LoadedApp:
        if envelope.get("ok"):
            return LoadedApp(
                openapi=envelope["openapi"],
                exposure=envelope.get("exposure", []),
                pydantic_version=envelope.get("pydantic_version", ""),
                fastapi_version=envelope.get("fastapi_version", ""),
            )
        raise _raise_from_envelope(envelope, target)


def _raise_from_envelope(envelope: dict[str, Any], target: str) -> AppLoadError:
    category = envelope.get("category", "")
    message = envelope.get("message", "Unknown application error.")
    hint = envelope.get("hint")
    types: dict[str, type[AppLoadError]] = {
        "PydanticCompatibilityError": PydanticCompatibilityError,
        "OpenAPIGenerationError": OpenAPIGenerationError,
    }
    error_type = types.get(category, AppLoadError)
    detail = envelope.get("traceback") or ""
    return error_type(f"{message}\n{detail}".strip(), target=target, hint=hint)


class InProcessLoader(AppLoader):
    def load(self, target: str, *, root: Path, sys_paths: Sequence[str] = ()) -> LoadedApp:
        return self._envelope_to_app(build_payload(target, sys_paths), target)


class SubprocessLoader(AppLoader):
    """Runs the payload builder in a child interpreter so a bad app cannot kill us."""

    def __init__(self, timeout: int = 60) -> None:
        self._timeout = timeout

    def load(self, target: str, *, root: Path, sys_paths: Sequence[str] = ()) -> LoadedApp:
        command = [sys.executable, "-m", "contractwatch._bootstrap", target, *sys_paths]
        try:
            completed = subprocess.run(
                command, cwd=root, capture_output=True, text=True, check=False, timeout=self._timeout
            )
        except subprocess.TimeoutExpired as exc:
            raise AppLoadError(
                f"Importing '{target}' timed out after {self._timeout}s.",
                target=target,
                hint="The application may run blocking code at import time.",
            ) from exc
        payload = _extract_json(completed.stdout)
        if payload is None:
            raise AppLoadError(
                f"The application loader produced no result for '{target}'.",
                target=target,
                hint=(completed.stderr or "No output was produced.").strip()[-600:],
            )
        return self._envelope_to_app(payload, target)


def _extract_json(stdout: str) -> dict[str, Any] | None:
    for line in reversed(stdout.splitlines()):
        line = line.strip()
        if line.startswith("{") and line.endswith("}"):
            try:
                parsed = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(parsed, dict) and "ok" in parsed:
                return parsed
    return None


ANALYSIS_ERROR_REMEDIATION = Remediation(
    summary="Fix the application so it imports and generates OpenAPI, then re-run.",
    steps=[
        "Run `contractwatch inspect --app <module:attr>` to reproduce the error.",
        "Resolve the reported import or schema error in the application code.",
        "Verify the app starts: `python -c \"import <module>\"`.",
        "Re-run `contractwatch check`.",
    ],
    commands=["contractwatch check"],
)


__all__ = [
    "ANALYSIS_ERROR_REMEDIATION",
    "AppLoader",
    "InProcessLoader",
    "LoadedApp",
    "SubprocessLoader",
    "build_payload",
]
