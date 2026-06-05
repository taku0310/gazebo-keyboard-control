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
#   The keyboard controller is launched with `docker compose run` (not
#   `exec`): the keyboard_controller service's default command already starts
#   the controller, and in a headless `up -d` container pynput cannot attach,
#   so the container would exit and `exec` would have nothing to attach to.
#   `run` gives a fresh, interactive (tty) instance with the requested args.

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
# ---------------------------------------------------------------------------- #
echo "🚀 Starting backing services (ros_master, control_logic, gazebo)..."
if ! "${COMPOSE[@]}" up -d ros_master control_logic gazebo; then
  echo "❌ Failed to start services. See output above."
  exit 1
fi

# Wait for ROS Master to come up (spec: ~3s). The compose healthcheck already
# gates dependents, but we give roscore a moment before attaching.
echo "⏳ Waiting for ROS Master (roscore)..."
sleep 3
echo "✅ ROS Master should be up."

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
