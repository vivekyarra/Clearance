"""Reference rooms shared by the simulator stress tests."""
import math

from clearance_core.schemas import Task
from jobs.sim.twin_builder import RoomCanvas, box, make_twin, rect_robot


def cafe_like(extra_waypoints=(), reverse_primitives=False, key="sites/stress_sim/cafe"):
    """8 x 6 m room: tables with chairs (jittered), a shelf, a door wall, two traffic zones, clutter allowed."""
    c = RoomCanvas(8.0, 6.0, res=0.025)
    c.rect(4.9, 0.0, 5.1, 2.4)
    c.rect(4.9, 3.6, 5.1, 6.0)
    prims = []
    for i, (x, y) in enumerate([(1.8, 1.5), (3.4, 1.5), (1.8, 4.3), (3.4, 4.3)]):
        prims.append(box(f"T{i}", x, y, 0.75, 0.75, yaw=0.1 * i))
        prims.append(box(f"C{i}", x + 0.62, y, 0.45, 0.45, label="chair"))
    prims.append(box("SHELF", 7.0, 5.5, 1.4, 0.4, label="shelf", movable=False))
    prims.append(box("CART", 6.5, 1.0, 0.8, 0.5, label="cart"))
    if reverse_primitives:
        prims = prims[::-1]
    wps = [("DOCK", 0.6, 3.0, 0.0, "dock"), ("PICK", 7.2, 3.0, math.pi, "pickup"), ("D1", 2.6, 3.0, 0.0, "dest")]
    wps += list(extra_waypoints)
    zones = [("W", [(0.2, 0.2), (1.0, 0.2), (1.0, 1.2), (0.2, 1.2)]),
             ("E", [(6.8, 4.0), (7.8, 4.0), (7.8, 5.0), (6.8, 5.0)])]
    twin = make_twin(c, key, wps, primitives=prims, zones=zones, od_pairs=[("W", "E", 1.0), ("E", "W", 1.0)],
                     persons_per_min={"quiet": 3, "normal": 10, "peak": 30})
    return twin, rect_robot(0.45, 0.55), Task(sequence=["DOCK", "PICK", "D1", "DOCK"])
