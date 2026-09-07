# =============================================================================
# Project AirSim - Simulation Server (Unreal Engine 5 packaged binary)
#
# Runs the packaged UE5 environment (Blocks / Neighborhood / CityEnviron ...)
# headless with Vulkan GPU rendering via the NVIDIA container runtime.
# =============================================================================
FROM ubuntu:22.04

ARG DEBIAN_FRONTEND=noninteractive
ARG USERNAME=airsim
ARG USER_UID=1000
ARG USER_GID=1000

RUN apt-get update && apt-get install -y --no-install-recommends \
      ca-certificates curl wget unzip xz-utils sudo tzdata locales \
      procps net-tools iproute2 vim-tiny less \
      # --- Vulkan loader + tools (UE5 renders through Vulkan on Linux) ---
      libvulkan1 vulkan-tools mesa-vulkan-drivers \
      # --- X11 / windowing libs that UE links against even when offscreen ---
      libx11-6 libx11-xcb1 libxcb1 libxext6 libxrandr2 libxcursor1 libxinerama1 libxi6 \
      libxxf86vm1 libxfixes3 libxrender1 libsm6 libice6 libxkbcommon0 \
      # --- SDL2: UE's Linux application backend ---
      libsdl2-2.0-0 \
      # --- OpenGL / EGL ---
      libgl1 libglu1-mesa libglx-mesa0 libegl1 libgles2 \
      # --- Audio: libs are dlopen'ed even when running with -nosound ---
      libasound2 libpulse0 libopenal1 \
      # --- Misc UE runtime deps ---
      libatomic1 libnss3 libnspr4 libdbus-1-3 fontconfig xdg-user-dirs \
      python3 \
  && rm -rf /var/lib/apt/lists/*

# --- NVIDIA driver discovery files -----------------------------------------
#
# nvidia-container-toolkit injects the driver *libraries* but, at least up to
# 1.19.x, not the two JSON manifests that tell the Vulkan loader and GLVND how
# to find them. Both are needed:
#
#  1. The Vulkan ICD manifest, or the loader never considers the NVIDIA driver
#     and falls back to Mesa's llvmpipe (software rendering, ~100x slower).
#
#  2. The GLVND *EGL vendor* manifest. This one is easy to miss: the NVIDIA
#     Vulkan ICD initialises itself through EGL when there is no X display, and
#     GLVND picks its vendor by scanning egl_vendor.d. With only Mesa's
#     50_mesa.json present, GLVND hands it libEGL_mesa, whose headless init
#     fails, so vk_icdNegotiateLoaderICDInterfaceVersion returns
#     VK_ERROR_INITIALIZATION_FAILED and the whole ICD is skipped. The symptom
#     is Vulkan reporting only "llvmpipe" even though every NVIDIA library and
#     device node is present in the container.
#
# The 10_ prefix keeps NVIDIA ahead of Mesa in GLVND's scan order.
RUN mkdir -p /usr/share/vulkan/icd.d /usr/share/glvnd/egl_vendor.d \
 && printf '%s\n' \
    '{' \
    '    "file_format_version" : "1.0.1",' \
    '    "ICD": {' \
    '        "library_path": "libGLX_nvidia.so.0"' \
    '    }' \
    '}' > /usr/share/vulkan/icd.d/nvidia_icd.json \
 && printf '%s\n' \
    '{' \
    '    "file_format_version" : "1.0.0",' \
    '    "ICD" : {' \
    '        "library_path" : "libEGL_nvidia.so.0"' \
    '    }' \
    '}' > /usr/share/glvnd/egl_vendor.d/10_nvidia.json

# Ask the NVIDIA container runtime for graphics (Vulkan/GL), not just compute.
ENV NVIDIA_VISIBLE_DEVICES=all \
    NVIDIA_DRIVER_CAPABILITIES=compute,utility,graphics,display

# Restrict the Vulkan loader to the NVIDIA ICD. Without this, Unreal can
# enumerate Mesa's llvmpipe as a valid device and silently render on the CPU at
# a few frames per second. VK_ICD_FILENAMES is the pre-1.3.207 spelling of
# VK_DRIVER_FILES; both are set so the image works with old and new loaders.
ENV VK_DRIVER_FILES=/usr/share/vulkan/icd.d/nvidia_icd.json \
    VK_ICD_FILENAMES=/usr/share/vulkan/icd.d/nvidia_icd.json \
    __GLX_VENDOR_LIBRARY_NAME=nvidia

# Run as a normal user whose UID matches the host user, so anything written to
# bind-mounted volumes (logs, captured images) stays editable on the host.
RUN groupadd -g ${USER_GID} ${USERNAME} 2>/dev/null || true \
 && useradd -m -u ${USER_UID} -g ${USER_GID} -s /bin/bash ${USERNAME} \
 && echo "${USERNAME} ALL=(ALL) NOPASSWD:ALL" > /etc/sudoers.d/${USERNAME}

COPY entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

USER ${USERNAME}
WORKDIR /home/${USERNAME}

# topics (pub/sub) and services (req/resp)
EXPOSE 8989 8990

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
