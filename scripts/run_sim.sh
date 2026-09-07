#!/usr/bin/env bash
# -----------------------------------------------------------------------------
# Start the Project AirSim simulation server container.
#
#   ./scripts/run_sim.sh                 # headless, detached, follows the log
#   ./scripts/run_sim.sh --fg            # stay in the foreground
#   ./scripts/run_sim.sh --env Neighborhood
#   ./scripts/run_sim.sh --render nullrhi
#   ./scripts/run_sim.sh -- -NoVSync -benchmark      # extra Unreal switches
# -----------------------------------------------------------------------------
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
COMPOSE=( docker compose -f "${REPO_ROOT}/docker/docker-compose.yml" )

FOREGROUND=0
EXTRA=()
while [[ $# -gt 0 ]]; do
  case "$1" in
    --fg|--foreground) FOREGROUND=1; shift ;;
    --env)    export SIM_ENV_NAME="$2"; shift 2 ;;
    --render) export SIM_RENDER_MODE="$2"; shift 2 ;;
    --res)    export SIM_RES_X="${2%%x*}"; export SIM_RES_Y="${2##*x}"; shift 2 ;;
    --)       shift; EXTRA=( "$@" ); break ;;
    -h|--help)
      sed -n '2,12p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "[ERROR] Unknown option: $1" >&2; exit 1 ;;
  esac
done

env_name="${SIM_ENV_NAME:-Blocks}"
if [[ ! -d "${REPO_ROOT}/envs/${env_name}" ]]; then
  echo "[ERROR] Environment '${env_name}' is not in envs/." >&2
  echo "        Fetch it with: ./scripts/fetch_env.sh ${env_name}" >&2
  exit 1
fi

[[ ${#EXTRA[@]} -gt 0 ]] && export SIM_EXTRA_ARGS="${EXTRA[*]}"

echo "[info] environment : ${env_name}"
echo "[info] render mode : ${SIM_RENDER_MODE:-offscreen}"
[[ -n "${SIM_EXTRA_ARGS:-}" ]] && echo "[info] extra args   : ${SIM_EXTRA_ARGS}"

if [[ $FOREGROUND -eq 1 ]]; then
  exec "${COMPOSE[@]}" up sim
fi

"${COMPOSE[@]}" up -d sim
echo
echo "[ok]   Simulator started. API on 127.0.0.1:8989 (topics) / 8990 (services)."
echo "[hint] Client shell : ./scripts/run_client.sh"
echo "[hint] Stop it      : docker compose -f docker/docker-compose.yml down"
echo
echo "--- following the Unreal log (Ctrl-C detaches, sim keeps running) ---"
exec "${COMPOSE[@]}" logs -f sim
