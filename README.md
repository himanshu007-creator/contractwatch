<div align="center">

# ContractWatch

**Catch breaking FastAPI changes before your clients do.**

[![CI](https://github.com/himanshu007-creator/contractwatch/actions/workflows/ci.yml/badge.svg)](https://github.com/himanshu007-creator/contractwatch/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/contractwatch.svg?cacheSeconds=3600)](https://pypi.org/project/contractwatch/)
[![Python](https://img.shields.io/pypi/pyversions/contractwatch.svg?cacheSeconds=3600)](https://pypi.org/project/contractwatch/)
[![License](https://img.shields.io/pypi/l/contractwatch.svg?cacheSeconds=3600)](LICENSE)

</div>

Your unit tests pass, you rename a response field, and the frontend breaks in production.
ContractWatch snapshots your app's OpenAPI contract, then fails your commit or CI run if a change would break existing clients.

<p align="center">
  <img src="https://raw.githubusercontent.com/himanshu007-creator/contractwatch/main/docs/img/check-fail.svg" alt="contractwatch check blocking a removed endpoint and a removed response field" width="800">
</p>

## What it catches

- **Breaking changes**: removed endpoints, removed or retyped response fields, newly required request fields, removed status codes, narrowed enums.
- **Schema-name collisions**: two Pydantic models with the same name in different modules, which makes OpenAPI component names ambiguous.
- **Reliability pitfalls**: blocking calls inside `async` endpoints, HTTP calls without a timeout, duplicate routes, conflicting operation IDs.
- **Fixes, not just errors**: every finding says what broke, who it hurts, and how to fix it.

## Install

```bash
pip install contractwatch    # or: uv add --dev contractwatch
```

Python 3.11+, FastAPI, Pydantic v2.

## Use it

```bash
contractwatch init     # save the current contract as the baseline (commit .contractwatch/baseline.json)
contractwatch check    # compare against the baseline
```

Exit codes: `0` safe, `1` blocking change, `2` analysis error.

Changed the API on purpose? Accept it:

```bash
contractwatch baseline update
```

Collisions that aren't breaking show up as warnings:

<p align="center">
  <img src="https://raw.githubusercontent.com/himanshu007-creator/contractwatch/main/docs/img/collision.svg" alt="contractwatch check warning about two models named Item" width="800">
</p>

## Commands

| Command | What it does |
|---|---|
| `init` | Create the baseline and optionally install the pre-commit hook |
| `check` | Compare against the baseline; saves a report on failure |
| `inspect` | Show the current routes and schemas |
| `baseline update` | Accept the current contract as the new baseline |
| `report --latest` | Re-open the last report (`--format json`, `--output <path>`, `--list`, `--open`) |

Run `contractwatch <command> --help` for all options.

## Pre-commit and CI

`contractwatch init` offers to install the hook for you (skip with `--no-install-hook`). Or add it by hand:

```yaml
repos:
  - repo: local
    hooks:
      - id: contractwatch
        name: ContractWatch
        entry: contractwatch check
        language: system
        pass_filenames: false
```

In CI, just run `contractwatch check`; the exit code does the rest.

## Configuration

Optional. Put it in `contractwatch.toml` or `[tool.contractwatch]` in `pyproject.toml`:

```toml
[tool.contractwatch]
app = "app.main:app"           # skip auto-detection
severity_threshold = "error"   # error | warning | info
baseline = ".contractwatch/baseline.json"
ignore = ["CW110"]             # silence specific codes

[tool.contractwatch.retention]
keep = 20                      # keep the last 20 reports...
days = 7                       # ...or the last 7 days, whichever is stricter
```

Env vars: `CONTRACTWATCH_HOME` (reports/cache location), `NO_COLOR`.

## Diagnostic codes

| Code | Meaning | Severity |
|---|---|---|
| CW101 | Removed endpoint | error |
| CW102 | Removed response field | error |
| CW103 | Incompatible type change | error |
| CW104 | Newly required request field | error |
| CW105 | Requiredness / nullability change | error |
| CW106 | Removed response status | error |
| CW107 | Breaking enum change | error |
| CW108 | Incompatible schema shape | error |
| CW110 | Compatible addition | info |
| CW201 | Confirmed schema collision | error |
| CW202 | Ambiguous repeated model name | warning |
| CW203 | Repeated model name, not exposed | info |
| CW204 | Component reference change | warning |
| RL101 | Blocking call in async endpoint | warning |
| RL102 | HTTP client call without a timeout | warning |
| RL103 | Duplicate route/method registration | warning |
| RL104 | Conflicting operationId values | warning |

## Contributing

```bash
uv sync --group dev
uv run ruff check . && uv run mypy && uv run pytest -q
```

## License

MIT
