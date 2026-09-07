# Handoff — Project AirSim environment setup

**Date:** 2026-09-07
**Host:** `iot` — Ubuntu 22.04.5, 56 cores, 1 TB RAM, 4 × NVIDIA RTX 6000 Ada (46 GB each), driver 595.84
**Repo:** `git@github.com:kafeiyin00/MultiCamDrone.git` (working copy at `/home/iot/workspace/airsim`)

---

## 1. What this repository is

A containerised Project AirSim environment for multi-camera drone work. The
upstream simulator is vendored as a submodule; everything else here is the
container stack, helper scripts, and the place your own scene/robot configs go.

```
MultiCamDrone/
├── ProjectAirSim/        submodule → iamaisim/ProjectAirSim @ 45187d9 (v1.0.0-7)
├── docker/
│   ├── sim/              Unreal 5.2 runtime image (Vulkan, headless)
│   ├── client/           Python client image
│   ├── docker-compose.yml
│   ├── .env              local settings (git-ignored)
│   └── .env.example
├── scripts/              setup / fetch_env / run_sim / run_client / verify
├── workspace/            your scripts and configs (tracked)
├── envs/                 packaged Unreal environments (git-ignored, ~880 MB for Blocks)
├── downloads/            release archives (git-ignored)
└── output/               run artefacts (git-ignored)
```

---

## 2. Current state

**Working and verified:**

* `ProjectAirSim` submodule registered and checked out.
* `envs/Blocks` unpacked from release `v0.3.0` (`Blocks-Linux-0.3.0.zip`, 880 MB on disk).
* `multicamdrone/airsim-sim:latest` built (411 MB).
* Vulkan inside the sim container reports **4 × NVIDIA RTX 6000 Ada as
  `PHYSICAL_DEVICE_TYPE_DISCRETE_GPU`** — hardware rendering, not llvmpipe.
* Simulator runs headless: UE 5.2.1 starts, binds 8989/8990, and holds ~1.1 GB
  of GPU memory (`nvidia-smi` shows `Blocks-Linux-Shipping`).
* Ports published to the host at `127.0.0.1:8989` / `127.0.0.1:8990`.

**Not yet verified at the time of writing:**

* The client image build was still running; the `hello_drone.py` round trip and
  `scripts/verify.sh` sections 6 had not been exercised end to end.
  Run `./scripts/verify.sh` first thing — it covers exactly this.
* Nothing has been pushed to the remote yet; the repo is empty upstream.

---

## 3. The one thing worth knowing: Vulkan in the container

This consumed most of the setup effort and the fix is non-obvious, so it is
worth reading before touching `docker/sim/Dockerfile`.

**Symptom.** Inside the container, `vulkaninfo` reported only
`llvmpipe (LLVM 15.0.7)` — software rendering — even though `nvidia-smi` worked,
`/dev/nvidia*` were present, `libGLX_nvidia.so.0` was byte-identical to the
host's, and `ldd` resolved every dependency.

**How it was pinned down.** Calling the ICD entry point directly with `ctypes`:

```python
lib = ctypes.CDLL("libGLX_nvidia.so.0")
lib.vk_icdNegotiateLoaderICDInterfaceVersion(...)   # → -3 in container, 0 on host
```

`-3` is `VK_ERROR_INITIALIZATION_FAILED`. `strace`, bracketed around that exact
call, showed the container scanning `/usr/share/glvnd/egl_vendor.d/`, finding
only `50_mesa.json`, and loading `libEGL_mesa.so.0`.

**Root cause.** With no X display the NVIDIA Vulkan ICD initialises through
EGL, and GLVND picks its EGL vendor by scanning `egl_vendor.d`. The NVIDIA
container runtime injects the driver *libraries* but not
`/usr/share/glvnd/egl_vendor.d/10_nvidia.json` (toolkit 1.19.1 here). GLVND
therefore handed the NVIDIA ICD Mesa's EGL, whose headless init fails, so
negotiation failed and the Vulkan loader silently dropped the ICD.

**Fix.** `docker/sim/Dockerfile` writes both manifests itself:

* `/usr/share/vulkan/icd.d/nvidia_icd.json` — so the loader sees the driver.
* `/usr/share/glvnd/egl_vendor.d/10_nvidia.json` — so GLVND gives it
  `libEGL_nvidia.so.0`. The `10_` prefix sorts ahead of Mesa's `50_`.

