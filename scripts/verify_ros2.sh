#!/usr/bin/env bash
# -----------------------------------------------------------------------------
# Check the ROS2 bridge and Foxglove leg of the stack.
#
#   ./scripts/verify_ros2.sh              # topics only, quick
#   ./scripts/verify_ros2.sh --rates      # also measure rates (needs an idle box)
#
# Rate numbers are meaningless while something else is saturating the CPU --
# an Unreal build, for instance. The script says so rather than reporting
# figures that will be misread later.
# -----------------------------------------------------------------------------
set -uo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
COMPOSE=( docker compose -f "${REPO_ROOT}/docker/docker-compose.yml" )
SCENE="${SCENE_NAME:-SceneMulticamDrone}"
FAILED=0
MEASURE_RATES=0
[[ "${1:-}" == "--rates" ]] && MEASURE_RATES=1

pass() { echo -e "  \033[32m✓\033[0m $*"; }
fail() { echo -e "  \033[31m✗\033[0m $*"; FAILED=1; }
note() { echo -e "    \033[90m$*\033[0m"; }
hdr()  { echo; echo -e "\033[1m$*\033[0m"; }

echo "=============================================================="
echo " ROS2 bridge + Foxglove verification"
echo "=============================================================="

hdr "1. Containers"
for c in airsim-sim airsim-ros2; do
  if [[ -n "$(docker ps -q --filter name=$c)" ]]; then
    pass "$c running"
  else
    fail "$c is not running"
    note "docker compose -f docker/docker-compose.yml up -d sim ros2"
  fi
done

hdr "2. Foxglove WebSocket"
if (command -v ss >/dev/null && ss -ltn 2>/dev/null | grep -q ':8765 ') \
   || (command -v nc >/dev/null && nc -z 127.0.0.1 8765 2>/dev/null); then
  pass "port 8765 accepting connections"
  note "connect Foxglove to ws://$(hostname -I 2>/dev/null | awk '{print $1}'):8765"
else
  fail "port 8765 is not listening"
fi

# The bridge now strips /Sim/<SceneId>, so ROS names carry no scene id and a
# layout survives a scene change. SCENE_IN_TOPIC_PATH=1 restores the old shape.
if [[ "${SCENE_IN_TOPIC_PATH:-0}" == "1" ]]; then
  ROOT="/ProjectAirsim/${SCENE}/robots/${VEHICLE:-Drone1}"
else
  ROOT="/ProjectAirsim/robots/${VEHICLE:-Drone1}"
fi

hdr "3. Bridged topics under ${ROOT}"
topics="$("${COMPOSE[@]}" exec -T ros2 bash -lc '
  source /opt/ros/humble/setup.bash
  source /workspace/ProjectAirSim/ros/install/setup.bash
  ros2 topic list' 2>/dev/null)"

if [[ -z "$topics" ]]; then
  fail "could not list ROS2 topics"
else
  for eye in front right back left; do
    if grep -q "${ROOT}/sensors/${eye}/scene_camera$" <<< "$topics"; then
      pass "camera ${eye}"
    else
      fail "camera ${eye} not published"
    fi
  done
  if grep -q "${ROOT}/sensors/IMU1/imu$" <<< "$topics"; then
    pass "IMU"
  else
    fail "IMU not published"
  fi
  n_info=$(grep -c "${ROOT}/sensors/.*/camera_info$" <<< "$topics")
  [[ "$n_info" -ge 4 ]] && pass "camera_info on $n_info topics" \
                        || fail "only $n_info camera_info topics"

fi

hdr "4. Rates"
load=$(awk '{print $1}' /proc/loadavg)
cores=$(nproc)
busy=$(awk -v l="$load" -v c="$cores" 'BEGIN{print (l > c*0.5) ? 1 : 0}')

if [[ "$MEASURE_RATES" -ne 1 ]]; then
  note "skipped; pass --rates to measure"
elif [[ "$busy" -eq 1 ]]; then
  fail "load is ${load} on ${cores} cores — rate numbers would be meaningless"
  note "wait for whatever is loading the machine (an Unreal build?) to finish"
else
  # Camera target is 20 Hz by design, not 30: see docs/FISHEYE.md 8.4 for why
  # 30 needs a renderer-count change rather than a config tweak. Override with
  # CAM_TARGET_HZ / IMU_TARGET_HZ.
  for t in "sensors/front/scene_camera:${CAM_TARGET_HZ:-20}" \
           "sensors/IMU1/imu:${IMU_TARGET_HZ:-200}"; do
    topic="${t%%:*}"; target="${t##*:}"
    hz=$("${COMPOSE[@]}" exec -T ros2 bash -lc "
      source /opt/ros/humble/setup.bash
      source /workspace/ProjectAirSim/ros/install/setup.bash
      timeout 15 ros2 topic hz ${ROOT}/${topic} 2>/dev/null | grep 'average rate' | tail -1" \
      2>/dev/null | awk '{print $3}')
    if [[ -z "$hz" ]]; then
      fail "${topic}: no messages"
    else
      ok=$(awk -v h="$hz" -v t="$target" 'BEGIN{print (h > t*0.8) ? 1 : 0}')
      [[ "$ok" -eq 1 ]] && pass "${topic}: ${hz} Hz (target ${target})" \
                        || fail "${topic}: ${hz} Hz, below target ${target}"
    fi
  done
fi

echo
echo "=============================================================="
[[ $FAILED -eq 0 ]] && echo -e " \033[32mAll checks passed.\033[0m" \
                    || echo -e " \033[31mSome checks failed.\033[0m"
echo "=============================================================="
exit $FAILED
