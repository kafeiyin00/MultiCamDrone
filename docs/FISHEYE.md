# Simulating 220° fisheye cameras in Project AirSim

Everything here was measured or read out of the engine and plugin source on this
host. Where something is still unverified it says so.

---

## 1. The problem

A perspective camera cannot render more than 180°.

Project AirSim feeds `fov-degrees` straight into a perspective projection, so the
focal length is `fx = width / (2·tan(fov/2))`. Past 180° the tangent goes
negative. Read back from the simulator's own `camera_info` topic at 512 px wide:

| Requested FOV | `fx` reported | Effective FOV |
| --- | --- | --- |
| 90° | 256.00 | 90° — correct |
| 150° | 68.60 | 150° — correct |
| 179° | 2.23 | 179° — correct, but almost all resolution is at the rim |
| 200° | **−45.14** | −160° — projection inverted |
| 220° | **−93.18** | −140° — projection inverted |
| 270° | **−256.00** | −90° — projection inverted |

Nothing errors. The simulator renders *something* for all of them, which is the
trap: the images simply stop meaning what the config says. `distortion_model`
comes back as an empty string with all-zero parameters, so there is no built-in
fisheye model either.

The exact expression is `core_sim/src/sensors/camera.cpp:955`, and it reproduces
every number in that table.

---

## 2. What the performance budget allows

Measured against Blocks on this host (56 cores, RTX 6000 Ada):

| Configuration | Achieved | Pixel throughput | GPU 0 | Sim CPU |
| --- | --- | --- | --- | --- |
| 20 cameras @ 400×400, 30 Hz target | 14.8 Hz | 50 Mpix/s | 58% | 230% |
| 4 cameras @ 1920×1080, 30 Hz target | 26.7 Hz | 221 Mpix/s | 60% | — |

Fitting `cost = c + k·megapixels` gives **c ≈ 2.64 ms fixed per camera-frame**,
k ≈ 3.24 ms/Mpix. Neither GPU nor CPU is saturated in either case.

That fixed cost caps the rig at roughly **9–10 camera streams at 30 Hz**, almost
independently of resolution.

**The fixed cost is a readback stall, not an inherent limit.** It is dominated by
a synchronous `vkDeviceWaitIdle` inside the `RHICmdList.ReadSurfaceData` call the
plugin makes per camera per frame. That is fixable on its own, and doing so lifts
the ceiling for every approach below.

In the heavier CityEnviron map, 4 × 1920×1080 currently manages only ~10 Hz on an
otherwise idle machine, so scene cost matters too and the 30 Hz target is not yet
met even at four streams.

---

## 3. Approaches, and why the obvious one is wrong

### 3.1 Rejected: five perspective faces per eye, remapped in the client

Render a 5-face cube per eye as five `USceneCaptureComponent2D`s, ship all 20
streams to the client, and remap there. Straightforward, no engine work.

Rejected on frame rate: 20 streams lands at 13–15 Hz, measured. The problem is
not the pixels, it is 20 readbacks and 20 transports per frame.

### 3.2 Rejected: `USceneCaptureComponentCube`

The intuitive fix — let the engine capture a cubemap in one call — does not work
in UE 5.2. From `Engine/Source/Runtime/Renderer/Private/SceneCaptureRendering.cpp`
lines 1062–1172:

* It hard-codes 90° FOV (:1122) and loops all six faces unconditionally (:1123).
* It builds a **separate `FSceneRenderer` per face** (:1145), each with its own
  `FSceneViewFamilyContext` and its own `ENQUEUE_RENDER_COMMAND`. Six fully
  independent scene renders with no sharing, plus six face blits the 2D path
  does not pay. That is *more* work than the five-face plan, not less.
* It passes a stack-local default-constructed `FPostProcessSettings` with blend
  weight 0 (:1141, :1148), so the component's own post-process is discarded.
  Every Project AirSim image type implemented as a post-process blendable —
  depth, segmentation, normals, disparity — is lost, along with exposure and
  camera overrides.
