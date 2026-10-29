"""Race replay frames: every car state on a fixed time grid, plus track status, outline and weather."""

from concurrent.futures import ThreadPoolExecutor

import numpy as np
import pandas as pd

from pit_wall.core.ingestion import (
    DataError,
    get_drivers,
    get_intervals,
    get_laps,
    get_pits,
    get_positions,
    get_race_control,
    get_stints,
    get_telemetry,
    get_weather,
)
from pit_wall.core.telemetry import fastest_lap, lap_telemetry

REPLAY_HZ = 4
FRAME = pd.Timedelta(seconds=1 / REPLAY_HZ)
# OpenF1 lap starts and start + duration can disagree by a few tenths
LAP_TOLERANCE = pd.Timedelta(seconds=1)
# Parallel telemetry downloads, each OpenF1 call waits ~2 s so 3 workers stay under its ~3 req/s limit
OPENF1_WORKERS = 3
CAR_CHANNELS = ["speed", "throttle", "brake", "n_gear", "drs"]


def _lap_ends(laps: pd.DataFrame) -> pd.Series:
    # Here a lap ends at its start plus its duration, NaT when OpenF1 has no time for it
    return laps["date_start"] + pd.to_timedelta(laps["lap_duration"], unit="s")


def chequered_flag(laps: pd.DataFrame) -> pd.Timestamp:
    """Chequered flag time (UTC): first car completing the last timed lap."""
    timed = laps[laps["lap_duration"].notna()]
    if timed.empty:
        raise DataError("No timed lap in this session")
    last = timed[timed["lap_number"] == timed["lap_number"].max()]
    return _lap_ends(last).min()


def race_laps(laps: pd.DataFrame) -> pd.DataFrame:
    """Laps started before the chequered flag, OpenF1 also lists the untimed cool down lap."""
    return laps[laps["date_start"] < chequered_flag(laps) - LAP_TOLERANCE].reset_index(drop=True)


def final_classification(laps: pd.DataFrame) -> pd.DataFrame:
    """Cars taking the flag, from lap times: driver_number, position, gap_to_leader (s), interval (s), laps_down.

    Gaps are NaN for lapped cars, laps_down says how far behind they are.
    """
    ends = laps.assign(end=_lap_ends(laps)).dropna(subset=["end"])
    last = ends.sort_values("end").groupby("driver_number").tail(1)
    last = last[last["end"] >= chequered_flag(laps) - LAP_TOLERANCE]
    # Here more laps first, then earlier crossing, like the official order
    last = last.sort_values(["lap_number", "end"], ascending=[False, True], ignore_index=True)
    laps_down = last["lap_number"].iloc[0] - last["lap_number"]
    gap = (last["end"] - last["end"].iloc[0]).dt.total_seconds()
    on_lead_lap = laps_down == 0
    return pd.DataFrame({
        "driver_number": last["driver_number"],
        "position": np.arange(1, len(last) + 1),
        "gap_to_leader": gap.where(on_lead_lap),
        "interval": gap.diff().fillna(0.0).where(on_lead_lap),
        "laps_down": laps_down,
    })


def race_window(laps: pd.DataFrame) -> tuple[pd.Timestamp, pd.Timestamp]:
    """Race start (lap 1 start) and end (last car crossing the line), UTC."""
    first = laps.loc[laps["lap_number"] == 1, "date_start"]
    if first.empty:
        raise DataError("No lap 1 in this session, replay needs a race or sprint")
    # Here the end is the latest lap end over all cars, so lapped cars finish their last lap too
    return first.min(), _lap_ends(laps).max()


def _seconds(dates: pd.Series, start: pd.Timestamp) -> np.ndarray:
    # Here I turn dates into float seconds from start, a unit-safe base for np.interp and searchsorted
    return (dates - start).dt.total_seconds().to_numpy()


def _grid(start: pd.Timestamp, end: pd.Timestamp) -> pd.Series:
    # Here I build the replay clock, one frame every 1/REPLAY_HZ s, in µs like parsed OpenF1 dates so merges match
    # One extra frame past end so the last car is shown crossing the line
    return pd.Series(pd.date_range(start, end + FRAME, freq=FRAME, unit="us"), name="date")


