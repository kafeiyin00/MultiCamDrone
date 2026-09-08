"""Save one frame from each rig camera to /output, for eyeballing the mounting.

    ./scripts/run_flight.sh --stop           # optional: hold still first
    docker compose -f docker/docker-compose.yml exec ros2 bash -lc \
      'source /opt/ros/humble/setup.bash; source /workspace/ProjectAirSim/ros/install/setup.bash; \
       python3 /workspace/workspace/grab_frames.py'
"""
import os
import sys

import numpy as np
import rclpy
from rclpy.node import Node
from rclpy.qos import QoSProfile, ReliabilityPolicy
from sensor_msgs.msg import Image

OUT = os.environ.get("GRAB_OUT_DIR", "/output/frames")
# The bridge strips /Sim/<SceneId>, so ROS topic names carry no scene id and
# this works whatever scene is loaded. SCENE_NAME only matters if the bridge is
# run with SCENE_IN_TOPIC_PATH=1.
SCENE = os.environ.get("SCENE_NAME", "")
EYES = ["front", "right", "back", "left"]


def to_png(msg, path):
    """Write a sensor_msgs/Image as PNG without pulling in cv2."""
    import png  # pypng, if available
    arr = np.frombuffer(msg.data, dtype=np.uint8).reshape(msg.height, msg.width, -1)
    if msg.encoding == "bgr8":
        arr = arr[:, :, ::-1]
    writer = png.Writer(msg.width, msg.height, greyscale=False)
    with open(path, "wb") as fh:
        writer.write(fh, arr.reshape(msg.height, -1))


def main():
    os.makedirs(OUT, exist_ok=True)
    rclpy.init()
    node = Node("grab_frames")
    vehicle = os.environ.get("VEHICLE", "Drone1")
    root = (f"/ProjectAirsim/{SCENE}/robots/{vehicle}" if SCENE
            else f"/ProjectAirsim/robots/{vehicle}")
    got = {}

    # Image publishers use KEEP_LAST with a shallow depth; best effort is enough
    # for a single grab and avoids blocking on a reliable handshake.
    qos = QoSProfile(depth=1, reliability=ReliabilityPolicy.BEST_EFFORT)
    for eye in EYES:
        node.create_subscription(
            Image, f"{root}/sensors/{eye}/scene_camera",
            lambda m, eye=eye: got.setdefault(eye, m), qos)

    deadline = 30.0
    start = node.get_clock().now()
    while len(got) < len(EYES):
        rclpy.spin_once(node, timeout_sec=0.5)
        if (node.get_clock().now() - start).nanoseconds / 1e9 > deadline:
            break

    for eye in EYES:
        msg = got.get(eye)
        if msg is None:
            print(f"  {eye:6s} no frame within {deadline:g}s")
            continue
        path = os.path.join(OUT, f"{eye}.png")
        arr = np.frombuffer(msg.data, dtype=np.uint8).reshape(msg.height, msg.width, -1)
        try:
            to_png(msg, path)
            print(f"  {eye:6s} {msg.width}x{msg.height} {msg.encoding} -> {path}")
        except ImportError:
            # No PNG encoder available; a raw dump still lets the frame be inspected.
            path = path.replace(".png", f"_{msg.width}x{msg.height}_{msg.encoding}.raw")
            open(path, "wb").write(msg.data)
            print(f"  {eye:6s} {msg.width}x{msg.height} {msg.encoding} -> {path} (raw)")
        print(f"         pixel range {arr.min()}-{arr.max()}, mean {arr.mean():.1f}")

    node.destroy_node()
    rclpy.shutdown()
    return 0 if len(got) == len(EYES) else 1


if __name__ == "__main__":
    sys.exit(main())
