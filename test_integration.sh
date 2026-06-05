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

dc() { "${COMPOSE[@]}" "$@"; }

# Run a ROS CLI command inside the (running) control_logic container, which
# ships rospy and the ROS CLI. Sources the ROS environment first.
ros_cli() {
  dc exec -T control_logic bash -lc "source /opt/ros/noetic/setup.bash && $*" 2>/dev/null
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
if ! dc up -d ros_master control_logic gazebo >/dev/null 2>&1; then
  fail "└─" "docker compose up failed" "$(dc up -d ros_master control_logic gazebo 2>&1 | tail -3)"
else
  # Give roscore a moment (compose healthcheck also gates dependents).
  wait_for 30 ros_cli "rostopic list" || true

  for svc in ros_master control_logic; do
    if is_running "$svc"; then
      pass "├─" "$svc running"
    else
      fail "├─" "$svc not running" "$(dc logs --tail 5 "$svc" 2>&1 | tail -3)"
    fi
  done

  # keyboard_controller is run on demand, not kept up; verify it is runnable.
  if dc run --rm -T keyboard_controller python3 -c "import sys; sys.exit(0)" \
       >/dev/null 2>&1; then
    pass "├─" "keyboard_controller runnable (compose run)"
  else
    fail "├─" "keyboard_controller image not runnable"
  fi

  # gazebo may not be runnable yet (image/bridge WIP) -> warn, don't fail.
  if is_running gazebo; then
    pass "├─" "gazebo running"
  else
    warn "├─" "gazebo not running (image/bridge still WIP)" \
      "build the gazebo container to enable full E2E"
  fi

  # ROS Master port 11311.
  if ros_cli "rostopic list" >/dev/null 2>&1; then
    pass "├─" "ROS Master listening on 11311"
  else
    fail "├─" "ROS Master not responding on 11311"
  fi

  # Gazebo web UI port 8080.
  if curl -fsS -o /dev/null --max-time 3 "http://localhost:8080" 2>/dev/null; then
    pass "└─" "Gazebo Web UI listening on 8080"
  else
    warn "└─" "Gazebo Web UI not reachable on 8080" \
      "expected once the gazebo container + gzweb are running"
  fi
fi

# ============================================================================ #
# Test 2: ROS communication.
# ============================================================================ #
section "Test 2: ROS Communication"

# Publish a test command on /cmd_vel for a few seconds (auto-stops via timeout).
dc exec -d control_logic bash -lc \
  "source /opt/ros/noetic/setup.bash && \
   timeout 12 rostopic pub -r 10 /cmd_vel geometry_msgs/Twist \
   '{linear: {x: 1.0, y: 0.0, z: 0.0}, angular: {x: 0.0, y: 0.0, z: 0.5}}'" \
  >/dev/null 2>&1 || true
sleep 3

# /cmd_vel exists.
if ros_cli "rostopic list" | grep -q "^/cmd_vel$"; then
  pass "├─" "/cmd_vel topic exists"
else
  fail "├─" "/cmd_vel topic missing"
fi

# Publisher active: check the Publishers block of `rostopic info` is non-empty.
if ros_cli "rostopic info /cmd_vel" | \
   awk '/Publishers:/{p=1;next} /Subscribers:/{p=0} p&&/\*/{f=1} END{exit !f}'; then
  pass "├─" "Publisher active on /cmd_vel"
else
  fail "├─" "No publisher on /cmd_vel"
fi

# Subscriber active: control_logic republishes to /gazebo/cmd_vel, so receiving
# one message there proves it subscribed to /cmd_vel and processed it.
if ros_cli "timeout 5 rostopic echo -n1 /gazebo/cmd_vel" | grep -q "linear:"; then
  pass "├─" "Subscriber active (control_logic -> /gazebo/cmd_vel)"
else
  fail "├─" "control_logic did not republish to /gazebo/cmd_vel"
fi

# Gazebo receives commands: a ROS subscriber on /gazebo/cmd_vel (the bridge).
if ros_cli "rostopic info /gazebo/cmd_vel" | grep -qi "Subscribers:" \
   && ros_cli "rostopic info /gazebo/cmd_vel" | grep -qi "gazebo\|bridge"; then
  pass "└─" "Gazebo subscribed to /gazebo/cmd_vel"
else
  warn "└─" "No Gazebo subscriber on /gazebo/cmd_vel" \
    "needs the gazebo container + ros_gz_bridge"
fi

# ============================================================================ #
# Test 3: E2E latency measurement.
# ============================================================================ #
section "Test 3: Latency Measurement"

# Measure /cmd_vel -> /gazebo/cmd_vel propagation (keyboard publish rate +
# control_logic processing). Run a probe node inside control_logic.
LAT_OUT="$(dc exec -T control_logic bash -lc \
  "source /opt/ros/noetic/setup.bash && python3 -" <<'PY' 2>/dev/null
import time
import rospy
from geometry_msgs.msg import Twist

rospy.init_node("latency_probe", anonymous=True, disable_signals=True)
state = {}

def cb(msg):
    if abs(msg.linear.x) > 1e-3 and "t1" not in state:
        state["t1"] = time.time()

rospy.Subscriber("/gazebo/cmd_vel", Twist, cb)
pub = rospy.Publisher("/cmd_vel", Twist, queue_size=1)
time.sleep(1.0)  # let connections establish

m = Twist()
m.linear.x = 1.0
state["t0"] = time.time()
rate = rospy.Rate(50)
deadline = time.time() + 5.0
while time.time() < deadline and "t1" not in state:
    pub.publish(m)
    rate.sleep()

if "t1" in state:
    print("LATENCY_MS=%.1f" % ((state["t1"] - state["t0"]) * 1000.0))
else:
    print("LATENCY_MS=TIMEOUT")
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