It also pins `VK_DRIVER_FILES`/`VK_ICD_FILENAMES` to the NVIDIA ICD so Unreal
cannot fall back to a software device.

**Why it matters.** The failure is silent. Unreal starts, the client connects,
images come back — everything looks fine while rendering on the CPU at a few
FPS. Always confirm with `nvidia-smi` that `Blocks-Linux-Shipping` is holding
GPU memory.

Dead ends ruled out along the way, so nobody repeats them: mounting `/dev/dri`,
running as root, mounting `libnvidia-api.so.1`, X11/`DISPLAY` passthrough, and
the Vulkan loader version (22.04's 1.3.204 vs 24.04's 1.3.275 — neither was the
problem).

---

## 4. Decisions taken, and why

| Decision | Reasoning |
| --- | --- |
| Pre-built binary environments, not a source build | Building the plugin needs Unreal Engine 5.2/5.7 source (~200 GB, Epic account). The packaged `Blocks` binary gives a full sim server immediately, and robot/scene configs — where multi-camera work happens — are runtime JSONC, so no rebuild is needed. |
| `ProjectAirSim` as a submodule | Pins an exact upstream commit without pulling 1.3 GB of upstream history into this repo. Switch to a fork only if the *sim C++ source* needs changes; camera rigs do not. |
| `envs/`, `downloads/`, `output/` git-ignored | Multi-GB binaries. `scripts/fetch_env.sh` re-fetches them reproducibly from the pinned release. |
| Two images, not one | The sim needs the Vulkan/UE runtime; the client needs Python and OpenCV. Separating them keeps each small and lets the client image swap to a CUDA base for the `autonomy` extras. |
| Ports published to `127.0.0.1` | A client on the host works unchanged against the default `127.0.0.1`. Set `SIM_BIND_ADDR=0.0.0.0` in `docker/.env` to reach the sim from another machine. |
| Container user matches host UID/GID | Files written to `envs/` and `output/` stay editable on the host. |

---

## 5. Picking up from here

1. **Verify the stack.** `./scripts/verify.sh` — six sections, from host GPU to
   a live client connection. Anything red names the fix.
2. **Push the repo.** Nothing is on the remote yet:
   ```bash
   git add -A && git commit -m "..." && git push -u origin main
   ```
   Note the submodule: collaborators must clone with
   `--recurse-submodules`, or run `git submodule update --init --recursive`.
3. **Start the multi-camera work.** Cameras live in the robot config's
   `sensors` array, not in the binary. Copy a scene + robot pair out of
   `ProjectAirSim/client/python/example_user_scripts/sim_config/` into
   `workspace/sim_config/`, add camera entries, and load them from your client
   script. See `docs/config_robot.md` and
   `docs/sensors/camera_capture_settings.md` upstream.

---

## 6. Gotchas worth remembering

* **`nullrhi` kills cameras.** It disables rendering entirely — fine for
  physics/control runs, useless for image capture. Use `offscreen` for anything
  with a camera.
* **Four GPUs are visible to the container.** Unreal picks one. To pin it, set
  `NVIDIA_VISIBLE_DEVICES=0` (or a UUID) in the `sim` service environment.
* **The sim keeps running after a client script exits.** It waits for the next
  connection. Stop it with `docker compose -f docker/docker-compose.yml down`.
* **The scene is client-supplied.** The environment starts empty; no vehicle
  exists until a client sends a scene config. An "empty" sim is not a fault.
* **Client address differs by location.** `sim` inside the compose network,
  `127.0.0.1` from the host.
* **Upstream release assets ≠ upstream source.** The submodule tracks `main`
  (v1.0.0+); the packaged environments are from release `v0.3.0`. That is the
  newest release carrying Linux environment binaries. `scripts/fetch_env.sh`
  pins it via `AIRSIM_RELEASE`.

---

# Part 2 — Multi-camera fisheye rig (in progress)

**Added:** 2026-09-07, after the base environment was verified.

## 7. The target

| Requirement | Value |
| --- | --- |
| Cameras | 4 fisheye, facing front / back / left / right |
| FOV | 220 degrees |
| Resolution | 1920x1080 each |
| Frame rate | 30 Hz, synchronised across all four |
| IMU | 200 Hz |
| Output | Foxglove, via ROS2 + foxglove_bridge |

