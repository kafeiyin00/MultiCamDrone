#!/usr/bin/env bash
# -----------------------------------------------------------------------------
# Open a Python client session against the running simulator.
#
#   ./scripts/run_client.sh                    # interactive shell
#   ./scripts/run_client.sh hello_drone.py     # run one example script
#   ./scripts/run_client.sh python -c "import projectairsim; print('ok')"
#
# The working directory inside the container is the example_user_scripts folder
# of the ProjectAirSim submodule; the whole repo is mounted at /workspace.
# -----------------------------------------------------------------------------
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
COMPOSE=( docker compose -f "${REPO_ROOT}/docker/docker-compose.yml" )

# cv2.imshow() windows from the examples need an X11 cookie for the container.
if [[ -n "${DISPLAY:-}" ]] && command -v xhost >/dev/null 2>&1; then
  xhost +local:docker >/dev/null 2>&1 || true
fi

if [[ $# -eq 0 ]]; then
  set -- bash
elif [[ "$1" == *.py ]]; then
  set -- python "$@"
fi

# --no-deps: attach to whatever sim is already up rather than starting another.
exec "${COMPOSE[@]}" run --rm --no-deps client "$@"
