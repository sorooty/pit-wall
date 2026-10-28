"""Core replay tests on synthetic data, no network."""

import numpy as np
import pandas as pd

from pit_wall.core.ingestion import UTC_DATE
from pit_wall.core.replay import (
    FRAME,
    _grid,
    chequered_flag,
    race_laps,
    race_window,
    sector_flags,
    track_status_frames,
    weather_frames,
)

T0 = pd.Timestamp("2024-03-02 15:00:00", tz="UTC")


def _at(seconds: list[float]) -> pd.Series:
    return pd.Series(T0 + pd.to_timedelta(seconds, unit="s")).astype(UTC_DATE)


def _laps() -> pd.DataFrame:
    # Here two cars do 2 laps of 100 s, car 2 is 1 s behind, both list an untimed cool down lap 3
    return pd.DataFrame({
        "driver_number": [1, 1, 1, 2, 2, 2],
        "lap_number": [1, 2, 3, 1, 2, 3],
        "date_start": _at([0, 100, 199.6, 1, 101, 201]),
        "lap_duration": [100, 100, np.nan, 100, 100, np.nan],
    })


def _race_control(rows: list[tuple[float, str | None, str | None, int | None, str]]) -> pd.DataFrame:
    # Here each row is (seconds, flag, scope, sector, message)
    seconds, flag, scope, sector, message = zip(*rows)
    return pd.DataFrame({"date": _at(list(seconds)), "flag": flag, "scope": scope, "sector": sector, "message": message})


def test_chequered_flag_and_cool_down_lap_dropped() -> None:
    # Here the winner crosses at 200 s, cool down laps start just before or after it and must go
    laps = _laps()
    assert chequered_flag(laps) == T0 + pd.Timedelta(seconds=200)
    kept = race_laps(laps)
    assert kept["lap_number"].max() == 2
    assert race_window(kept) == (T0, T0 + pd.Timedelta(seconds=201))


def test_grid_covers_race_plus_one_frame() -> None:
    grid = _grid(T0, T0 + pd.Timedelta(seconds=10))
    assert grid.iloc[0] == T0
    assert grid.iloc[-1] == T0 + pd.Timedelta(seconds=10) + FRAME


def test_track_status_virtual_safety_car_not_read_as_safety_car() -> None:
    rc = _race_control([
        (2, None, None, None, "VIRTUAL SAFETY CAR DEPLOYED"),
        (4, None, None, None, "VIRTUAL SAFETY CAR ENDING"),
        (6, None, None, None, "SAFETY CAR DEPLOYED"),
        (8, "RED", "Track", None, "RED FLAG"),
    ])
    status = track_status_frames(_grid(T0, T0 + pd.Timedelta(seconds=9)), rc).set_index("frame")["track_status"]
    hz = round(pd.Timedelta(seconds=1) / FRAME)
    assert [status[s * hz] for s in [1, 3, 5, 7, 9]] == ["GREEN", "VSC", "GREEN", "SC", "RED"]


def test_track_status_ignores_sector_yellow() -> None:
    rc = _race_control([(2, "YELLOW", "Sector", 5, "YELLOW IN TRACK SECTOR 5")])
    status = track_status_frames(_grid(T0, T0 + pd.Timedelta(seconds=4)), rc)
    assert (status["track_status"] == "GREEN").all()


def test_sector_flags_end_on_clear_or_track_reset_and_clip_to_race() -> None:
    # Here sector 5 clears itself, sector 9 is reset by a red flag, sector 3 starts before the race
    rc = _race_control([
        (-30, "YELLOW", "Sector", 3, ""),
        (10, "YELLOW", "Sector", 5, ""),
        (15, "CLEAR", "Sector", 5, ""),
        (20, "DOUBLE YELLOW", "Sector", 9, ""),
        (25, "RED", "Track", None, ""),
    ])
    flags = sector_flags(rc, T0, T0 + pd.Timedelta(seconds=100)).set_index("sector")
    assert flags.loc[3, "start"] == T0
    assert flags.loc[3, "end"] == T0 + pd.Timedelta(seconds=25)
    assert flags.loc[5, "end"] == T0 + pd.Timedelta(seconds=15)
    assert flags.loc[9, "flag"] == "DOUBLE YELLOW"
    assert flags.loc[9, "end"] == T0 + pd.Timedelta(seconds=25)


def test_weather_carries_last_sample_and_fills_before_first() -> None:
    weather = pd.DataFrame({"date": _at([1, 3]), "air_temperature": [20.0, 21.0]})
    frames = weather_frames(_grid(T0, T0 + pd.Timedelta(seconds=4)), weather).set_index("frame")["air_temperature"]
    hz = round(pd.Timedelta(seconds=1) / FRAME)
    assert [frames[0], frames[2 * hz], frames[4 * hz]] == [20.0, 20.0, 21.0]
