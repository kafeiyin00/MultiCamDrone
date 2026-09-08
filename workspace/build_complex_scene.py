"""
Populate the running scene with a dense block city, through the ROS2 bridge.

Why through the bridge: the simulator's topic channel is an NNG Pair0 socket,
which is strictly point-to-point, so only one native client can attach. The
bridge holds it. Its /projectairsim/request service forwards arbitrary native
requests, which is how this reaches SpawnObject without a second native client.

Why at runtime: only unreal/Blocks/Blocks.uproject exists in the repo, so the
packaged environments (CityEnviron and friends) cannot be rebuilt with a
modified plugin. Making the self-built Blocks dense is the way to get a complex
scene and a 220-degree fisheye at the same time.

    docker compose -f docker/docker-compose.yml exec ros2 bash -lc \
      'source /opt/ros/humble/setup.bash; source /workspace/ProjectAirSim/ros/install/setup.bash; \
       python3 /workspace/workspace/build_complex_scene.py --list'

    ... same, with:  --blocks 400 --extent 120
"""

import argparse
import json
import math
import random
import sys

import rclpy
from rclpy.node import Node

from projectairsim_ros2_cpp.srv import RawRequest

SCENE = "SceneMulticamDrone"


class Spawner(Node):
    def __init__(self, scene, service_root="/projectairsim"):
        super().__init__("complex_scene_builder")
        self.root = f"/Sim/{scene}"
        self.cli = self.create_client(RawRequest, f"{service_root}/request")

    def wait(self, timeout=60.0):
        if not self.cli.wait_for_service(timeout_sec=timeout):
            self.get_logger().error("the bridge's /projectairsim/request service never appeared")
            return False
        return True

    def call(self, method, params, timeout=30.0):
        req = RawRequest.Request()
        req.method = f"{self.root}/{method}"
        req.json_parameters = json.dumps(params)
        fut = self.cli.call_async(req)
        rclpy.spin_until_future_complete(self, fut, timeout_sec=timeout)
        if not fut.done() or fut.result() is None:
            return None, f"{method}: no response within {timeout:g}s"
        res = fut.result()
        if not res.success:
            # `status` is only the transport status, which is "OK" whenever the
            # request reached the simulator; the useful detail is the
            # application error the sim put in result_json.
            detail = res.result_json or ""
            try:
                detail = json.loads(detail).get("message", detail)
            except Exception:
                pass
            return None, f"{method}: {detail} (code {res.error_code})"
        return res.result_json, None


# A mix rather than one repeated mesh. Uniform cubes give a scene whose every
# feature looks alike, which flatters feature matching and hides seams; varied
# silhouettes and surfaces are what actually exercise a fisheye rig.
# Names come from the running build's own ListAssets, so they are known to
# resolve. `centred` says whether the mesh pivot is at its middle (so it has to
# be raised by half its height to sit on the ground) or at its base.
PALETTE = [
    # asset,            weight, centred, scale range,        height range
    ("1M_Cube",            34,  True,  (3.0, 9.0),  (4.0, 26.0)),
    ("1M_Cube_Chamfer",    16,  True,  (3.0, 8.0),  (4.0, 20.0)),
    ("Cylinder",           12,  True,  (2.0, 5.0),  (5.0, 22.0)),
    ("Cone",                8,  True,  (2.0, 6.0),  (4.0, 14.0)),
    ("Sphere",              6,  True,  (2.0, 5.0),  (2.0, 5.0)),
    ("BasicLandingPad",     6, False,  (1.0, 2.5),  (1.0, 1.0)),
    ("Suv",                 6, False,  (1.0, 1.0),  (1.0, 1.0)),
    ("SKM_SportsCar",       4, False,  (1.0, 1.0),  (1.0, 1.0)),
    ("SM_Offroad_Body",     4, False,  (1.0, 1.0),  (1.0, 1.0)),
    ("c172_body",           2, False,  (1.0, 1.0),  (1.0, 1.0)),
    ("MapleLeaf01",         2, False,  (4.0, 12.0), (1.0, 1.0)),
]


