#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Control Logic node.

Subscribes to ``/cmd_vel`` (raw user commands), applies safety constraints
and smoothing, then republishes the result to ``/gazebo/cmd_vel`` for the
simulator.

Constraint pipeline (applied in order):
  1. Velocity limits   - clip linear/angular speed to a max.
  2. Acceleration limits - cap the per-cycle change (rate limiting).
  3. Exponential smoothing - low-pass filter (alpha) to remove jitter.
  4. Safety rules      - emergency stop / contact-triggered stop (optional).

Designed to run at a fixed control rate (20 Hz) with < 10 ms processing
latency per cycle.
"""

import argparse
import sys
import time

try:
    import rospy
    from geometry_msgs.msg import Twist
    from std_msgs.msg import Bool
    _ROS_AVAILABLE = True
except ImportError:  # pragma: no cover - allows --dry-run without a ROS install
    _ROS_AVAILABLE = False


# --------------------------------------------------------------------------- #
# Constants / defaults
# --------------------------------------------------------------------------- #
NODE_NAME = "control_logic"
INPUT_TOPIC = "/cmd_vel"
OUTPUT_TOPIC = "/gazebo/cmd_vel"
ESTOP_TOPIC = "/emergency_stop"
CONTACT_TOPIC = "/contact"
CONTROL_RATE_HZ = 20.0

DEFAULT_MAX_LINEAR_SPEED = 2.0    # m/s
DEFAULT_MAX_ANGULAR_SPEED = 2.0   # rad/s
DEFAULT_MAX_ACCEL = 1.0           # m/s^2
DEFAULT_MAX_ANGULAR_ACCEL = 1.0   # rad/s^2
DEFAULT_ALPHA = 0.3               # exponential filter coefficient (0..1)


def clip(value, limit):
    """Clip ``value`` to the symmetric range [-limit, +limit]."""
    if value > limit:
        return limit
    if value < -limit:
        return -limit
    return value


def rate_limit(target, current, max_delta):
    """Limit the change from ``current`` toward ``target`` to ``max_delta``."""
    delta = target - current
    if delta > max_delta:
        return current + max_delta
    if delta < -max_delta:
        return current - max_delta
    return target


class ControlLogic:
    """Apply safety constraints and smoothing to velocity commands."""

    def __init__(self,
                 max_linear_speed=DEFAULT_MAX_LINEAR_SPEED,
                 max_angular_speed=DEFAULT_MAX_ANGULAR_SPEED,
                 max_accel=DEFAULT_MAX_ACCEL,
                 max_angular_accel=DEFAULT_MAX_ANGULAR_ACCEL,
                 alpha=DEFAULT_ALPHA,
                 control_rate_hz=CONTROL_RATE_HZ,
                 enable_contact_stop=True,
                 dry_run=False):
        self.enable_contact_stop = enable_contact_stop
        self.max_linear_speed = max_linear_speed
        self.max_angular_speed = max_angular_speed
        self.max_accel = max_accel
        self.max_angular_accel = max_angular_accel
        self.alpha = alpha
        self.control_rate_hz = control_rate_hz
        self.dt = 1.0 / control_rate_hz
        self.dry_run = dry_run or not _ROS_AVAILABLE

        # Most recent raw command (target) received from /cmd_vel.
        self.target_linear = 0.0
        self.target_angular = 0.0

        # Current (published) state - what the robot is actually commanded.
        self.cur_linear = 0.0
        self.cur_angular = 0.0

        # Filtered state for exponential smoothing.
        self.filt_linear = 0.0
        self.filt_angular = 0.0

        # Safety
        self.emergency_stop = False
        self.contact = False

        # Diagnostics
        self.last_proc_ms = 0.0

        # Log throttling: category -> last-logged monotonic time (seconds).
        # Prevents per-cycle log spam (which would also threaten the latency
        # budget) during continuous ramping / clipping.
        self._log_interval = 1.0
        self._last_log = {}

        self.pub = None
        self.sub = None
        self.estop_sub = None
        self.contact_sub = None

        if not self.dry_run:
            rospy.init_node(NODE_NAME, anonymous=False)
            self.pub = rospy.Publisher(OUTPUT_TOPIC, Twist, queue_size=10)
            self.sub = rospy.Subscriber(INPUT_TOPIC, Twist,
                                        self.cmd_vel_callback, queue_size=10)
            self.estop_sub = rospy.Subscriber(ESTOP_TOPIC, Bool,
                                              self.estop_callback, queue_size=1)
            if self.enable_contact_stop:
                self.contact_sub = rospy.Subscriber(
                    CONTACT_TOPIC, Bool, self.contact_callback, queue_size=1)
            rospy.loginfo("control_logic started: %s -> %s @ %.0f Hz "
                          "(contact_stop=%s)",
                          INPUT_TOPIC, OUTPUT_TOPIC, control_rate_hz,
                          self.enable_contact_stop)
        else:
            print("⚠️  control_logic running in dry-run mode (no ROS).")

    # ------------------------------------------------------------------ #
    # Callbacks
    # ------------------------------------------------------------------ #
    def cmd_vel_callback(self, msg):
        """Store the latest raw command as the control target."""
        self.target_linear = msg.linear.x
        self.target_angular = msg.angular.z

    def estop_callback(self, msg):
        """Toggle the emergency-stop flag."""
        engaged = bool(msg.data)
        if engaged and not self.emergency_stop:
            print("🛑 EMERGENCY STOP engaged.")
        elif not engaged and self.emergency_stop:
            print("✅ Emergency stop released.")
        self.emergency_stop = engaged

    def contact_callback(self, msg):
        """Force a stop while a contact/collision is reported (optional)."""
        touching = bool(msg.data)
        if touching and not self.contact:
            print("🚧 Contact detected -> forcing stop.")
        elif not touching and self.contact:
            print("✅ Contact cleared.")
        self.contact = touching

    def _log_throttled(self, category, message):
        """Print ``message`` at most once per ``_log_interval`` per category."""
        now = time.monotonic()
        last = self._last_log.get(category, 0.0)
        if now - last >= self._log_interval:
            self._last_log[category] = now
            print(message)

    # ------------------------------------------------------------------ #
    # Constraint pipeline
    # ------------------------------------------------------------------ #
    def process(self, target_linear, target_angular):
        """Run the full constraint pipeline for one control cycle.

        Returns the (linear, angular) command to publish and mutates the
        controller's current/filtered state. Pure enough to unit-test:
        feed targets, inspect the returned values.
        """
        start = time.perf_counter()

        # --- Safety: emergency stop / contact override everything ------- #
        if self.emergency_stop or (self.enable_contact_stop and self.contact):
            target_linear = 0.0
            target_angular = 0.0

        # --- 1. Velocity limits (clip) ---------------------------------- #
        cmd_linear = clip(target_linear, self.max_linear_speed)
        cmd_angular = clip(target_angular, self.max_angular_speed)
        # Velocity clipping is a genuine constraint violation (upstream sent an
        # out-of-range command), so warn - throttled to avoid log spam.
        if cmd_linear != target_linear:
            self._log_throttled(
                "clip_linear",
                "⚠️  linear.x %.2f exceeds max %.2f -> clipped"
                % (target_linear, self.max_linear_speed))
        if cmd_angular != target_angular:
            self._log_throttled(
                "clip_angular",
                "⚠️  angular.z %.2f exceeds max %.2f -> clipped"
                % (target_angular, self.max_angular_speed))

        # --- 2. Acceleration limits (rate limit per cycle) -------------- #
        # Rate limiting is normal smoothing behavior (fires on every ramp), so
        # it is not logged per-cycle.
        max_dv = self.max_accel * self.dt
        max_dw = self.max_angular_accel * self.dt
        rl_linear = rate_limit(cmd_linear, self.cur_linear, max_dv)
        rl_angular = rate_limit(cmd_angular, self.cur_angular, max_dw)

        # --- 3. Exponential smoothing (low-pass) ------------------------ #
        self.filt_linear = (self.alpha * rl_linear
                            + (1.0 - self.alpha) * self.filt_linear)
        self.filt_angular = (self.alpha * rl_angular
                             + (1.0 - self.alpha) * self.filt_angular)

        # Re-clip after filtering for safety (filter can't add energy, but be
        # defensive against parameter mistakes).
        out_linear = clip(self.filt_linear, self.max_linear_speed)
        out_angular = clip(self.filt_angular, self.max_angular_speed)

        # Commit current state.
        self.cur_linear = out_linear
        self.cur_angular = out_angular

        self.last_proc_ms = (time.perf_counter() - start) * 1000.0
        return out_linear, out_angular

    # ------------------------------------------------------------------ #
    # Publish
    # ------------------------------------------------------------------ #
    def _publish(self, linear, angular):
        if self.dry_run:
            return
        twist = Twist()
        twist.linear.x = linear
        twist.linear.y = 0.0
        twist.linear.z = 0.0
        twist.angular.x = 0.0
        twist.angular.y = 0.0
        twist.angular.z = angular
        self.pub.publish(twist)

    # ------------------------------------------------------------------ #
    # Control loop
    # ------------------------------------------------------------------ #
    def _publish_stop(self):
        """Publish a single zero-velocity command so the robot halts."""
        self.cur_linear = 0.0
        self.cur_angular = 0.0
        self.filt_linear = 0.0
        self.filt_angular = 0.0
        self._publish(0.0, 0.0)

    def spin(self):
        if self.dry_run:
            print("⚠️  dry-run: control loop not started "
                  "(use process() directly for testing).")
            return
        # Halt the robot on shutdown so it does not keep moving with the last
        # commanded velocity after the node dies (Ctrl+C / signal_shutdown).
        rospy.on_shutdown(self._publish_stop)
        rate = rospy.Rate(self.control_rate_hz)
        while not rospy.is_shutdown():
            linear, angular = self.process(self.target_linear,
                                           self.target_angular)
            self._publish(linear, angular)
            if self.last_proc_ms > 10.0:
                self._log_throttled(
                    "latency",
                    "⚠️  control_logic processing %.2f ms > 10 ms budget"
                    % self.last_proc_ms)
            rate.sleep()


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="Control Logic safety node")
    parser.add_argument("--max-linear", type=float,
                        default=DEFAULT_MAX_LINEAR_SPEED)
    parser.add_argument("--max-angular", type=float,
                        default=DEFAULT_MAX_ANGULAR_SPEED)
    parser.add_argument("--max-accel", type=float, default=DEFAULT_MAX_ACCEL)
    parser.add_argument("--max-angular-accel", type=float,
                        default=DEFAULT_MAX_ANGULAR_ACCEL)
    parser.add_argument("--alpha", type=float, default=DEFAULT_ALPHA)
    parser.add_argument("--rate", type=float, default=CONTROL_RATE_HZ)
    parser.add_argument("--no-contact-stop", action="store_true",
                        help="Disable the optional contact-triggered stop.")
    parser.add_argument("--dry-run", action="store_true",
                        help="Run without ROS (for local testing).")
    args, _unknown = parser.parse_known_args(argv)
    return args


def main(argv=None):
    args = parse_args(argv)
    node = ControlLogic(
        max_linear_speed=args.max_linear,
        max_angular_speed=args.max_angular,
        max_accel=args.max_accel,
        max_angular_accel=args.max_angular_accel,
        alpha=args.alpha,
        control_rate_hz=args.rate,
        enable_contact_stop=not args.no_contact_stop,
        dry_run=args.dry_run,
    )
    node.spin()


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # pragma: no cover - top-level guard
        print("❌ fatal: %s" % exc, file=sys.stderr)
        sys.exit(1)
