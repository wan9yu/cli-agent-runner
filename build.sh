#!/usr/bin/env bash
# build.sh — convenience wrapper for common dev tasks.
# Usage: ./build.sh <task>

set -euo pipefail
PY=${PY:-.venv/bin/python}
# Parallel worker count for the `test` gate. Default `auto` (one worker per
# CPU) is safe on a dev laptop or CI runner; NEVER go above `-n 2` on a
# memory-constrained host (e.g. a sub-512MB single-board machine) -- set
# AR_TEST_JOBS=2 (or 1) there.
AR_TEST_JOBS=${AR_TEST_JOBS:-auto}

case "${1:-help}" in
  docs)
    "$PY" -m agent_runner._docgen
    ;;
  literate)
    "$PY" -m pytest tests/literate/ -v
    ;;
  test)
    # Two passes so `serial` tests (install process-wide signal handlers /
    # self-signals -- unsafe to share an xdist worker process with anything
    # else) still run every time: parallel set + serial set == the full suite.
    "$PY" -m pytest -q --ignore=tests/e2e --ignore=tests/literate \
      -n "$AR_TEST_JOBS" --dist worksteal -m "not serial" --durations=15
    "$PY" -m pytest -q --ignore=tests/e2e --ignore=tests/literate -m serial
    ;;
  test-lf)
    .venv/bin/pytest -q --lf --ignore=tests/e2e --ignore=tests/literate
    ;;
  coverage)
    "$PY" -m pytest -q --ignore=tests/e2e --ignore=tests/literate \
      --cov --cov-report=term --cov-report=html
    echo "HTML report: htmlcov/index.html"
    ;;
  lint)
    "$PY" -m ruff check . && "$PY" -m ruff format --check .
    ;;
  vulture)
    # Dead-code scan. Config in [tool.vulture]; exits nonzero on any finding.
    # No pipe here — a `| tail`/`| head` would swallow that nonzero exit code
    # and silently let dead code through the gate.
    "$PY" -m vulture
    ;;
  vulture-whitelist)
    "$PY" -m tests.generate_vulture_whitelist
    ;;
  check)
    "$0" lint
    "$0" vulture
    "$0" literate
    "$0" docs            # NOTE: must run before git diff --exit-code below
    git diff --exit-code docs/
    "$0" test
    ;;
  e2e)
    AGENT_RUNNER_E2E_PI=1 "$PY" -m pytest tests/e2e/ -v
    ;;
  help)
    cat <<HELP
Usage: $0 <task>

  docs      Render <!-- gen:* --> blocks in docs/*.md.
  literate  Run quickstart.md as a test (bash blocks executed in sequence).
  test      Unit + integration suite. Parallel via pytest-xdist (-n \$AR_TEST_JOBS,
            default 'auto') plus a serial pass for tests unsafe to parallelize.
            NEVER set AR_TEST_JOBS above 2 on a memory-constrained host (e.g.
            a sub-512MB single-board machine) -- AR_TEST_JOBS=2 ./build.sh test.
  test-lf   Re-run only last-failed tests (red->green inner loop; not the gate).
  lint      ruff check + ruff format --check.
  vulture   Dead-code scan ([tool.vulture]); fails on any finding.
  vulture-whitelist  Regenerate .vulture-whitelist.py from @dataclass fields.
  check     Full local-CI sweep: lint + vulture + test + literate + docs (gate).
  coverage  Run unit + integration tests with coverage (HTML + terminal).
  e2e       Pi e2e suite (needs ssh alias 'pi' and AGENT_RUNNER_E2E_PI=1).
HELP
    ;;
  *)
    echo "build.sh: unknown task '$1' — run \`$0 help\` for usage" >&2
    exit 2
    ;;
esac
