# Control Logic

Safety/constraint node. Subscribes to raw user commands on `/cmd_vel`, applies
a constraint pipeline, and republishes the safe result to `/gazebo/cmd_vel`
for the simulator.

- **Node name:** `control_logic`
- **Subscribes:** `/cmd_vel` (`geometry_msgs/Twist`), `/emergency_stop`
  (`std_msgs/Bool`, optional), `/contact` (`std_msgs/Bool`, optional)
- **Publishes:** `/gazebo/cmd_vel` (`geometry_msgs/Twist`)
- **Rate:** 20 Hz, < 10 ms processing latency per cycle

## Constraint pipeline (in order)

1. **Velocity limits** — clip `linear.x` / `angular.z` to `max_linear_speed`
   (2.0 m/s) / `max_angular_speed` (2.0 rad/s). A clip is a real violation and
   is logged (throttled to once/sec).
2. **Acceleration limits** — rate-limit the per-cycle change to
   `max_accel` (1.0 m/s²) / `max_angular_accel` (1.0 rad/s²). This is normal
   smoothing and is not logged per cycle.
3. **Exponential smoothing** — low-pass filter, `alpha = 0.3`.
4. **Safety rules** — two triggers force the target to zero, and combined with
   the acceleration limit they produce a smooth, bounded deceleration:
   - **Emergency stop** — `/emergency_stop` (`std_msgs/Bool`).
   - **Contact stop (optional)** — `/contact` (`std_msgs/Bool`), e.g. from a
     bumper/collision sensor. Enabled by default; disable with
     `--no-contact-stop`.

The output is re-clipped after filtering as a defensive measure.

> Note: because acceleration limiting and exponential smoothing are applied in
> series, a step command ramps to its target more gradually than the
> acceleration limit alone would imply (extra smoothing lag). It still
> converges exactly to the commanded value and never exceeds the configured
> acceleration. This is intentional for a smooth, safe demo.

## Usage

```bash
# With a running roscore:
python3 control.py

# Tunable parameters:
python3 control.py --max-linear 2.0 --max-angular 2.0 \
    --max-accel 1.0 --max-angular-accel 1.0 --alpha 0.3 --rate 20

# Local testing without ROS (drive process() directly):
python3 control.py --dry-run
```

## Testing

```bash
# Inject commands and observe the smoothed/clipped output:
rostopic pub -r 20 /cmd_vel geometry_msgs/Twist '{linear: {x: 5.0}}'
rostopic echo /gazebo/cmd_vel     # should clip to 2.0 and ramp smoothly
rostopic hz   /gazebo/cmd_vel     # ~20 Hz

# Emergency stop:
rostopic pub /emergency_stop std_msgs/Bool 'data: true'

# Contact-triggered stop (optional, on by default):
rostopic pub /contact std_msgs/Bool 'data: true'
```

The constraint pipeline is implemented in `process()`, which is pure enough to
unit-test directly (feed targets, inspect returned `(linear, angular)` and
`last_proc_ms`).

### Unit tests

Headless tests (no ROS needed) cover velocity/accel limits, emergency stop,
contact stop, and the latency budget:

```bash
python3 -m unittest discover -s control_logic/tests
```