* On Linux/Vulkan an 8-bit readback of a `UTextureRenderTargetCube` returns an
  all-black image: the dimension switch in `RHIReadSurfaceData` bails to zeros
  for anything that is not `Texture2D`/`Texture2DArray`, and
  `RHIMapStagingSurface` `check()`s for `Texture2D`, so the async path cannot
  take a cube either.
* There is no single-pass cube capture in 5.2. `r.SceneCapture.CubeSinglePass`
  does not exist anywhere in `Engine/Source`; `bIsSceneCaptureCube` only disables
  AO history and Lumen.

### 3.3 Chosen: five perspective faces per eye, remapped **in the plugin**

Same five faces, but composited on the GPU inside the plugin and read back once:

```
5 × USceneCaptureComponent2D  @ 90° FOV, SCS_FinalColorLDR
        ↓ (5 × UTextureRenderTarget2D)
  equidistant fisheye remap material / compute pass
        ↓
  1 × UTextureRenderTarget2D  1920×1080
        ↓
  existing UnrealCameraRenderRequest::ReadPixels path — unchanged
```

Why this is the right shape:

* **Readbacks drop from 20 to 4.** Since the fixed per-camera cost is the
  readback stall, this is the number that matters.
* **Pixel work does not go up.** Five 600×600 faces is 1.8 Mpix per eye, against
  2.07 Mpix for one 1920×1080 frame. Section 5 derives the 600 px figure.
* **Post-processing survives**, because each face is an ordinary 2D capture with
  `SCS_FinalColorLDR`. Depth and segmentation keep working.
* **Nothing downstream changes.** The composited target is a `Texture2D`, so the
  existing readback, transport, and ROS2 bridge are untouched.

Five faces at 90° cover ±135° along each axis, comfortably more than the ±110° a
220° fisheye needs.

---

## 4. The camera model

**Use an ideal equidistant projection, `r = f·θ`, and publish it as ROS
`equidistant` with all-zero distortion.**

Of the candidate radial laws, only equidistant, equisolid (`r = 2f·sin(θ/2)`) and
stereographic (`r = 2f·tan(θ/2)`) stay monotonic out to θ = 110°. Orthographic
folds back past 90° (`sin 110° = 0.9397 < sin 90° = 1.0`), and rectilinear is
singular there — which is exactly the failure that produced the negative `fx` in
section 1.

Equidistant is the right default because:

* Kannala-Brandt with `k1..k4 = 0` **is** `r = f·θ`. The model rendered and the
  model published coincide with zero approximation error, and the inverse is
  closed-form and exact: `θ = r/f`.
* ROS sanctions only three `distortion_model` strings, and `equidistant` is the
  only fisheye one.
* A simulator's value is that ground truth is exactly known. Any nonzero `k`
  would be invented, and cannot be cited to a real lens. Expose `k1..k4` as an
  opt-in knob for people who want to model a specific lens, but default to zero.

### 4.1 The consumer caveat, which matters

Every widely-used *implementation* of the equidistant model — OpenCV
`cv::fisheye`, ROS `image_geometry`, OpenVINS `CamEqui`, Foxglove's
`kannala_brandt` — computes `θ = atan(r)` on pinhole-normalised coordinates, and
is therefore hard-limited to θ < 90°, i.e. FOV < 180°. Foxglove's documentation
says so explicitly.

At 220° that limitation excludes **20.8% of the valid image**. So publishing the
string alone is not enough. Also publish, as extension fields:

* `theta_max` — 110° in radians
* the valid image radius, and the radius beyond which an `atan`-based consumer
  must mask

That lets a consumer either mask to `r < 785.5 px` for an `atan`-based front end,
or use the full 220° in VINS-Fusion / camodocal (which computes
`θ = acos(z/‖P‖)` and does handle θ > 90°) and Basalt.

---

## 5. Geometry and resolution

Keep `fov-degrees` meaning **horizontal**, which is the existing semantics. At
1920×1080 with a 220° horizontal FOV:

* `f = 960 / (110° in radians) = 960 / 1.9199 = ` **500.04 px/rad**
* image circle radius = 960 px — the circle spans the full width
* vertical coverage = `2 · 540 / 500.04` = **123.75°**
* **black corners = 5.6% of the frame**

