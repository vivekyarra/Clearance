"""J6 simulation: static feasibility and Monte Carlo navigation on the twin occupancy grid.
Public entry points live in jobs.sim.api."""
import os
from pathlib import Path

# compiled numba kernels are cached off the repo drive
os.environ.setdefault("NUMBA_CACHE_DIR", str(Path.home() / ".cache" / "pg" / "numba"))
