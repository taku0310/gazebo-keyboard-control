#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Unit tests for the Control Logic constraint pipeline.

These tests exercise ``ControlLogic.process()`` directly in dry-run mode, so
they run headless without ROS (suitable for CI). Run with:

    python3 -m unittest discover -s control_logic/tests
    # or, if pytest is available:
    pytest control_logic/tests
"""

import importlib.util
import os
import unittest

# Load control.py by path so the tests work regardless of PYTHONPATH.
_HERE = os.path.dirname(os.path.abspath(__file__))
_SRC = os.path.join(_HERE, os.pardir, "src", "control.py")
_spec = importlib.util.spec_from_file_location("control", _SRC)
control = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(control)


class _Msg:
    """Minimal stand-in for std_msgs/Bool."""

    def __init__(self, data):
        self.data = data


def make_node(**kwargs):
    kwargs.setdefault("dry_run", True)
    return control.ControlLogic(**kwargs)


class TestHelpers(unittest.TestCase):
    def test_clip(self):
        self.assertEqual(control.clip(5.0, 2.0), 2.0)
        self.assertEqual(control.clip(-5.0, 2.0), -2.0)
        self.assertEqual(control.clip(1.0, 2.0), 1.0)

    def test_rate_limit(self):
        self.assertEqual(control.rate_limit(2.0, 0.0, 0.05), 0.05)
        self.assertEqual(control.rate_limit(-2.0, 0.0, 0.05), -0.05)
        self.assertEqual(control.rate_limit(0.02, 0.0, 0.05), 0.02)


class TestVelocityLimit(unittest.TestCase):
    def test_output_never_exceeds_max(self):
        node = make_node()
        for _ in range(500):
            lin, ang = node.process(10.0, 10.0)
            self.assertLessEqual(abs(lin), node.max_linear_speed + 1e-9)
            self.assertLessEqual(abs(ang), node.max_angular_speed + 1e-9)

    def test_converges_to_clipped_target(self):
        node = make_node()
        for _ in range(2000):
            node.process(10.0, 10.0)
        self.assertAlmostEqual(node.cur_linear, node.max_linear_speed, places=2)
        self.assertAlmostEqual(node.cur_angular, node.max_angular_speed,
                               places=2)


class TestAccelLimit(unittest.TestCase):
    def test_per_cycle_change_bounded(self):
        node = make_node()
        prev_lin, prev_ang = 0.0, 0.0
        max_dv = node.max_accel * node.dt
        max_dw = node.max_angular_accel * node.dt
        for _ in range(300):
            lin, ang = node.process(2.0, 2.0)
            # Output change per cycle must not exceed the accel budget
            # (filtering only reduces the change further).
            self.assertLessEqual(abs(lin - prev_lin), max_dv + 1e-9)
            self.assertLessEqual(abs(ang - prev_ang), max_dw + 1e-9)
            prev_lin, prev_ang = lin, ang

    def test_converges_exactly_to_target(self):
        node = make_node()
        for _ in range(3000):
            node.process(1.5, -1.0)
        self.assertAlmostEqual(node.cur_linear, 1.5, places=2)
        self.assertAlmostEqual(node.cur_angular, -1.0, places=2)


class TestEmergencyStop(unittest.TestCase):
    def test_estop_forces_zero(self):
        node = make_node()
        for _ in range(2000):
            node.process(2.0, 2.0)
        node.estop_callback(_Msg(True))
        for _ in range(2000):
            node.process(2.0, 2.0)
        self.assertAlmostEqual(node.cur_linear, 0.0, places=3)
        self.assertAlmostEqual(node.cur_angular, 0.0, places=3)

    def test_estop_release_resumes(self):
        node = make_node()
        node.estop_callback(_Msg(True))
        for _ in range(100):
            node.process(2.0, 0.0)
        self.assertAlmostEqual(node.cur_linear, 0.0, places=3)
        node.estop_callback(_Msg(False))
        for _ in range(3000):
            node.process(2.0, 0.0)
        self.assertAlmostEqual(node.cur_linear, 2.0, places=2)


class TestContactStop(unittest.TestCase):
    def test_contact_forces_zero_when_enabled(self):
        node = make_node(enable_contact_stop=True)
        for _ in range(2000):
            node.process(2.0, 0.0)
        node.contact_callback(_Msg(True))
        for _ in range(2000):
            node.process(2.0, 0.0)
        self.assertAlmostEqual(node.cur_linear, 0.0, places=3)

    def test_contact_ignored_when_disabled(self):
        node = make_node(enable_contact_stop=False)
        node.contact = True  # simulate a contact report
        for _ in range(3000):
            node.process(2.0, 0.0)
        # With contact-stop disabled, the robot still follows the command.
        self.assertAlmostEqual(node.cur_linear, 2.0, places=2)


class TestLatency(unittest.TestCase):
    def test_processing_under_budget(self):
        node = make_node()
        worst = 0.0
        for _ in range(5000):
            node.process(1.5, 1.5)
            worst = max(worst, node.last_proc_ms)
        self.assertLess(worst, 10.0, "processing exceeded 10 ms budget")


class TestNonFiniteInput(unittest.TestCase):
    """A NaN/Inf target must not poison the exponential filter (defense in
    depth; ros_bridge already rejects these upstream)."""

    def test_nan_is_sanitized_and_recovers(self):
        import math
        node = make_node()
        node.process(float("nan"), float("inf"))
        out = (0.0, 0.0)
        for _ in range(100):
            out = node.process(1.0, 0.0)
        self.assertTrue(math.isfinite(out[0]) and math.isfinite(out[1]))
        self.assertTrue(math.isfinite(node.filt_linear))
        self.assertGreater(out[0], 0.0)  # converged toward the real target

    def test_single_nan_yields_finite_output(self):
        import math
        node = make_node()
        out = node.process(float("nan"), float("nan"))
        self.assertEqual(out, (0.0, 0.0))
        self.assertTrue(math.isfinite(node.filt_linear))


if __name__ == "__main__":
    unittest.main(verbosity=2)
