# Control Logic container (ROS 2 Jazzy)
# Subscribes /cmd_vel, applies safety constraints, publishes /gazebo/cmd_vel.
FROM ros:jazzy-ros-base

ENV DEBIAN_FRONTEND=noninteractive

# pip in case requirements grow; std-lib + rclpy only today.
RUN apt-get update && apt-get install -y --no-install-recommends \
        python3-pip \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# requirements.txt is std-lib-only today (rclpy comes from the base image);
# install real deps if any are added. --break-system-packages is needed on
# Ubuntu 24.04 (PEP 668) and is safe inside a container.
COPY requirements.txt /app/requirements.txt
RUN pip3 install --no-cache-dir --break-system-packages -r /app/requirements.txt

COPY src/ /app/src/

ENV PYTHONUNBUFFERED=1

# Drop privileges: run as a non-root user (defense in depth).
RUN useradd -m -u 1000 appuser && chown -R appuser /app
USER appuser
ENV HOME=/home/appuser

# ROS 2 is masterless (DDS discovery); ROS_DOMAIN_ID is provided by
# docker-compose. No tty needed (headless node).
ENTRYPOINT ["/ros_entrypoint.sh"]
CMD ["python3", "/app/src/control.py"]