## 8. Measurements that shaped the design

These were taken on this host against the Blocks environment. They are the
reason the design looks the way it does, so re-measure before overriding them.

### 8.1 A perspective camera cannot exceed 180 degrees

`fov-degrees` is fed straight into a perspective projection, so the focal length
is `fx = width / (2 * tan(fov/2))`. Past 180 degrees the tangent goes negative.
Read back from the simulator's own `camera_info` topic:

| Requested | fx reported | Effective FOV |
| --- | --- | --- |
| 90 | 256.00 | 90 (correct) |
| 150 | 68.60 | 150 (correct) |
| 179 | 2.23 | 179 (correct, but useless resolution distribution) |
| 200 | **-45.14** | -160 (projection inverted) |
| 220 | **-93.18** | -140 (projection inverted) |
| 270 | **-256.00** | -90 (projection inverted) |

The sim renders *something* for all of them, which is the trap: no error is
raised, the images just do not mean what the config says. `distortion_model`
comes back empty with zero parameters, so there is no built-in fisheye model
either.

### 8.2 Per-camera cost dominates, not pixels

| Configuration | Achieved | Pixel throughput | GPU 0 | Sim CPU |
| --- | --- | --- | --- | --- |
| 20 cams @ 400x400, 30 Hz target | 14.8 Hz | 50 Mpix/s | 58% | 230% (2.3 of 56 cores) |
| 4 cams @ 1920x1080, 30 Hz target | 26.7 Hz | 221 Mpix/s | 60% | - |

Fitting `cost = c + k * megapixels` to those two points gives **c = 2.64 ms
fixed per camera-frame**, k = 3.24 ms/Mpix. Neither the GPU nor the CPU is
saturated in either case — the limit is the simulator's per-camera serial
capture path.

The consequence: **roughly 9-10 camera streams at 30 Hz**, almost independent
of resolution. Adding GPUs does not help; only GPU 0 is ever used.

This is what rules out the obvious client-side approach of rendering a 5-face
cube per eye (4 x 5 = 20 streams) and remapping in the client. It would land
around 13-15 Hz.

### 8.3 What already works

