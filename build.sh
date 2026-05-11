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
  coverage)
    "$PY" -m pytest -q --ignore=tests/e2e --ignore=tests/literate \
      --cov --cov-report=term --cov-report=html
    echo "HTML report: htmlcov/index.html"
    ;;
  lint)
    "$PY" -m ruff check . && "$PY" -m ruff format --check .
    ;;
  check)
    "$0" lint
    "$0" test
    "$0" literate
    "$0" docs            # NOTE: must run before git diff --exit-code below
    git diff --exit-code docs/
    ;;
  e2e)
    AGENT_RUNNER_E2E_PI=1 "$PY" -m pytest tests/e2e/ -v
    ;;
  help)
    cat <<HELP
Usage: $0 <task>

  docs      Render <!-- gen:* --> blocks in docs/*.md.
  literate  Run quickstart.md as a test (bash blocks executed in sequence).
  test      Unit + integration suite.
  lint      ruff check + ruff format --check.
  check     Full local-CI sweep: lint + test + literate + docs (gate).
  coverage  Run unit + integration tests with coverage (HTML + terminal).
  e2e       Pi e2e suite (needs ssh alias 'pi' and AGENT_RUNNER_E2E_PI=1).
HELP
    ;;
  *)
    echo "build.sh: unknown task '$1' — run \`$0 help\` for usage" >&2
    exit 2
    ;;
esac