def city_layout(n, extent, seed=7, keep_clear=45.0, max_height=20.0):
    """Objects on a jittered grid, so the view has real parallax.

    A regular grid would give a repeating scene; the jitter, the height spread
    and the mesh mix are what make it a useful test. The middle is left clear so
    the drone's circuit is not flying through a wall.
    """
    rng = random.Random(seed)
    choices = [p for p in PALETTE for _ in range(p[1])]
    side = int(math.ceil(math.sqrt(n)))
    step = (2 * extent) / side
    out = []
    for i in range(side):
        for j in range(side):
            if len(out) >= n:
                return out
            north = -extent + (i + 0.5) * step + rng.uniform(-step * 0.3, step * 0.3)
            east = -extent + (j + 0.5) * step + rng.uniform(-step * 0.3, step * 0.3)
            if math.hypot(north, east) < keep_clear:
                continue
            asset, _, centred, srange, hrange = rng.choice(choices)
            w = rng.uniform(*srange)
            d = rng.uniform(*srange) if srange[0] != srange[1] else w
            h = min(rng.uniform(*hrange), max_height)
            out.append({
                "asset": asset,
                "north": north, "east": east,
                # NED: +z is down, so a centred pivot must be raised by h/2.
                "down": -h / 2.0 if centred else 0.0,
                "scale": [w, d, h] if centred else [w, w, w],
                "yaw": rng.uniform(0, 2 * math.pi),
            })
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--list", action="store_true",
                    help="list the assets the running build can spawn, then exit")
    ap.add_argument("--asset", default=None,
                    help="asset to spawn (default: first cube-like name found)")
    ap.add_argument("--blocks", type=int, default=300)
    ap.add_argument("--keep-clear", type=float, default=45.0,
                    help="radius around the origin left empty, metres. Must clear\n                          the drone's takeoff column and the tallest object it\n                          will fly past (default 45)")
    ap.add_argument("--max-height", type=float, default=20.0,
                    help="tallest object, metres; fly above this (default 20)")
    ap.add_argument("--extent", type=float, default=110.0,
                    help="half-width of the built area, metres")
    ap.add_argument("--scene", default=SCENE)
    ap.add_argument("--clear", action="store_true",
                    help="destroy previously spawned blocks instead of adding")
    args = ap.parse_args()

    rclpy.init()
    node = Spawner(args.scene)
    try:
        if not node.wait():
            return 1

        if args.list:
            out, err = node.call("ListAssets", {"name": ".*"})
            if err:
                node.get_logger().error(err)
                return 1
            print(out)
            return 0

        if args.clear:
            removed = 0
            for i in range(args.blocks):
                _, err = node.call("DestroyObject", {"object_name": f"cityblock_{i}"},
                                   timeout=10.0)
                if not err:
                    removed += 1
            print(f"destroyed {removed} blocks")
            return 0

        # Only offer meshes the running build actually has, so a missing asset
        # is reported once here instead of as N spawn failures.
        out, err = node.call("ListAssets", {"name": ".*"})
        if err:
            node.get_logger().error(err)
            return 1
        available = set(json.loads(out) if out else [])
        missing = sorted({p[0] for p in PALETTE} - available)
        if missing:
            print(f"note: not in this build, skipping: {', '.join(missing)}")
        usable = [p for p in PALETTE if p[0] in available]
        if not usable:
            node.get_logger().error("none of the palette meshes exist in this build")
            return 1
        PALETTE[:] = usable

        layout = city_layout(args.blocks, args.extent,
                             keep_clear=args.keep_clear,
                             max_height=args.max_height)
        print(f"spawning {len(layout)} blocks over +/-{args.extent:g} m ...")
        ok = failed = 0
        for i, b in enumerate(layout):
            params = {
                "object_name": f"cityblock_{i}",
                "asset_path": b["asset"],
                # frame_id is required: without it the sim throws while
                # deserialising the pose and reports a bare "std::exception".
                "pose": {
                    "translation": {"x": b["north"], "y": b["east"], "z": b["down"]},
                    "rotation": {"w": math.cos(b["yaw"] / 2), "x": 0.0, "y": 0.0,
                                 "z": math.sin(b["yaw"] / 2)},
                    "frame_id": "DEFAULT_ID",
                },
                "scale": b["scale"],
                "enable_physics": False,
            }
            _, err = node.call("SpawnObject", params, timeout=20.0)
            if err:
                failed += 1
                if failed <= 3:
                    node.get_logger().warn(err)
            else:
                ok += 1
            if (i + 1) % 50 == 0:
                print(f"  {i+1}/{len(layout)}  ok={ok} failed={failed}")
        print(f"\nspawned {ok}, failed {failed}")
        return 0 if ok else 1
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    sys.exit(main())
