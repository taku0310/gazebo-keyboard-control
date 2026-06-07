#!/usr/bin/env bash
#
# run_keyboard.sh - launch the robot simulation stack and the keyboard
# controller. Works on macOS, Linux and WSL2.
#
# Usage:
#   bash run_keyboard.sh
#   bash run_keyboard.sh --scenario scenarios/demo_scenario_01.json
#   bash run_keyboard.sh --scenario scenarios/demo_scenario_01.json --auto
#   bash run_keyboard.sh --log run.log
#
# Flags:
#   --scenario <file>  Scenario JSON to load (host path under scenarios/).
#   --auto             Auto-play the scenario on start, then exit.
#   --log <file>       Tee all output to <file> as well as the console.
#   -h | --help        Show this help.
#
# Notes:
#   The keyboard controller is launched with `docker compose run -it` (not
#   `exec`): the controller defaults to stdin input mode when a TTY is
#   attached, which is the only reliable live-input path inside a container
#   (pynput needs a display backend the container does not have). `run -it`
#   gives a fresh interactive instance with the requested args.

set -euo pipefail

# ---------------------------------------------------------------------------- #
# Resolve project directory (so the script works from anywhere).
# ---------------------------------------------------------------------------- #
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# ---------------------------------------------------------------------------- #
# Argument parsing.
# ---------------------------------------------------------------------------- #
SCENARIO=""
AUTO=0
LOG_FILE=""

print_help() {
  sed -n '2,30p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --scenario)
      SCENARIO="${2:-}"
      [[ -z "$SCENARIO" ]] && { echo "❌ --scenario needs a file path"; exit 1; }
      shift 2
      ;;
    --auto)
      AUTO=1
      shift
      ;;
    --log)
      LOG_FILE="${2:-}"
      [[ -z "$LOG_FILE" ]] && { echo "❌ --log needs a file path"; exit 1; }
      shift 2
      ;;
    -h|--help)
      print_help
      exit 0
      ;;
    *)
      echo "❌ Unknown argument: $1"
      echo "   Try: bash run_keyboard.sh --help"
      exit 1
      ;;
  esac
done

# Redirect all output through tee if logging was requested.
if [[ -n "$LOG_FILE" ]]; then
  exec > >(tee -a "$LOG_FILE") 2>&1
  echo "📝 Logging to $LOG_FILE"
fi

# ---------------------------------------------------------------------------- #
# OS detection.
# ---------------------------------------------------------------------------- #
detect_os() {
  local uname_s
  uname_s="$(uname -s)"
  case "$uname_s" in
    Darwin)
      echo "macOS"
      ;;
    Linux)
      if grep -qiE "(microsoft|wsl)" /proc/version 2>/dev/null; then
        echo "WSL2"
      else
        echo "Linux"
      fi
      ;;
    *)
      echo "$uname_s"
      ;;
  esac
}

OS="$(detect_os)"
echo "🖥️  Detected platform: $OS"

# ---------------------------------------------------------------------------- #
# Pick a docker compose command (v2 plugin vs legacy v1).
# ---------------------------------------------------------------------------- #
if docker compose version >/dev/null 2>&1; then
  COMPOSE=(docker compose)
elif command -v docker-compose >/dev/null 2>&1; then
  COMPOSE=(docker-compose)
else
  echo "❌ Docker Compose not found. Install Docker Desktop / docker compose."
  exit 1
fi

if ! docker info >/dev/null 2>&1; then
  echo "❌ Docker daemon is not running. Start Docker and retry."
  exit 1
fi

# Use the Fast DDS Discovery Server overlay by default. Simple (multicast)
# discovery is unreliable on Docker Desktop (macOS/Windows) VMs - the IGMP
# packets get dropped and the ROS nodes silently fail to find each other. The
# overlay adds a small discovery_server sidecar and switches to unicast, which
# Docker bridges forward reliably. Harmless on native Linux.
COMPOSE+=(-f docker-compose.yml -f docker-compose.discovery.yml)

echo "🐳 Using: ${COMPOSE[*]}"

# ---------------------------------------------------------------------------- #
# Cleanup on exit / Ctrl+C: stop all containers.
# ---------------------------------------------------------------------------- #
cleanup() {
  echo ""
  echo "🧹 Stopping all containers..."
  "${COMPOSE[@]}" down --remove-orphans || true
  echo "✅ Done."
}
trap cleanup EXIT INT TERM

# ---------------------------------------------------------------------------- #
# Start the backing services (everything except keyboard_controller).
# keyboard_controller is a TCP client of ros_bridge; ros_bridge is the DDS
# publisher of /cmd_vel. ROS 2 is masterless - nodes discover over DDS.
# ---------------------------------------------------------------------------- #
echo "🚀 Starting backing services (discovery_server, ros_bridge, control_logic, gazebo)..."
if ! "${COMPOSE[@]}" up -d discovery_server ros_bridge control_logic gazebo; then
  echo "❌ Failed to start services. See output above."
  exit 1
fi

# Give the services a moment to come up and discover each other over DDS.
echo "⏳ Waiting for services (ROS 2 DDS discovery + bridge TCP)..."
sleep 3
echo "✅ Services should be up."

# ---------------------------------------------------------------------------- #
# Build the keyboard controller command.
# ---------------------------------------------------------------------------- #
CTRL_CMD=(python3 /app/src/keyboard_input_controller.py)

if [[ -n "$SCENARIO" ]]; then
  if [[ ! -f "$SCENARIO" ]]; then
    echo "⚠️  Scenario file not found on host: $SCENARIO"
    echo "   (continuing; it must exist under scenarios/ to be mounted)"
  fi
  # scenarios/ is mounted at /app/scenarios inside the container.
  CONTAINER_SCENARIO="/app/scenarios/$(basename "$SCENARIO")"
  CTRL_CMD+=(--scenario "$CONTAINER_SCENARIO")
  echo "🎬 Scenario: $SCENARIO -> $CONTAINER_SCENARIO"
fi

if [[ "$AUTO" -eq 1 ]]; then
  CTRL_CMD+=(--auto)
  echo "▶️  Auto-play enabled."
fi

# ---------------------------------------------------------------------------- #
# Run the keyboard controller interactively (fresh container, tty + stdin).
# ---------------------------------------------------------------------------- #
echo "⌨️  Launching keyboard controller... (Ctrl+C to stop everything)"
echo "--------------------------------------------------------------"
"${COMPOSE[@]}" run --rm keyboard_controller "${CTRL_CMD[@]}"

# When the controller exits normally, the EXIT trap performs cleanup.
