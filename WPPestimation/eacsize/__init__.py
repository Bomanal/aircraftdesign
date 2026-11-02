"""
eacsize - conceptual / preliminary sizing of battery-electric light aircraft.

Algorithm (see "Electric Aircraft Sizing Algorithm" page):
  1 inputs -> 2 class-I guess (mode B) -> 3 constraint analysis (W/S, P/W)
  -> 4 geometry -> 5 aerodynamics (C_D0 build-up) -> 6 class-II weights (Raymer GA)
  -> 7 battery / mass closure (mode A: fixed MTOM, mode B: solved MTOM)
  -> 8 feasibility checks -> 9 point + mission performance -> 10 outputs.

Standard library only. SI units internally; user-facing inputs carry their unit in the name.
"""
from .model import DesignInputs, Mission, Requirements, Assumptions
from .sizing import run_design
from .presets import PRESETS, preset

__all__ = ["DesignInputs", "Mission", "Requirements", "Assumptions", "run_design", "PRESETS", "preset"]
