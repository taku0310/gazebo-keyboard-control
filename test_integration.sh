#!/usr/bin/env bash
#
# test_integration.sh - end-to-end integration tests for the robot
# simulation stack (macOS / Linux / WSL2).
#
# Runs four test groups in order and prints a tree-formatted report:
#   1. Container startup (containers running, ROS Master :11311, Gazebo :8080)
#   2. ROS communication (/cmd_vel topic, publisher, subscriber, gazebo input)
#   3. E2E latency measurement (target < 100 ms)
#   4. Scenario playback (demo_scenario_01 / demo_scenario_02)
#
# Each check records pass / fail / warn independently; the script continues on
# failure and prints a final summary. Gazebo-related checks degrade to warnings
# when the Gazebo container is not yet runnable (its image/bridge is still WIP).
#
# Usage:
#   bash test_integration.sh              # full run, tears the stack down after
#   bash test_integration.sh --keep       # leave containers running afterwards
#   bash test_integration.sh --quick      # skip the long scenario_02 playback
#   bash test_integration.sh -h|--help

# Note: intentionally NOT using `set -e` - tests must continue after a failure.
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# ---------------------------------------------------------------------------- #
# Options.
# ---------------------------------------------------------------------------- #
KEEP=0
QUICK=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --keep)  KEEP=1; shift ;;
    --quick) QUICK=1; shift ;;
    -h|--help)
      sed -n '2,28p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
      exit 0 ;;
    *) echo "Unknown argument: $1"; exit 1 ;;
  esac
done

# ---------------------------------------------------------------------------- #
# Counters.
# ---------------------------------------------------------------------------- #
PASS=0
FAIL=0
WARN=0

# Tree-formatted reporters. $1 = branch ("├─" or "└─"), $2 = message,
# $3 = optional detail (shown indented on failure).
pass() { echo "  $1 ✅ $2"; PASS=$((PASS + 1)); }
warn() { echo "  $1 ⚠️  $2"; WARN=$((WARN + 1)); [[ -n "${3:-}" ]] && echo "        ↳ $3"; }
info() { echo "  $1 ℹ️  $2"; }
fail() { echo "  $1 ❌ $2"; FAIL=$((FAIL + 1)); [[ -n "${3:-}" ]] && echo "        ↳ $3"; }

section() { echo ""; echo "▶ $1"; }

# ---------------------------------------------------------------------------- #
# Docker / compose detection.
# ---------------------------------------------------------------------------- #
if docker compose version >/dev/null 2>&1; then
  COMPOSE=(docker compose)
elif command -v docker-compose >/dev/null 2>&1; then
  COMPOSE=(docker-compose)
else
  echo "❌ Docker Compose not found. Install Docker / docker compose."
  exit 1
fi

if ! docker info >/dev/null 2>&1; then
  echo "❌ Docker daemon is not running. Start Docker and retry."
  exit 1
fi

# Use the Fast DDS Discovery Server overlay by default: multicast discovery is
# unreliable on Docker Desktop (macOS/Windows) VMs, which silently leaves the
# ROS nodes unable to find each other (and makes discovery-sensitive checks
# flaky). The overlay switches to unicast via a discovery_server sidecar;
# harmless on native Linux.
COMPOSE+=(-f docker-compose.yml -f docker-compose.discovery.yml)

dc() { "${COMPOSE[@]}" "$@"; }

# Run a ROS 2 CLI command inside the (running) control_logic container, which
# ships rclpy and the ROS 2 CLI. Sources the ROS 2 environment first.
ros_cli() {
  dc exec -T control_logic bash -lc "source /opt/ros/jazzy/setup.bash && $*" 2>/dev/null
}

# Poll until a command succeeds or timeout (seconds) elapses.
wait_for() {
  local timeout="$1"; shift
  local elapsed=0
  while ! "$@" >/dev/null 2>&1; do
    sleep 1
    elapsed=$((elapsed + 1))
    [[ $elapsed -ge $timeout ]] && return 1
  done
  return 0
}

