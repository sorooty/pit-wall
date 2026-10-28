"""Fastest lap comparison: telemetry vs distance, delta time, mini sectors, sector table."""

import numpy as np
import pandas as pd

from pit_wall.core.ingestion import SPEED_TRAPS, DataError, get_drivers, get_laps, get_telemetry

# Margin around the lap window so distance can be anchored exactly at lap start
PAD = pd.Timedelta(seconds=1)
# Delta curve resolution, about 10 m per point on a 5 km lap
DELTA_POINTS = 500
SECTORS = ["duration_sector_1", "duration_sector_2", "duration_sector_3"]
# 8 mini sectors per official sector, so they never straddle a timing line
N_MINI_SECTORS = 24


def fastest_lap(laps: pd.DataFrame) -> pd.Series:
    """Fastest timed lap of one driver, pit out laps excluded.

    OpenF1 does not flag laps deleted for track limits, so this is the fastest timed lap, not always the official one.
    """
    # Here I keep timed laps that did not start from the pit lane, then I pick the shortest one
    timed = laps[laps["lap_duration"].notna() & ~laps["is_pit_out_lap"]]
    if timed.empty:
        raise DataError("No timed lap for this driver")
    return timed.loc[timed["lap_duration"].idxmin()]


def _rel_distance(lap: pd.Series, t: np.ndarray, dist: np.ndarray) -> np.ndarray:
    """Lap fraction anchored on official timing lines: 0 start, 1/3 and 2/3 sector lines, 1 finish.

    Integrated distance drifts differently per driver, anchoring on lines crossed at official times
    makes drivers comparable and the delta exact at each line. Falls back to start/finish if a sector time is missing.
    """
    sectors = lap[SECTORS].to_numpy(dtype=float)
    anchor_t = np.concatenate(([0.0], np.cumsum(sectors))) if np.isfinite(sectors).all() else np.array([0.0, lap["lap_duration"]])
    # Here I find the distance at each line crossing, then the segment each sample belongs to
    anchor_d = np.interp(anchor_t, t, dist)
    n = len(anchor_d) - 1
    k = np.clip(np.searchsorted(anchor_d, dist, side="right") - 1, 0, n - 1)
    # Then I place each sample inside its segment, edge segments extrapolate linearly for the padding samples
    return (k + (dist - anchor_d[k]) / (anchor_d[k + 1] - anchor_d[k])) / n


def lap_telemetry(session_key: int, lap: pd.Series) -> pd.DataFrame:
    """Telemetry of one lap: time (s from lap start), distance (m), rel_distance (0 to 1), speed, throttle, brake, n_gear, x, y.

    Keeps one sample beyond each lap edge so interpolation at exact lap start and end stays exact.
    """
    start = lap["date_start"]
    duration = lap["lap_duration"]
    tel = get_telemetry(session_key, int(lap["driver_number"]), start - PAD, start + pd.Timedelta(seconds=duration) + PAD)
    # Here I convert timestamps to seconds from lap start
    t = (tel["date"] - start).dt.total_seconds().to_numpy()
    # Then I integrate speed (km/h to m/s) over time with trapezoids to get the distance
    v = tel["speed"].to_numpy() / 3.6
    dist = np.concatenate(([0.0], np.cumsum(np.diff(t) * (v[1:] + v[:-1]) / 2)))
    # I shift it so distance is 0 exactly at lap start, the padding gives a point before it to interpolate
    dist -= np.interp(0.0, t, dist)
    tel = tel.drop(columns="date").assign(time=t, distance=dist, rel_distance=_rel_distance(lap, t, dist))
    # Finally I cut the padding off, keeping the closest sample outside each edge
    inside = (t >= 0) & (t <= duration)
    keep = inside.copy()
    keep[1:] |= inside[:-1]
    keep[:-1] |= inside[1:]
    return tel[keep].reset_index(drop=True)


def _time_at(tel: pd.DataFrame, rel: np.ndarray) -> np.ndarray:
    # Here I read the lap time at given lap fractions by linear interpolation, rel_distance is monotonic
    return np.interp(rel, tel["rel_distance"], tel["time"])


