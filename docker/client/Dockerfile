# =============================================================================
# Project AirSim - Python client
#
# Ships the `projectairsim` client library plus the tooling needed to run the
# example scripts. The repo is bind-mounted at /workspace/ProjectAirSim and the
# package is installed in editable mode, so edits on the host take effect with
# no rebuild.
#
# Build with a CUDA base if you need the `autonomy` extras (torch, onnxruntime):
#   docker compose build --build-arg BASE_IMAGE=nvidia/cuda:12.4.1-cudnn-runtime-ubuntu22.04 client
# =============================================================================
ARG BASE_IMAGE=ubuntu:22.04
FROM ${BASE_IMAGE}

ARG DEBIAN_FRONTEND=noninteractive
ARG USERNAME=airsim
ARG USER_UID=1000
ARG USER_GID=1000

RUN apt-get update && apt-get install -y --no-install-recommends \
      ca-certificates curl wget git unzip sudo tzdata locales \
      procps net-tools iproute2 iputils-ping vim-tiny less \
      build-essential cmake pkg-config \
      python3 python3-dev python3-pip python3-venv \
      # --- opencv-python runtime deps (cv2 imports fail without these) ---
      libgl1 libglib2.0-0 libsm6 libxext6 libxrender1 \
      # --- matplotlib / cv2 GUI windows over X11 ---
      libx11-6 libxcb1 libxkbcommon-x11-0 libgtk-3-0 \
      # --- open3d (lidar extra) needs GL + X ---
      libglu1-mesa libxi6 libxrandr2 libxinerama1 libxcursor1 \
      # --- geometry deps for Shapely / pykml ---
      libgeos-dev libxml2-dev libxslt1-dev \
  && rm -rf /var/lib/apt/lists/*

RUN groupadd -g ${USER_GID} ${USERNAME} 2>/dev/null || true \
 && useradd -m -u ${USER_UID} -g ${USER_GID} -s /bin/bash ${USERNAME} \
 && echo "${USERNAME} ALL=(ALL) NOPASSWD:ALL" > /etc/sudoers.d/${USERNAME}

# A venv keeps the client's pinned deps clear of the system python, and living
# under /opt (not the bind-mounted workspace) means it survives volume remounts.
ENV VIRTUAL_ENV=/opt/airsim-venv
RUN python3 -m venv ${VIRTUAL_ENV} \
 && chown -R ${USER_UID}:${USER_GID} ${VIRTUAL_ENV}
ENV PATH="${VIRTUAL_ENV}/bin:${PATH}"

USER ${USERNAME}

RUN python -m pip install --no-cache-dir --upgrade pip setuptools wheel

# Pre-install the dependency set so that the editable install of the mounted
# repo at container start is a fast, network-free no-op.
RUN python -m pip install --no-cache-dir \
      "pynng>=0.5.0" "msgpack>=1.0.5" "opencv-python>=4.2.0.32" numpy matplotlib \
      "commentjson>=0.9.0" "jsonschema>=4.4.0" inputs pqdict cryptography pykml \
      junit-xml jsonlines Shapely azure-storage-blob \
      pytest "psutil>=5.9.0" ipython

ENV PROJECTAIRSIM_REPO=/workspace/ProjectAirSim \
    PYTHONUNBUFFERED=1 \
    # Point example scripts at the sim container by default; override for localhost.
    SIM_ADDRESS=sim

COPY entrypoint.sh /usr/local/bin/entrypoint.sh

WORKDIR /workspace
ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
CMD ["bash"]
