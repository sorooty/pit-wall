"""Core telemetry tests on synthetic data, no network."""

import numpy as np
import pandas as pd
import pytest

from pit_wall.core.ingestion import DataError
from pit_wall.core.ingestion import SPEED_TRAPS
from pit_wall.core.telemetry import SECTORS, N_MINI_SECTORS, delta_time, fastest_lap, lap_summary, mini_sectors

TRACK_M = 5000.0


def _tel(times: np.ndarray) -> pd.DataFrame:
    # Here I build a lap telemetry with samples evenly spread on a circular track, times sets the pace
    rel = np.linspace(0.0, 1.0, len(times))
    angle = 2 * np.pi * rel
    return pd.DataFrame({
        "time": times,
        "distance": rel * TRACK_M,
        "rel_distance": rel,
        "x": np.cos(angle),
        "y": np.sin(angle),
    })


def test_fastest_lap_skips_pit_out_and_untimed_laps() -> None:
    # Here the pit out lap is the shortest and one lap has no time, so lap 3 must win
    laps = pd.DataFrame({
        "lap_number": [1, 2, 3, 4],
        "lap_duration": [70.0, np.nan, 80.0, 81.0],
        "is_pit_out_lap": [True, False, False, False],
    })
    assert fastest_lap(laps)["lap_number"] == 3


def test_fastest_lap_raises_without_timed_lap() -> None:
    laps = pd.DataFrame({"lap_duration": [np.nan, 75.0], "is_pit_out_lap": [False, True]})
    with pytest.raises(DataError):
        fastest_lap(laps)


def test_delta_time_constant_pace() -> None:
    # Here both laps run at constant speed, 80 s vs 82 s, so the gap grows linearly from 0 to 2 s
    delta = delta_time(_tel(np.linspace(0, 80, 200)), _tel(np.linspace(0, 82, 200)))
    assert delta["delta"].iloc[0] == pytest.approx(0.0)
    assert delta["delta"].iloc[-1] == pytest.approx(2.0)
    expected = 2.0 * delta["distance"] / TRACK_M
    np.testing.assert_allclose(delta["delta"], expected, atol=1e-9)


def test_mini_sectors_split_by_half() -> None:
    # Here A runs the first half in 38 s and the second in 42 s, B runs both in 40 s, same lap time
    rel = np.linspace(0.0, 1.0, 241)
    a = _tel(np.where(rel <= 0.5, rel * 2 * 38, 38 + (rel - 0.5) * 2 * 42))
    b = _tel(rel * 80)
    result = mini_sectors({"A": a, "B": b})
    # Then each mini sector must have one winner, A on the first half and B on the second
    winners = result.groupby("mini_sector")["fastest"].agg(set)
    half = N_MINI_SECTORS // 2
    assert winners.index.tolist() == list(range(N_MINI_SECTORS))
    assert all(w == {"A"} for w in winners.iloc[:half])
    assert all(w == {"B"} for w in winners.iloc[half:])
    # The map follows A's lap in order
    assert result["mini_sector"].is_monotonic_increasing
    np.testing.assert_allclose(result[["x", "y"]], a[["x", "y"]])


def _lap(number: int, duration: float) -> pd.Series:
    # Here I build a lap row with equal sectors and fixed speed traps
    return pd.Series({
        "lap_number": number,
        "lap_duration": duration,
        **dict.fromkeys(SECTORS, duration / 3),
        **dict.fromkeys(SPEED_TRAPS, 330.0),
    })


def test_lap_summary_gap_to_best() -> None:
    # Here the slower driver is passed first, the gap must still be measured to the best lap
    summary = lap_summary({"SLO": _lap(5, 80.5), "FST": _lap(7, 80.0)})
    assert summary.columns.tolist() == ["driver", "lap_number", "lap_duration", "gap", *SECTORS, *SPEED_TRAPS]
    assert summary["driver"].tolist() == ["SLO", "FST"]
    np.testing.assert_allclose(summary["gap"], [0.5, 0.0])
