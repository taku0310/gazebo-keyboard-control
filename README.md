# gazebo-keyboard-control

Keyboard-driven robot control in a Gazebo (Ignition Fortress) simulation,
wired together over ROS in a multi-container Docker stack. Drive a
differential-drive robot with the keyboard, or replay recorded JSON scenarios
for reproducible demos. Runs the same way on macOS, WSL2 and Ubuntu.

```
┌──────────────────┐   /cmd_vel    ┌───────────────┐  /gazebo/cmd_vel  ┌──────────┐
│ keyboard_         │ ───────────▶ │ control_logic │ ───────────────▶ │  gazebo  │
│ controller        │   (Twist)     │ (safety/      │     (Twist)       │ (Ignition│
│ (pynput → Twist)  │               │  smoothing)   │                   │ Fortress)│
└──────────────────┘               └───────────────┘                   └──────────┘
          │                                 │                                 │
          └─────────────────────────────────┴─────────────────────────────────┘
                              ROS Master (roscore) :11311
                                                          Web UI (noVNC) :8080
```

## Components

| Container | Role | Key tech |
|-----------|------|----------|
| `ros_master` | Node registration / topic discovery hub (`roscore`) | `ros:noetic` |
| `keyboard_controller` | Keyboard input → `geometry_msgs/Twist` on `/cmd_vel` @ 20 Hz | Python, pynput |
| `control_logic` | Safety constraints + smoothing → `/gazebo/cmd_vel` | Python, rospy |
| `gazebo` | 3D physics + web visualization | Ignition Fortress, ros_ign bridge, noVNC |

### Data flow

1. **keyboard_controller** turns key presses (or a JSON scenario) into `Twist`
   messages on `/cmd_vel` at 20 Hz.
2. **control_logic** subscribes to `/cmd_vel`, applies velocity limits,
   acceleration limits, exponential smoothing and safety stops, then
   republishes to `/gazebo/cmd_vel` (< 10 ms processing).
3. **gazebo** bridges `/gazebo/cmd_vel` into Ignition, drives the robot's
   differential drive, and publishes `/odom`, `/imu`, `/clock` back to ROS.

## Repository layout

```
.
├── docker-compose.yml          # 4-container stack on the ros_net bridge
├── run_keyboard.sh             # launcher (macOS / Linux / WSL)
├── run_keyboard.ps1            # launcher (Windows)
├── test_integration.sh         # end-to-end integration tests
├── keyboard_input/             # keyboard_controller container
│   ├── Dockerfile
│   ├── requirements.txt
│   └── src/keyboard_input_controller.py
├── control_logic/              # control_logic container
│   ├── Dockerfile
│   ├── requirements.txt
│   ├── src/control.py
│   └── tests/test_control.py   # headless unit tests (no ROS needed)
├── gazebo_simulator/           # gazebo container
│   ├── Dockerfile
│   ├── entrypoint.sh
│   ├── models/simple_robot/    # URDF + model.config
│   └── worlds/empty.world      # SDF world
└── scenarios/                  # JSON demo scenarios
    ├── demo_scenario_01.json   # "Simple Forward Motion"
    └── demo_scenario_02.json   # "Square Pattern"
```

## Quick start

Requires Docker + Docker Compose.

```bash
# Manual keyboard control (W/A/S/D, arrows, etc.):
bash run_keyboard.sh

# Replay a scenario (press SPACE to start, or use --auto):
bash run_keyboard.sh --scenario scenarios/demo_scenario_01.json --auto
```

On Windows:

```powershell
.\run_keyboard.ps1 -Scenario "scenarios/demo_scenario_01.json" -Auto
```

Open the simulator in a browser at **http://localhost:8080** (noVNC).

The launcher starts the backing services detached, waits for ROS Master, then
runs the keyboard controller interactively. `Ctrl+C` stops everything.

### Key bindings

| Key | Action | Key | Action |
|-----|--------|-----|--------|
| `W` / `↑` | forward | `+` / `=` | speed scale up |
| `S` / `↓` | backward | `-` / `_` | speed scale down |
| `A` / `←` | turn left | `R` | reset velocities |
| `D` / `→` | turn right | `SPACE` | play scenario |
| | | `Q` / `ESC` | quit |

## Scenarios

JSON files in `scenarios/` describe a timeline of `Twist` commands for
reproducible demos:

```json
{
  "name": "Simple Forward Motion",
  "description": "...",
  "duration_seconds": 10,
  "commands": [
    {"timestamp": 0.0, "description": "前進",
     "linear": {"x": 1.0, "y": 0.0, "z": 0.0},
     "angular": {"x": 0.0, "y": 0.0, "z": 0.0}}
  ]
}
```

Commands are applied in timestamp order; the final state is held until
`duration_seconds`, then the robot stops.

## Safety constraints (control_logic)

| Constraint | Default |
|------------|---------|
| Max linear speed | 2.0 m/s (clip) |
| Max angular speed | 2.0 rad/s (clip) |
| Max acceleration | 1.0 m/s² (rate limit) |
| Max angular acceleration | 1.0 rad/s² (rate limit) |
| Smoothing | exponential filter, α = 0.3 |
| Emergency stop | `/emergency_stop` (`std_msgs/Bool`) → forced stop |
| Contact stop | `/contact` (`std_msgs/Bool`) → forced stop (optional) |

All tunable via CLI flags (see `control_logic/src/README.md`).

## Testing

```bash
# Headless unit tests for the safety pipeline (no Docker / ROS required):
python3 -m unittest discover -s control_logic/tests

# Full integration tests (requires Docker):
bash test_integration.sh          # add --quick to skip the long scenario
```

The integration suite checks container startup, ROS communication, E2E latency
(target < 100 ms) and scenario playback, with a tree-formatted summary.

## Robot

`simple_robot` is a differential-drive robot: a 0.5×0.3×0.2 m box chassis
(10 kg), two 0.05 m drive wheels (continuous joints, 5 rad/s limit), and a
front caster. Defined in `gazebo_simulator/models/simple_robot/simple_robot.urdf`
with inertials, friction, an Ignition DiffDrive plugin and an IMU sensor.

## Known caveats

This project targets **ROS Noetic (ROS1)** together with **Gazebo Ignition
Fortress**, which are different ecosystem generations; bridging them is the
main source of risk:

- The `gazebo` container layers ROS Noetic + the `ros_ign` bridge onto the
  Ignition image, and serves the GUI over **noVNC** (gzweb is
  Gazebo-Classic-only). The bridge package's apt availability for
  Noetic+Fortress can vary; a source-build fallback is documented in
  `gazebo_simulator/Dockerfile`.
- Ignition's default physics engine is DART; the world's `<physics type="ode">`
  settings are honored where supported.
- The robot spawns at `z=0.5` and settles onto its wheels (~0.13 m).

The Python nodes, scenarios and safety pipeline are verified headless. The full
Docker/Gazebo build and runtime should be validated on a real Docker host.
```

## License

See repository.
