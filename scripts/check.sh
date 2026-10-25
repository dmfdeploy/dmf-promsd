#!/usr/bin/env bash
# check.sh — run the checks that CI runs: format, lint, and tests.
#
# Usage:
#
#   scripts/check.sh
#
# Needs ruff and pytest on PATH (pip install -e '.[dev]').

set -euo pipefail

cd "$(git rev-parse --show-toplevel)"

ruff format --check .
ruff check .
pytest -q
