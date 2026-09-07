#!/usr/bin/env bash
# -----------------------------------------------------------------------------
# One-shot bootstrap for a fresh clone of MultiCamDrone.
#
#   git clone --recurse-submodules git@github.com:kafeiyin00/MultiCamDrone.git
#   cd MultiCamDrone && ./scripts/setup.sh
#
# Checks the host prerequisites, pulls the submodule, writes docker/.env for the
# current user, downloads the Blocks environment, and builds both images.
# -----------------------------------------------------------------------------
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

ok()   { echo -e "  \033[32m[ok]\033[0m   $*"; }
warn() { echo -e "  \033[33m[warn]\033[0m $*"; }
die()  { echo -e "  \033[31m[FAIL]\033[0m $*" >&2; exit 1; }

echo "=============================================================="
echo " MultiCamDrone / Project AirSim - environment setup"
echo "=============================================================="

# --- 1. Host prerequisites ------------------------------------------------
echo; echo "[1/5] Checking host prerequisites"

command -v docker >/dev/null 2>&1 || die "docker not found. Install Docker Engine first."
ok "docker $(docker --version | awk '{print $3}' | tr -d ,)"

docker compose version >/dev/null 2>&1 || die "'docker compose' (v2) not found."
ok "compose $(docker compose version --short)"

docker info >/dev/null 2>&1 || die "Cannot talk to the docker daemon. Add yourself to the 'docker' group: sudo usermod -aG docker \$USER (then re-login)."
ok "docker daemon reachable without sudo"

if command -v nvidia-smi >/dev/null 2>&1; then
  ok "GPU: $(nvidia-smi --query-gpu=name --format=csv,noheader | head -1)"
else
  warn "nvidia-smi not found. The simulator needs an NVIDIA GPU for Vulkan rendering."
fi

if docker info 2>/dev/null | grep -q 'nvidia'; then
  ok "nvidia container runtime registered with docker"
else
  warn "nvidia runtime not listed by 'docker info'. Install nvidia-container-toolkit and run: sudo nvidia-ctk runtime configure --runtime=docker && sudo systemctl restart docker"
fi

# --- 2. Submodule ---------------------------------------------------------
echo; echo "[2/5] Syncing the ProjectAirSim submodule"
if [[ -f .gitmodules ]]; then
  git submodule update --init --recursive
  ok "ProjectAirSim at $(git -C ProjectAirSim describe --tags --always 2>/dev/null || echo 'unknown')"
else
  warn "No .gitmodules found; skipping."
fi

# --- 3. Local docker settings --------------------------------------------
echo; echo "[3/5] Writing docker/.env"
if [[ -f docker/.env ]]; then
  ok "docker/.env already exists, leaving it alone"
else
  sed -e "s/^USER_UID=.*/USER_UID=$(id -u)/" \
      -e "s/^USER_GID=.*/USER_GID=$(id -g)/" \
      docker/.env.example > docker/.env
  ok "docker/.env created for uid=$(id -u) gid=$(id -g)"
fi

# --- 4. Simulation environment -------------------------------------------
echo; echo "[4/5] Fetching the Blocks environment (~550MB, skipped if present)"
./scripts/fetch_env.sh "${SIM_ENV_NAME:-Blocks}"

# --- 5. Images ------------------------------------------------------------
echo; echo "[5/5] Building docker images"
docker compose -f docker/docker-compose.yml build
ok "images built"

cat <<EOF

==============================================================
 Setup complete.

   Start the simulator :  ./scripts/run_sim.sh
   Open a client shell :  ./scripts/run_client.sh
   Verify the stack    :  ./scripts/verify.sh
==============================================================
EOF