# Is a container running? $1 = service name.
is_running() {
  local cid
  cid="$(dc ps -q "$1" 2>/dev/null)"
  [[ -n "$cid" ]] && [[ "$(docker inspect -f '{{.State.Running}}' "$cid" 2>/dev/null)" == "true" ]]
}

# ---------------------------------------------------------------------------- #
# Cleanup.
# ---------------------------------------------------------------------------- #
cleanup() {
  if [[ "$KEEP" -eq 1 ]]; then
    echo ""
    echo "ℹ️  --keep set: leaving containers running."
    return
  fi
  echo ""
  echo "🧹 Tearing down stack..."
  dc down --remove-orphans >/dev/null 2>&1 || true
}
trap cleanup EXIT

echo "════════════════════════════════════════"
echo " Integration tests: robot simulation"
echo " Using: ${COMPOSE[*]}"
echo "════════════════════════════════════════"

# ============================================================================ #
# Test 1: Container startup.
# ============================================================================ #
section "Test 1: Container Startup"

# Start only the long-lived backing services. keyboard_controller is launched
# on demand via `compose run` (Test 4): its default container has no display,
# so a headless `up -d` instance would exit immediately - checking it for
# "running" here would be a guaranteed false failure.
echo "  🚀 Starting backing services (docker compose up -d)..."
if ! dc up -d discovery_server ros_bridge control_logic gazebo >/dev/null 2>&1; then
  fail "└─" "docker compose up failed" "$(dc up -d discovery_server ros_bridge control_logic gazebo 2>&1 | tail -3)"
else
  # ROS 2 is masterless; wait until the CLI can list topics over DDS.
  wait_for 30 ros_cli "ros2 topic list" || true

  if is_running ros_bridge; then
    pass "├─" "ros_bridge running"
  else
    fail "├─" "ros_bridge not running" "$(dc logs --tail 5 ros_bridge 2>&1 | tail -3)"
  fi

  if is_running control_logic; then
    pass "├─" "control_logic running"
  else
    fail "├─" "control_logic not running" "$(dc logs --tail 5 control_logic 2>&1 | tail -3)"
  fi

  # keyboard_controller is run on demand, not kept up; verify it is runnable.
  if dc run --rm -T keyboard_controller python3 -c "import sys; sys.exit(0)" \
       >/dev/null 2>&1; then
    pass "├─" "keyboard_controller runnable (compose run)"
  else
    fail "├─" "keyboard_controller image not runnable"
  fi

  # gazebo may not be runnable yet (heavy image) -> warn, don't fail.
  if is_running gazebo; then
    pass "├─" "gazebo running"
  else
    warn "├─" "gazebo not running (heavy image)" \
      "build the gazebo container to enable full E2E"
  fi

  # ROS 2 discovery: the CLI can enumerate topics.
  if ros_cli "ros2 topic list" >/dev/null 2>&1; then
    pass "├─" "ROS 2 discovery working (ros2 topic list)"
  else
    fail "├─" "ROS 2 CLI cannot list topics"
  fi

  # Gazebo web UI port 8080.
  if curl -fsS -o /dev/null --max-time 3 "http://localhost:8080" 2>/dev/null; then
    pass "└─" "Gazebo Web UI listening on 8080"
  else
    warn "└─" "Gazebo Web UI not reachable on 8080" \
      "expected once the gazebo container + noVNC are running"
  fi
fi

# ============================================================================ #
# Test 2: ROS communication.
# ============================================================================ #
section "Test 2: ROS Communication"

# Publish a test command on /cmd_vel for a few seconds (auto-stops via timeout).
dc exec -d control_logic bash -lc \
  "source /opt/ros/jazzy/setup.bash && \
   timeout 12 ros2 topic pub -r 10 /cmd_vel geometry_msgs/msg/Twist \
   '{linear: {x: 1.0, y: 0.0, z: 0.0}, angular: {x: 0.0, y: 0.0, z: 0.5}}'" \
  >/dev/null 2>&1 || true
