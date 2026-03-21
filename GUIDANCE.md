# Code Style & Conventions

Rules and corrections for writing code in this project.

## General

- Never use `from __future__ import annotations`. We target Python 3.12+ where modern syntax is native.
- Never import inside functions. All imports must be at the top of the file.
- Never use `# noqa: F401` — if something is imported, it must be used.
- Maximum line length is 120 characters.

## Tooling

- **ruff** — linting (replaces flake8). Config in pyproject.toml.
- **black** — code formatting (line-length=120, preview=true).
- **isort** — import sorting (profile=black).
- **pyright** — type checking (strict mode).
- **pre-commit** — runs all checks on commit. Config in `.pre-commit-config.yaml`.

## Pre-commit

Pre-commit hooks are configured and must be installed:

```bash
pip install pre-commit
pre-commit install
```

Hooks run: check-yaml, check-toml, end-of-file-fixer, trailing-whitespace, mixed-line-ending, isort, black, pyproject-fmt, ruff-check.

## Ruff Rules

We use an extended ruff rule set (from Visionatrix):
`A, B, C, E, F, G, I, PIE, Q, RET, RUF, S, SIM, UP, W`

Ignored: `I001` (isort handles imports), `RUF100`, `S311`, `S603`, `SIM117`.
