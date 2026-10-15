#!/usr/bin/env bash
# gcp-agent-queue — one entry point.
#
#   ./run.sh test      governance + queue logic, no dependencies, no GCP
#   ./run.sh verify    REAL GCP round trip (needs: gcloud auth application-default login)
#   ./run.sh publish   publish one run request to real GCP
set -euo pipefail
cd "$(dirname "$0")"
PY="${PY:-python3}"

case "${1:-test}" in
  test)   "$PY" test_agent_queue.py ;;
  verify) "$PY" verify_gcp.py ;;
  publish)
    shift
    "$PY" agent_queue.py publish "$@" ;;
  worker) "$PY" agent_queue.py worker --once ;;
  *) sed -n '2,7p' "$0" ;;
esac
