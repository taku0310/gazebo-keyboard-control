# ROS Bridge container (ROS 2 Jazzy)
# TCP (JSON Lines) server -> publishes geometry_msgs/msg/Twist on /cmd_vel.
# This is the single DDS publisher of /cmd_vel; the command source (keyboard
# today, a SoftPLC later) connects over TCP and never touches ROS directly.
FROM ros:jazzy-ros-base

ENV DEBIAN_FRONTEND=noninteractive

WORKDIR /app

# requirements.txt is std-lib-only today (rclpy comes from the base image).
RUN apt-get update && apt-get install -y --no-install-recommends \
        python3-pip \
    && rm -rf /var/lib/apt/lists/*
COPY requirements.txt /app/requirements.txt
RUN pip3 install --no-cache-dir --break-system-packages -r /app/requirements.txt

COPY src/ /app/src/

ENV PYTHONUNBUFFERED=1

# Drop privileges: run as a non-root user (defense in depth). The listen port
# (9090) is > 1024, so no root is needed. A home dir is provided for ROS logs.
RUN useradd -m -u 1000 appuser && chown -R appuser /app
USER appuser
ENV HOME=/home/appuser

# ROS 2 is masterless (DDS discovery); ROS_DOMAIN_ID is provided by
# docker-compose. The TCP port is published by docker-compose so external
# clients (e.g. a SoftPLC) can connect.
ENTRYPOINT ["/ros_entrypoint.sh"]
CMD ["python3", "/app/src/ros_bridge.py"]