def _driver_frames(laps: pd.DataFrame, tel: pd.DataFrame, grid: pd.Series) -> pd.DataFrame:
    """One driver on the grid: date, driver_number, x, y, car channels, lap, finished_at (end of last lap)."""
    driver_number = int(laps["driver_number"].iloc[0])
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
    lap_end = last["date_start"] + pd.Timedelta(seconds=np.nan_to_num(last["lap_duration"]))
    # Capped by its last movement, OpenF1 sometimes dates the lap of a crashed car at the end of the race
    moving = tel.loc[tel["speed"] > 0, "date"]
    frames["finished_at"] = min(lap_end, moving.max()) if not moving.empty else lap_end
    return frames


def _last_known(
    frames: pd.DataFrame, events: pd.DataFrame, columns: list[str], by: str | None = "driver_number"
) -> pd.DataFrame:
    # Here I attach to each frame the latest event value (of the same driver if by), frames must be sorted by date
    keys = ["date", by] if by else ["date"]
    events = events[[*keys, *columns]].assign(date=events["date"].dt.as_unit(frames["date"].dt.unit))
    return pd.merge_asof(frames, events, on="date", by=by)


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
    # Here I download every driver telemetry in parallel, then build each one on the shared clock (vectorized per driver)
    groups = [drv_laps for _, drv_laps in laps.groupby("driver_number")]
    with ThreadPoolExecutor(max_workers=OPENF1_WORKERS) as pool:
        tels = list(pool.map(lambda g: get_telemetry(session_key, int(g["driver_number"].iloc[0])), groups))
    frames = pd.concat([_driver_frames(drv_laps, tel, grid) for drv_laps, tel in zip(groups, tels)])
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
    # Once a car takes the flag I freeze its classification, live intervals keep drifting during the cool down lap
    finished = frames["status"].eq("FIN")
    final = final_classification(laps).set_index("driver_number").reindex(frames.loc[finished, "driver_number"])
    for column in ["position", "gap_to_leader", "interval"]:
        frames.loc[finished, column] = final[column].to_numpy(dtype=float)
    frames.loc[finished, "laps_down"] = final["laps_down"].fillna(0).to_numpy(dtype=int)
    frames["frame"] = ((frames["date"] - grid.iloc[0]) / FRAME).round().astype(int)
    columns = [
        "frame", "date", "driver_number", "x", "y", *CAR_CHANNELS, "lap", "position",
        "gap_to_leader", "interval", "laps_down", "compound", "tyre_age", "status",
    ]
    return frames[columns].sort_values(["frame", "driver_number"], ignore_index=True)


def track_status_frames(grid: pd.Series, race_control: pd.DataFrame) -> pd.DataFrame:
    """Track-wide status at every frame: frame, date, track_status (GREEN, YELLOW, SC, VSC, RED, CHEQUERED)."""
    frames = pd.DataFrame({"frame": np.arange(len(grid)), "date": grid})
    if race_control.empty:
        return frames.assign(track_status="GREEN")
    # Here I map each message to the status it starts, VSC is tested before SC since its text contains it
    msg, flag = race_control["message"].fillna("").str.upper(), race_control["flag"]
    track_wide = race_control["scope"].eq("Track")
    status = np.select(
        [
            msg.str.contains("VIRTUAL SAFETY CAR DEPLOYED"),
            msg.str.contains("SAFETY CAR DEPLOYED"),
            msg.str.contains("VIRTUAL SAFETY CAR ENDING"),
            flag.eq("RED"),
            flag.eq("CHEQUERED"),
            track_wide & flag.isin(["YELLOW", "DOUBLE YELLOW"]),
            track_wide & flag.isin(["GREEN", "CLEAR"]),
        ],
        ["VSC", "SC", "GREEN", "RED", "CHEQUERED", "YELLOW", "GREEN"],
        default="",
    )
    events = race_control.loc[status != "", ["date"]].assign(track_status=status[status != ""])
    # Then each frame keeps the latest status, green before any event
    frames = _last_known(frames, events, ["track_status"], by=None)
    frames["track_status"] = frames["track_status"].fillna("GREEN")
    return frames


