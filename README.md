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

## Prerequisites（前提条件）

- **Docker** (Engine 20.10+) and **Docker Compose** v2 (`docker compose`) — or
  legacy v1 (`docker-compose`); both are auto-detected by the launchers.
- One of: **macOS** (Docker Desktop), **WSL2** on Windows (Docker Desktop with
  the WSL2 backend), or **Ubuntu/Linux** (Docker Engine).
- A web browser (to view the simulator at `localhost:8080`).
- No local Python/ROS install is needed — everything runs in containers. (The
  optional headless unit tests need only Python 3.)

## Setup（導入手順）

```bash
# 1. Clone the repository
git clone https://github.com/taku0310/gazebo-keyboard-control.git
cd gazebo-keyboard-control

# 2. Build the container images (first run only; takes a while for Gazebo)
docker compose build

# 3. (Optional) sanity-check the compose file and run the headless unit tests
docker compose config >/dev/null && echo "compose OK"
python3 -m unittest discover -s control_logic/tests
```

> The `gazebo` image is large (Ignition Fortress + ROS Noetic + the ros_ign
> bridge + noVNC). The first `build` will take several minutes; later runs are
> cached. See **Known caveats** below for the ROS Noetic ↔ Ignition Fortress
> bridge notes.

## Usage（実施手順）

The launcher scripts start the backing services (`ros_master`, `control_logic`,
`gazebo`) detached, wait for ROS Master, then run the keyboard controller
interactively. `Ctrl+C` stops and tears down everything.

### 1. Manual keyboard control

```bash
# macOS / Linux / WSL2
bash run_keyboard.sh
```
```powershell
# Windows
.\run_keyboard.ps1
```

Drive the robot with the keys below, and watch it move in the browser at
**http://localhost:8080**.

### 2. Scenario playback（自動再生）

```bash
# Load a scenario, then press SPACE to start:
bash run_keyboard.sh --scenario scenarios/demo_scenario_01.json

# Or auto-play on startup and exit when finished (reproducible demo):
bash run_keyboard.sh --scenario scenarios/demo_scenario_01.json --auto
```
```powershell
.\run_keyboard.ps1 -Scenario "scenarios/demo_scenario_01.json" -Auto
```

Optional flags: `--log <file>` to tee output to a log file; `bash run_keyboard.sh --help`.

### 3. View the simulation

Open **http://localhost:8080** in a browser (noVNC streams the Gazebo GUI).

### 4. Stop

Press `Ctrl+C` in the launcher terminal — it runs `docker compose down` to stop
all containers. To stop manually: `docker compose down`.

### 5. Run tests

```bash
# Headless unit tests (no Docker/ROS needed):
python3 -m unittest discover -s control_logic/tests

# Full integration tests (requires Docker):
bash test_integration.sh            # --quick to skip the long scenario
```


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
