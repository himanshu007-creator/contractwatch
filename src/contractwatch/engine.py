"""The analysis engine: orchestrates loading, caching, comparison, and collision checks."""

from __future__ import annotations

import time
from contextlib import suppress
from pathlib import Path
from typing import Any

from . import __version__
from .baseline import Baseline
from .cache import Cache, CacheKey, digest_file
from .collisions import CollisionContext, Exposure, analyze_collisions
from .compare import compare
from .config import Configuration
from .contract import Contract
from .diagnostics import AnalysisError, AnalysisResult, Remediation
from .discovery import ProjectLayout
from .errors import AppLoadError, OpenAPIGenerationError, PydanticCompatibilityError
from .loader import ANALYSIS_ERROR_REMEDIATION, AppLoader, LoadedApp
from .reliability import ReliabilityContext, run_reliability
from .reporting import ReportStore
from .source import SourceIndex

_LOAD_ERROR_CODES: dict[type[Exception], str] = {
    PydanticCompatibilityError: "CW301",
    OpenAPIGenerationError: "CW304",
    AppLoadError: "CW303",
}

_NO_BASELINE_ERROR = AnalysisError(
    code="CW401",
    message="No contract baseline exists yet.",
    detail="Run `contractwatch init` (or `contractwatch baseline update`) to accept the current contract.",
    remediation=Remediation(
        summary="Create the initial baseline and re-run.",
        steps=[
            "Run `contractwatch init` to accept the current contract as the baseline.",
            "Commit the generated `.contractwatch/baseline.json`.",
            "Re-run `contractwatch check`.",
        ],
        commands=["contractwatch init", "contractwatch check"],
    ),
)

_PYDANTIC_V1_ERROR = AnalysisError(
    code="CW301",
    message="Pydantic v1 is installed, but ContractWatch requires Pydantic v2.",
    detail=(
        "The generated OpenAPI is unreliable under v1. Upgrade to Pydantic v2 and migrate "
        "the application."
    ),
    remediation=Remediation(
        summary="Upgrade Pydantic to v2.",
        steps=[
            "Run `uv add 'pydantic>=2'` or `pip install 'pydantic>=2'`.",
            "Migrate v1 constructs (`@validator`, `class Config`) to v2 equivalents.",
            "Re-run `contractwatch check`.",
        ],
        commands=["uv add 'pydantic>=2'"],
    ),
)

_V1_MARKERS_ERROR = AnalysisError(
    code="CW302",
    message="Pydantic v1 API usage was detected in the application sources.",
    detail="v1 decorators or v1 Config keys make the contract unreliable on Pydantic v2.",
    remediation=Remediation(
        summary="Migrate the flagged v1 constructs to Pydantic v2.",
        steps=[
            "Replace `@validator`/`@root_validator` with `@field_validator`/`@model_validator`.",
            "Replace `class Config` with `model_config`.",
            "Re-run `contractwatch check`.",
        ],
        commands=["contractwatch check"],
    ),
)


