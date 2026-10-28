"""Race replay frames: every car state on a fixed time grid, plus track status, outline and weather."""

import numpy as np
import pandas as pd

from pit_wall.core.ingestion import DataError, get_telemetry

REPLAY_HZ = 4
FRAME = pd.Timedelta(seconds=1 / REPLAY_HZ)
CAR_CHANNELS = ["speed", "throttle", "brake", "n_gear", "drs"]


def race_window(laps: pd.DataFrame) -> tuple[pd.Timestamp, pd.Timestamp]:
    """Race start (lap 1 start) and end (last car crossing the line), UTC."""
    first = laps.loc[laps["lap_number"] == 1, "date_start"]
    if first.empty:
        raise DataError("No lap 1 in this session, replay needs a race or sprint")
    # Here the end is the latest lap end over all cars, so lapped cars finish their last lap too
    ends = laps["date_start"] + pd.to_timedelta(laps["lap_duration"], unit="s")
    return first.min(), ends.max()


def _seconds(dates: pd.Series, start: pd.Timestamp) -> np.ndarray:
    # Here I turn dates into float seconds from start, a unit-safe base for np.interp and searchsorted
    return (dates - start).dt.total_seconds().to_numpy()


def _grid(start: pd.Timestamp, end: pd.Timestamp) -> pd.Series:
    # Here I build the replay clock, one frame every 1/REPLAY_HZ s, in µs like parsed OpenF1 dates so merges match
    # One extra frame past end so the last car is shown crossing the line
    return pd.Series(pd.date_range(start, end + FRAME, freq=FRAME, unit="us"), name="date")


def _driver_frames(session_key: int, laps: pd.DataFrame, grid: pd.Series) -> pd.DataFrame:
    """One driver on the grid: date, driver_number, x, y, car channels, lap, finished_at (end of last lap)."""
    driver_number = int(laps["driver_number"].iloc[0])
    tel = get_telemetry(session_key, driver_number)
    start = grid.iloc[0]
    t_grid, t_tel = _seconds(grid, start), _seconds(tel["date"], start)
    # Here I interpolate the position so cars move smoothly, then take the nearest sample for discrete channels (gear, DRS)
    frames = pd.DataFrame({"date": grid, "driver_number": driver_number})
    frames["x"] = np.interp(t_grid, t_tel, tel["x"])
    frames["y"] = np.interp(t_grid, t_tel, tel["y"])
    frames = pd.merge_asof(frames, tel[["date", *CAR_CHANNELS]], on="date", direction="nearest")
    # Then I find the lap each frame belongs to from lap start times, 0 before the start
    t_laps = _seconds(laps["date_start"], start)
    idx = np.searchsorted(t_laps, t_grid, side="right") - 1
    frames["lap"] = np.where(idx >= 0, laps["lap_number"].to_numpy()[np.clip(idx, 0, None)], 0)
    # The last lap end tells when the car stopped racing (finish or retirement)
    last = laps.iloc[-1]
    frames["finished_at"] = last["date_start"] + pd.Timedelta(seconds=np.nan_to_num(last["lap_duration"]))
    return frames


def _last_known(frames: pd.DataFrame, events: pd.DataFrame, columns: list[str]) -> pd.DataFrame:
    # Here I attach to each frame the latest event value of the same driver, frames must be sorted by date
    events = events[["date", "driver_number", *columns]].assign(date=events["date"].dt.as_unit(frames["date"].dt.unit))
    return pd.merge_asof(frames, events, on="date", by="driver_number")


def car_frames(
    session_key: int,
    laps: pd.DataFrame,
    grid: pd.Series,
    positions: pd.DataFrame,
    intervals: pd.DataFrame,
    stints: pd.DataFrame,
    pits: pd.DataFrame,
) -> pd.DataFrame:
    """Every car at every frame.

    Columns: frame, date, driver_number, x, y, speed, throttle, brake, n_gear, drs, lap, position,
    gap_to_leader, interval, laps_down, compound, tyre_age, status (RUN, PIT, OUT retired, FIN finished).
    """
    # Here I build each driver on the shared clock (loop over ~20 drivers, each one vectorized)
    frames = pd.concat([_driver_frames(session_key, drv_laps, grid) for _, drv_laps in laps.groupby("driver_number")])
    frames = frames.sort_values(["date", "driver_number"], ignore_index=True)
    frames = _last_known(frames, positions, ["position"])
    frames = _last_known(frames, intervals, ["gap_to_leader", "interval", "laps_down"])
    frames["laps_down"] = frames["laps_down"].fillna(0).astype(int)
    # Then I join tyres by lap: the stint started at or before the current lap, its age grows one per lap
    frames = pd.merge_asof(
        frames.sort_values("lap"), stints.sort_values("lap_start"), left_on="lap", right_on="lap_start", by="driver_number"
    )
    frames["tyre_age"] = frames["tyre_age_at_start"] + frames["lap"] - frames["lap_start"]
    # A car is in the pit lane between lane entry (exit date minus lane duration) and exit
    pit_lane = pits.assign(pit_exit=pits["date"], date=pits["date"] - pd.to_timedelta(pits["lane_duration"], unit="s"))
    frames = _last_known(frames.sort_values("date"), pit_lane.sort_values("date"), ["pit_exit"])
    frames["status"] = _car_status(frames, laps)
    frames["frame"] = ((frames["date"] - grid.iloc[0]) / FRAME).round().astype(int)
    columns = [
        "frame", "date", "driver_number", "x", "y", *CAR_CHANNELS, "lap", "position",
        "gap_to_leader", "interval", "laps_down", "compound", "tyre_age", "status",
    ]
    return frames[columns].sort_values(["frame", "driver_number"], ignore_index=True)


def _car_status(frames: pd.DataFrame, laps: pd.DataFrame) -> np.ndarray:
    # Here the chequered flag is the first car completing the last lap, cars stopping before it retired
    last_lap = laps["lap_number"].max()
    winner = laps[laps["lap_number"] == last_lap]
    chequered = (winner["date_start"] + pd.to_timedelta(winner["lap_duration"], unit="s")).min()
    done = frames["date"] > frames["finished_at"]
    in_pit = frames["date"] <= frames["pit_exit"]
    return np.select(
        [done & (frames["finished_at"] >= chequered), done, in_pit],
        ["FIN", "OUT", "PIT"],
        default="RUN",
    )
