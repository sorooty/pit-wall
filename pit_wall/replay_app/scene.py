"""Replay reshaped for drawing: one row per frame, one column per driver, plain numpy arrays."""

import numpy as np
import pandas as pd
from arcade.types import Color

from pit_wall.core.replay import FRAME
from pit_wall.replay_app.theme import FALLBACK_TEAM

NUMERIC = ["x", "y", "speed", "throttle", "brake", "n_gear", "drs", "lap", "position",
           "gap_to_leader", "interval", "laps_down", "tyre_age"]
TEXT_FIELDS = ["compound", "status"]


class Scene:
    """Read only view of build_replay output, indexed [frame, driver slot]."""

    def __init__(self, replay: dict[str, pd.DataFrame], title: str) -> None:
        self.title = title
        cars = replay["cars"]
        # Here I pivot cars once to (frame, driver) arrays, the window then reads a frame with plain indexing
        wide = cars.pivot(index="frame", columns="driver_number")
        self.numbers: list[int] = list(wide["x"].columns)
        self.n_frames = len(wide)
        self.start = cars["date"].iloc[0]
        self.num = {f: wide[f][self.numbers].to_numpy(dtype=float) for f in NUMERIC}
        self.txt = {f: wide[f][self.numbers].fillna("").to_numpy(dtype=object) for f in TEXT_FIELDS}
        drivers = replay["drivers"].set_index("driver_number").reindex(self.numbers)
        self.acronyms = [a if isinstance(a, str) else str(n) for a, n in zip(drivers["name_acronym"], self.numbers)]
        self.colours = [Color.from_hex_string(c) if isinstance(c, str) else FALLBACK_TEAM for c in drivers["colour"]]
        self.track_status = replay["track_status"]["track_status"].to_numpy(dtype=object)
        # Here I cut the status into runs (start frame, end frame, status) so the timeline draws a few rects
        change = np.flatnonzero(self.track_status[1:] != self.track_status[:-1]) + 1
        bounds = np.concatenate([[0], change, [len(self.track_status)]])
        self.status_runs = [(int(a), int(b), self.track_status[a]) for a, b in zip(bounds[:-1], bounds[1:])]
        self.weather = replay["weather"]
        self.outline = replay["outline"][["x", "y"]].to_numpy(dtype=float)
        self.total_laps = int(np.nanmax(self.num["lap"]))
        self.lap_marks = replay["leader_laps"]
        # Sector yellows as frame ranges for the timeline
        flags = replay["sector_flags"]
        self.yellows = np.column_stack([self.to_frame(flags["start"]), self.to_frame(flags["end"])]) if len(flags) else np.empty((0, 2))

    def to_frame(self, dates: pd.Series) -> np.ndarray:
        """UTC dates to fractional frame numbers."""
        return ((dates - self.start) / FRAME).to_numpy(dtype=float)

    def order(self, frame: int) -> np.ndarray:
        """Driver slots sorted by race position at a frame, unknown positions last."""
        pos = self.num["position"][frame]
        return np.argsort(np.where(np.isnan(pos), np.inf, pos), kind="stable")