Black corners are correct and expected: that is what a real fisheye on a 16:9
sensor looks like. The alternative — scaling the circle to fill the diagonal —
gives 100% fill but throws away field of view.

### 5.1 Per-face resolution

An equidistant projection has uniform angular resolution: 500.04 px/rad, or
**8.73 px/degree**.

A perspective face has its *lowest* angular resolution at the centre, where for
an N×N face at 90° FOV it is `N/2 / 57.3` px/degree. Matching 8.73 px/degree at
the face centre needs:

```
N/2 / 57.3 = 8.73   →   N ≈ 1000
```

That is the no-undersampling-anywhere figure. In practice the outer faces map to
the compressed rim of the fisheye, so 600–768 px per face is the sensible
starting point, with the centre face larger than the peripheral ones. This is the
main quality/performance dial and should be exposed in config.

---

## 6. Where the code changes go

The plumbing is shorter than expected, because it is already half built.

| # | File | Change |
| --- | --- | --- |
| 1 | `core_sim/src/sensors/camera.cpp:1287-1364` | `Camera::Loader::LoadCaptureSetting` never reads `projection_mode` from JSON. `CaptureSettings` (`core_sim/include/core_sim/sensors/camera.hpp:106`) **already declares `int projection_mode`**, and the Unreal side **already consumes it** (`UnrealCamera.cpp:435`) — it is a dead field. Parse it, plus a fisheye model enum and per-face resolution. |
| 2 | `UnrealCamera.cpp:368` and `:436` | FOV reaches the component in two places. **`:368` is unguarded and is the one that wins at runtime**; `:436` is NaN-guarded. Both need to skip the perspective path for fisheye cameras. |
| 3 | `UnrealCamera.cpp` (new) | Create the five face captures and the composited 2D target; add the remap material; drive the composite before the existing readback. |
| 4 | `core_sim/src/sensors/camera.cpp:955` | `camera_info` focal length. Publish `f = (width/2)/(fov/2)` for fisheye instead of the tangent form, set `distortion_model = "equidistant"`, and add the extension fields from §4.1. |
| 5 | `core_sim/src/sensors/camera.cpp:868` | `GetRay` is a linear pixel→angle map, which happens to be *correct* for equidistant. Verify rather than assume. |
| 6 | `UnrealCamera.cpp:1075, :1097, :1134, :1234` | `CalculateProjectionMatrix`, `ComputeFrustumVertices` (`FMath::Tan` goes negative past 180°), `ProjectPoint`, `GetBoundingBoxProjections`. These must either use the fisheye forward model or be disabled for fisheye cameras — bounding-box projection is meaningless through a perspective matrix that does not exist. |

There is **no serialization boundary** between `core_sim` and the plugin:
`UUnrealCamera` holds a copy of the sim `Camera` object and calls
`GetCameraSettings()` directly (`UnrealCamera.cpp:63`), so adding struct fields is
sufficient.

Schema validation will not fight this: the JSONC schema has no
`additionalProperties: false`, and validation is Python-client-only
(`utils.py:538`) — the C++ server never validates. So a `"projection": "fisheye"`
key already survives today.

All of this is in the `ProjectAirSim` submodule, i.e. upstream code, so the
changes have to be carried as a patch or a fork.

---

## 7. Build path

Verified working on this host. The **source tree**, not the installed build, must
be `UE_ROOT`: `unreal-linux-toolchain.cmake` derives the clang SDK from
`${UE_ROOT}/Engine/Extras/ThirdPartyNotUE/SDKs/HostLinux/Linux_x64`, which the
installed build does not contain.

```bash
export UE_ROOT=/home/iot/workspace/airsim/engine/UnrealEngine-5.2
export PATH="$UE_ROOT/Engine/Binaries/ThirdParty/DotNet/6.0.302/linux:$PATH"

cd ProjectAirSim
./build.sh simlibs_release
./build.sh package_simlibs
./build.sh package_plugin
./build.sh package_blocks_shipping
```

* **Put `/usr/bin` first in `PATH`.** This host also has a pip-installed cmake
  4.4 in `~/.local/bin`, and CMake 4 removed support for
  `cmake_minimum_required(VERSION < 3.5)`, which JSBSim still declares. With the
  pip cmake ahead, the build dies at the JSBSim configure step with
  "Compatibility with CMake < 3.5 has been removed"; the visible tail of the log
  is an unrelated OpenSSL banner, so the real error is easy to miss. System
  cmake 3.22.1 works.
