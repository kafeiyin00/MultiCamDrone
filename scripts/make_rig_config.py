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

# Four eyes, each looking outward along a body axis.
#
# Mounting geometry matters more than it looks. The quadrotor's rotors sit on the
# diagonals at (+/-0.253, +/-0.253) in robot_quadrotor_fastphysics.jsonc, so the
# four body axes (+x forward, +y right, -x back, -y left) pass cleanly BETWEEN
# the arms and the propeller discs -- which is why the cameras point along the
# axes rather than the diagonals.
#
# The radius still has to clear the frame mesh, though. At 0.10 m the cameras sit
# inside the body and the drone occludes its own view. MOUNT_RADIUS pushes them
# out past the frame while staying inboard of the rotor tips.
#
# A wide lens will always catch some of the airframe at the rim: a 220-degree
# camera sees 110 degrees off-axis, i.e. 20 degrees *behind* its own mounting
# point. Real multi-fisheye drones live with that. MOUNT_Z drops the cameras
# slightly below the frame plane so the body and props fall at the very edge of
# the circle instead of across the middle of it.
MOUNT_RADIUS = 0.30   # metres from the body origin, along each axis
MOUNT_Z = 0.06        # metres below the frame plane (NED: +z is down)

def eye_positions(radius, mount_z):
    return {
        "front": {"xyz": (radius, 0.0, mount_z), "yaw": 0},
        "right": {"xyz": (0.0, radius, mount_z), "yaw": 90},
        "back": {"xyz": (-radius, 0.0, mount_z), "yaw": 180},
        "left": {"xyz": (0.0, -radius, mount_z), "yaw": -90},
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
        # A real lens does see the airframe, and at 220 degrees it always does:
        # the camera looks 20 degrees behind its own mounting plane. Masking it
        # is usually what a dataset wants, so this defaults on -- but it is
        # written into the config rather than assumed, and --no-hide-self keeps
        # the physically faithful view.
        "hide-self": args.hide_self,
    }
    if args.projection == "fisheye":
        # Requires the patched plugin. A stock Project AirSim ignores these
        # keys (the JSONC schema has no additionalProperties:false and the C++
        # server never validates), so it would silently render a broken
        # perspective projection at this FOV instead -- which is exactly what
        # the patched core_sim now refuses to do.
        settings["projection"] = "fisheye"
        settings["fisheye-model"] = args.fisheye_model
        # Edge length of each of the five 90-degree faces rendered before the
        # remap. Faces are never read back, so this costs GPU time but does not
        # affect the frame size that matters for the readback leak.
        settings["fisheye-face-resolution"] = args.fisheye_face_resolution

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
                    default="equidistant",
                    help="equidistant is Kannala-Brandt with k1..k4 = 0, so "
                         "what is rendered and what camera_info publishes "
                         "coincide exactly (default)")
    ap.add_argument("--fisheye-face-resolution", type=int, default=768,
                    help="edge length of each of the five perspective faces "
                         "composited into the fisheye (default 768). This is "
                         "the quality/cost dial")
    ap.add_argument("--fov", type=float, default=150.0,
                    help="degrees. A perspective camera cannot exceed 180; use "
                         "--projection fisheye above that (see docs/FISHEYE.md). "
                         "Default 150.")
    ap.add_argument("--width", type=int, default=1920)
    ap.add_argument("--height", type=int, default=1080)
    ap.add_argument("--rate", type=float, default=30.0, help="camera Hz")
    ap.add_argument("--imu-rate", type=float, default=200.0,
                    help="IMU Hz; this sets the scene clock, which also caps "
                         "the physics rate")
    ap.add_argument("--name", default="multicam_drone")
    ap.add_argument("--hide-self", action="store_true", default=True,
                    help="keep the drone's own airframe out of its cameras "
                         "(default). At 220 degrees the rotors and arms fill "
                         "the image rim otherwise")
    ap.add_argument("--no-hide-self", dest="hide_self", action="store_false",
                    help="render the airframe, as a real lens would see it")
    ap.add_argument("--mount-radius", type=float, default=MOUNT_RADIUS,
                    help="metres from the body origin along each axis; must clear "
                         "the frame mesh or the drone occludes its own cameras "
                         f"(default {MOUNT_RADIUS})")
    ap.add_argument("--mount-z", type=float, default=MOUNT_Z,
                    help="metres below the frame plane, NED (+z down); keeps the "
                         f"airframe at the rim of a wide FOV (default {MOUNT_Z})")
    args = ap.parse_args()

    eyes = eye_positions(args.mount_radius, args.mount_z)

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
    robot["sensors"] = imu + [camera(n, e, args) for n, e in eyes.items()]

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

    print(f"  cameras   : {len(eyes)} x {args.width}x{args.height} "
          f"@{args.rate:g}Hz, {args.projection}, fov {args.fov:g} deg")
    print(f"  mounting  : radius {args.mount_radius:g}m, {args.mount_z:g}m below "
          f"the frame plane (rotors are on the diagonals at +/-0.253m)")
    frame_bytes = args.width * args.height * 3
    if frame_bytes > 4 * 1024 * 1024:
        print(f"  NOTE      : {frame_bytes} bytes/frame exceeds UE's 4 MiB pooling")
        print(f"              threshold, so each frame leaks a VMA. Needs")
        print(f"              vm.max_map_count raised well above 65530.")
    if args.projection == "fisheye":
        print(f"  model     : {args.fisheye_model}, "
              f"{args.fisheye_face_resolution}px faces x5 per eye")
    print(f"  airframe  : {'hidden from its own cameras' if args.hide_self else 'visible (as a real lens sees it)'}")
    print(f"  IMU       : {args.imu_rate:g}Hz (scene clock step {step_ns} ns)")
    print(f"  robot     : {robot_path}")
    print(f"  scene     : {scene_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
