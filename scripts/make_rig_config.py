#!/usr/bin/env python3
"""
Generate the MultiCamDrone four-camera rig configs.

Writes a robot config and a matching scene config into workspace/sim_config/.
Keeping this as a generator rather than hand-maintained JSONC means the camera
poses stay consistent and the perspective/fisheye variants cannot drift apart.

    ./scripts/make_rig_config.py                     # current native build
    ./scripts/make_rig_config.py --projection fisheye --fov 220

`--projection fisheye` emits the config the modified Unreal plugin will consume
(see HANDOFF.md part 2). Until that plugin ships, the simulator ignores the
extra fields and clamps FOV, so the default stays perspective.
"""

import argparse
import json
import os
import re
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
UPSTREAM = os.path.join(
    REPO, "ProjectAirSim", "client", "python", "example_user_scripts", "sim_config"
)
OUT_DIR = os.path.join(REPO, "workspace", "sim_config")

# Four eyes on a square baseline, each looking outward along its own axis.
# xyz is body-frame NED in metres: +x forward, +y right, +z down.
EYES = {
    "front": {"xyz": (0.10, 0.00, 0.00), "yaw": 0},
    "right": {"xyz": (0.00, 0.10, 0.00), "yaw": 90},
    "back": {"xyz": (-0.10, 0.00, 0.00), "yaw": 180},
    "left": {"xyz": (0.00, -0.10, 0.00), "yaw": -90},
}


def strip_jsonc(text: str) -> str:
    """Drop // comments so json can parse an upstream .jsonc file."""
    return re.sub(r"(?<!:)//.*", "", text)


def load_upstream(name: str) -> dict:
    with open(os.path.join(UPSTREAM, name)) as fh:
        return json.loads(strip_jsonc(fh.read()))


def camera(name: str, eye: dict, args) -> dict:
    settings = {
        "image-type": 0,  # scene / RGB
        "width": args.width,
        "height": args.height,
        "fov-degrees": args.fov,
        "capture-enabled": True,
        "streaming-enabled": False,
        "pixels-as-float": False,
        "compress": False,
        "target-gamma": 2.5,
    }
    if args.projection == "fisheye":
        # Consumed by the modified plugin; harmless to a stock build, which
        # ignores unknown keys and clamps fov-degrees to the perspective limit.
        settings["projection"] = "fisheye"
        settings["fisheye-model"] = args.fisheye_model

    x, y, z = eye["xyz"]
    return {
        "id": name,
        "type": "camera",
        "enabled": True,
        "parent-link": "Frame",
        "capture-interval": round(1.0 / args.rate, 6),
        "capture-settings": [settings],
        "origin": {"xyz": f"{x} {y} {z}", "rpy-deg": f"0 0 {eye['yaw']}"},
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--projection", choices=["perspective", "fisheye"],
                    default="perspective")
    ap.add_argument("--fisheye-model",
                    choices=["equidistant", "equisolid", "stereographic"],
                    default="equidistant")
    ap.add_argument("--fov", type=float, default=150.0,
                    help="degrees; a perspective camera cannot exceed 180 "
                         "(see HANDOFF.md 8.1). Default 150.")
    ap.add_argument("--width", type=int, default=1920)
    ap.add_argument("--height", type=int, default=1080)
    ap.add_argument("--rate", type=float, default=30.0, help="camera Hz")
    ap.add_argument("--imu-rate", type=float, default=200.0,
                    help="IMU Hz; this sets the scene clock, which also caps "
                         "the physics rate")
    ap.add_argument("--name", default="multicam_drone")
    args = ap.parse_args()

    if args.projection == "perspective" and args.fov >= 180:
        print(f"error: fov {args.fov} is impossible for a perspective camera -- "
              f"the focal length goes negative past 180 degrees. Use "
              f"--projection fisheye, or a fov below 180.", file=sys.stderr)
        return 1

    os.makedirs(OUT_DIR, exist_ok=True)

    robot = load_upstream("robot_quadrotor_fastphysics.jsonc")
    # Keep the IMU exactly as upstream tuned it; replace every other sensor.
    imu = [s for s in robot["sensors"] if s.get("type") == "imu"]
    if not imu:
        print("error: no IMU found in the upstream robot config", file=sys.stderr)
        return 1
    robot["sensors"] = imu + [camera(n, e, args) for n, e in EYES.items()]

    scene = load_upstream("scene_basic_drone.jsonc")
    scene["id"] = f"Scene{args.name.title().replace('_', '')}"
    scene["actors"][0]["robot-config"] = f"robot_{args.name}.jsonc"
    # The IMU publishes on the scene tick, so the clock step *is* the IMU rate.
    step_ns = int(round(1e9 / args.imu_rate))
    scene["clock"]["step-ns"] = step_ns
    scene["clock"]["real-time-update-rate"] = step_ns

    robot_path = os.path.join(OUT_DIR, f"robot_{args.name}.jsonc")
    scene_path = os.path.join(OUT_DIR, f"scene_{args.name}.jsonc")
    with open(robot_path, "w") as fh:
        json.dump(robot, fh, indent=2)
    with open(scene_path, "w") as fh:
        json.dump(scene, fh, indent=2)

    print(f"  cameras   : {len(EYES)} x {args.width}x{args.height} "
          f"@{args.rate:g}Hz, {args.projection}, fov {args.fov:g} deg")
    if args.projection == "fisheye":
        print(f"  model     : {args.fisheye_model}")
    print(f"  IMU       : {args.imu_rate:g}Hz (scene clock step {step_ns} ns)")
    print(f"  robot     : {robot_path}")
    print(f"  scene     : {scene_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
