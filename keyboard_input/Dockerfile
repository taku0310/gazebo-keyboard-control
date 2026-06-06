# Keyboard Controller container (ROS 2 Jazzy)
# Converts keyboard input into geometry_msgs/msg/Twist messages on /cmd_vel.
FROM ros:jazzy-ros-base

# Avoid interactive prompts during build.
ENV DEBIAN_FRONTEND=noninteractive

# pynput needs the Xlib backend bits on Linux; python3-pip to install pynput.
RUN apt-get update && apt-get install -y --no-install-recommends \
        python3-pip \
        python3-xlib \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python dependencies first for better layer caching. Ubuntu 24.04
# marks the system Python externally-managed (PEP 668); inside a container it
# is fine to install into it.
COPY requirements.txt /app/requirements.txt
RUN pip3 install --no-cache-dir --break-system-packages -r /app/requirements.txt

# Copy application source.
COPY src/ /app/src/

ENV PYTHONUNBUFFERED=1

# rclpy / geometry_msgs are provided by the ROS 2 base image and sourced by the
# entrypoint. ROS 2 is masterless (DDS discovery); ROS_DOMAIN_ID is provided by
# docker-compose. stdin/tty are required for keyboard capture (compose run -it).
ENTRYPOINT ["/ros_entrypoint.sh"]
CMD ["python3", "/app/src/keyboard_input_controller.py"]