* IMU at exactly **200.0 Hz** with the scene clock at `step-ns: 5000000`.
  The IMU has no rate of its own — `core_sim/src/sensors/imu.cpp` updates on
  the scene tick ("Using sim_dt_nanos supplied by the Scene as it is the
  fastest ticking"), so **IMU rate is the scene clock rate**. Setting it also
  caps physics at 200 Hz, which is fine for a quadrotor.
* Four cameras are **frame-synchronous**: 570 of 570 frames shared an identical
  `time_stamp` across all four eyes. No extra synchronisation work is needed.
* 4 x 1920x1080 at 26.7 Hz, sim at 0.94x real time.

## 9. Chosen approach

**Render true fisheye inside the Unreal plugin.** One capture per eye instead
of five, which keeps the transport cost identical to the 4-camera case already
measured at 26.7 Hz.

```
USceneCaptureComponentCube  ->  UTextureRenderTargetCube
                                      |
                        fisheye mapping post-process material  (GPU)
                                      |
                                UTextureRenderTarget2D  (1920x1080)
                                      |
                     existing ReadPixels path in UnrealCamera.cpp
```

The alternative — 5 perspective faces per eye remapped client-side — was
measured and rejected on frame rate (8.2). Modifying the plugin costs an engine
build up front but is the only route to 220 degrees at 30 Hz.

### Where the code goes

`unreal/Blocks/Plugins/ProjectAirSim/Source/ProjectAirSim/Private/Sensors/UnrealCamera.cpp`

Today each image type gets a `USceneCaptureComponent2D` with
`CaptureSource = SCS_FinalColorLDR`, `bCaptureEveryFrame = false`, captured
manually and read back through `OnRendered()` -> `UnrealCameraRenderRequest::ReadPixels()`.

The work:

1. Add a `USceneCaptureComponentCube` + `UTextureRenderTargetCube` for cameras
   configured as fisheye.
2. Add a post-process material sampling the cube through the fisheye model.
   Equidistant is `r = f * theta`, so a pixel at radius `r` from the image
   centre maps to `theta = (r / R) * (fov / 2)`; build the direction vector from
   `theta` and the azimuth and sample the cubemap.
3. Render the material into a `UTextureRenderTarget2D` and hand that to the
   existing readback path, so nothing downstream changes.
4. Extend the robot config schema: a `projection` field (`perspective` |
   `fisheye`), a `fisheye-model` field (`equidistant` | `equisolid` |
   `stereographic`), and lift the `fov-degrees` ceiling for fisheye cameras.
5. Publish the fisheye intrinsics through `camera_info` using a model that can
   express them — `distortion_model: "equidistant"` (Kannala-Brandt), which is
   what ROS and most VIO front ends expect.

### Engine prerequisite

`Blocks.uproject` declares `EngineAssociation: "5.2"`, so the plugin needs
**UE 5.2 source**, and an *installed build* of it to avoid recompiling the
engine on every plugin iteration. Upstream's own estimate: ~200 GB and 4+
hours. The host has 1.2 TB free.

`git@github.com:EpicGames/UnrealEngine.git` is reachable from this machine, so
the GitHub account is already linked to an Epic account — that usual blocker
does not apply.

## 10. Progress

| Step | State |
| --- | --- |
| UE 5.2 source + `Setup.sh` dependencies (`engine/UnrealEngine-5.2`, 257 GB) | done |
| Installed engine build (51 GB) | done |
| Fisheye capture in the plugin | written, core_sim compiles — see `docs/FISHEYE.md` |
| Blocks repackaged with the modified plugin | in progress |
| Fisheye verified against rendered images | **not yet** — `docs/FISHEYE.md` 8.2 |
| ROS2 bridge + foxglove_bridge | done and verified |
| Four-camera rig config generator | done |
| Continuous flight through the ROS2 bridge | done — `./scripts/run_flight.sh` |

The fisheye work is carried as `patches/0001-fisheye-220-degree-cameras.patch`,
because `ProjectAirSim` is a submodule pinned to upstream. `./scripts/apply_patches.sh`
applies, checks, and reverts it. **The design, the measurements behind it, and
what remains unverified are all in `docs/FISHEYE.md`** — read that before
touching the camera path.

The ROS2 leg is live: all four cameras, the IMU, and four `camera_info` topics
are bridged, and Foxglove connects on `ws://<host>:8765`. Verify with
`./scripts/verify_ros2.sh`.

**Rates, measured on an idle machine** with `./scripts/verify_ros2.sh --rates`:

| Scene | Rig | Cameras (target 30) | IMU (target 200) |
| --- | --- | --- | --- |
| Blocks | 4 x 1920x1080 | 26.7 Hz | 200.0 Hz |
| CityEnviron | 4 x 1920x1080 | 4.4 Hz | 98.5 Hz |

CityEnviron is far heavier to render than Blocks and misses 30 Hz badly at
1920x1080. It also sits above the 4 MiB frame-size threshold that leaks,
which is the other reason the rig now runs at 640x640.

An earlier attempt read 7.8 Hz and 82 Hz, but the engine build had the load
average at 58 on 56 cores at the time, so those numbers meant nothing. The
script refuses to report rates when the load is above half the core count for
exactly that reason.

The engine build is finished; `./scripts/build_engine.sh status` reports what is
on disk.

`engine/` is git-ignored — it is a quarter-terabyte of build tree.

## 11. ROS2 / Foxglove path

`docker/ros2/` builds `ros:humble-ros-base` with `foxglove_bridge` and the
upstream `ros/projectairsim_ros2_cpp` bridge. The bridge connects over the
native Project AirSim client protocol, republishes sensors as typed ROS2
messages, and `foxglove_bridge` serves them on `ws://0.0.0.0:8765`.

```bash
docker compose -f docker/docker-compose.yml up -d sim ros2
# then point Foxglove at ws://<host>:8765
```

The colcon workspace builds on first container start into
`ProjectAirSim/ros/{build,install,log}` (all git-ignored), so bridge edits on
the host rebuild without touching the image.

Two things to watch:

* **`SCENE_CONFIG` empty means "attach to the loaded scene"** rather than
  replacing it. Set it only when you want the bridge to own scene loading.
* A raw 1920x1080 BGR frame is ~6 MB. Four at 30 Hz will overrun Fast DDS's
  default shared-memory segment. The bridge ships `fastdds_shm_256m.xml`;
  `USE_SHM_PROFILE=1` enables it, but that profile is **shared-memory only** —
  DDS then cannot cross the container boundary, so subscribers must live in the
  same container.
