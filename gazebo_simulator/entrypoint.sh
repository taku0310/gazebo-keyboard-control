#!/usr/bin/env bash
#
# Gazebo Sim (Harmonic) container entrypoint, paired with ROS 2 Jazzy.
#
# Starts, in order:
#   1. Xvfb            - virtual display so the GUI can render headless
#   2. x11vnc + noVNC  - stream that display to a browser on :8080
#   3. ros_gz bridge   - ROS 2 <-> Gazebo topic translation
#   4. gz sim          - the simulator (foreground; keeps the container alive)
#
# Topic bridge (control_logic -> Gazebo, sensors -> ROS 2):
#   /gazebo/cmd_vel  ROS geometry_msgs/msg/Twist  -> GZ gz.msgs.Twist
#   /odom            GZ  gz.msgs.Odometry  -> ROS nav_msgs/msg/Odometry
#   /imu             GZ  gz.msgs.IMU       -> ROS sensor_msgs/msg/Imu
#   /clock           GZ  gz.msgs.Clock     -> ROS rosgraph_msgs/msg/Clock
set -e

WORLD="${WORLD_FILE:-/root/.gazebo/worlds/empty.world}"
DISPLAY_NUM="${DISPLAY:-:1}"
VNC_PORT=5900
WEB_PORT="${WEB_PORT:-8080}"

source /opt/ros/jazzy/setup.bash

echo "🖥️  Starting virtual display ${DISPLAY_NUM}..."
Xvfb "${DISPLAY_NUM}" -screen 0 1280x720x24 &
sleep 2

echo "🌐 Starting VNC + noVNC web UI on :${WEB_PORT}..."
x11vnc -display "${DISPLAY_NUM}" -forever -shared -nopw -rfbport "${VNC_PORT}" -bg -quiet
# noVNC ships a launcher; fall back to websockify if not present.
if [ -x /usr/share/novnc/utils/novnc_proxy ]; then
  /usr/share/novnc/utils/novnc_proxy --vnc "localhost:${VNC_PORT}" --listen "${WEB_PORT}" &
elif [ -x /usr/share/novnc/utils/launch.sh ]; then
  /usr/share/novnc/utils/launch.sh --vnc "localhost:${VNC_PORT}" --listen "${WEB_PORT}" &
else
  websockify --web=/usr/share/novnc "${WEB_PORT}" "localhost:${VNC_PORT}" &
fi

echo "🔗 Starting ROS 2 <-> Gazebo bridge..."
# The bridge is backgrounded, so its failure would not trip `set -e` and would
# otherwise die silently, leaving /gazebo/cmd_vel unbridged. Check the package
# is present first and warn loudly if not.
if ros2 pkg prefix ros_gz_bridge >/dev/null 2>&1; then
  ros2 run ros_gz_bridge parameter_bridge \
    "/gazebo/cmd_vel@geometry_msgs/msg/Twist]gz.msgs.Twist" \
    "/odom@nav_msgs/msg/Odometry[gz.msgs.Odometry" \
    "/imu@sensor_msgs/msg/Imu[gz.msgs.IMU" \
    "/clock@rosgraph_msgs/msg/Clock[gz.msgs.Clock" \
    &
  BRIDGE_PID=$!
else
  echo "❌ ros_gz_bridge not found: /gazebo/cmd_vel will NOT reach Gazebo" >&2
  echo "   and no /odom,/imu,/clock will be published back to ROS 2." >&2
  echo "   Continuing with visualization only." >&2
  BRIDGE_PID=""
fi

# Stop background jobs cleanly on exit.
cleanup() {
  echo "🧹 Stopping Gazebo container services..."
  [ -n "${BRIDGE_PID}" ] && kill "${BRIDGE_PID}" 2>/dev/null || true
  pkill -x x11vnc 2>/dev/null || true
  pkill -f websockify 2>/dev/null || true
  pkill -f novnc_proxy 2>/dev/null || true
  pkill -x Xvfb 2>/dev/null || true
}
trap cleanup EXIT INT TERM

echo "🚀 Launching Gazebo Sim with world: ${WORLD}"
# -r: run physics immediately; -v 3: info logging. Runs in the foreground so
# the container's lifetime tracks the simulator.
exec gz sim -r -v 3 "${WORLD}"
