# Agent team

Project-specific [Claude Code subagents](https://docs.claude.com/en/docs/claude-code/sub-agents)
for gazebo-keyboard-control. They carry this project's hard-won context (topic
graph, REP-103 conventions, the ROS 2 Jazzy ↔ Gazebo Harmonic caveats, the
latency budget, cross-platform launch quirks) so reviews are consistent and
fast.

Each agent has its own context window and a restricted tool set. The three
reviewers are **read-only**; the test engineer may write tests.

## The team

| Agent | Role | Edits files? | Use when |
|-------|------|:---:|----------|
| `code-reviewer` | General correctness & quality review of a diff | No | After changing Python/shell/YAML, before commit/PR |
| `ros-robotics-reviewer` | ROS wiring, URDF/SDF validity, control **safety** | No | Changes to topics, controllers, robot description, safety logic |
| `docker-infra-reviewer` | Dockerfiles, compose, CI, launch scripts, portability | No | Changes to Docker/compose/CI/run scripts |
| `test-engineer` | Writes & strengthens headless tests and CI | Yes | New tests, coverage, regression tests after a fix |

## How to use

- **Explicit**: ask for one by name, e.g. *"Use the ros-robotics-reviewer to
  check the URDF changes"* or `@ros-robotics-reviewer review the diff`.
- **Automatic**: Claude may delegate to the matching agent based on each
  agent's `description`.
- **In parallel**: for a broad change, run several reviewers at once (e.g.
  code-reviewer + docker-infra-reviewer) and consolidate their findings.

### Suggested review flow before a PR

1. `test-engineer` — add/extend tests for the change; confirm green.
2. `code-reviewer` — correctness & quality pass on the diff.
3. `ros-robotics-reviewer` and/or `docker-infra-reviewer` — domain pass for the
   areas touched.
4. Address **Must-fix** findings, then commit/push.

## Conventions the reviewers enforce

- Topic graph: `keyboard_controller`→`/cmd_vel`→`control_logic`→`/gazebo/cmd_vel`→`gazebo`.
- REP-103: +linear.x forward, +angular.z = left (CCW); only x/z non-zero.
- Safety: output always within limits; bounded acceleration; e-stop/contact →
  smooth stop; control_logic < 10 ms/cycle.
- Headless testability: guarded `pynput`/`rclpy` imports, `--dry-run`, no
  display/ROS needed for unit tests and CI.
- Masterless DDS discovery (same `ROS_DOMAIN_ID`, no roscore); launch scripts
  use `compose run` (not `exec`).

## Adding or editing agents

Drop a new `<name>.md` here with YAML frontmatter (`name`, `description`,
optional `tools`, optional `model`) followed by the system prompt. Keep
reviewers read-only (omit `Write`/`Edit` from `tools`). Update this table when
the team changes.
