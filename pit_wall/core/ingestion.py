"""OpenF1 data loading with local HTTP cache."""

import io
from functools import cache
from pathlib import Path

import numpy as np
import pandas as pd
import requests
from requests.adapters import HTTPAdapter
from requests_cache import NEVER_EXPIRE, CachedSession
from urllib3.util.retry import Retry

BASE_URL = "https://api.openf1.org/v1"
TIMEOUT_S = 30
# Here I put the sqlite HTTP cache at the repo root, ignored by git
CACHE_DIR = Path(__file__).resolve().parents[2] / "cache"
# Past data never changes, only the session list grows during a season
CACHE_EXPIRY = {f"{BASE_URL}/sessions": 3600, "*": NEVER_EXPIRE}
FALLBACK_COLOUR = "#9E9E9E"
LAP_COLUMNS = [
    "driver_number", "lap_number", "date_start", "lap_duration",
    "duration_sector_1", "duration_sector_2", "duration_sector_3",
    "i1_speed", "i2_speed", "st_speed", "is_pit_out_lap",
]
SPEED_TRAPS = ["i1_speed", "i2_speed", "st_speed"]
UTC_DATE = "datetime64[us, UTC]"
CAR_COLUMNS = ["date", "speed", "throttle", "brake", "n_gear", "drs"]
# OpenF1 DRS codes: 0-9 closed or eligible, 10/12/14 open
DRS_OPEN_MIN = 10


class DataError(ValueError):
    """Requested data missing or unavailable."""


@cache
def _http() -> CachedSession:
    # Here we build one cached session for the whole app, @cache makes it a singleton
    CACHE_DIR.mkdir(exist_ok=True)
    session = CachedSession(str(CACHE_DIR / "openf1"), backend="sqlite", urls_expire_after=CACHE_EXPIRY)
    # Then I mount a retry policy so rate limit (429) and server errors back off instead of failing
    retry = Retry(total=4, backoff_factor=1, status_forcelist=(429, 500, 502, 503, 504))
    session.mount("https://", HTTPAdapter(max_retries=retry))
    return session


def _get(endpoint: str, **params: str | int | None) -> pd.DataFrame:
    """GET an OpenF1 endpoint as a DataFrame, empty if no results. None params are skipped."""
    # Here I call the API through the cache, OpenF1 answers 404 when a filter matches nothing
    try:
        resp = _http().get(f"{BASE_URL}/{endpoint}", params={**params, "csv": "true"}, timeout=TIMEOUT_S)
        if resp.status_code == 404:
            return pd.DataFrame()
        resp.raise_for_status()
    except requests.RequestException as exc:
        raise DataError(f"OpenF1 {endpoint} request failed: {exc}") from exc
    # I ask for CSV: half the size of JSON and parsed by pandas' C reader, hex colours stay text
    return pd.read_csv(io.StringIO(resp.text), dtype={"team_colour": str})


def _dated(endpoint: str, **params: str | int | None) -> pd.DataFrame:
    """Like _get, with the date column parsed to UTC and rows sorted by date."""
    df = _get(endpoint, **params)
    if df.empty:
        return df
    # Here I parse ISO dates (mixed precision in OpenF1) then sort, merge_asof and interp need ordered time
    return df.assign(date=pd.to_datetime(df["date"], utc=True, format="ISO8601")).sort_values("date", ignore_index=True)


def _started(date_start: pd.Series) -> pd.Series:
    # Here I compare ISO dates in UTC with now, future events and sessions have no data yet
    return pd.to_datetime(date_start, utc=True) <= pd.Timestamp.now(tz="UTC")


def get_events(year: int) -> pd.DataFrame:
    """Started Grand Prix of a season: meeting_key, meeting_name, circuit_short_name."""
    meetings = _get("meetings", year=year)
    if meetings.empty:
        raise DataError(f"No events for {year} (OpenF1 covers 2023 onwards)")
    # Here I drop testing and cancelled meetings, then keep only started ones
    is_gp = ~meetings["meeting_name"].str.contains("Testing") & ~meetings["is_cancelled"].fillna(False).astype(bool)
    events = meetings.loc[is_gp & _started(meetings["date_start"]), ["meeting_key", "meeting_name", "circuit_short_name"]]
    if events.empty:
        raise DataError(f"No started Grand Prix in {year}")
    return events.reset_index(drop=True)


def get_sessions(meeting_key: int) -> pd.DataFrame:
    """Started sessions of a Grand Prix in time order: session_key, session_name."""
    # Here I fetch the sessions of the meeting, then keep started ones sorted by start date
    sessions = _get("sessions", meeting_key=meeting_key)
    if sessions.empty:
        raise DataError(f"No sessions for meeting {meeting_key}")
    sessions = sessions[_started(sessions["date_start"])].sort_values("date_start")
    if sessions.empty:
        raise DataError(f"No started session for meeting {meeting_key}")
    return sessions[["session_key", "session_name"]].reset_index(drop=True)


def get_drivers(session_key: int) -> pd.DataFrame:
    """Session drivers: driver_number, name_acronym, team_name, colour, teammate_rank."""
    drivers = _get("drivers", session_key=session_key)
    if drivers.empty:
        raise DataError(f"No drivers for session {session_key}")
    drivers = drivers.sort_values("driver_number")
    # Here I turn the hex team colour into a CSS colour, grey when missing
    drivers["colour"] = ("#" + drivers["team_colour"]).fillna(FALLBACK_COLOUR)
    # Then I rank teammates 0/1 so each UI can tell them apart its own way (dash, outline...)
    drivers["teammate_rank"] = drivers.groupby("team_name").cumcount()
    return drivers[["driver_number", "name_acronym", "team_name", "colour", "teammate_rank"]].reset_index(drop=True)


