#!/bin/bash
# sentiment-tagger — THE ARTIFACT. Runs the agent headless, enforced by its charter,
# then gates on its evals and logs the run.
#
#   ./run.sh              # trigger defaults to "manual"
#   ./run.sh cron         # label the trigger (manual / cron / webhook / ...)
#
# Needs the `claude` CLI on PATH and Python 3 + PyYAML.
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
exec python3 "$here/../../run_headless.py" \
  --charter "$here/charter.yaml" \
  --trigger "${1:-manual}" \
  --eval
