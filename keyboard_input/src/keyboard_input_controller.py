#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Keyboard Controller node.

Reads keyboard input via pynput (cross-platform: macOS / Windows / Linux),
converts it into geometry_msgs/Twist messages and publishes them to the
``/cmd_vel`` topic at a fixed rate (20 Hz).

Also supports replaying a recorded scenario from a JSON file so that demos
are 100% reproducible.

Key bindings
------------
W / Up      : linear.x  = +max_speed
S / Down    : linear.x  = -max_speed
A / Left    : angular.z = +max_speed
D / Right   : angular.z = -max_speed
+ / =       : max_speed *= 1.1  (speed scale up)
- / _       : max_speed /= 1.1  (speed scale down)
SPACE       : play the JSON scenario file
R           : reset velocities (linear.x = 0, angular.z = 0)
Q / ESC     : quit (rospy.signal_shutdown)
"""

import argparse
import json
import os
import sys
import threading
import time

try:
    import rospy
    from geometry_msgs.msg import Twist
    _ROS_AVAILABLE = True
except ImportError:  # pragma: no cover - allows --dry-run without a ROS install
    _ROS_AVAILABLE = False

# pynput requires a display backend (X/Quartz/Win32). Import it lazily so that
# scenario parsing / publishing logic remains importable and testable on
# headless machines and CI. The listener is only needed for live key capture.
try:
    from pynput import keyboard
    _PYNPUT_AVAILABLE = True
except Exception:  # ImportError or backend (Xlib) errors when headless
    keyboard = None
    _PYNPUT_AVAILABLE = False


# --------------------------------------------------------------------------- #
# Constants
# --------------------------------------------------------------------------- #
NODE_NAME = "keyboard_controller"
TOPIC_NAME = "/cmd_vel"
PUBLISH_RATE_HZ = 20.0
DEFAULT_MAX_SPEED = 2.0
SPEED_SCALE_FACTOR = 1.1


class KeyboardController:
    """Translate keyboard events into Twist messages and publish them."""

    def __init__(self, scenario_path=None, auto=False,
                 ignore_keys_during_scenario=True, dry_run=False):
        self.scenario_path = scenario_path
        self.auto = auto
        self.ignore_keys_during_scenario = ignore_keys_during_scenario
        self.dry_run = dry_run or not _ROS_AVAILABLE

        # Velocity state
        self.max_speed = DEFAULT_MAX_SPEED
        self.linear_x = 0.0
        self.angular_z = 0.0

        # Set of currently pressed "movement" keys for simultaneous input.
        self.pressed_keys = set()
        self._state_lock = threading.Lock()

        # Scenario playback state
        self.scenario_running = False
        self._scenario_thread = None

        # Shutdown flag (used in dry-run where rospy is unavailable).
        self._shutdown_requested = False

        # ROS publisher
        self.pub = None
        self.listener = None

        if not self.dry_run:
            rospy.init_node(NODE_NAME, anonymous=False)
            self.pub = rospy.Publisher(TOPIC_NAME, Twist, queue_size=10)
            rospy.loginfo("keyboard_controller node started, publishing to %s",
                          TOPIC_NAME)
        else:
            print("⚠️  Running in dry-run mode (no ROS publisher).")

    # ------------------------------------------------------------------ #
    # Velocity helpers
    # ------------------------------------------------------------------ #
    def _recompute_velocity(self):
        """Recompute linear/angular velocity from the set of pressed keys."""
        linear = 0.0
        angular = 0.0
        if "forward" in self.pressed_keys:
            linear += self.max_speed
        if "backward" in self.pressed_keys:
            linear -= self.max_speed
        if "left" in self.pressed_keys:
            angular += self.max_speed
        if "right" in self.pressed_keys:
            angular -= self.max_speed

        self.linear_x = linear
        self.angular_z = angular

    def _reset(self):
        self.pressed_keys.clear()
        self.linear_x = 0.0
        self.angular_z = 0.0
        print("🔄 reset | linear.x=0.00 angular.z=0.00")

    def _print_state(self):
        print("📊 linear.x=%.2f angular.z=%.2f (max_speed=%.2f)"
              % (self.linear_x, self.angular_z, self.max_speed))

    # ------------------------------------------------------------------ #
    # Key mapping
    # ------------------------------------------------------------------ #
    @staticmethod
    def _key_to_char(key):
        """Return a normalized lowercase character for a pynput key, or None."""
        if isinstance(key, keyboard.KeyCode) and key.char is not None:
            return key.char.lower()
        return None

    def _direction_for_key(self, key):
        """Map a key to a movement direction name, or None if not a move key."""
        char = self._key_to_char(key)
        if char == "w":
            return "forward"
        if char == "s":
            return "backward"
        if char == "a":
            return "left"
        if char == "d":
            return "right"
        if key == keyboard.Key.up:
            return "forward"
        if key == keyboard.Key.down:
            return "backward"
        if key == keyboard.Key.left:
            return "left"
        if key == keyboard.Key.right:
            return "right"
        return None

    # ------------------------------------------------------------------ #
    # pynput callbacks
    # ------------------------------------------------------------------ #
    def on_press(self, key):
        try:
            # During scenario playback, optionally ignore manual movement keys.
            if self.scenario_running and self.ignore_keys_during_scenario:
                # Still allow quit keys to work.
                if not self._is_quit_key(key):
                    return

            with self._state_lock:
                direction = self._direction_for_key(key)
                if direction is not None:
                    if direction not in self.pressed_keys:
                        self.pressed_keys.add(direction)
                        self._recompute_velocity()
                        self._print_state()
                    return

                char = self._key_to_char(key)

                # Speed scaling
                if char in ("+", "="):
                    self.max_speed *= SPEED_SCALE_FACTOR
                    self._recompute_velocity()
                    print("⬆️  speed up | max_speed=%.2f" % self.max_speed)
                    return
                if char in ("-", "_"):
                    self.max_speed /= SPEED_SCALE_FACTOR
                    self._recompute_velocity()
                    print("⬇️  speed down | max_speed=%.2f" % self.max_speed)
                    return

                # Reset
                if char == "r":
                    self._reset()
                    return

                # Scenario playback
                if key == keyboard.Key.space:
                    self._start_scenario()
                    return

                # Quit
                if self._is_quit_key(key):
                    self._shutdown()
                    return False  # stop the listener
        except Exception as exc:  # pragma: no cover - defensive
            print("❌ on_press error: %s" % exc)
        return None

    def on_release(self, key):
        if self.scenario_running and self.ignore_keys_during_scenario:
            return
        with self._state_lock:
            direction = self._direction_for_key(key)
            if direction is not None and direction in self.pressed_keys:
                self.pressed_keys.discard(direction)
                self._recompute_velocity()
                self._print_state()

    @staticmethod
    def _is_quit_key(key):
        if key == keyboard.Key.esc:
            return True
        if isinstance(key, keyboard.KeyCode) and key.char is not None:
            return key.char.lower() == "q"
        return False

    # ------------------------------------------------------------------ #
    # Scenario playback
    # ------------------------------------------------------------------ #
    def _load_scenario(self):
        if not self.scenario_path:
            print("❌ No scenario file specified (use --scenario <file.json>).")
            return None
        if not os.path.isfile(self.scenario_path):
            print("❌ Scenario file not found: %s" % self.scenario_path)
            return None
        try:
            with open(self.scenario_path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, json.JSONDecodeError) as exc:
            print("❌ Failed to load scenario: %s" % exc)
            return None

        if "commands" not in data or not isinstance(data["commands"], list):
            print("❌ Invalid scenario: missing 'commands' list.")
            return None
        return data

    def _start_scenario(self):
        if self.scenario_running:
            print("⚠️  Scenario already running.")
            return
        scenario = self._load_scenario()
        if scenario is None:
            return
        self._scenario_thread = threading.Thread(
            target=self._run_scenario, args=(scenario,), daemon=True)
        self._scenario_thread.start()

    def _run_scenario(self, scenario):
        """Replay scenario commands ordered by timestamp."""
        self.scenario_running = True
        name = scenario.get("name", "scenario")
        print("▶️  Playing scenario: %s" % name)
        if scenario.get("description"):
            print("   %s" % scenario["description"])

        commands = sorted(scenario["commands"],
                          key=lambda c: c.get("timestamp", 0.0))
        start_time = time.time()
        try:
            for cmd in commands:
                if self._stopping():
                    break
                ts = float(cmd.get("timestamp", 0.0))
                # Wait until the scheduled timestamp.
                while time.time() - start_time < ts:
                    if self._stopping():
                        break
                    time.sleep(0.005)
                if self._stopping():
                    break

                linear = cmd.get("linear", {})
                angular = cmd.get("angular", {})
                with self._state_lock:
                    self.linear_x = float(linear.get("x", 0.0))
                    self.angular_z = float(angular.get("z", 0.0))
                desc = cmd.get("description", "")
                print("[%.2fs] %s | linear.x=%.2f angular.z=%.2f"
                      % (ts, desc, self.linear_x, self.angular_z))

            # Honor duration_seconds: hold final state until end, then stop.
            duration = scenario.get("duration_seconds")
            if duration is not None and not self._stopping():
                while time.time() - start_time < float(duration):
                    if self._stopping():
                        break
                    time.sleep(0.01)
        finally:
            # Auto-stop at completion.
            with self._state_lock:
                self.linear_x = 0.0
                self.angular_z = 0.0
            self.scenario_running = False
            print("⏹️  Scenario complete. Robot stopped.")
            if self.auto:
                self._shutdown()

    def _stopping(self):
        if self._shutdown_requested:
            return True
        return (not self.dry_run) and rospy.is_shutdown()

    # ------------------------------------------------------------------ #
    # Publish loop
    # ------------------------------------------------------------------ #
    def _make_twist(self):
        twist = Twist()
        with self._state_lock:
            twist.linear.x = self.linear_x
            twist.linear.y = 0.0
            twist.linear.z = 0.0
            twist.angular.x = 0.0
            twist.angular.y = 0.0
            twist.angular.z = self.angular_z
        return twist

    def publish_loop(self):
        """Publish Twist messages at PUBLISH_RATE_HZ until shutdown."""
        period = 1.0 / PUBLISH_RATE_HZ
        if self.dry_run:
            # Faithfully idle at the publish rate so scenario threads run to
            # completion during headless/local testing.
            while not self._shutdown_requested:
                time.sleep(period)
            return
        rate = rospy.Rate(PUBLISH_RATE_HZ)
        while not rospy.is_shutdown():
            self.pub.publish(self._make_twist())
            rate.sleep()
        # Send a final zero command so the robot stops instead of coasting on
        # the last velocity after the node shuts down.
        with self._state_lock:
            self.linear_x = 0.0
            self.angular_z = 0.0
        try:
            self.pub.publish(self._make_twist())
        except Exception:
            pass

    # ------------------------------------------------------------------ #
    # Lifecycle
    # ------------------------------------------------------------------ #
    def _shutdown(self):
        if self._shutdown_requested:
            return
        self._shutdown_requested = True
        print("👋 Shutting down keyboard_controller.")
        if self.listener is not None:
            self.listener.stop()
        if not self.dry_run:
            rospy.signal_shutdown("user requested shutdown")

    def run(self):
        print("=" * 60)
        print(" keyboard_controller  (publishing to %s @ %d Hz)"
              % (TOPIC_NAME, int(PUBLISH_RATE_HZ)))
        print("=" * 60)
        print(" W/↑ forward  S/↓ back  A/← left  D/→ right")
        print(" +/- speed scale   R reset   SPACE scenario   Q/ESC quit")
        print("=" * 60)

        # Auto-play scenario immediately if requested.
        if self.auto and self.scenario_path:
            self._start_scenario()

        # Start the keyboard listener in a background thread (live key capture).
        if _PYNPUT_AVAILABLE:
            self.listener = keyboard.Listener(
                on_press=self.on_press, on_release=self.on_release)
            self.listener.start()
        elif self.auto:
            # Headless auto-play: no listener needed, just publish + scenario.
            print("⚠️  pynput unavailable; running scenario without key capture.")
        else:
            print("❌ pynput is not available (no display backend). "
                  "Live keyboard control requires a display; use --auto with "
                  "--scenario for headless playback.")
            return

        try:
            self.publish_loop()
        except KeyboardInterrupt:
            pass
        finally:
            self._shutdown()
            if self.listener is not None:
                self.listener.stop()


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description="Keyboard -> ROS Twist controller")
    parser.add_argument("--scenario", metavar="FILE",
                        help="Path to a JSON scenario file to replay.")
    parser.add_argument("--auto", action="store_true",
                        help="Auto-play the scenario on startup, then exit.")
    parser.add_argument("--allow-keys-during-scenario", action="store_true",
                        help="Do not ignore manual keys while a scenario runs.")
    parser.add_argument("--dry-run", action="store_true",
                        help="Run without ROS (for local testing).")
    # rospy may inject __name/__log args; ignore unknowns.
    args, _unknown = parser.parse_known_args(argv)
    return args


def main(argv=None):
    args = parse_args(argv)
    controller = KeyboardController(
        scenario_path=args.scenario,
        auto=args.auto,
        ignore_keys_during_scenario=not args.allow_keys_during_scenario,
        dry_run=args.dry_run,
    )
    controller.run()


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # pragma: no cover - top-level guard
        print("❌ fatal: %s" % exc, file=sys.stderr)
        sys.exit(1)
