#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ROS Bridge node: TCP (JSON Lines) -> ROS 2 /cmd_vel.

Accepts newline-delimited JSON velocity commands over a TCP socket and
republishes them as ``geometry_msgs/msg/Twist`` on ``/cmd_vel`` (DDS). This
decouples the command source from ROS 2: the keyboard_controller today, a
SoftPLC (or any TCP-capable gateway) in the future. Anything that can open a
TCP socket and write a line of JSON can drive the robot.

Wire protocol
-------------
One JSON object per line, UTF-8, ``\\n``-terminated. Both fields are optional
and default to 0.0::

    {"linear_x": 1.0, "angular_z": 0.5}

A nested, Twist-like form is also accepted::

    {"linear": {"x": 1.0}, "angular": {"z": 0.5}}

Safety (watchdog)
-----------------
The node publishes at a fixed rate (20 Hz) from the most recent command. If no
valid command arrives within ``--watchdog`` seconds, it publishes zero velocity
so the robot halts when a client disconnects or stalls.
"""

import argparse
import json
import socket
import sys
import threading
import time

try:
    import rclpy
    from rclpy.node import Node
    from geometry_msgs.msg import Twist
    _ROS_AVAILABLE = True
except ImportError:  # pragma: no cover - allows --dry-run without ROS 2
    _ROS_AVAILABLE = False


# --------------------------------------------------------------------------- #
# Constants / defaults
# --------------------------------------------------------------------------- #
NODE_NAME = "ros_bridge"
OUTPUT_TOPIC = "/cmd_vel"
DEFAULT_PORT = 9090
DEFAULT_RATE_HZ = 20.0
DEFAULT_WATCHDOG_S = 0.5
RECV_BUFSIZE = 4096


def parse_command(line):
    """Parse one wire line into ``(linear_x, angular_z)`` or ``None``.

    Pure function (no I/O, no ROS) so it can be unit-tested headlessly. Returns
    ``None`` for blank lines, malformed JSON, non-objects, or non-numeric
    fields. Accepts both the flat (``linear_x``/``angular_z``) and the nested
    Twist-like (``linear.x``/``angular.z``) forms.
    """
    if isinstance(line, bytes):
        line = line.decode("utf-8", "replace")
    line = line.strip()
    if not line:
        return None
    try:
        data = json.loads(line)
    except (ValueError, TypeError):
        return None
    if not isinstance(data, dict):
        return None

    # Flat form: {"linear_x": .., "angular_z": ..}
    if "linear_x" in data or "angular_z" in data:
        try:
            return (float(data.get("linear_x", 0.0)),
                    float(data.get("angular_z", 0.0)))
        except (TypeError, ValueError):
            return None

    # Nested form: {"linear": {"x": ..}, "angular": {"z": ..}}
    lin = data.get("linear")
    ang = data.get("angular")
    if isinstance(lin, dict) or isinstance(ang, dict):
        try:
            return (float((lin or {}).get("x", 0.0)),
                    float((ang or {}).get("z", 0.0)))
        except (TypeError, ValueError):
            return None
    return None


class RosBridge:
    """TCP server that republishes JSON velocity commands as ROS 2 Twist."""

    def __init__(self, port=DEFAULT_PORT, rate_hz=DEFAULT_RATE_HZ,
                 watchdog_s=DEFAULT_WATCHDOG_S, dry_run=False):
        self.port = port
        self.rate_hz = rate_hz
        self.watchdog_s = watchdog_s
        self.dry_run = dry_run or not _ROS_AVAILABLE

        # Latest commanded target + when it was received (monotonic seconds).
        self._lock = threading.Lock()
        self.target_linear = 0.0
        self.target_angular = 0.0
        self.last_rx = 0.0

        self._shutdown = False
        self._server = None

        self.node = None
        self.pub = None
        if not self.dry_run:
            if not rclpy.ok():
                rclpy.init()
            self.node = Node(NODE_NAME)
            self.pub = self.node.create_publisher(Twist, OUTPUT_TOPIC, 10)
            self.node.get_logger().info(
                "ros_bridge: TCP :%d -> %s @ %.0f Hz (watchdog %.2fs)"
                % (self.port, OUTPUT_TOPIC, self.rate_hz, self.watchdog_s))
        else:
            print("⚠️  ros_bridge dry-run (TCP server only, no ROS publisher).")

    # ------------------------------------------------------------------ #
    # Command state
    # ------------------------------------------------------------------ #
    def _set_target(self, linear_x, angular_z):
        with self._lock:
            self.target_linear = linear_x
            self.target_angular = angular_z
            self.last_rx = time.monotonic()

    def _current_command(self):
        """Return the velocity to publish now, applying the watchdog."""
        now = time.monotonic()
        with self._lock:
            if now - self.last_rx > self.watchdog_s:
                return 0.0, 0.0
            return self.target_linear, self.target_angular

    # ------------------------------------------------------------------ #
    # TCP server
    # ------------------------------------------------------------------ #
    def _handle_client(self, conn, addr):
        print("🔌 client connected: %s:%d" % addr)
        buf = b""
        conn.settimeout(1.0)
        try:
            while not self._shutdown:
                try:
                    chunk = conn.recv(RECV_BUFSIZE)
                except socket.timeout:
                    continue
                except OSError:
                    break
                if not chunk:
                    break  # client closed
                buf += chunk
                # Process complete, newline-terminated lines.
                while b"\n" in buf:
                    line, buf = buf.split(b"\n", 1)
                    cmd = parse_command(line)
                    if cmd is not None:
                        self._set_target(cmd[0], cmd[1])
        finally:
            try:
                conn.close()
            except OSError:
                pass
            print("🔌 client disconnected: %s:%d" % addr)

    def _accept_loop(self):
        while not self._shutdown:
            try:
                conn, addr = self._server.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            threading.Thread(target=self._handle_client,
                             args=(conn, addr), daemon=True).start()

    def _start_server(self):
        self._server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._server.bind(("0.0.0.0", self.port))
        self._server.listen(5)
        self._server.settimeout(1.0)
        threading.Thread(target=self._accept_loop, daemon=True).start()
        print("🛰️  ros_bridge listening on TCP 0.0.0.0:%d" % self.port)

    # ------------------------------------------------------------------ #
    # Publish
    # ------------------------------------------------------------------ #
    def _publish_cycle(self):
        linear, angular = self._current_command()
        twist = Twist()
        twist.linear.x = linear
        twist.linear.y = 0.0
        twist.linear.z = 0.0
        twist.angular.x = 0.0
        twist.angular.y = 0.0
        twist.angular.z = angular
        self.pub.publish(twist)

    def serve(self):
        self._start_server()
        if self.dry_run:
            try:
                while not self._shutdown:
                    time.sleep(0.1)
            except KeyboardInterrupt:
                pass
            self._shutdown = True
            self._close_server()
            return

        # A timer drives the fixed-rate publish; rclpy.spin services it.
        self.node.create_timer(1.0 / self.rate_hz, self._publish_cycle)
        try:
            rclpy.spin(self.node)
        except KeyboardInterrupt:
            pass
        finally:
            self._shutdown = True
            # Publish a final zero so the robot stops on shutdown.
            try:
                self._set_target(0.0, 0.0)
                self.last_rx = 0.0
                self._publish_cycle()
            except Exception:
                pass
            self._close_server()
            self.node.destroy_node()
            if rclpy.ok():
                rclpy.shutdown()

    def _close_server(self):
        if self._server is not None:
            try:
                self._server.close()
            except OSError:
                pass
            self._server = None


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description="TCP (JSON Lines) -> ROS 2 /cmd_vel bridge")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT,
                        help="TCP port to listen on (default: %d)."
                             % DEFAULT_PORT)
    parser.add_argument("--rate", type=float, default=DEFAULT_RATE_HZ,
                        help="Publish rate in Hz (default: %.0f)."
                             % DEFAULT_RATE_HZ)
    parser.add_argument("--watchdog", type=float, default=DEFAULT_WATCHDOG_S,
                        help="Seconds without a command before publishing zero "
                             "(default: %.2f)." % DEFAULT_WATCHDOG_S)
    parser.add_argument("--dry-run", action="store_true",
                        help="Run the TCP server without ROS (for testing).")
    # Strip ROS 2 args (--ros-args ...) when rclpy is present.
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
    bridge = RosBridge(port=args.port, rate_hz=args.rate,
                       watchdog_s=args.watchdog, dry_run=args.dry_run)
    bridge.serve()


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # pragma: no cover - top-level guard
        print("❌ fatal: %s" % exc, file=sys.stderr)
        sys.exit(1)
