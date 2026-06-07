# Keyboard Controller container (ROS-free TCP client)
#
# The controller no longer touches ROS: it sends velocity commands as JSON over
# TCP to the ros_bridge service. So this image is a slim Python image, not a ROS
# base. Input in the container is via stdin/termios (works headless, standard
# library only); pynput is a native-desktop-only fallback and is deliberately
# not installed in the image (see requirements.txt).
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1

WORKDIR /app

# Install Python dependencies first for better layer caching. requirements.txt
# is intentionally free of third-party packages (the stdin input path is pure
# standard library); this step is a no-op today but kept for caching and any
# future deps. pynput is NOT installed here - it pulls evdev, which fails to
# build without kernel headers (notably on arm64). See requirements.txt.
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
