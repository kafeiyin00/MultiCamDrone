#!/usr/bin/env bash
# -----------------------------------------------------------------------------
# Fly the four-camera rig around a repeating circuit.
#
#   ./scripts/run_flight.sh                        # 40m square, 10m up, 5 m/s
#   ./scripts/run_flight.sh --shape figure8 --size 30
#   ./scripts/run_flight.sh --detach               # leave it flying in background
#   ./scripts/run_flight.sh --stop
#
# Flight commands go through the ROS2 bridge's services and action, not a second
# native client. The simulator's topic socket is NNG Pair0 (point-to-point, one
# peer only), so a native client would steal it from the bridge and Foxglove
# would show topics with no data. See workspace/fly_loop_ros2.py for detail.
# -----------------------------------------------------------------------------
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
COMPOSE=( docker compose -f "${REPO_ROOT}/docker/docker-compose.yml" )
NAME=airsim-flight
DETACH=0

ARGS=()
while [[ $# -gt 0 ]]; do
  case "$1" in
    --detach|-d) DETACH=1; shift ;;
    --stop)
      if docker exec airsim-ros2 pkill -INT -f fly_loop_ros2.py >/dev/null 2>&1; then
        echo "[ok] pilot signalled; it lands and disarms before exiting"
        # Give the landing sequence time before anyone starts another pilot.
        for _ in $(seq 1 30); do
          docker exec airsim-ros2 pgrep -f fly_loop_ros2.py >/dev/null 2>&1 || break
          sleep 1
        done
      else
        echo "[info] no pilot was running"
      fi
      exit 0 ;;
    --logs)
      exec tail -f "${REPO_ROOT}/output/pilot.log" ;;
    -h|--help)
      sed -n '2,14p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
      echo; echo "Flight options are passed through:"
      exec "${COMPOSE[@]}" exec -T ros2 bash -lc \
        "source /opt/ros/humble/setup.bash; source /workspace/ProjectAirSim/ros/install/setup.bash; python3 /workspace/workspace/fly_loop_ros2.py --help" ;;
    *) ARGS+=( "$1" ); shift ;;
  esac
done

if [[ -z "$(docker ps -q --filter name=airsim-sim)" ]]; then
  echo "[ERROR] the simulator is not running. Start it with ./scripts/run_sim.sh" >&2
  exit 1
fi
if [[ -z "$(docker ps -q --filter name=airsim-ros2)" ]]; then
  echo "[ERROR] the ROS2 bridge is not running; it serves the flight services." >&2
  echo "        docker compose -f docker/docker-compose.yml up -d ros2" >&2
  exit 1
fi

LOG=/output/pilot.log
PILOT="source /opt/ros/humble/setup.bash
source /workspace/ProjectAirSim/ros/install/setup.bash
exec python3 -u /workspace/workspace/fly_loop_ros2.py ${ARGS[*]}"

# One pilot at a time: two would fight over the same drone.
docker exec airsim-ros2 pkill -INT -f fly_loop_ros2.py >/dev/null 2>&1 || true

if [[ $DETACH -eq 1 ]]; then
  docker exec -d airsim-ros2 bash -lc "$PILOT > $LOG 2>&1"
  echo "[ok]   flying in the background inside airsim-ros2"
  echo "[hint] logs: ./scripts/run_flight.sh --logs   (or tail output/pilot.log)"
  echo "[hint] stop: ./scripts/run_flight.sh --stop"
else
  exec docker exec -it airsim-ros2 bash -lc "$PILOT"
fi
