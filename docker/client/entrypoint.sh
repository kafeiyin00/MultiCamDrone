#!/usr/bin/env bash
# -----------------------------------------------------------------------------
# Project AirSim Python client entrypoint.
#
# Installs the bind-mounted client package in editable mode (fast no-op once the
# image's pre-installed deps are in place) and then runs whatever was asked for:
#
#     docker compose run --rm client bash
#     docker compose run --rm client python hello_drone.py
# -----------------------------------------------------------------------------
set -euo pipefail

REPO="${PROJECTAIRSIM_REPO:-/workspace/ProjectAirSim}"
PKG_DIR="${REPO}/client/python/projectairsim"

if [[ -d "$PKG_DIR" ]]; then
  if ! python -c "import projectairsim" >/dev/null 2>&1; then
    echo "[info] Installing projectairsim (editable) from $PKG_DIR ..."
    python -m pip install --no-cache-dir --no-build-isolation -e "$PKG_DIR" >/dev/null
    echo "[ok]   projectairsim installed."
  fi
else
  echo "[WARN] Client package not found at $PKG_DIR."
  echo "       Is the ProjectAirSim repo bind-mounted into the container?"
fi

# The example scripts default to 127.0.0.1; inside compose the sim is a separate
# host. Exporting this lets scripts pick it up via os.environ if they support it.
export SIM_ADDRESS="${SIM_ADDRESS:-sim}"

exec "$@"
