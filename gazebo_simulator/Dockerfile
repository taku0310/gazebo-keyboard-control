# Gazebo Sim (Harmonic) simulator container, paired with ROS 2 Jazzy.
#
# Built on the ROS 2 Jazzy base (Ubuntu 24.04) and adds:
#   - Gazebo Sim Harmonic (gz-harmonic) from the OSRF apt repo
#   - the ros_gz bridge (ros-jazzy-ros-gz-bridge) to translate ROS 2 <-> Gazebo
#     topics  (ros_gz is the first-class, supported pairing for Jazzy+Harmonic)
#   - a headless web visualization (Xvfb + x11vnc + noVNC) served on :8080,
#     because Gazebo Sim has no built-in web UI
#
# NOTE: this image is large. These build steps could not be executed in the
# authoring environment and should be validated on a real Docker build.
FROM ros:jazzy-ros-base

ENV DEBIAN_FRONTEND=noninteractive

# ---------------------------------------------------------------------------- #
# Gazebo Harmonic apt repo + Gazebo + ros_gz bridge + headless web viz.
# ---------------------------------------------------------------------------- #
RUN apt-get update && apt-get install -y --no-install-recommends \
        curl gnupg2 lsb-release ca-certificates \
    && curl -sSL https://packages.osrfoundation.org/gazebo.gpg \
        -o /usr/share/keyrings/pkgs-osrf-archive-keyring.gpg \
    && echo "deb [signed-by=/usr/share/keyrings/pkgs-osrf-archive-keyring.gpg] \
http://packages.osrfoundation.org/gazebo/ubuntu-stable $(lsb_release -cs) main" \
        > /etc/apt/sources.list.d/gazebo-stable.list \
    && apt-get update && apt-get install -y --no-install-recommends \
        gz-harmonic \
        ros-jazzy-ros-gz-bridge \
        ros-jazzy-ros-gz-sim \
        xvfb x11vnc novnc websockify net-tools \
    && rm -rf /var/lib/apt/lists/*

# ---------------------------------------------------------------------------- #
# Resources: models (incl. simple_robot) and worlds. Also bind-mounted by
# docker-compose; copying makes the image self-contained when run standalone.
# ---------------------------------------------------------------------------- #
COPY models/ /root/.gazebo/models/
COPY worlds/ /root/.gazebo/worlds/

# Let Gazebo Sim find model://simple_robot and the world file.
ENV GZ_SIM_RESOURCE_PATH=/root/.gazebo/models:/root/.gazebo/worlds
# Headless software rendering for the GUI on the virtual display.
ENV DISPLAY=:1
ENV LIBGL_ALWAYS_SOFTWARE=1

COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

EXPOSE 8080

ENTRYPOINT ["/entrypoint.sh"]
