"""Headless unit tests for the keyboard controller's stdin input path.

These mirror the style of control_logic/tests: drive the controller directly,
inspect state, no ROS / no display. Confirms that the stdin token -> command
dispatch and the timeout-based release logic do what they should.
"""

import os
import sys
import time
import unittest

# Make the source importable without installing the package.
HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.normpath(os.path.join(HERE, "..", "src"))
if SRC not in sys.path:
    sys.path.insert(0, SRC)

import keyboard_input_controller as kic  # noqa: E402


def make_controller():
    # dry_run=True keeps everything ROS-free; input_mode is irrelevant for
    # the pure-function tests below (we drive _stdin_handle_token directly).
    return kic.KeyboardController(dry_run=True, input_mode="stdin")


class StdinDirectionTokens(unittest.TestCase):
    def test_letter_keys(self):
        self.assertEqual(kic.KeyboardController._stdin_direction("w"), "forward")
        self.assertEqual(kic.KeyboardController._stdin_direction("S"), "backward")
        self.assertEqual(kic.KeyboardController._stdin_direction("a"), "left")
        self.assertEqual(kic.KeyboardController._stdin_direction("D"), "right")

    def test_arrow_tokens(self):
        self.assertEqual(kic.KeyboardController._stdin_direction("UP"), "forward")
        self.assertEqual(kic.KeyboardController._stdin_direction("DOWN"), "backward")
        self.assertEqual(kic.KeyboardController._stdin_direction("LEFT"), "left")
        self.assertEqual(kic.KeyboardController._stdin_direction("RIGHT"), "right")

    def test_irrelevant_keys_return_none(self):
        self.assertIsNone(kic.KeyboardController._stdin_direction("x"))
        self.assertIsNone(kic.KeyboardController._stdin_direction(""))


class StdinHandlerDispatch(unittest.TestCase):
    def test_press_drives_forward_at_max_speed(self):
        c = make_controller()
        c._stdin_handle_token("w")
        self.assertEqual(c.linear_x, kic.DEFAULT_MAX_SPEED)
        self.assertEqual(c.angular_z, 0.0)

    def test_speed_scaling(self):
        c = make_controller()
        initial = c.max_speed
        c._stdin_handle_token("+")
        self.assertGreater(c.max_speed, initial)
        c._stdin_handle_token("-")
        self.assertAlmostEqual(c.max_speed, initial, places=6)

    def test_reset_clears_state(self):
        c = make_controller()
        c._stdin_handle_token("w")
        c._stdin_handle_token("d")
        self.assertNotEqual(c.linear_x, 0.0)
        c._stdin_handle_token("r")
        self.assertEqual(c.linear_x, 0.0)
        self.assertEqual(c.angular_z, 0.0)
        self.assertEqual(c.pressed_keys, set())

    def test_quit_returns_false(self):
        c = make_controller()
        # The listen loop uses the return value to stop itself.
        self.assertFalse(c._stdin_handle_token("q"))
        self.assertTrue(c._shutdown_requested)


class StdinTimeoutRelease(unittest.TestCase):
    def test_held_key_is_released_after_timeout(self):
        c = make_controller()
        c._stdin_handle_token("d")  # right -> negative angular.z
        self.assertIn("right", c.pressed_keys)
        # Make the press look old, then run one sweep of the release loop.
        c._stdin_last_press["right"] = time.monotonic() - 10.0
        now = time.monotonic()
        with c._state_lock:
            for direction in list(c.pressed_keys):
                if now - c._stdin_last_press.get(direction, 0.0) > \
                        kic.STDIN_KEY_HOLD_SECONDS:
                    c.pressed_keys.discard(direction)
            c._recompute_velocity()
        self.assertNotIn("right", c.pressed_keys)
        self.assertEqual(c.angular_z, 0.0)


class IgnoresKeysDuringScenario(unittest.TestCase):
    def test_movement_ignored_while_scenario_runs(self):
        c = make_controller()
        c.scenario_running = True
        c.ignore_keys_during_scenario = True
        c._stdin_handle_token("w")
        # Direction must be suppressed while a scenario is playing.
        self.assertEqual(c.linear_x, 0.0)
        # Quit still works.
        self.assertFalse(c._stdin_handle_token("q"))


if __name__ == "__main__":
    unittest.main()
