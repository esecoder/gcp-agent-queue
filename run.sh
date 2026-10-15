#!/usr/bin/env bash
# gcp-agent-queue — one entry point.
#
#   ./run.sh test      governance + queue logic, no dependencies, no GCP
#   ./run.sh verify    REAL GCP round trip (needs: gcloud auth application-default login)
#   ./run.sh publish   publish one run request to real GCP
set -euo pipefail
cd "$(dirname "$0")"

# ⚠️⚠️ THE PROJECT'S VENV FIRST, THEN the environment, THEN the system.
#
# The first version used `${PY:-python3}` and picked the system interpreter, which does
# not have google-cloud-pubsub -- so `./run.sh verify` printed "pip install
# google-cloud-pubsub" even though the library was installed, in ./.venv, exactly where
# README said it would be.
#
# ⚠️ The same mistake was made in adaptive-practice-loop: a script tested with one
# interpreter and shipped using another. A run.sh that does not use the venv the README
# instructs you to create is a run.sh that fails on the first command a reviewer types.
for cand in "${PY:-}" "./.venv/bin/python" "$(command -v python3 || true)"; do
  [ -n "$cand" ] && [ -x "$cand" ] && PY="$cand" && break
done
: "${PY:?no python interpreter found}"

case "${1:-test}" in
  test)   "$PY" test_agent_queue.py ;;
  verify) "$PY" verify_gcp.py ;;
  publish) shift; "$PY" agent_queue.py publish "$@" ;;
  worker) "$PY" agent_queue.py worker --once ;;
  *) sed -n '2,7p' "$0" ;;
esac
