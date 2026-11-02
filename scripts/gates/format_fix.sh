#!/usr/bin/env bash
# Apply ruff fixes to the files you changed, then review and stage them yourself.
# Hooks only check formatting; they never rewrite or re-stage files.
set -euo pipefail
cd "$(git rev-parse --show-toplevel)"
files=$(git diff --name-only --diff-filter=ACMR HEAD -- '*.py')
[ -z "$files" ] && { echo "no changed Python files"; exit 0; }
# shellcheck disable=SC2086
ruff check --fix $files
# shellcheck disable=SC2086
ruff format $files
echo "Done. Review with git diff, then stage."