def weather_frames(grid: pd.Series, weather: pd.DataFrame) -> pd.DataFrame:
    """Latest weather sample at every frame: frame, date, then get_weather columns."""
    frames = pd.DataFrame({"frame": np.arange(len(grid)), "date": grid})
    # Here weather comes every minute, I carry the last sample forward, the first one if the race starts before it
    columns = [c for c in weather.columns if c != "date"]
    return _last_known(frames, weather, columns, by=None).bfill()


def sector_flags(race_control: pd.DataFrame, race_start: pd.Timestamp, race_end: pd.Timestamp) -> pd.DataFrame:
    """Marshal sector yellow periods within the race: sector, flag (YELLOW, DOUBLE YELLOW), start, end (UTC)."""
    columns = ["sector", "flag", "start", "end"]
    is_sector = race_control["scope"].eq("Sector") & race_control["flag"].isin(["YELLOW", "DOUBLE YELLOW", "CLEAR"])
    sector = race_control.loc[is_sector, ["date", "sector", "flag"]]
    if sector.empty:
        return pd.DataFrame(columns=columns)
    # Here a sector flag lasts until the next message of the same sector
    sector["next_sector"] = sector.groupby("sector")["date"].shift(-1)
    # Or until a track-wide green, clear, red or chequered, which resets every sector
    resets = race_control.loc[
        race_control["scope"].eq("Track") & race_control["flag"].isin(["GREEN", "CLEAR", "RED", "CHEQUERED"]), ["date"]
    ].rename(columns={"date": "reset"})
    sector = pd.merge_asof(
        sector, resets, left_on="date", right_on="reset", direction="forward", allow_exact_matches=False
    )
    sector["end"] = sector[["next_sector", "reset"]].min(axis=1).fillna(race_end)
    # Then I keep yellows overlapping the race, clipped to it (pre race and post race messages are dropped)
    periods = sector[sector["flag"].ne("CLEAR") & (sector["end"] > race_start) & (sector["date"] < race_end)]
    periods = periods.assign(start=periods["date"].clip(lower=race_start), end=periods["end"].clip(upper=race_end))
    return periods.astype({"sector": int})[columns].reset_index(drop=True)


def track_outline(session_key: int, laps: pd.DataFrame) -> pd.DataFrame:
    """Track line from the fastest lap of the session: distance (m), x, y. First row is the finish line."""
    # Here the fastest lap is a clean flying lap, its path starts and ends on the timing line, same x/y as car frames
    return lap_telemetry(session_key, fastest_lap(laps))[["distance", "x", "y"]]


def _car_status(frames: pd.DataFrame, laps: pd.DataFrame) -> np.ndarray:
    # Here cars that stopped racing after the chequered flag finished, the others retired
    chequered = chequered_flag(laps)
    done = frames["date"] > frames["finished_at"]
    in_pit = frames["date"] <= frames["pit_exit"]
    return np.select(
        [done & (frames["finished_at"] >= chequered), done, in_pit],
        ["FIN", "OUT", "PIT"],
        default="RUN",
    )


def leader_laps(cars: pd.DataFrame) -> pd.DataFrame:
    """First frame of each lap of the race leader: frame, lap. Lap marks for timelines."""
    # Here cars is sorted by frame, so the first leader row of each lap is where that lap starts
    lead = cars[(cars["position"] == 1) & (cars["lap"] > 0)]
    return lead.drop_duplicates("lap")[["frame", "lap"]].reset_index(drop=True)


def build_replay(session_key: int) -> dict[str, pd.DataFrame]:
    """Everything a replay UI draws for a race or sprint, frames at REPLAY_HZ.

    Keys: drivers, cars (car_frames), leader_laps, track_status, sector_flags, outline, weather.
    """
    # Here I load the race once, then build every layer on the same clock so UIs only index by frame
    laps = race_laps(get_laps(session_key))
    start, end = race_window(laps)
    grid = _grid(start, end)
    race_control = get_race_control(session_key)
    cars = car_frames(
        session_key, laps, grid, get_positions(session_key), get_intervals(session_key),
        get_stints(session_key), get_pits(session_key),
    )
    return {
        "drivers": get_drivers(session_key),
        "cars": cars,
        "leader_laps": leader_laps(cars),
        "track_status": track_status_frames(grid, race_control),
        "sector_flags": sector_flags(race_control, start, end),
        "outline": track_outline(session_key, laps),
        "weather": weather_frames(grid, get_weather(session_key)),
    }