* clang 15.0.1 comes from the in-tree SDK (`v21_clang-15.0.1-centos7`); UBT finds
  it through `GetInTreeSDKRoot()` when `LINUX_MULTIARCH_ROOT` is unset.
* dotnet comes from the engine bundle; the host needs neither clang nor dotnet
  installed.
* `nice -n 15 taskset -c 0-43` keeps the build off the cores the running
  simulator needs.
* Use **`package_blocks_shipping`**, not `package_plugin`, to get something
  runnable: `package_plugin` depends on `blocks_debuggame blocks_development
  blocks_shipping` and on Debug simlibs, so it rebuilds every third-party
  dependency in Debug as well. `package_blocks_shipping` depends only on
  `simlibs_release`.

### 7.1 Only Blocks can be rebuilt

`unreal/Blocks/Blocks.uproject` is the only Unreal *project* in the repo. The
other environments — CityEnviron, Neighborhood, LandscapeMountains — ship as
packaged binaries on the releases page with no project files, so **they cannot
be repackaged with a modified plugin**. Fisheye therefore only works in Blocks
until an environment's project is available.

That is a real constraint on "fisheye in a complex scene": pick one of a wide
field of view or a dense map, or obtain the map's Unreal project.

---

## 7.5 Carrying the changes

The changes are to upstream code in the `ProjectAirSim` submodule, which is
pinned to a specific commit, so they cannot be committed here. They live in
`patches/` instead:

```bash
./scripts/apply_patches.sh --check    # what is applied
./scripts/apply_patches.sh            # apply
./scripts/apply_patches.sh --revert   # back to upstream
```

`--revert` also removes the added files, which a plain `git checkout` would
leave behind.

`scripts/build_engine.sh` fetches and builds the engine itself (~257 GB source,
51 GB installed build, 4+ hours).

---

## 8. Status

| Step | State |
| --- | --- |
| 220° shown impossible with the stock perspective path | done — measured, §1 |
| Performance budget measured | done — §2 |
| Cube-capture approach evaluated and rejected | done — §3.2, from engine source |
| Camera model and geometry decided | done — §4, §5 |
| Change sites identified | done — §6 |
| UE 5.2 engine built | done — installed build 51 GB |
| Plugin build path verified | done — §7, CMake configures |
| Baseline plugin/simlibs build | done |
| Fisheye implementation | done |
| Repackaged environment with fisheye | done — `envs/BlocksFisheye`, via `scripts/install_env.sh` |
| Fisheye verified against rendered images | **done** — §8.2 |
| 30 Hz at four eyes | **not met** — 23.1 Hz; §8.4 has the reason and the route |

### 8.1 What was implemented

Eight files, carried as `patches/0001-fisheye-220-degree-cameras.patch`:

| File | Change |
| --- | --- |
| `core_sim/src/constant.hpp` | JSON keys `projection`, `fisheye-model`, `fisheye-face-resolution` |
| `core_sim/include/core_sim/sensors/camera.hpp` | `fisheye_enabled`, `fisheye_model`, `fisheye_face_resolution` on `CaptureSettings`. Kept **separate** from the existing `projection_mode`, which is cast straight to Unreal's `ECameraProjectionMode` and would reinterpret a third value as a garbage enum. |
| `core_sim/src/sensors/camera.cpp` | Parses those keys; computes the fisheye focal length per radial law and publishes `distortion_model = "equidistant"`. Also **throws** on a perspective FOV at or above 180° instead of rendering an inverted projection. |
| `.../Private/Shaders/FisheyeRemapCS.usf` | The remap: invert the radial law, build the ray, pick the face by largest dot product with its axis, gnomonic-project into it, sample. |
| `.../Private/Sensors/FisheyeRemapCS.{h,cpp}` | `FGlobalShader` declaration and dispatch, modelled on the plugin's existing `LidarPointCloudCS`. |
| `.../Private/Sensors/UnrealCamera.{h,cpp}` | Builds the five face captures, reuses the scene render target as the UAV-capable composite, captures the faces instead of the scene component, and runs the remap on the render thread before the readback. |