def get_laps(session_key: int, driver_number: int | None = None) -> pd.DataFrame:
    """Laps of one driver, or all drivers if None. Times in s, speeds in km/h, date_start UTC."""
    laps = _get("laps", session_key=session_key, driver_number=driver_number)
    if laps.empty:
        who = f"driver {driver_number}" if driver_number is not None else "any driver"
        raise DataError(f"No laps for {who} in session {session_key}")
    laps = laps[LAP_COLUMNS].sort_values(["driver_number", "lap_number"])
    # Here I parse lap start to UTC datetime, it is the anchor to slice telemetry later
    laps["date_start"] = pd.to_datetime(laps["date_start"], utc=True, format="ISO8601")
    # Then I cast speed traps to float so missing values become NaN instead of breaking dtypes
    laps[SPEED_TRAPS] = laps[SPEED_TRAPS].astype(float)
    return laps.reset_index(drop=True)


def get_telemetry(
    session_key: int, driver_number: int, start: pd.Timestamp | None = None, end: pd.Timestamp | None = None
) -> pd.DataFrame:
    """Car data with interpolated x/y, whole session if no window (UTC).

    Columns: date, speed, throttle, brake 0/1, n_gear, drs 0/1 (open), x, y.
    """
    # Here I fetch car data and positions on the same time window, OpenF1 filters with date> and date<
    window = {
        "session_key": session_key,
        "driver_number": driver_number,
        "date>": start.isoformat() if start is not None else None,
        "date<": end.isoformat() if end is not None else None,
    }
    car, pos = _dated("car_data", **window), _dated("location", **window)
    if car.empty or pos.empty:
        raise DataError(f"No telemetry for driver {driver_number} in session {session_key}")
    car = car[CAR_COLUMNS]
    # Then I interpolate x/y at car data timestamps (both ~4 Hz but not aligned), more accurate than nearest match
    car_t, pos_t = car["date"].astype("int64"), pos["date"].astype("int64")
    car["x"] = np.interp(car_t, pos_t, pos["x"])
    car["y"] = np.interp(car_t, pos_t, pos["y"])
    # OpenF1 brake is 0 or 100 and DRS uses codes where 10, 12, 14 mean open, I keep both as on/off flags
    car["brake"] = (car["brake"] > 0).astype(int)
    car["drs"] = (car["drs"] >= DRS_OPEN_MIN).astype(int)
    return car


def get_positions(session_key: int) -> pd.DataFrame:
    """Classification changes: date, driver_number, position. One row per change, not per sample."""
    positions = _dated("position", session_key=session_key)
    if positions.empty:
        raise DataError(f"No positions for session {session_key}")
    return positions[["date", "driver_number", "position"]]


def get_intervals(session_key: int) -> pd.DataFrame:
    """Race gaps every ~4 s: date, driver_number, gap_to_leader (s), interval (s), laps_down."""
    intervals = _dated("intervals", session_key=session_key)
    if intervals.empty:
        raise DataError(f"No intervals for session {session_key} (race and sprint only)")
    # Here OpenF1 mixes seconds and text like "+1 LAP", I split it into a numeric gap and a laps down count
    gap = intervals["gap_to_leader"].astype(str)
    intervals["laps_down"] = pd.to_numeric(gap.str.extract(r"\+(\d+) LAP", expand=False)).fillna(0).astype(int)
    intervals["gap_to_leader"] = pd.to_numeric(gap, errors="coerce")
    intervals["interval"] = pd.to_numeric(intervals["interval"], errors="coerce")
    return intervals[["date", "driver_number", "gap_to_leader", "interval", "laps_down"]]


def get_stints(session_key: int) -> pd.DataFrame:
    """Tyre stints: driver_number, stint_number, lap_start, lap_end, compound, tyre_age_at_start."""
    stints = _get("stints", session_key=session_key)
    if stints.empty:
        raise DataError(f"No stints for session {session_key}")
    columns = ["driver_number", "stint_number", "lap_start", "lap_end", "compound", "tyre_age_at_start"]
    # Here I drop stints without laps (car out on lap 1), so lap_start is an int lap number like in laps
    stints = stints.dropna(subset=["lap_start"]).astype({"lap_start": int})
    return stints[columns].sort_values(["driver_number", "stint_number"], ignore_index=True)


def get_pits(session_key: int) -> pd.DataFrame:
    """Pit stops: date, driver_number, lap_number, lane_duration (s), stop_duration (s). Empty if no stop."""
    pits = _dated("pit", session_key=session_key)
    columns = ["date", "driver_number", "lap_number", "lane_duration", "stop_duration"]
    # Here I keep typed columns even when empty, so date and duration arithmetic still works downstream
    return pits.reindex(columns=columns).astype({"date": UTC_DATE, "driver_number": int, "lane_duration": float, "stop_duration": float})


def get_race_control(session_key: int) -> pd.DataFrame:
    """Race control messages: date, lap_number, category, flag, scope, sector, driver_number, message."""
    messages = _dated("race_control", session_key=session_key)
    columns = ["date", "lap_number", "category", "flag", "scope", "sector", "driver_number", "message"]
    return messages.reindex(columns=columns).astype({"date": UTC_DATE})


def get_weather(session_key: int) -> pd.DataFrame:
    """Weather every minute: date, air/track temperature (C), humidity (%), wind speed (m/s) and direction, rainfall."""
    weather = _dated("weather", session_key=session_key)
    if weather.empty:
        raise DataError(f"No weather for session {session_key}")
    columns = ["date", "air_temperature", "track_temperature", "humidity", "wind_speed", "wind_direction", "rainfall"]
    return weather[columns]
