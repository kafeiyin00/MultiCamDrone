# MultiCamDrone

Project AirSim simulation environment, containerised for GPU-accelerated
headless operation.

[Project AirSim](https://github.com/iamaisim/ProjectAirSim) is the successor to
Microsoft AirSim, now maintained by IAMAI Simulations. It pairs a packaged
Unreal Engine 5 environment (the simulation server) with a Python/C++ client
that drives vehicles and reads sensors over two TCP sockets.

---

## Layout

| Path | Tracked | What it is |
| --- | --- | --- |
| `ProjectAirSim/` | submodule | Upstream Project AirSim source (client libs, sim server, docs) |
| `docker/` | yes | Dockerfiles and compose stack for the sim server and Python client |
| `scripts/` | yes | Setup, run and verification helpers |
| `workspace/` | yes | Your own client scripts and scene configs |
| `envs/` | **no** | Packaged Unreal environments (GB-scale; re-fetch with `scripts/fetch_env.sh`) |
| `downloads/` | **no** | Release archives |
| `output/` | **no** | Images, logs and data produced by runs |

---

## Quick start

```bash
git clone --recurse-submodules git@github.com:kafeiyin00/MultiCamDrone.git
cd MultiCamDrone
./scripts/setup.sh          # prerequisites, submodule, Blocks env, docker images
./scripts/run_sim.sh        # start the simulator (headless, GPU)
./scripts/run_client.sh hello_drone.py
```

`./scripts/verify.sh` checks every link in the chain — host GPU, container
Vulkan, running server, client round trip — and tells you which one is broken.

---

## Architecture

```
                            ┌────────────────────────────┐
                            │  airsim-client             │
                       8989 │  projectairsim (Python)    │
  ┌──────────────────────┐◄─┤  your scripts              │
  │  airsim-sim          │  └────────────────────────────┘
  │                      │  ┌────────────────────────────┐
  │  Unreal Engine 5.2   │  │  airsim-ros2               │
  │  -RenderOffScreen    │◄─┤  projectairsim_ros2_cpp    │
  │                      │  │            ↓               │  8765
  │  Vulkan → NVIDIA GPU │  │  foxglove_bridge           │──────► Foxglove
  └──────────────────────┘  └────────────────────────────┘   ws://
       8990 services
```

* **8989 — topics.** Pub/sub: sensor images, poses, telemetry.
* **8990 — services.** Request/response: takeoff, move, scene setup.

Both ports are published on the host (`127.0.0.1` by default), so a client
running outside docker works too — just leave the address at its `127.0.0.1`
default instead of `sim`.

The scene is not baked into the binary. The client sends a JSONC scene
description on connect, which is what makes a multi-camera rig a client-side
config change rather than a rebuild.

---

## Everyday commands

```bash
# Simulator
./scripts/run_sim.sh                      # headless, detached, follows the log
./scripts/run_sim.sh --fg                 # stay in the foreground
./scripts/run_sim.sh --env Neighborhood   # a different environment
./scripts/run_sim.sh --render nullrhi     # no rendering at all (fastest)
./scripts/run_sim.sh -- -NoVSync -benchmark

# Client
./scripts/run_client.sh                   # interactive shell
./scripts/run_client.sh hello_drone.py    # run an example
./scripts/run_client.sh python -m pytest  # anything else

# Environments
./scripts/fetch_env.sh --list
./scripts/fetch_env.sh Neighborhood

# Housekeeping
docker compose -f docker/docker-compose.yml logs -f sim
docker compose -f docker/docker-compose.yml down
```

Local settings live in `docker/.env` (git-ignored; `docker/.env.example` is the
template): which environment to launch, render mode, resolution, the address
the API ports bind to, and the client image's base.

---

## ROS2 and Foxglove

`docker/ros2/` runs the upstream `projectairsim_ros2_cpp` bridge next to
`foxglove_bridge`. The bridge speaks the native Project AirSim protocol and
republishes sensors as typed ROS2 messages; `foxglove_bridge` serves them over
a WebSocket.

```bash
docker compose -f docker/docker-compose.yml up -d sim ros2
# then connect Foxglove to  ws://<host>:8765
```

The colcon workspace builds on first start into `ProjectAirSim/ros/{build,install,log}`
(git-ignored), so edits to the bridge rebuild without touching the image.

```bash
# Rebuild the workspace after editing the bridge
docker compose -f docker/docker-compose.yml run --rm --no-deps ros2 build

# Poke at the topics
docker compose -f docker/docker-compose.yml exec ros2 bash
ros2 topic list
ros2 topic hz /ProjectAirsim/Drone1/front/scene_camera
```

Settings worth knowing:

| Variable | Effect |
| --- | --- |
| `SCENE_CONFIG` | Empty (default) means *attach to the scene already loaded*. Set it to have the bridge load a scene itself. |
| `SIM_CONFIG_PATH` | Where scene/robot configs are resolved from. Defaults to `workspace/sim_config`. |
| `USE_SHM_PROFILE=1` | Enables the 256 MB Fast DDS shared-memory profile. Needed for 1080p images at 30 Hz, **but it disables UDP**, so subscribers must run in the same container. |

A raw 1920×1080 BGR frame is about 6 MB; four of them at 30 Hz is ~750 MB/s,
which is past Fast DDS's default shared-memory segment. If image topics stall
or drop, that profile is the first thing to try.

---

## The four-camera rig

`scripts/make_rig_config.py` generates the robot and scene configs for a
four-camera rig — front, right, back, left — plus the scene clock that sets the
IMU rate:

```bash
./scripts/make_rig_config.py                              # 4 × 1920×1080 @30Hz, 150° 
./scripts/make_rig_config.py --rate 30 --imu-rate 200
./scripts/make_rig_config.py --projection fisheye --fov 220   # needs the modified plugin
```

Two things the generator encodes, both measured on this host:

* **The IMU has no rate of its own.** It publishes on the scene tick, so
  `--imu-rate` sets `clock.step-ns`, which also caps the physics rate. 5 ms
  gives exactly 200.0 Hz.
* **A perspective camera cannot exceed 180°.** `fx = width / (2·tan(fov/2))`
  goes negative past 180, and the simulator raises no error — it just renders
  something that does not match the config. The generator refuses instead.
  True 220° needs the in-engine fisheye path described in `HANDOFF.md`.

---

## Rendering modes

| `SIM_RENDER_MODE` | Unreal switch | Cameras work? | Use it for |
| --- | --- | --- | --- |
| `offscreen` *(default)* | `-RenderOffScreen` | yes | Normal headless operation |
| `nullrhi` | `-nullrhi` | **no** | Physics/control-only runs, maximum speed |
| `windowed` | `-windowed` | yes | Watching the sim on an X display |

---

## GPU rendering in a container: the part that bites

The NVIDIA container runtime injects the driver *libraries* but — at least
through toolkit 1.19.x — not the JSON manifests that tell userspace how to find
them. `docker/sim/Dockerfile` writes both:

| File | Without it |
| --- | --- |
| `/usr/share/vulkan/icd.d/nvidia_icd.json` | The Vulkan loader never considers the NVIDIA driver. |
| `/usr/share/glvnd/egl_vendor.d/10_nvidia.json` | GLVND hands the NVIDIA ICD Mesa's `libEGL_mesa`, its headless init fails, and the loader skips the ICD entirely. |

The second one is the subtle one. Every NVIDIA library and device node can be
present in the container and Vulkan will still report only `llvmpipe`, because
the ICD's `vk_icdNegotiateLoaderICDInterfaceVersion` returns
`VK_ERROR_INITIALIZATION_FAILED` and the loader silently drops it. Unreal then
"works" — on the CPU, at a few frames per second.

The image also pins `VK_DRIVER_FILES` to the NVIDIA ICD so Unreal cannot pick a
software device even if Mesa's drivers are installed.

To confirm hardware rendering:

```bash
./scripts/verify.sh
# or directly:
docker compose -f docker/docker-compose.yml run --rm --no-deps sim vulkaninfo --summary | grep deviceName
nvidia-smi   # Blocks-Linux-Shipping should be holding ~1GB while the sim runs
```

Seeing `llvmpipe` means software rendering, not a working GPU.

---

## Working on multi-camera rigs

Cameras are declared in the robot config, not the binary. Start from the
upstream examples:

```
ProjectAirSim/client/python/example_user_scripts/sim_config/
```

`scene_basic_drone.jsonc` references a robot config whose `sensors` array
carries the camera definitions — pose, FOV, resolution, and capture types
(RGB, depth, segmentation). Add entries there for extra cameras, keep your
edited configs in `workspace/`, and point the client at them:

```python
world = World(client, "your_scene.jsonc", sim_config_path="/workspace/workspace/sim_config")
```

Relevant upstream docs: `ProjectAirSim/docs/config_robot.md`,
`docs/sensors/camera_capture_settings.md`, `docs/multiple_robots.md`.

---

## Available environments

Release `v0.3.0`, Linux builds:

| Environment | Size | Notes |
| --- | --- | --- |
| `Blocks` | 0.55 GB | Minimal geometry, fast to load — the default |
| `LandscapeMountains` | 0.58 GB | Outdoor terrain |
| `Neighborhood` | 1.8 GB | Suburban streets |
| `CityEnviron` | 3.2 GB | Dense city, split archive |

---

## Requirements

* Linux with an NVIDIA GPU and a driver new enough for Vulkan 1.3
* Docker Engine 20.10+ with Compose v2 and `nvidia-container-toolkit`
* Disk: ~2 GB images + 0.5–3.2 GB per environment

Verified on Ubuntu 22.04, driver 595.84, NVIDIA RTX 6000 Ada, Docker 28.1.1.

---

## Licence

Project AirSim is MIT-licensed (Microsoft, IAMAI Consulting Corp). This
repository's own scripts and container definitions follow the same terms.