sleep 3

# /cmd_vel exists.
if ros_cli "ros2 topic list" | grep -q "^/cmd_vel$"; then
  pass "├─" "/cmd_vel topic exists"
else
  fail "├─" "/cmd_vel topic missing"
fi

# Publisher active: `ros2 topic info` reports a non-zero publisher count.
# Poll: cross-container DDS discovery can take several seconds (notably on
# Docker Desktop for Mac/Windows), so retry rather than judging in one shot.
# The END guard treats empty/failed output as "not yet" so wait_for keeps
# trying instead of mistaking no output for success.
has_cmd_vel_publisher() {
  ros_cli "ros2 topic info /cmd_vel" | \
    awk -F': ' '/Publisher count/{found=1; exit !($2 > 0)} END{if(!found) exit 1}'
}
if wait_for 20 has_cmd_vel_publisher; then
  pass "├─" "Publisher active on /cmd_vel"
else
  fail "├─" "No publisher on /cmd_vel" "$(ros_cli "ros2 topic info /cmd_vel" | tail -3)"
fi

# Subscriber active: control_logic republishes to /gazebo/cmd_vel, so receiving
# one message there proves it subscribed to /cmd_vel and processed it.
if ros_cli "timeout 5 ros2 topic echo --once /gazebo/cmd_vel" | grep -q "linear:"; then
  pass "├─" "Subscriber active (control_logic -> /gazebo/cmd_vel)"
else
  fail "├─" "control_logic did not republish to /gazebo/cmd_vel"
fi

# Gazebo receives commands: the ros_gz bridge subscribes to /gazebo/cmd_vel.
if ros_cli "ros2 topic info /gazebo/cmd_vel" | \
   awk -F': ' '/Subscription count/{exit !($2 > 0)}'; then
  pass "└─" "Gazebo/bridge subscribed to /gazebo/cmd_vel"
else
  warn "└─" "No subscriber on /gazebo/cmd_vel" \
    "needs the gazebo container + ros_gz_bridge"
fi

# ============================================================================ #
# Test 2b: ROS Bridge (TCP JSON Lines -> /cmd_vel)
# ============================================================================ #
section "Test 2b: ROS Bridge (TCP -> /cmd_vel)"

# Send a steady JSON Lines stream to the bridge over TCP (from inside the
# control_logic container, reaching ros_bridge by service name) and confirm it
# is republished on /cmd_vel. A distinct value (1.5) proves it is the bridge,
# not the ros2-topic-pub publisher from Test 2. The sender runs long enough to
# cover cross-container DDS discovery latency while we poll for the value.
dc exec -T control_logic python3 -c '
import socket, time
end = time.time() + 45
try:
    s = socket.create_connection(("ros_bridge", 9090), timeout=5)
    while time.time() < end:  # 20 Hz
        s.sendall(b"{\"linear_x\":1.5,\"angular_z\":0.5}\n")
        time.sleep(0.05)
    s.close()
except OSError:
    pass
' >/dev/null 2>&1 &
TCP_PID=$!

# Use ONE long-lived subscriber rather than repeated short ones. Under the Fast
# DDS Discovery Server every fresh `ros2 topic echo` is a brand-new participant
# that must redo the data-channel handshake with ros_bridge; that can take more
# than a few seconds, and a retry loop of short echoes restarts the handshake
# from scratch each time, so it can never converge. A single subscriber kept
# alive long enough completes the handshake once and then receives data. --once
# returns as soon as the first message arrives - the sender streams 1.5
# continuously (well inside the 0.5s watchdog), so the first value seen is 1.5.
if ros_cli "timeout 15 ros2 topic echo --once /cmd_vel" | grep -q "x: 1.5"; then
  pass "└─" "TCP JSON reached /cmd_vel via ros_bridge"
else
  fail "└─" "ros_bridge did not republish TCP command to /cmd_vel"
fi
kill "$TCP_PID" 2>/dev/null || true
wait "$TCP_PID" 2>/dev/null || true

