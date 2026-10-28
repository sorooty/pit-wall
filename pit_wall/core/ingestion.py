"""OpenF1 data loading with local HTTP cache."""

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
CAR_COLUMNS = ["date", "speed", "throttle", "brake", "n_gear"]


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
        resp = _http().get(f"{BASE_URL}/{endpoint}", params=params, timeout=TIMEOUT_S)
        if resp.status_code == 404:
            return pd.DataFrame()
        resp.raise_for_status()
    except requests.RequestException as exc:
        raise DataError(f"OpenF1 {endpoint} request failed: {exc}") from exc
    return pd.DataFrame(resp.json())


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


def get_telemetry(session_key: int, driver_number: int, start: pd.Timestamp, end: pd.Timestamp) -> pd.DataFrame:
    """Car data with interpolated x/y between start and end (UTC): date, speed, throttle, brake 0/1, n_gear, x, y."""
    # Here I fetch car data and positions on the same time window, OpenF1 filters with date> and date<
    window = {"session_key": session_key, "driver_number": driver_number, "date>": start.isoformat(), "date<": end.isoformat()}
    car, pos = _get("car_data", **window), _get("location", **window)
    if car.empty or pos.empty:
        raise DataError(f"No telemetry for driver {driver_number} between {start} and {end}")
    car = car[CAR_COLUMNS].assign(date=pd.to_datetime(car["date"], utc=True, format="ISO8601")).sort_values("date")
    pos = pos.assign(date=pd.to_datetime(pos["date"], utc=True, format="ISO8601")).sort_values("date")
    # Then I interpolate x/y at car data timestamps (both ~4 Hz but not aligned), more accurate than nearest match
    car_t, pos_t = car["date"].astype("int64"), pos["date"].astype("int64")
    car["x"] = np.interp(car_t, pos_t, pos["x"])
    car["y"] = np.interp(car_t, pos_t, pos["y"])
    # OpenF1 brake is 0 or 100, I keep it as an on/off flag
    car["brake"] = (car["brake"] > 0).astype(int)
    return car.reset_index(drop=True)
