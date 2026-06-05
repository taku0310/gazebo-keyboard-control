# Keyboard Controller container
# Converts keyboard input into geometry_msgs/Twist messages on /cmd_vel.
FROM ros:noetic-ros-base

# Avoid interactive prompts during build.
ENV DEBIAN_FRONTEND=noninteractive

# pynput needs Xlib backend bits on Linux; python3-pip to install pynput.
RUN apt-get update && apt-get install -y --no-install-recommends \
        python3-pip \
        python3-xlib \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python dependencies first for better layer caching.
COPY requirements.txt /app/requirements.txt
RUN pip3 install --no-cache-dir -r /app/requirements.txt

# Copy application source and scenarios.
COPY src/ /app/src/

# Scenarios are mounted/added at runtime via docker-compose volume, but copy
# a default location too.
ENV PYTHONUNBUFFERED=1

# roscore must be reachable; ROS_MASTER_URI is provided by docker-compose.
# stdin/tty are required for keyboard capture (-it / tty: true in compose).
ENTRYPOINT ["/ros_entrypoint.sh"]
CMD ["python3", "/app/src/keyboard_input_controller.py"]
