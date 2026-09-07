"""
Headless end-to-end smoke test for the MultiCamDrone / Project AirSim stack.

Loads the basic drone scene, arms and takes off, then captures one frame from
each camera and writes it to PNG. Unlike the upstream `hello_drone.py` this
opens no OpenCV windows, so it runs over SSH and in CI.

    ./scripts/run_client.sh python /workspace/scripts/smoke_test.py

A saved PNG with real image content is the proof that the whole chain works:
client transport, scene loading, physics, and GPU rendering. If Vulkan had
fallen back to software rendering this still passes -- but takes far longer
and `nvidia-smi` shows no memory held by Blocks-Linux-Shipping.
"""

import asyncio
import os
import sys

import cv2
import numpy as np

from projectairsim import Drone, ProjectAirSimClient, World
from projectairsim.utils import projectairsim_log

# Inside the compose network the simulator answers to "sim"; from the host it is
# reachable on localhost. SIM_ADDRESS lets the same script serve both.
ADDRESS = os.environ.get("SIM_ADDRESS", "127.0.0.1")
OUT_DIR = os.environ.get("SMOKE_OUT_DIR", "/output/smoke_test")

# Cameras declared in robot_quadrotor_fastphysics.jsonc, as (sensor, stream).
CAMERAS = [
    ("Chase", "scene_camera"),
    ("DownCamera", "scene_camera"),
    ("DownCamera", "depth_camera"),
]


# Image topic messages carry an `encoding` field; trust it rather than guessing
# from the payload size. Scene cameras already deliver BGR, so converting from
# RGB would silently swap red and blue. Depth arrives as 16-bit millimetres
# (16UC1) or float metres (32FC1) depending on the config's `pixels-as-float`.
_DTYPES = {
    "BGR": (np.uint8, 3),
    "RGB": (np.uint8, 3),
    "BGRA": (np.uint8, 4),
    "RGBA": (np.uint8, 4),
    "16UC1": (np.uint16, 1),
    "32FC1": (np.float32, 1),
    "8UC1": (np.uint8, 1),
}


def decode(image_msg):
    """Turn a Project AirSim image topic message into a writable numpy array."""
    h, w = image_msg["height"], image_msg["width"]
    encoding = image_msg.get("encoding", "BGR")

    dtype, channels = _DTYPES.get(encoding, (np.uint8, 0))
    buf = np.frombuffer(image_msg["data"], dtype=dtype)
    if channels == 0:  # unknown encoding: infer the channel count
        channels = max(1, buf.size // (h * w))

    img = buf.reshape(h, w, channels) if channels > 1 else buf.reshape(h, w)
    if encoding in ("RGB", "RGBA"):
        img = cv2.cvtColor(img, cv2.COLOR_RGB2BGR)
    return img


def save(img, path):
    """Write `img` as PNG, plus an 8-bit preview when it is a depth frame."""
    cv2.imwrite(path, img)
    if img.dtype != np.uint8:
        # 16-bit/float depth is unreadable in most viewers; normalise a copy.
        finite = img[np.isfinite(img)] if img.dtype == np.float32 else img
        if finite.size and finite.max() > finite.min():
            preview = cv2.normalize(
                np.nan_to_num(img), None, 0, 255, cv2.NORM_MINMAX
            ).astype(np.uint8)
            cv2.imwrite(path.replace(".png", "_preview.png"), preview)


async def main() -> int:
    os.makedirs(OUT_DIR, exist_ok=True)
    client = ProjectAirSimClient(address=ADDRESS)
    frames: dict[str, dict] = {}
    failures: list[str] = []

    try:
        projectairsim_log().info(f"Connecting to {ADDRESS} ...")
        client.connect()

        world = World(client, "scene_basic_drone.jsonc", delay_after_load_sec=2)
        drone = Drone(client, world, "Drone1")
        projectairsim_log().info("Scene loaded, Drone1 spawned.")

        # Keep only the most recent frame per camera; that is all we need.
        for sensor, stream in CAMERAS:
            key = f"{sensor}_{stream}"
            client.subscribe(
                drone.sensors[sensor][stream],
                lambda _, msg, key=key: frames.__setitem__(key, msg),
            )

        drone.enable_api_control()
        drone.arm()

        projectairsim_log().info("Taking off ...")
        await (await drone.takeoff_async())
        projectairsim_log().info("Airborne.")

        # Climb a little so the downward camera sees more than the launch pad.
        await (await drone.move_by_velocity_async(
            v_north=0.0, v_east=0.0, v_down=-1.0, duration=3.0
        ))

        # Cameras publish on their own interval (20-30 ms); give them a moment.
        await asyncio.sleep(2.0)

        for sensor, stream in CAMERAS:
            key = f"{sensor}_{stream}"
            msg = frames.get(key)
            if msg is None:
                failures.append(f"{key}: no frame received")
                continue
            img = decode(msg)
            path = os.path.join(OUT_DIR, f"{key}.png")
            save(img, path)
            # A uniform frame means the camera saw nothing, or nothing rendered
            # -- worth flagging, but not a transport failure.
            note = "  <-- WARNING: uniform image, check camera pose" \
                if img.max() == img.min() else ""
            print(f"  {key:32s} {img.shape[1]}x{img.shape[0]} {msg['encoding']:>6s}  "
                  f"range {img.min()}-{img.max()}  -> {path}{note}")

        projectairsim_log().info("Landing ...")
        await (await drone.land_async())
        drone.disarm()
        drone.disable_api_control()

    except Exception as exc:  # noqa: BLE001 - smoke test reports, never raises
        failures.append(f"{type(exc).__name__}: {exc}")
    finally:
        try:
            client.disconnect()
        except Exception:
            pass

    print()
    if failures:
        print("SMOKE TEST FAILED")
        for f in failures:
            print(f"  - {f}")
        return 1
    print(f"SMOKE TEST PASSED - images in {OUT_DIR}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
