# Keyboard Controller container (ROS-free TCP client)
#
# The controller no longer touches ROS: it sends velocity commands as JSON over
# TCP to the ros_bridge service. So this image is a slim Python image, not a ROS
# base. Input is via stdin (works headless in containers) with pynput as a
# native-desktop fallback.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1

WORKDIR /app

# Install Python dependencies first for better layer caching. pynput pulls
# python-xlib on Linux automatically; it is only used in the optional native
# pynput input mode (the container path uses stdin).
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY src/ /app/src/

# Drop privileges: run as a non-root user (defense in depth).
RUN useradd -m appuser && chown -R appuser /app
USER appuser
ENV HOME=/home/appuser

# The bridge endpoint is provided by docker-compose (BRIDGE_HOST/BRIDGE_PORT).
# stdin/tty are required for keyboard capture (compose run -it).
CMD ["python3", "/app/src/keyboard_input_controller.py"]
