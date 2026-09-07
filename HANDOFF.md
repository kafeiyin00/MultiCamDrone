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
