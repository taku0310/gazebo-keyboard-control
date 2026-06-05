# Gazebo (Ignition) Fortress simulator container.
#
# Base image ships Ignition Fortress on Ubuntu 20.04 (Focal), which matches
# ROS Noetic. On top of it we add:
#   - ROS Noetic (ros-base) so we can talk to the rest of the ROS1 stack
#   - the ros_ign bridge to translate ROS1 <-> Ignition topics
#   - a headless web visualization (Xvfb + x11vnc + noVNC) served on :8080,
#     because gzweb is Gazebo-Classic-only and Fortress has no built-in web UI
#
# NOTE: This image is heavy and the ros_ign bridge availability for
# Noetic+Fortress via apt can vary by mirror/date; if the apt package is
# missing, build ros_ign from source with IGNITION_VERSION=fortress (see the
# commented fallback below). These build steps could not be executed in the
# authoring environment and should be validated on a real Docker build.
FROM osrf/gazebo:ignition-fortress

ENV DEBIAN_FRONTEND=noninteractive
ENV IGNITION_VERSION=fortress

# ---------------------------------------------------------------------------- #
# ROS Noetic apt repository + base tools.
# ---------------------------------------------------------------------------- #
RUN apt-get update && apt-get install -y --no-install-recommends \
        curl gnupg2 lsb-release ca-certificates \
    && curl -sSL "https://raw.githubusercontent.com/ros/rosdistro/master/ros.key" \
        -o /usr/share/keyrings/ros-archive-keyring.gpg \
    && echo "deb [signed-by=/usr/share/keyrings/ros-archive-keyring.gpg] \
http://packages.ros.org/ros/ubuntu $(lsb_release -cs) main" \
        > /etc/apt/sources.list.d/ros1.list \
    && rm -rf /var/lib/apt/lists/*

# ---------------------------------------------------------------------------- #
# ROS Noetic base + the ROS1 <-> Ignition bridge.
# ---------------------------------------------------------------------------- #
RUN apt-get update && apt-get install -y --no-install-recommends \
        ros-noetic-ros-base \
        ros-noetic-ros-ign-bridge \
    && rm -rf /var/lib/apt/lists/*

# Fallback if ros-noetic-ros-ign-bridge is unavailable for Fortress:
#   RUN mkdir -p /bridge_ws/src && cd /bridge_ws/src \
#       && git clone https://github.com/gazebosim/ros_ign.git -b noetic \
#       && cd /bridge_ws \
#       && rosdep install -r --from-paths src -i -y --rosdistro noetic \
#       && bash -c "source /opt/ros/noetic/setup.bash && catkin_make install"

# ---------------------------------------------------------------------------- #
# Headless web visualization stack.
# ---------------------------------------------------------------------------- #
RUN apt-get update && apt-get install -y --no-install-recommends \
        xvfb x11vnc novnc websockify net-tools \
    && rm -rf /var/lib/apt/lists/*

# ---------------------------------------------------------------------------- #
# Resources: models (incl. simple_robot) and worlds.
# These are also bind-mounted by docker-compose; copying makes the image
# self-contained when run standalone.
# ---------------------------------------------------------------------------- #
COPY models/ /root/.gazebo/models/
COPY worlds/ /root/.gazebo/worlds/

# Let Ignition find model://simple_robot and the world file.
ENV IGN_GAZEBO_RESOURCE_PATH=/root/.gazebo/models:/root/.gazebo/worlds
ENV DISPLAY=:1

COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

EXPOSE 8080

ENTRYPOINT ["/entrypoint.sh"]
