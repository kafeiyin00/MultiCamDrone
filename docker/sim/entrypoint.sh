#!/usr/bin/env bash
# -----------------------------------------------------------------------------
# Project AirSim simulation server entrypoint.
#
# Locates the packaged Unreal launcher for $SIM_ENV_NAME under $SIM_ENV_ROOT and
# starts it headless. Any arguments passed to `docker run` are appended to the
# launcher, so you can override anything on the fly:
#
#     docker compose run --rm sim -ResX=1280 -ResY=720
#
# Run it with a shell instead by passing `bash` as the first argument.
# -----------------------------------------------------------------------------
set -euo pipefail

SIM_ENV_ROOT="${SIM_ENV_ROOT:-/environments}"
SIM_ENV_NAME="${SIM_ENV_NAME:-Blocks}"
SIM_TOPICS_PORT="${SIM_TOPICS_PORT:-8989}"
SIM_SERVICES_PORT="${SIM_SERVICES_PORT:-8990}"
SIM_RENDER_MODE="${SIM_RENDER_MODE:-offscreen}"   # offscreen | nullrhi | windowed
SIM_RES_X="${SIM_RES_X:-1280}"
SIM_RES_Y="${SIM_RES_Y:-720}"

# Escape hatch: if the first argument names a real command rather than an Unreal
# `-switch`, run that instead of the simulator. Lets you poke at the container
# with `docker compose run --rm sim vulkaninfo` or `... sim bash`.
if [[ $# -gt 0 && "$1" != -* ]] && command -v "$1" >/dev/null 2>&1; then
  exec "$@"
fi

echo "=============================================================="
echo " Project AirSim simulation server"
echo "=============================================================="

# --- GPU / Vulkan sanity check -------------------------------------------
if command -v vulkaninfo >/dev/null 2>&1; then
  devices="$(vulkaninfo --summary 2>/dev/null | grep 'deviceName' | sed 's/.*= //' \
             | sort | uniq -c | sed 's/^ *\([0-9]*\) \(.*\)/\2 x\1/' || true)"
  if [[ -z "$devices" ]]; then
    echo "[WARN] Vulkan enumerated no devices at all. Rendering will fail."
    echo "       Start the container with GPU access and make sure"
    echo "       NVIDIA_DRIVER_CAPABILITIES includes 'graphics'."
  elif grep -qi 'llvmpipe\|swrast' <<< "$devices"; then
    echo "[WARN] Vulkan is falling back to SOFTWARE rendering (${devices//$'\n'/, })."
    echo "       The simulation will run, but at a few frames per second."
    echo "       Run ./scripts/verify.sh on the host to diagnose."
  else
    echo "[ok]   Vulkan device(s): ${devices//$'\n'/, }"
  fi
fi

# --- Locate the packaged launcher ----------------------------------------
env_dir="${SIM_ENV_ROOT}/${SIM_ENV_NAME}"
if [[ ! -d "$env_dir" ]]; then
  echo "[ERROR] Environment directory not found: $env_dir" >&2
  echo "        Available under ${SIM_ENV_ROOT}:" >&2
  ls -1 "$SIM_ENV_ROOT" 2>/dev/null | sed 's/^/          /' >&2 || echo "          (empty)" >&2
  exit 1
fi

# The packaged environment ships a top-level <Name>.sh next to the Engine/ and
# <Name>/ folders. Prefer an exact name match, else take the first .sh found.
launcher="$(find "$env_dir" -maxdepth 3 -name "${SIM_ENV_NAME}.sh" -type f | head -n1)"
if [[ -z "$launcher" ]]; then
  launcher="$(find "$env_dir" -maxdepth 3 -name '*.sh' -type f | head -n1)"
fi
if [[ -z "$launcher" ]]; then
  echo "[ERROR] No launcher .sh found under $env_dir" >&2
  exit 1
fi
chmod +x "$launcher" 2>/dev/null || true

# --- Assemble command line ------------------------------------------------
args=( "-topicsport=${SIM_TOPICS_PORT}" "-servicesport=${SIM_SERVICES_PORT}" "-nosound" )
case "$SIM_RENDER_MODE" in
  offscreen) args+=( "-RenderOffScreen" "-ResX=${SIM_RES_X}" "-ResY=${SIM_RES_Y}" ) ;;
  nullrhi)   args+=( "-nullrhi" ) ;;
  windowed)  args+=( "-windowed" "-ResX=${SIM_RES_X}" "-ResY=${SIM_RES_Y}" ) ;;
  *)         echo "[WARN] Unknown SIM_RENDER_MODE='$SIM_RENDER_MODE', using -RenderOffScreen"
             args+=( "-RenderOffScreen" ) ;;
esac
[[ -n "${SIM_EXTRA_ARGS:-}" ]] && read -r -a extra <<< "$SIM_EXTRA_ARGS" && args+=( "${extra[@]}" )
args+=( "$@" )

echo "[info] Launcher : $launcher"
echo "[info] Args     : ${args[*]}"
echo "[info] Listening: topics=${SIM_TOPICS_PORT} services=${SIM_SERVICES_PORT}"
echo "--------------------------------------------------------------"

exec "$launcher" "${args[@]}"