Two details worth knowing:

* **The composite *is* `RenderTargets[kScene]`.** `TextureTarget` is assigned in
  exactly one place (`UnrealCamera.cpp:335`), so re-initialising that target
  with `bCanCreateUAV` makes it both the compute shader's output and what
  `OnRendered()` reads. Nothing downstream of the readback changed.
* **Face bases are computed in C++**, from the rotation actually applied to each
  face component, and handed to the shader in its own camera frame (x right,
  y down, z forward). The shader contains no Unreal axis conventions.

Perspective-only helpers are clamped rather than corrected:
`ComputeFrustumVertices` and `CalculateProjectionMatrix` would otherwise take
`tan` of a half-angle above 90° and invert the frustum. Bounding-box projection
through a perspective matrix is not meaningful for a fisheye image and is not
attempted.

### 8.2 Verified against rendered frames

Captured through the ROS2 bridge with `workspace/grab_frames.py`, four eyes at
640x640, 220 degrees, equidistant, in the locally-built Blocks:

| Check | Result |
| --- | --- |
| Composite is written | yes — real content inside the circle on all four eyes |
| Image circle radius | exactly `width/2` = 320 px; **100.0%** of the area outside it is pure black |
| Circle boundary | hard cliff, luminance 92 -> 0 across r = 320 |
| Seam continuity | step across the 45-degree face seam is 0.5-1.5x the typical radial step, i.e. no discontinuity |
| Rendered focal length | 320 / 1.9199 = **166.68 px/rad** by construction |

The frames also show the drone's own rotors and arms at the left and right
extremes. That is correct, not a bug: at 220 degrees the camera sees 20 degrees
*behind* its own mounting plane, so a real lens would see the airframe too.
Increase `--mount-radius` to push it out, or mask the affected annulus for VIO
use.

### 8.3 Still to verify

* **The published `camera_info` intrinsics.** `core_sim` publishes those once at
  scene load on a non-latched topic, so by the time a ROS subscriber attaches
  the message is gone; `ros2 topic hz` on the info topic reports nothing. The
  rendered geometry is confirmed and both values come from the same expression,
  but that the published `fx` equals 166.68 has not been observed. Making that
  topic latched (`transient_local`) upstream would fix the observability.
* `GetRay` (`camera.cpp:868`) consistency with the equidistant model — §6 item 5
  predicts it already holds.

---

## 8.4 Measured cost, and why 30 Hz is still short

Four 220-degree eyes at 640x640 in Blocks, flying:

| Faces per eye | Camera rate | IMU |
| --- | --- | --- |
| 512 px | 23.14 Hz | 199.99 Hz |
| **768 px** | **23.11 Hz** | 199.98 Hz |
| 1024 px | 20.31 Hz | 200.04 Hz |

**Face resolution is almost free between 512 and 768** and costs 12% at 1024, so
768 is the operating point: it is the best quality available at no measurable
cost over 512.

That flatness is the point. 20 face captures per frame set at ~2.1 ms of fixed
cost each is 42 ms, or 23.8 Hz — which is what is measured. The wall is the
*number of scene captures*, not their pixels, exactly as section 2 predicted for
the per-camera cost.

So the lever for 30 Hz is fewer renderers, not smaller faces. The route
identified while reading the engine: UE 5.2 already supports **N views inside
one `FSceneViewFamily` and one `FSceneRenderer`** — the planar-reflection path
does precisely that (`PlanarReflectionRendering.cpp:634-649`), and
`SetupViewFamilyForSceneCapture` takes a `TArrayView` of views, with only the
*cube* path constrained to one. Rendering an eye's five faces as five views of a
single scene renderer, into one atlased target, would collapse 20 renderers to
4. That is the next optimisation, and it is not attempted here.

Making the readback asynchronous (`FRHIGPUTextureReadback`, avoiding the
`vkDeviceWaitIdle` inside `RHIReadSurfaceData`) is a separate win, but a smaller
one now: compositing in the plugin already cut readbacks from 20 to 4.
