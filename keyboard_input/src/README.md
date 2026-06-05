# Keyboard Controller

Converts keyboard input into `geometry_msgs/Twist` messages and publishes them
to the `/cmd_vel` topic at **20 Hz**. Built on [pynput](https://pypi.org/project/pynput/)
for cross-platform (macOS / Windows / Linux) keyboard capture.

- **Node name:** `keyboard_controller`
- **Topic:** `/cmd_vel` (`geometry_msgs/Twist`)
- **Rate:** 20 Hz

## Key bindings

| Key            | Action                                  |
|----------------|-----------------------------------------|
| `W` / `↑`      | `linear.x  = +max_speed` (forward)      |
| `S` / `↓`      | `linear.x  = -max_speed` (backward)     |
| `A` / `←`      | `angular.z = +max_speed` (turn left)    |
| `D` / `→`      | `angular.z = -max_speed` (turn right)   |
| `+` / `=`      | speed scale **up** (`max_speed *= 1.1`) |
| `-` / `_`      | speed scale **down** (`max_speed /= 1.1`)|
| `SPACE`        | play the JSON scenario file             |
| `R`            | reset velocities to 0                    |
| `Q` / `ESC`    | quit (`rospy.signal_shutdown`)          |

Opposite keys cancel out (e.g. holding `W`+`S` → `linear.x = 0`). Simultaneous
key presses are supported via an internal `pressed_keys` set, so you can move
forward and turn at the same time (e.g. `W`+`A`).

## Usage

### Manual control

```bash
# With a running roscore (ROS_MASTER_URI set):
python3 keyboard_input_controller.py
```

### Scenario playback

```bash
# Load a scenario; press SPACE to start playback.
python3 keyboard_input_controller.py --scenario /scenarios/demo_scenario_01.json

# Auto-play on startup and exit when finished (great for CI / reproducible demos):
python3 keyboard_input_controller.py --scenario /scenarios/demo_scenario_01.json --auto
```

During playback, manual movement keys are ignored by default (so the recorded
trajectory is reproduced exactly). Pass `--allow-keys-during-scenario` to keep
manual keys active. `Q`/`ESC` always work.

### Local testing without ROS

```bash
python3 keyboard_input_controller.py --dry-run
```

`--dry-run` (also auto-enabled when `rospy` is not importable) skips the ROS
publisher so you can verify key handling and scenario parsing on any machine.

## Scenario file format

```json
{
  "name": "demo_scenario_01",
  "description": "Forward, turn, and stop",
  "duration_seconds": 10,
  "commands": [
    {
      "timestamp": 0.0,
      "description": "Move forward",
      "linear":  {"x": 1.0, "y": 0.0, "z": 0.0},
      "angular": {"x": 0.0, "y": 0.0, "z": 0.0}
    }
  ]
}
```

- `commands` are sorted by `timestamp` (seconds from playback start) and applied
  at the scheduled time.
- After the last command, the final state is held until `duration_seconds`,
  then the robot is stopped automatically.

## Testing

1. **Start ROS Master** (`roscore` or the `ros_master` container).
2. **Run the controller** and watch `/cmd_vel`:
   ```bash
   rostopic echo /cmd_vel        # see Twist messages
   rostopic hz   /cmd_vel        # confirm ~20 Hz
   ```
3. **Manual keys:** press `W`/`A`/`S`/`D` and confirm `linear.x` / `angular.z`
   change in the echo output and the `📊` debug line.
4. **Scenario:** run with `--scenario ... --auto` and confirm the `[T.TTs] ...`
   log lines and matching `/cmd_vel` values, ending in a stop.
5. **Cross-platform:** verify on macOS, Windows (`run_keyboard.ps1`) and Linux
   (`run_keyboard.sh`). Note pynput requires accessibility permission on macOS
   and an X server / `python3-xlib` on Linux.

## Notes on Docker

Keyboard capture needs a real tty/stdin. Run the container with `-it`
(or `stdin_open: true` + `tty: true` in docker-compose). On Linux you may also
need to share the X socket or `/dev/input` depending on the pynput backend.
