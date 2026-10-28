"""Simulation constants. Values shared with other modules come from config/sim_defaults.yaml (key `sim`);
the motion-model values below are simulator settings, not vendor specifications."""
from __future__ import annotations

import math
from functools import lru_cache
from pathlib import Path

import yaml

CONFIG = Path(__file__).resolve().parents[2] / "config" / "sim_defaults.yaml"


@lru_cache(maxsize=1)
def sim_config() -> dict:
    try:
        return dict(yaml.safe_load(CONFIG.read_text(encoding="utf-8")).get("sim", {}))
    except (OSError, AttributeError, yaml.YAMLError):
        return {}


def _cfg(key: str, default):
    return type(default)(sim_config().get(key, default))


DT = _cfg("dt_s", 0.1)
TIMEOUT_FACTOR = _cfg("timeout_factor", 3.0)
MIN_TIMEOUT_S = 10.0
SAFETY_STOP_DEFAULT_M = _cfg("safety_stop_distance_m", 0.5)
REPLAN_AFTER_S = _cfg("replan_after_blocked_s", 10.0)
NEAR_MISS_DIST_M = _cfg("near_miss_distance_m", 0.5)
NEAR_MISS_SPEED_MPS = _cfg("near_miss_min_speed_mps", 0.3)
PED_RADIUS_M = _cfg("pedestrian_radius_m", 0.25)
SHARD_SIZE = int(_cfg("shard_size", 25))
DEFAULT_P90_M = _cfg("default_p90_scan_err_m", 0.03)

K_HEADINGS = 36
DTHETA = 2.0 * math.pi / K_HEADINGS

# robot motion model (simulator settings)
ROBOT_ACCEL = 1.0          # m/s^2
ROBOT_BRAKE = 1.5          # m/s^2
ROBOT_OMEGA_MAX = 1.2      # rad/s for in-place rotation
FALLBACK_NOMINAL_SPEED = 0.5
CONTROL_HORIZON_S = 1.5
DYN_MARGIN_M = 0.05        # extra margin used by the velocity window against predicted pedestrian discs
WAYPOINT_SNAP_M = 0.10     # waypoint may be shifted this far to reach a collision-free robot pose

# planner
PLANNER_MAX_NODES = 3_000_000
REPLAN_MAX_NODES = 600_000   # a detour around pedestrians is local; a blocked replan just waits again
FINE_RETRY_MAX_NODES = 400_000  # budget of the finer closed-set retry after an exhausted search
HEURISTIC_WEIGHT = 2.0     # weighted A*: bounded-suboptimal, far fewer expansions; smoothing straightens paths
GAP_COST_REF_M = 0.25      # paths are pushed away from obstacles closer than this
GAP_COST_WEIGHT = 2.0
ROTATION_PENALTY_S = 0.1
REVERSE_COST_FACTOR = 2.0  # in-place robots may back up (undocking), at twice the cost
REVERSE_RADIUS_M = 1.0     # ... within this distance of the leg start
SMOOTH_MIN_MARGIN_M = 0.15

# per-episode variation
JITTER_MOVABLE_M = 0.10
JITTER_CHAIR_M = 0.20
JITTER_YAW_RAD = math.radians(10.0)
CLUTTER_MAX = 2
CLUTTER_SIZE_RANGE = (0.30, 0.60)
CLUTTER_WAYPOINT_KEEPAWAY_M = 1.0

# pedestrians (social force, Helbing-style; accelerations in m/s^2)
PED_TAU = 0.5
PED_A = 2.1
PED_B = 0.3
PED_LAMBDA = 0.35
PED_WALL_A = 5.0
PED_WALL_B = 0.15
PED_ROBOT_A = 3.0
PED_ROBOT_B = 0.2
PED_MAX_SPEED_FACTOR = 1.3
PED_WARMUP_S = 30.0
PED_MAX_LIFETIME_S = 120.0
PED_STUCK_MAX_S = 20.0       # a pedestrian without 0.5 m of progress this long changes plans and leaves
PED_MAX_COUNT = 80
PED_GOAL_RADIUS_M = 0.4

LIVE_DT = 0.05
JOYSTICK_MAX_SPEED = 1.4

FAIL_REASONS = ("no_path_static", "blocked_timeout", "collision")
CHAIR_LABELS = ("chair", "stool")


def defaults_dict() -> dict:
    """Effective run settings (merged with per-run `sim_config`)."""
    return {
        "dt": DT,
        "timeout_factor": TIMEOUT_FACTOR,
        "replan_after_s": REPLAN_AFTER_S,
        "clutter_max": CLUTTER_MAX,
        "jitter": True,
        "warmup_s": PED_WARMUP_S,
        "accel": ROBOT_ACCEL,
        "brake": ROBOT_BRAKE,
        "omega_max": ROBOT_OMEGA_MAX,
    }