# ============================================================================ #
# Test 3: E2E latency measurement.
# ============================================================================ #
section "Test 3: Latency Measurement"

# Measure /cmd_vel -> /gazebo/cmd_vel propagation (keyboard publish rate +
# control_logic processing). Run a probe node inside control_logic.
LAT_OUT="$(dc exec -T control_logic bash -lc \
  "source /opt/ros/jazzy/setup.bash && python3 -" <<'PY' 2>/dev/null
import time
import rclpy
from rclpy.node import Node
from geometry_msgs.msg import Twist

rclpy.init()
node = Node("latency_probe")
state = {}

def cb(msg):
    if abs(msg.linear.x) > 1e-3 and "t1" not in state:
        state["t1"] = time.time()

node.create_subscription(Twist, "/gazebo/cmd_vel", cb, 10)
pub = node.create_publisher(Twist, "/cmd_vel", 10)
time.sleep(1.0)  # let discovery / connections establish

m = Twist()
m.linear.x = 1.0
state["t0"] = time.time()
deadline = time.time() + 5.0
while time.time() < deadline and "t1" not in state:
    pub.publish(m)
    rclpy.spin_once(node, timeout_sec=0.02)

if "t1" in state:
    print("LATENCY_MS=%.1f" % ((state["t1"] - state["t0"]) * 1000.0))
else:
    print("LATENCY_MS=TIMEOUT")
rclpy.shutdown()
PY
)"

LAT="$(echo "$LAT_OUT" | grep -o 'LATENCY_MS=[0-9.]*' | cut -d= -f2 | head -1)"
if [[ -z "$LAT" ]]; then
  fail "└─" "Latency probe failed (no measurement)" "$(echo "$LAT_OUT" | tail -2)"
else
  # Compare against 100 ms target using awk (floating point).
  if awk "BEGIN{exit !($LAT < 100)}"; then
    info "├─" "E2E latency (/cmd_vel -> /gazebo/cmd_vel): ${LAT}ms (Target < 100ms)"
    pass "└─" "Latency within target"
  else
    info "├─" "E2E latency: ${LAT}ms (Target < 100ms)"
    fail "└─" "Latency exceeds 100ms target"
  fi
fi

# ============================================================================ #
# Test 4: Scenario playback.
# ============================================================================ #
section "Test 4: Scenario Playback"

# Run a scenario through the keyboard controller (headless --auto path) and
# confirm it plays to completion. $1 = scenario file (host path under
# scenarios/), $2 = branch.
run_scenario() {
  local file="$1" branch="$2"
  local name; name="$(basename "$file")"
  if [[ ! -f "$file" ]]; then
    fail "$branch" "$name not found on host"
    return
  fi
  local out
  out="$(dc run --rm -T keyboard_controller \
        python3 /app/src/keyboard_input_controller.py \
        --scenario "/app/scenarios/$name" --auto 2>&1)"
  if echo "$out" | grep -q "Scenario complete"; then
    pass "$branch" "$name OK"
  else
    fail "$branch" "$name did not complete" "$(echo "$out" | tail -3)"
  fi
}

run_scenario "scenarios/demo_scenario_01.json" "├─"
if [[ "$QUICK" -eq 1 ]]; then
  warn "└─" "demo_scenario_02.json skipped (--quick)"
else
  run_scenario "scenarios/demo_scenario_02.json" "└─"
fi

# ============================================================================ #
# Summary.
# ============================================================================ #
echo ""
echo "══════════════════════════════════════"
echo "  Passed: $PASS   Failed: $FAIL   Warnings: $WARN"
if [[ "$FAIL" -eq 0 ]]; then
  if [[ "$WARN" -eq 0 ]]; then
    echo "  ✅ ALL TESTS PASSED"
  else
    echo "  ✅ TESTS PASSED (with $WARN warning(s))"
  fi
  echo "══════════════════════════════════════"
  exit 0
else
  echo "  ❌ $FAIL TEST(S) FAILED"
  echo "══════════════════════════════════════"
  exit 1
fi
