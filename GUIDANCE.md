# Code Style & Conventions

Rules and corrections for writing code in this project.

## General

- Never use `from __future__ import annotations`. We target Python 3.12+ where modern syntax is native.
- Maximum line length is 120 characters. Enforced by ruff (`line-length = 120` in pyproject.toml).
