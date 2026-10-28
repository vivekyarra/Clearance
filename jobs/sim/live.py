"""Chaos mode: a live 20 Hz session where one pedestrian is driven by a phone joystick."""
from __future__ import annotations

import math
from collections import deque

import numpy as np

from clearance_core.schemas import RobotSpec, Task, Twin

from . import params as P
from .episode import MissionSim, make_context, plan_mission
from .routing import make_geom


EVENTS_LOG_MAX = 5000


class LiveSession:
    """The robot loops its mission with its normal controller and safety stop; pedestrian 0 follows
    the joystick (speed capped at 1.4 m/s, walls and the robot are hard constraints). Every event is
    kept in `events_log` (labelled as manual stress-test observations by the caller)."""

    def __init__(self, twin: Twin, robot: RobotSpec, task: Task, density: str = "normal", seed: int = 0,
                 *, overrides: dict | None = None, bound: str = "nominal"):
        self.ctx = make_context(twin, robot, task, density, bound, overrides)
        self.geom = make_geom(self.ctx.base, self.ctx.layers.movable_polys())
        rails, reason = plan_mission(self.ctx, self.geom, None)
        if rails is None:
            raise ValueError(f"no static path for this robot and task: {reason}")
        self.rng = np.random.default_rng(seed)
        self.sim = MissionSim(self.ctx, self.geom, list(rails), self.rng, P.LIVE_DT, loop=True)
        self.sim.warmup(min(self.ctx.cfg.get("warmup_s", P.PED_WARMUP_S), 10.0))
        self.events_log: deque = deque(maxlen=EVENTS_LOG_MAX)  # newest events; a long session stays bounded
        self.joy = (0.0, 0.0)
        self._place_controlled()

    def _place_controlled(self):
        """Joystick pedestrian starts beside the first leg: 1.5 m to the side of its midpoint."""
        cr = self.sim.crowd
        r0 = self.sim.rails[0]
        m = len(r0.x) // 2
        hx, hy = math.cos(r0.th[m]), math.sin(r0.th[m])
        tx, ty = float(r0.x[m]) - 1.5 * hy, float(r0.y[m]) + 1.5 * hx
        walk = cr.edt > P.PED_RADIUS_M + 0.05
        rr, cc = np.nonzero(walk)
        if len(rr) == 0:
            raise ValueError("no walkable floor for the joystick pedestrian")
        xs, ys = cr._cells_world(rr, cc)
        d = np.hypot(xs - tx, ys - ty)
        d[np.hypot(xs - self.sim.x, ys - self.sim.y) < self.ctx.model.radius + 1.0] = np.inf
        j = int(np.argmin(d))
        cr.alive[0] = 0
        cr.add_controlled(float(xs[j]), float(ys[j]))

    def set_joystick(self, vx: float, vy: float):
        """Joystick velocity (m/s, site frame). Anything that is not a pair of finite numbers stops the pedestrian;
        the speed is capped at JOYSTICK_MAX_SPEED."""
        try:
            if isinstance(vx, bool) or isinstance(vy, bool):
                raise TypeError
            vx, vy = float(vx), float(vy)
        except (TypeError, ValueError, OverflowError):
            vx, vy = 0.0, 0.0
        if not (math.isfinite(vx) and math.isfinite(vy)):
            vx, vy = 0.0, 0.0
        m = max(abs(vx), abs(vy))
        if m > 0:
            sp = m * math.hypot(vx / m, vy / m)  # no overflow near the float limit
            if sp > P.JOYSTICK_MAX_SPEED:
                vx, vy = (vx / m) * (P.JOYSTICK_MAX_SPEED * m / sp), (vy / m) * (P.JOYSTICK_MAX_SPEED * m / sp)
        self.joy = (float(vx), float(vy))

    def step(self) -> dict:
        cr = self.sim.crowd
        cr.vx[0], cr.vy[0] = self.joy
        events = self.sim.step()
        for e in events:
            self.events_log.append({"t": round(self.sim.t, 2), **e})
        if self.sim.done:  # a collision ends the loop: log it, then continue from the current pose
            self.sim.done = False
            self.sim.outcome = None
            self.sim.state = "moving"
        s = self.sim
        return {"t": round(s.t, 2),
                "robot": {"x": round(s.x, 3), "y": round(s.y, 3), "yaw": round((s.th + math.pi) % (2 * math.pi) - math.pi, 3),
                          "v": round(s.v_lin, 3), "state": s.state},
                "peds": cr.positions(),
                "controlled_ped": [round(float(cr.px[0]), 2), round(float(cr.py[0]), 2)],
                "events": events}
