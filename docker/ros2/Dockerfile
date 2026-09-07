# =============================================================================
# Project AirSim - ROS2 bridge + Foxglove
#
# Runs the upstream `projectairsim_ros2_cpp` bridge, which connects to the
# simulator over the native client protocol and republishes sensors as typed
# ROS2 messages, alongside foxglove_bridge, which serves those topics to
# Foxglove over a WebSocket.
#
# The workspace is bind-mounted and built on first start into ros/build and
# ros/install, so edits to the bridge source take effect without a rebuild.
# =============================================================================
FROM ros:humble-ros-base

ARG DEBIAN_FRONTEND=noninteractive
ARG USERNAME=airsim
ARG USER_UID=1000
ARG USER_GID=1000

RUN apt-get update && apt-get install -y --no-install-recommends \
      build-essential cmake git ninja-build pkg-config \
      python3-colcon-common-extensions python3-vcstool python3-rosdep \
      sudo vim-tiny less procps net-tools iproute2 iputils-ping \
      # --- the WebSocket server Foxglove connects to ---
      ros-humble-foxglove-bridge \
      # --- bridge dependencies named in package.xml ---
      ros-humble-rclcpp ros-humble-rclcpp-action ros-humble-tf2-ros \
      ros-humble-sensor-msgs ros-humble-nav-msgs ros-humble-geometry-msgs \
      ros-humble-rosgraph-msgs ros-humble-std-msgs \
      ros-humble-rosidl-default-generators ros-humble-rosidl-default-runtime \
      # --- handy for poking at topics from inside the container ---
      ros-humble-rosbag2 ros-humble-rosbag2-storage-mcap \
      ros-humble-topic-tools \
  && rm -rf /var/lib/apt/lists/*

# The image ships as root; add a user matching the host so build artefacts
# written into the bind-mounted ros/ tree stay editable outside the container.
RUN if id -u ${USER_UID} >/dev/null 2>&1; then \
        userdel -r "$(id -nu ${USER_UID})" 2>/dev/null || true; \
    fi \
 && groupadd -g ${USER_GID} ${USERNAME} 2>/dev/null || true \
 && useradd -m -u ${USER_UID} -g ${USER_GID} -s /bin/bash ${USERNAME} \
 && echo "${USERNAME} ALL=(ALL) NOPASSWD:ALL" > /etc/sudoers.d/${USERNAME}

# A raw 1920x1080 BGR frame is ~6MB; four of them at 30Hz will overrun the
# default Fast DDS shared-memory segment. The bridge ships a 256MB profile.
ENV RMW_IMPLEMENTATION=rmw_fastrtps_cpp \
    PROJECTAIRSIM_REPO=/workspace/ProjectAirSim \
    ROS_DOMAIN_ID=0

COPY entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

USER ${USERNAME}
WORKDIR /workspace/ProjectAirSim/ros

EXPOSE 8765

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
CMD ["bridge"]
