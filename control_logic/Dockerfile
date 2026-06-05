# Control Logic container
# Subscribes /cmd_vel, applies safety constraints, publishes /gazebo/cmd_vel.
FROM ros:noetic-ros-base

ENV DEBIAN_FRONTEND=noninteractive

# Install pip in case requirements grow; std-lib-only today.
RUN apt-get update && apt-get install -y --no-install-recommends \
        python3-pip \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt /app/requirements.txt
# requirements.txt is std-lib-only today; install real deps if any are added
# (no `|| true` so a genuine install failure surfaces instead of being masked).
RUN pip3 install --no-cache-dir -r /app/requirements.txt

COPY src/ /app/src/

ENV PYTHONUNBUFFERED=1

# ROS_MASTER_URI is provided by docker-compose. No tty needed (headless node).
ENTRYPOINT ["/ros_entrypoint.sh"]
CMD ["python3", "/app/src/control.py"]
