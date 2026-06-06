#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Keyboard Controller node (ROS 2 / rclpy).

Reads keyboard input via pynput (cross-platform: macOS / Windows / Linux),
converts it into geometry_msgs/msg/Twist messages and publishes them to the
``/cmd_vel`` topic at a fixed rate (20 Hz).

Also supports replaying a recorded scenario from a JSON file so that demos
are 100% reproducible.

Key bindings
------------
W / Up      : linear.x  = +max_speed
S / Down    : linear.x  = -max_speed
A / Left    : angular.z = +max_speed  (left / CCW, REP-103)
D / Right   : angular.z = -max_speed  (right / CW)
+ / =       : max_speed *= 1.1  (speed scale up)
- / _       : max_speed /= 1.1  (speed scale down)
SPACE       : play the JSON scenario file
R           : reset velocities (linear.x = 0, angular.z = 0)
Q / ESC     : quit
"""

import argparse
import json
import os
import sys
import threading
import time

# rclpy is imported lazily/guarded so the scenario + publish logic stays
# importable and testable on headless machines / CI without a ROS 2 install.
try:
    import rclpy
    from rclpy.node import Node
    from geometry_msgs.msg import Twist
    _ROS_AVAILABLE = True
except ImportError:  # pragma: no cover - allows --dry-run without ROS 2
    _ROS_AVAILABLE = False

# pynput requires a display backend (X/Quartz/Win32). Import it lazily too; the
# listener is only needed for live key capture in the pynput mode.
try:
    from pynput import keyboard
    _PYNPUT_AVAILABLE = True
except Exception:  # ImportError or backend (Xlib) errors when headless
    keyboard = None
    _PYNPUT_AVAILABLE = False

# termios + select powers a stdin-based listener that does NOT need a display.
# This is what makes interactive keyboard control work inside a headless
# container (docker compose run -it) where pynput cannot attach. Windows ships
# msvcrt instead, which is handled below.
try:
    import select as _select
    import termios as _termios
    import tty as _tty
    _TERMIOS_AVAILABLE = True
except ImportError:  # Windows
    _termios = None
    _TERMIOS_AVAILABLE = False
try:
    import msvcrt as _msvcrt  # Windows
    _MSVCRT_AVAILABLE = True
except ImportError:
    _msvcrt = None
    _MSVCRT_AVAILABLE = False


# --------------------------------------------------------------------------- #
# Constants
# --------------------------------------------------------------------------- #
NODE_NAME = "keyboard_controller"
TOPIC_NAME = "/cmd_vel"
PUBLISH_RATE_HZ = 20.0
DEFAULT_MAX_SPEED = 2.0
SPEED_SCALE_FACTOR = 1.1

# stdin mode: how long after the last keypress the movement direction is
# considered "released". Without OS-level key-up events, releases are inferred
# from the absence of repeats. 0.25 s comfortably covers typical key repeat
# rates (~30 Hz) while keeping the robot responsive to letting go of a key.
STDIN_KEY_HOLD_SECONDS = 0.25


class KeyboardController:
    """Translate keyboard events into Twist messages and publish them."""

    def __init__(self, scenario_path=None, auto=False,
                 ignore_keys_during_scenario=True, dry_run=False,
                 input_mode="auto"):
        self.scenario_path = scenario_path
        self.auto = auto
        self.ignore_keys_during_scenario = ignore_keys_during_scenario
        self.dry_run = dry_run or not _ROS_AVAILABLE
        # "auto" -> stdin if a tty is attached (containers / SSH), else pynput
        # if available (native desktop with a display), else no live input.
        self.input_mode = input_mode

        # Velocity state
        self.max_speed = DEFAULT_MAX_SPEED
        self.linear_x = 0.0
        self.angular_z = 0.0

        # Set of currently pressed "movement" keys for simultaneous input.
        self.pressed_keys = set()
        self._state_lock = threading.Lock()
        # In stdin mode there are no OS-level release events; track the last
        # time each direction key was seen so a timeout can release it.
        self._stdin_last_press = {}

        # Scenario playback state
        self.scenario_running = False
        self._scenario_thread = None

        # Shutdown flag (also used in dry-run where rclpy is unavailable).
        self._shutdown_requested = False

        # ROS node / publisher / input
        self.node = None
        self.pub = None
        self.listener = None
        self._stdin_thread = None
        self._stdin_release_thread = None
        self._termios_saved = None

        if not self.dry_run:
            if not rclpy.ok():
                rclpy.init()
            self.node = Node(NODE_NAME)
            self.pub = self.node.create_publisher(Twist, TOPIC_NAME, 10)
            self.node.get_logger().info(
                "keyboard_controller node started, publishing to %s"
                % TOPIC_NAME)
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

    # ------------------------------------------------------------------ #
    # stdin (termios) listener - works in headless containers, over SSH,
    # and on any TTY. No display backend required.
    # ------------------------------------------------------------------ #
    @staticmethod
    def _stdin_direction(token):
        """Map a stdin token (single char or arrow escape) to a direction."""
        if token in ("w", "W", "UP"):
            return "forward"
        if token in ("s", "S", "DOWN"):
            return "backward"
        if token in ("a", "A", "LEFT"):
            return "left"
        if token in ("d", "D", "RIGHT"):
            return "right"
        return None

    def _stdin_read_token(self):
        """Read one logical key token from stdin (handles ANSI arrows).

        Returns the token string or None on EOF / no data within the poll
        timeout. The function blocks at most ~50 ms so the loop can check
        ``_shutdown_requested`` regularly.
        """
        if not _TERMIOS_AVAILABLE:
            # Windows path: msvcrt.getwch handles single chars and arrows
            # (which arrive as 0x00/0xE0 prefix + code).
            if _MSVCRT_AVAILABLE and _msvcrt.kbhit():
                ch = _msvcrt.getwch()
                if ch in ("\x00", "\xe0"):
                    code = _msvcrt.getwch()
                    return {"H": "UP", "P": "DOWN",
                            "K": "LEFT", "M": "RIGHT"}.get(code)
                return ch
            time.sleep(0.05)
            return None

        # POSIX: poll stdin with select; expand CSI arrow sequences.
        r, _, _ = _select.select([sys.stdin], [], [], 0.05)
        if not r:
            return None
        ch = sys.stdin.read(1)
        if ch == "":
            return None
        if ch != "\x1b":
            return ch
        # Possible ESC sequence; read up to 2 more chars without blocking.
        seq = ""
        for _i in range(2):
            r2, _, _ = _select.select([sys.stdin], [], [], 0.01)
            if not r2:
                break
            seq += sys.stdin.read(1)
        if seq == "[A":
            return "UP"
        if seq == "[B":
            return "DOWN"
        if seq == "[D":
            return "LEFT"
        if seq == "[C":
            return "RIGHT"
        # Bare ESC (no follow-up) = quit, matching pynput's Key.esc behavior.
        if seq == "":
            return "ESC"
        return None

    def _stdin_handle_token(self, token):
        """Route a token through the same state machine as pynput callbacks."""
        if token is None:
            return True  # keep listening

        # Quit keys are honored even during scenario playback.
        if token in ("q", "Q", "ESC", "\x03"):  # ^C also quits
            self._shutdown()
            return False

        if self.scenario_running and self.ignore_keys_during_scenario:
            return True

        with self._state_lock:
            direction = self._stdin_direction(token)
            if direction is not None:
                self._stdin_last_press[direction] = time.monotonic()
                if direction not in self.pressed_keys:
                    self.pressed_keys.add(direction)
                    self._recompute_velocity()
                    self._print_state()
                return True

            if token in ("+", "="):
                self.max_speed *= SPEED_SCALE_FACTOR
                self._recompute_velocity()
                print("⬆️  speed up | max_speed=%.2f" % self.max_speed)
                return True
            if token in ("-", "_"):
                self.max_speed /= SPEED_SCALE_FACTOR
                self._recompute_velocity()
                print("⬇️  speed down | max_speed=%.2f" % self.max_speed)
                return True
            if token in ("r", "R"):
                self._reset()
                return True
            if token == " ":
                self._start_scenario()
                return True
        return True

    def _stdin_listen_loop(self):
        """Background thread: read stdin and dispatch tokens."""
        if _TERMIOS_AVAILABLE and sys.stdin.isatty():
            fd = sys.stdin.fileno()
            try:
                self._termios_saved = _termios.tcgetattr(fd)
                _tty.setcbreak(fd)
            except Exception as exc:
                print("❌ failed to set cbreak on stdin: %s" % exc)
                return
        try:
            while not self._shutdown_requested:
                token = self._stdin_read_token()
                if not self._stdin_handle_token(token):
                    return
        finally:
            if self._termios_saved is not None:
                try:
                    _termios.tcsetattr(sys.stdin.fileno(),
                                       _termios.TCSADRAIN,
                                       self._termios_saved)
                except Exception:
                    pass
                self._termios_saved = None

    def _stdin_release_loop(self):
        """Background thread: simulate key releases via timeout.

        stdin has no key-up events, so a direction key is considered released
        STDIN_KEY_HOLD_SECONDS after the last keypress. Key repeat keeps a
        held key alive; letting go stops the robot.
        """
        period = STDIN_KEY_HOLD_SECONDS / 4.0
        while not self._shutdown_requested:
            time.sleep(period)
            now = time.monotonic()
            released = []
            with self._state_lock:
                for direction in list(self.pressed_keys):
                    last = self._stdin_last_press.get(direction, 0.0)
                    if now - last > STDIN_KEY_HOLD_SECONDS:
                        self.pressed_keys.discard(direction)
                        released.append(direction)
                if released:
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
        return (not self.dry_run) and (not rclpy.ok())

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
        while rclpy.ok() and not self._shutdown_requested:
            self.pub.publish(self._make_twist())
            # No subscriptions, but spin briefly to service node internals.
            rclpy.spin_once(self.node, timeout_sec=0.0)
            time.sleep(period)
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
            try:
                if self.node is not None:
                    self.node.destroy_node()
                if rclpy.ok():
                    rclpy.shutdown()
            except Exception:
                pass

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

        mode = self._resolve_input_mode()
        if mode == "stdin":
            print("⌨️  Input: stdin (TTY). Hold a key to drive; release to stop.")
            self._stdin_thread = threading.Thread(
                target=self._stdin_listen_loop, daemon=True)
            self._stdin_release_thread = threading.Thread(
                target=self._stdin_release_loop, daemon=True)
            self._stdin_thread.start()
            self._stdin_release_thread.start()
        elif mode == "pynput":
            print("⌨️  Input: pynput (global key capture).")
            self.listener = keyboard.Listener(
                on_press=self.on_press, on_release=self.on_release)
            self.listener.start()
        elif self.auto:
            print("⚠️  No live input available; running scenario only.")
        else:
            print("❌ No live input available. Re-run with a TTY attached "
                  "(docker compose run -it ...) for stdin mode, or use "
                  "--auto with --scenario for headless playback.")
            return

        try:
            self.publish_loop()
        except KeyboardInterrupt:
            pass
        finally:
            self._shutdown()
            if self.listener is not None:
                self.listener.stop()

    def _resolve_input_mode(self):
        """Pick the live-input backend based on --input and what's available.

        Priority for "auto":
          1. stdin   - works inside containers / SSH / any TTY, no display.
          2. pynput  - global key capture on a desktop with a display.
          3. none    - headless without a TTY (auto-scenario only).
        """
        forced = self.input_mode
        stdin_ok = (
            (_TERMIOS_AVAILABLE or _MSVCRT_AVAILABLE)
            and sys.stdin.isatty()
        )
        if forced == "stdin":
            if not stdin_ok:
                print("⚠️  --input stdin requested but no TTY on stdin.")
                return "none"
            return "stdin"
        if forced == "pynput":
            if not _PYNPUT_AVAILABLE:
                print("⚠️  --input pynput requested but backend unavailable.")
                return "none"
            return "pynput"
        # auto
        if stdin_ok:
            return "stdin"
        if _PYNPUT_AVAILABLE:
            return "pynput"
        return "none"


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description="Keyboard -> ROS 2 Twist controller")
    parser.add_argument("--scenario", metavar="FILE",
                        help="Path to a JSON scenario file to replay.")
    parser.add_argument("--auto", action="store_true",
                        help="Auto-play the scenario on startup, then exit.")
    parser.add_argument("--allow-keys-during-scenario", action="store_true",
                        help="Do not ignore manual keys while a scenario runs.")
    parser.add_argument("--input", choices=("auto", "stdin", "pynput"),
                        default="auto",
                        help="Live input backend (default: auto - stdin if a "
                             "TTY is attached, else pynput if available).")
    parser.add_argument("--dry-run", action="store_true",
                        help="Run without ROS (for local testing).")
    # Strip ROS 2 args (--ros-args ...) before argparse when rclpy is present,
    # then warn about any other unknown args so typos are not silently dropped.
    if argv is None:
        argv = sys.argv[1:]
    if _ROS_AVAILABLE:
        from rclpy.utilities import remove_ros_args
        argv = remove_ros_args(args=["prog"] + list(argv))[1:]
    args, unknown = parser.parse_known_args(argv)
    if unknown:
        print("⚠️  Ignoring unknown argument(s): %s" % " ".join(unknown))
    return args


def main(argv=None):
    args = parse_args(argv)
    controller = KeyboardController(
        scenario_path=args.scenario,
        auto=args.auto,
        ignore_keys_during_scenario=not args.allow_keys_during_scenario,
        input_mode=args.input,
        dry_run=args.dry_run,
    )
    controller.run()


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # pragma: no cover - top-level guard
        print("❌ fatal: %s" % exc, file=sys.stderr)
        sys.exit(1)
