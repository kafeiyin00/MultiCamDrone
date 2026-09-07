#!/usr/bin/env bash
# -----------------------------------------------------------------------------
# End-to-end health check for the MultiCamDrone / Project AirSim stack.
#
#   ./scripts/verify.sh
#
# Walks the chain from host prerequisites up to a live client connection and
# says exactly which link is broken. Exits non-zero if any check fails.
# -----------------------------------------------------------------------------
set -uo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
COMPOSE=( docker compose -f "${REPO_ROOT}/docker/docker-compose.yml" )
FAILED=0

pass() { echo -e "  \033[32m✓\033[0m $*"; }
fail() { echo -e "  \033[31m✗\033[0m $*"; FAILED=1; }
note() { echo -e "    \033[90m$*\033[0m"; }
hdr()  { echo; echo -e "\033[1m$*\033[0m"; }

echo "=============================================================="
echo " MultiCamDrone / Project AirSim - stack verification"
echo "=============================================================="

# --- 1. Host ---------------------------------------------------------------
hdr "1. Host prerequisites"

if command -v docker >/dev/null 2>&1; then
  pass "docker $(docker --version | awk '{print $3}' | tr -d ,)"
else
  fail "docker not installed"
fi

if docker info >/dev/null 2>&1; then
  pass "docker daemon reachable without sudo"
else
  fail "cannot reach the docker daemon"
  note "sudo usermod -aG docker \$USER   (then log out and back in)"
fi

if command -v nvidia-smi >/dev/null 2>&1; then
  gpu_count=$(nvidia-smi --query-gpu=name --format=csv,noheader 2>/dev/null | wc -l)
  pass "$gpu_count NVIDIA GPU(s): $(nvidia-smi --query-gpu=name --format=csv,noheader 2>/dev/null | head -1)"
else
  fail "nvidia-smi not found — the simulator needs an NVIDIA GPU"
fi

if docker info 2>/dev/null | grep -q nvidia; then
  pass "nvidia container runtime registered with docker"
else
  fail "nvidia runtime missing from 'docker info'"
  note "sudo nvidia-ctk runtime configure --runtime=docker && sudo systemctl restart docker"
fi

# --- 2. Repo contents ------------------------------------------------------
hdr "2. Repository contents"

if [[ -f "${REPO_ROOT}/ProjectAirSim/client/python/projectairsim/pyproject.toml" ]]; then
  pass "ProjectAirSim submodule checked out"
else
  fail "ProjectAirSim submodule is empty"
  note "git submodule update --init --recursive"
fi

env_name="${SIM_ENV_NAME:-Blocks}"
if [[ -x "${REPO_ROOT}/envs/${env_name}/${env_name}.sh" ]]; then
  pass "environment '${env_name}' present ($(du -sh "${REPO_ROOT}/envs/${env_name}" 2>/dev/null | cut -f1))"
else
  fail "environment '${env_name}' not found under envs/"
  note "./scripts/fetch_env.sh ${env_name}"
fi

# --- 3. Images -------------------------------------------------------------
hdr "3. Docker images"

for img in multicamdrone/airsim-sim multicamdrone/airsim-client; do
  if docker image inspect "${img}:latest" >/dev/null 2>&1; then
    pass "${img}:latest"
  else
    fail "${img}:latest not built"
    note "docker compose -f docker/docker-compose.yml build"
  fi
done

# --- 4. GPU rendering inside the container --------------------------------
hdr "4. Vulkan GPU rendering inside the sim container"

if docker image inspect multicamdrone/airsim-sim:latest >/dev/null 2>&1; then
  devices="$("${COMPOSE[@]}" run --rm --no-deps sim vulkaninfo --summary 2>/dev/null \
             | grep 'deviceName' | sed 's/.*= //' | sort -u)"
  if [[ -z "$devices" ]]; then
    fail "Vulkan enumerated no devices in the container"
    note "The NVIDIA driver libraries are not reaching the container."
    note "Check NVIDIA_DRIVER_CAPABILITIES includes 'graphics'."
  elif grep -qi 'llvmpipe\|swrast' <<< "$devices"; then
    fail "Vulkan is using SOFTWARE rendering: $(paste -sd', ' - <<< "$devices")"
    note "The NVIDIA ICD failed to initialise. The usual cause is a missing"
    note "/usr/share/glvnd/egl_vendor.d/10_nvidia.json — see docker/sim/Dockerfile."
  else
    pass "hardware Vulkan: $(paste -sd',' - <<< "$devices" | sed 's/,/, /g')"
  fi
else
  fail "skipped — sim image not built"
fi

# --- 5. Running simulator --------------------------------------------------
hdr "5. Simulation server"

if [[ -n "$(docker ps -q --filter name=airsim-sim 2>/dev/null)" ]]; then
  pass "container airsim-sim is running"

  for port in 8989 8990; do
    if (command -v ss >/dev/null 2>&1 && ss -ltn 2>/dev/null | grep -q ":${port} ") \
       || (command -v nc >/dev/null 2>&1 && nc -z 127.0.0.1 "$port" 2>/dev/null); then
      pass "port ${port} accepting connections"
    else
      fail "port ${port} is not listening"
    fi
  done

  if nvidia-smi --query-compute-apps=process_name,used_memory --format=csv,noheader 2>/dev/null \
     | grep -q 'Shipping'; then
    used=$(nvidia-smi --query-compute-apps=process_name,used_memory --format=csv,noheader 2>/dev/null \
           | grep 'Shipping' | head -1 | cut -d, -f2 | xargs)
    pass "Unreal is resident on the GPU (${used})"
  else
    note "Unreal not yet visible in nvidia-smi (it may still be loading)"
  fi
else
  note "airsim-sim is not running — start it with ./scripts/run_sim.sh"
  note "(checks 5 and 6 need a running simulator)"
fi

# --- 6. Client round trip --------------------------------------------------
hdr "6. Python client round trip"

if docker image inspect multicamdrone/airsim-client:latest >/dev/null 2>&1; then
  if "${COMPOSE[@]}" run --rm --no-deps client python -c "
import projectairsim, sys
print('    projectairsim', projectairsim.__version__ if hasattr(projectairsim,'__version__') else '(version n/a)')
" 2>/dev/null | grep -q projectairsim; then
    pass "projectairsim imports in the client container"
  else
    fail "projectairsim does not import in the client container"
  fi

  if [[ -n "$(docker ps -q --filter name=airsim-sim 2>/dev/null)" ]]; then
    out="$("${COMPOSE[@]}" run --rm --no-deps client python -c "
from projectairsim import ProjectAirSimClient
c = ProjectAirSimClient(address='sim')
c.connect()
print('CONNECT_OK')
c.disconnect()
" 2>&1)"
    if grep -q CONNECT_OK <<< "$out"; then
      pass "client connected to the simulator over the compose network"
    else
      fail "client could not connect to the simulator"
      note "$(tail -3 <<< "$out" | tr '\n' ' ')"
    fi
  fi
else
  fail "skipped — client image not built"
fi

# --- Summary ---------------------------------------------------------------
echo
echo "=============================================================="
if [[ $FAILED -eq 0 ]]; then
  echo -e " \033[32mAll checks passed.\033[0m"
else
  echo -e " \033[31mSome checks failed — see the ✗ lines above.\033[0m"
fi
echo "=============================================================="
exit $FAILED
