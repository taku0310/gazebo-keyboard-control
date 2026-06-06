"""Headless unit tests for the ROS Bridge wire-protocol parser.

``parse_command`` is a pure function (no socket, no ROS), so these run on any
machine. They pin the JSON Lines contract that a SoftPLC / gateway must follow.
"""

import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.normpath(os.path.join(HERE, "..", "src"))
if SRC not in sys.path:
    sys.path.insert(0, SRC)

import ros_bridge as rb  # noqa: E402


class FlatForm(unittest.TestCase):
    def test_both_fields(self):
        self.assertEqual(rb.parse_command('{"linear_x": 1.0, "angular_z": 0.5}'),
                         (1.0, 0.5))

    def test_missing_fields_default_to_zero(self):
        self.assertEqual(rb.parse_command('{"linear_x": 2.0}'), (2.0, 0.0))
        self.assertEqual(rb.parse_command('{"angular_z": -1.0}'), (0.0, -1.0))

    def test_integer_values_coerced_to_float(self):
        self.assertEqual(rb.parse_command('{"linear_x": 1, "angular_z": 0}'),
                         (1.0, 0.0))

    def test_accepts_bytes(self):
        self.assertEqual(rb.parse_command(b'{"linear_x": 1.0}'), (1.0, 0.0))

    def test_trailing_newline_and_whitespace(self):
        self.assertEqual(rb.parse_command('  {"linear_x": 1.0}\n'), (1.0, 0.0))


class NestedForm(unittest.TestCase):
    def test_nested_twist_like(self):
        self.assertEqual(
            rb.parse_command('{"linear": {"x": 1.0}, "angular": {"z": 0.5}}'),
            (1.0, 0.5))

    def test_nested_partial(self):
        self.assertEqual(rb.parse_command('{"linear": {"x": 1.5}}'), (1.5, 0.0))


class Invalid(unittest.TestCase):
    def test_blank(self):
        self.assertIsNone(rb.parse_command(""))
        self.assertIsNone(rb.parse_command("   \n"))

    def test_malformed_json(self):
        self.assertIsNone(rb.parse_command("{not json"))
        self.assertIsNone(rb.parse_command("linear_x=1.0"))

    def test_non_object(self):
        self.assertIsNone(rb.parse_command("[1, 2, 3]"))
        self.assertIsNone(rb.parse_command("42"))

    def test_non_numeric_field(self):
        self.assertIsNone(rb.parse_command('{"linear_x": "fast"}'))

    def test_empty_object_without_known_fields(self):
        self.assertIsNone(rb.parse_command('{"foo": 1}'))


class NonFinite(unittest.TestCase):
    """NaN / Infinity must be rejected: they would lock the control filter."""

    def test_nan_rejected(self):
        self.assertIsNone(rb.parse_command('{"linear_x": NaN}'))
        self.assertIsNone(rb.parse_command('{"linear_x": 1.0, "angular_z": NaN}'))

    def test_infinity_rejected(self):
        self.assertIsNone(rb.parse_command('{"linear_x": Infinity}'))
        self.assertIsNone(rb.parse_command('{"linear_x": -Infinity}'))

    def test_nested_non_finite_rejected(self):
        self.assertIsNone(rb.parse_command('{"linear": {"x": Infinity}}'))


class Watchdog(unittest.TestCase):
    def test_zero_when_stale(self):
        bridge = rb.RosBridge(dry_run=True)
        bridge._set_target(1.0, 0.5)
        # Force the last receipt far in the past.
        bridge.last_rx = 0.0
        self.assertEqual(bridge._current_command(), (0.0, 0.0))

    def test_passthrough_when_fresh(self):
        bridge = rb.RosBridge(dry_run=True)
        bridge._set_target(1.0, 0.5)  # sets last_rx = now
        self.assertEqual(bridge._current_command(), (1.0, 0.5))


if __name__ == "__main__":
    unittest.main()
