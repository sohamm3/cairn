#!/usr/bin/env bash
#
# Run Cairn's full pytest suite inside the app container, then show the custom
# cairn_ metrics from /metrics for a quick visual confirmation.
#
# Exit code is pytest's: the script fails (non-zero) iff the test suite fails.
# The metrics check is informational and never changes the exit code.

set -uo pipefail

# Resolve the project root (this script lives in scripts/) so it works from
# anywhere and `docker compose` finds the compose file.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR/.."

METRICS_URL="http://localhost:8000/metrics"

echo "==> Running pytest suite inside the app container"
docker compose exec -T app pytest -v
pytest_rc=$?

echo
echo "==> Custom cairn_ metrics from ${METRICS_URL} (first 30 lines)"
# The raw endpoint lists prometheus_client's default python_/process_ collectors
# first, so we filter to the cairn_ families (HELP/TYPE/samples); otherwise the
# top of the output would contain none of our metrics. -L follows the mount's
# trailing-slash redirect (/metrics -> /metrics/).
curl -sL "$METRICS_URL" | grep -E '^(# (HELP|TYPE) )?cairn_' | head -n 30

echo
if [ "$pytest_rc" -ne 0 ]; then
  echo "FAIL: pytest exited with code ${pytest_rc}"
  exit "$pytest_rc"
fi

echo "PASS: test suite passed"
