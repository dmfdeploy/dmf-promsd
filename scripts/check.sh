#!/usr/bin/env bash
# check.sh — run the checks that CI runs: file size, format, lint, and tests.
#
# Usage:
#
#   scripts/check.sh
#
# Needs ruff and pytest on PATH (pip install -e '.[dev]').

set -euo pipefail

cd "$(git rev-parse --show-toplevel)"

# Fail on any tracked Python file over 400 lines.
over_cap=0
while IFS= read -r -d '' file; do
  [[ -f $file ]] || continue
  lines=$(($(wc -l < "$file")))
  if ((lines > 400)); then
    echo "$file has $lines lines, over the cap of 400"
    over_cap=1
  fi
done < <(git ls-files -z '*.py')
((over_cap == 0)) || exit 1

ruff format --check .
ruff check .
pytest -q