class Engine:
    """High-level pipeline. Presentation-free: returns an :class:`AnalysisResult`."""

    def __init__(
        self,
        *,
        config: Configuration,
        layout: ProjectLayout,
        loader: AppLoader,
        cache: Cache,
        store: ReportStore,
    ) -> None:
        self.config = config
        self.layout = layout
        self.loader = loader
        self.cache = cache
        self.store = store

    @classmethod
    def create(
        cls,
        config: Configuration,
        layout: ProjectLayout,
        *,
        prefer_subprocess: bool = True,
        cache_directory: Path | None = None,
    ) -> Engine:
        """Factory: build a fully wired engine."""
        loader = AppLoader.for_environment(prefer_subprocess=prefer_subprocess)
        cache_directory = cache_directory if cache_directory is not None else Cache.default().directory
        return cls(
            config=config,
            layout=layout,
            loader=loader,
            cache=Cache(cache_directory),
            store=ReportStore.default(),
        )

    def run(
        self,
        target: str,
        *,
        baseline: Baseline | None = None,
        max_findings: int | None = None,
    ) -> AnalysisResult:
        started = time.perf_counter()
        acquired = self._acquire(target)
        errors: list[AnalysisError] = []
        findings: list[Any] = []
        routes = components = 0
        baseline_ref: str | None = None

        if isinstance(acquired, AnalysisError):
            errors.append(acquired)
            current: Contract | None = None
            source: SourceIndex | None = None
        else:
            current = Contract.from_openapi(acquired["openapi"])
            source = self._build_source_index()
            errors.extend(_environment_errors(source, acquired))
            exposure = Exposure.from_records(acquired.get("exposure", []))
            if baseline is None:
                errors.append(_NO_BASELINE_ERROR)
            else:
                baseline_ref = baseline.app or f"baseline-{baseline.created[:10]}"
                findings = compare(baseline.contract(), current)
                findings.extend(analyze_collisions(CollisionContext(source=source, exposure=exposure)))
                findings.extend(
                    run_reliability(
                        ReliabilityContext(layout=self.layout, index=source, openapi=acquired["openapi"])
                    )
                )
            routes = len(current.routes)
            components = len(current.schemas)

        findings = [f for f in findings if not self.config.ignores(f.code)]
        findings = sorted(findings, key=lambda f: (-f.severity.rank, f.code, f.endpoint or "", f.summary))
        max_findings = max_findings or self.config.max_findings
        skipped: list[str] = []
        if len(findings) > max_findings:
            skipped.append(f"{len(findings) - max_findings} findings omitted (max_findings={max_findings})")
            findings = findings[:max_findings]

        return AnalysisResult(
            findings=findings,
            errors=errors,
            routes_inspected=routes,
            components_inspected=components,
            duration_ms=int((time.perf_counter() - started) * 1000),
            baseline_ref=baseline_ref,
            skipped=skipped,
            threshold=self.config.severity_threshold,
        )

    def load_for_inspection(self, target: str) -> dict[str, Any]:
        """Load the current contract without comparing. Raises on failure."""
        acquired = self._acquire(target)
        if isinstance(acquired, AnalysisError):
            raise AppLoadError(acquired.message, target=target, hint=acquired.detail)
        return acquired

    def _acquire(self, target: str) -> dict[str, Any] | AnalysisError:
        try:
            key = None
            if self.config.cache:
                key = self._cache_key(target)
                cached = self.cache.load(key)
                if cached is not None:
                    return dict(cached)
            loaded = self.loader.load(target, root=self.layout.root, sys_paths=self.layout.sys_paths)
            app = _app_to_dict(loaded)
            if self.config.cache and key is not None:
                with suppress(Exception):  # caching must never fail a run
                    self.cache.store(key, app)
            return app
        except (AppLoadError, PydanticCompatibilityError, OpenAPIGenerationError) as exc:
            return _load_error(exc)

    def _cache_key(self, target: str) -> CacheKey:
        baseline_path = self.config.baseline_path
        baseline_digest = digest_file(baseline_path) if baseline_path.exists() else ""
        roots = self.layout.source_roots or (self.layout.root,)
        return CacheKey.for_run(
            tool_version=__version__,
            app=target,
            config=self.config,
            baseline_digest=baseline_digest,
            source_roots=roots,
            root=self.layout.root,
        )

    def _build_source_index(self) -> SourceIndex:
        extra = self.layout.source_roots[1:] if len(self.layout.source_roots) > 1 else ()
        return SourceIndex.build(self.layout.root, extra_roots=extra)


def _app_to_dict(loaded: LoadedApp) -> dict[str, Any]:
    return {
        "ok": True,
        "openapi": loaded.openapi,
        "exposure": loaded.exposure,
        "pydantic_version": loaded.pydantic_version,
        "fastapi_version": loaded.fastapi_version,
    }


def _load_error(exc: Exception) -> AnalysisError:
    code = _LOAD_ERROR_CODES.get(type(exc), "CW303")
    return AnalysisError(
        code=code,
        message=str(exc),
        detail=getattr(exc, "hint", None),
        remediation=ANALYSIS_ERROR_REMEDIATION,
    )


def _environment_errors(source: SourceIndex, app: dict[str, Any]) -> list[AnalysisError]:
    errors: list[AnalysisError] = []
    version = app.get("pydantic_version", "")
    if version.startswith("1"):
        errors.append(_PYDANTIC_V1_ERROR)
    if source.markers:
        marker = source.markers[0]
        errors.append(
            AnalysisError(
                code=_V1_MARKERS_ERROR.code,
                message=_V1_MARKERS_ERROR.message,
                detail=f"First occurrence: {marker.file}:{marker.line} ({marker.kind}: {marker.detail}).",
                remediation=_V1_MARKERS_ERROR.remediation,
            )
        )
    return errors


__all__ = ["Engine"]
