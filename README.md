# pit-wall

F1 race analysis like a performance engineer, built on OpenF1.

## Features (phase 1)
- Select year, GP, session and two drivers
- Fastest laps comparison vs distance: speed, throttle, brake, gear
- Cumulative delta time between drivers
- Track map colored by fastest driver per mini sector
- Table: sector times, speed trap

## Structure
```
f1_ml_project/            # project root
  pit_wall/
    core/                 # pure analysis, no UI imports, returns DataFrames or dicts
      ingestion.py        # OpenF1 REST loading + local HTTP cache
      telemetry.py        # comparisons, delta, mini sectors
      replay.py           # race replay frames (cars, track status, flags, outline, weather)
    app/
      main.py             # Streamlit UI, calls core only
    replay_app/           # Arcade race replay + CLI menu, calls core only
  tests/                  # pytest, core only
  cache/                  # HTTP cache (git ignored)
  requirements.txt
  README.md
```

## Setup
Requires Python 3.13 (Arcade dependencies have no 3.14 wheels yet).
```
py -3.13 -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

## Run
Race replay (Arcade, pick season, Grand Prix and race or sprint in the terminal):
```
python -m pit_wall.replay_app
```
Controls: SPACE play/pause, LEFT/RIGHT seek 10 s (SHIFT 60 s), UP/DOWN speed, R restart, G interval or gap to leader,
click a leaderboard row to toggle a driver card (max 3), click or drag the timeline to seek, ESC quit.
First load of a race takes ~2 min (OpenF1 rate limit), then it comes from the cache.

Streamlit:
```
python -m streamlit run pit_wall/app/main.py
```

## Tests
```
python -m pytest
```

## Data
Data comes from the [OpenF1 API](https://openf1.org) (2023 onwards, free, no account, non commercial use).
First request of a session downloads it, then it is read from `cache/openf1.sqlite`.
FastF1 is not used: since October 2026 F1 timing data requires an F1TV account, also for past sessions.

## Roadmap
- Tyre degradation with fuel correction
- Strategy Monte Carlo
- Video analysis
- Next.js dashboard on top of an API exposing `core`
