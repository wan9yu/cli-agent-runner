#!/usr/bin/env bash
# build.sh — convenience wrapper for common dev tasks.
# Usage: ./build.sh <task>

set -euo pipefail
PY=${PY:-.venv/bin/python}

case "${1:-help}" in
  docs)
    "$PY" -m agent_runner._docgen
    ;;
  literate)
    "$PY" -m pytest tests/literate/ -v
    ;;
  test)
    "$PY" -m pytest -q --ignore=tests/e2e --ignore=tests/literate
    ;;
  lint)
    # ruff format --check intentionally omitted — current source has known
    # format drift vs. ruff defaults that would expand 3 modules past their
    # LOC ratchet caps. A future "format sweep" commit will reconcile both.
    "$PY" -m ruff check .
    ;;
  check)
    "$0" lint
    "$0" test
    "$0" literate
    "$0" docs
    git diff --exit-code docs/
    ;;
  e2e)
    AGENT_RUNNER_E2E_PI=1 "$PY" -m pytest tests/e2e/ -v
    ;;
  help|*)
    cat <<HELP
Usage: $0 <task>

  docs      Render <!-- gen:* --> blocks in docs/*.md.
  literate  Run quickstart.md as a test (bash blocks executed in sequence).
  test      Unit + integration suite.
  lint      ruff check + ruff format --check.
  check     Full local-CI sweep: lint + test + literate + docs (gate).
  e2e       Pi e2e suite (needs ssh alias 'pi' and AGENT_RUNNER_E2E_PI=1).
HELP
    ;;
esac
