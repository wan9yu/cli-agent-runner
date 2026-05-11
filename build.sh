#!/usr/bin/env bash
set -euo pipefail
PY=${PY:-.venv/bin/python}

case "${1:-help}" in
  docs)
    "$PY" -m agent_runner._docgen
    ;;
  help|*)
    cat <<HELP
Usage: $0 <task>

  docs      Render <!-- gen:* --> blocks in docs/*.md.
HELP
    ;;
esac
