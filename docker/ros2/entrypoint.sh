#!/usr/bin/env bash
# -----------------------------------------------------------------------------
# ROS2 bridge container entrypoint.
#
#   bridge   (default)  build if needed, then run the Project AirSim bridge
#   foxglove            run foxglove_bridge only
#   both                run the Project AirSim bridge and foxglove_bridge
#   build               build the workspace and exit
#   bash / anything     run that command with ROS2 sourced
# -----------------------------------------------------------------------------
set -eo pipefail

# ROS2's setup scripts reference unset variables (AMENT_TRACE_SETUP_FILES and
# friends), so -u has to be off while they run. Keep it off for the whole
# script rather than toggling it around every source site.
set +u

source /opt/ros/humble/setup.bash

REPO="${PROJECTAIRSIM_REPO:-/workspace/ProjectAirSim}"
ROS_WS="${REPO}/ros"
PKG=projectairsim_ros2_cpp

SIM_ADDRESS="${SIM_ADDRESS:-sim}"
SCENE_CONFIG="${SCENE_CONFIG:-}"
SIM_CONFIG_PATH="${SIM_CONFIG_PATH:-/workspace/workspace/sim_config}"
FOXGLOVE_PORT="${FOXGLOVE_PORT:-8765}"

build_ws() {
  echo "[info] Building ${PKG} (this takes a few minutes the first time) ..."
  cd "$ROS_WS"
  # The bridge pulls in the Project AirSim C++ client via add_subdirectory, so
  # colcon builds both. Release keeps image throughput usable.
  colcon build --packages-select "$PKG" \
      --cmake-args -DCMAKE_BUILD_TYPE=Release \
      --event-handlers console_direct+
  echo "[ok]   Build complete."
}

ensure_built() {
  # Test for the node binary itself. colcon leaves install/setup.bash behind
  # even when the build fails, so keying off that would skip the rebuild and
  # then fail at `ros2 run` with a much less obvious message.
  local node_bin="${ROS_WS}/install/${PKG}/lib/${PKG}/projectairsim_ros2_cpp_node"
  if [[ ! -x "$node_bin" ]]; then
    build_ws
  fi
  [[ -x "$node_bin" ]] || {
    echo "[ERROR] Build finished but ${node_bin} is missing." >&2
    exit 1
  }
  source "${ROS_WS}/install/setup.bash"

  # 4 x 1080p BGR at 30Hz is well past Fast DDS's default SHM segment.
  local profile
  profile="$(ros2 pkg prefix "$PKG" 2>/dev/null)/share/${PKG}/config/fastdds_shm_256m.xml"
  if [[ -f "$profile" && "${USE_SHM_PROFILE:-0}" == "1" ]]; then
    # SHM-only: fastest locally, but DDS cannot cross the container boundary.
    export FASTDDS_DEFAULT_PROFILES_FILE="$profile"
    export FASTRTPS_DEFAULT_PROFILES_FILE="$profile"
    echo "[info] Fast DDS shared-memory profile enabled (local subscribers only)."
  fi
}

run_projectairsim_bridge() {
  local args=(
    -p "address:=${SIM_ADDRESS}"
    -p "sim_config_path:=${SIM_CONFIG_PATH}"
  )
  # With no scene_config the bridge attaches to whatever scene is already
  # loaded instead of replacing it.
  [[ -n "$SCENE_CONFIG" ]] && args+=( -p "scene_config:=${SCENE_CONFIG}" )
  [[ -n "${VEHICLE_NAME:-}" ]] && args+=( -p "vehicle_name:=${VEHICLE_NAME}" )

  echo "[info] Project AirSim bridge -> ${SIM_ADDRESS}:8989/8990"
  echo "[info] scene_config='${SCENE_CONFIG:-<attach to loaded scene>}'"
  ros2 run "$PKG" projectairsim_ros2_cpp_node --ros-args "${args[@]}"
}

run_foxglove() {
  echo "[info] foxglove_bridge listening on ws://0.0.0.0:${FOXGLOVE_PORT}"
  ros2 run foxglove_bridge foxglove_bridge \
      --ros-args -p port:="${FOXGLOVE_PORT}" -p address:=0.0.0.0 \
                 -p max_qos_depth:=10 -p send_buffer_limit:=100000000
}

case "${1:-bridge}" in
  build)
    build_ws
    ;;
  bridge)
    ensure_built
    run_projectairsim_bridge
    ;;
  foxglove)
    ensure_built
    run_foxglove
    ;;
  both)
    ensure_built
    run_foxglove &
    fox_pid=$!
    # If either process dies the container should exit, not sit half-alive.
    trap 'kill $fox_pid 2>/dev/null || true' EXIT
    run_projectairsim_bridge
    ;;
  bash|sh)
    ensure_built 2>/dev/null || source /opt/ros/humble/setup.bash
    exec "$@"
    ;;
  *)
    source "${ROS_WS}/install/setup.bash" 2>/dev/null || true
    exec "$@"
    ;;
esac