def _distance_at(tel: pd.DataFrame, rel: np.ndarray) -> np.ndarray:
    # Same idea to map lap fractions back to metres of a driver, used for plot axes
    return np.interp(rel, tel["rel_distance"], tel["distance"])


def delta_time(ref: pd.DataFrame, comp: pd.DataFrame) -> pd.DataFrame:
    """Cumulative gap of comp to ref: distance (m, ref lap), delta (s). Positive: comp slower.

    Exact at sector lines and finish. Between lines, slow corners can show ~0.5 s artefacts (4 Hz data, distance drift).
    """
    # Here I sample both laps on the same lap fraction grid, 0 and 1 included so the end delta is the lap time gap
    rel = np.linspace(0.0, 1.0, DELTA_POINTS)
    return pd.DataFrame({"distance": _distance_at(ref, rel), "delta": _time_at(comp, rel) - _time_at(ref, rel)})


def mini_sectors(tels: dict[str, pd.DataFrame], n_sectors: int = N_MINI_SECTORS) -> pd.DataFrame:
    """Fastest driver per mini sector, mapped on the first driver's lap: distance, x, y, mini_sector, fastest."""
    drivers = np.array(list(tels))
    # Here I cut the lap in equal lap fractions and read each driver's time spent in each mini sector
    edges = np.linspace(0.0, 1.0, n_sectors + 1)
    sector_times = np.vstack([np.diff(_time_at(tels[d], edges)) for d in drivers])
    fastest = drivers[sector_times.argmin(axis=0)]
    # Then I tag each sample of the first driver's lap with its mini sector to draw the track map
    ref = tels[drivers[0]]
    ref = ref[ref["rel_distance"].between(0.0, 1.0)]
    sector = np.clip(np.searchsorted(edges, ref["rel_distance"], side="right") - 1, 0, n_sectors - 1)
    return pd.DataFrame({
        "distance": ref["distance"].to_numpy(),
        "x": ref["x"].to_numpy(),
        "y": ref["y"].to_numpy(),
        "mini_sector": sector,
        "fastest": fastest[sector],
    })


def lap_summary(laps: dict[str, pd.Series]) -> pd.DataFrame:
    """One row per driver: driver, lap_number, lap_duration, gap (s to best), sectors (s), speed traps (km/h)."""
    # Here I stack the drivers' laps into a table keyed by driver
    df = pd.DataFrame(list(laps.values()), index=pd.Index(list(laps), name="driver"))
    df = df[["lap_number", "lap_duration", *SECTORS, *SPEED_TRAPS]]
    # Then I add the gap to the best lap of the selection
    df.insert(2, "gap", df["lap_duration"] - df["lap_duration"].min())
    return df.reset_index()


def compare_drivers(session_key: int, drivers: tuple[str, str]) -> dict[str, pd.DataFrame]:
    """Fastest lap comparison of two drivers (3 letter codes), first one is the reference.

    Returns telemetry (long, with driver column), delta, mini_sectors and summary DataFrames.
    """
    if len(set(drivers)) != 2:
        raise DataError("Select two different drivers")
    # Here I map the 3 letter codes to driver numbers and stop early on unknown drivers
    numbers = get_drivers(session_key).set_index("name_acronym")["driver_number"]
    missing = [d for d in drivers if d not in numbers.index]
    if missing:
        raise DataError(f"Not in session {session_key}: {', '.join(missing)}")
    # Then I load each fastest lap, prefixing errors with the driver code so the UI message is clear
    laps = {}
    for drv in drivers:
        try:
            laps[drv] = fastest_lap(get_laps(session_key, int(numbers[drv])))
        except DataError as exc:
            raise DataError(f"{drv}: {exc}") from exc
    tels = {drv: lap_telemetry(session_key, lap) for drv, lap in laps.items()}
    ref, comp = drivers
    return {
        "telemetry": pd.concat(tels, names=["driver"]).reset_index(level=0),
        "delta": delta_time(tels[ref], tels[comp]),
        "mini_sectors": mini_sectors(tels),
        "summary": lap_summary(laps),
    }
