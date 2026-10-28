"""CLI menu before the replay window: banner, then season, Grand Prix, race or sprint."""

import pandas as pd
import pyfiglet
import questionary
from prompt_toolkit import print_formatted_text
from prompt_toolkit.formatted_text import FormattedText
from prompt_toolkit.styles import Style

from pit_wall.core.ingestion import DataError, get_events, get_sessions

FIRST_SEASON = 2023
REPLAY_SESSIONS = ["Race", "Sprint"]
BANNER_FONT = "ansi_shadow"
# Side view, rear wing on the left, nose and front wing on the right
CAR = """\
▄▄▄▄▄▄▄
███████                      ▄▄▄▄
  ███          ▄▄▄▄▄▄▄▄▄▄▄▄▄█▀  ▀█▄▄
  ███▄▄▄▄▄▄██████████████████████████▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄
 ▄█████▄  ▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀  ▄█████▄ ▀▀▀▀▀▀██▄
█████████                              █████████    ▀▀▀▀
 ▀█████▀                                ▀█████▀"""
SPEED_LINES = {3: " ══ ═══ ", 4: "  ═ ══  "}
WALL_INDENT = 34
TAGLINES = ["", "", "  RACE REPLAY", "  OPENF1 · 2023+", "", ""]
BANNER_STYLE = Style.from_dict({
    "flag": "#e6ecff",
    "pit": "#e10600 bold",
    "car": "#e10600",
    "speed": "#00e68c",
    "wall": "#e6ecff bold",
    "tag": "#a0acd2 italic",
})
MENU_STYLE = questionary.Style([
    ("qmark", "fg:#e10600 bold"),
    ("question", "bold"),
    ("pointer", "fg:#00e68c bold"),
    ("highlighted", "fg:#00e68c bold"),
    ("answer", "fg:#00e68c bold"),
    ("instruction", "fg:#a0acd2"),
])


def _figlet(word: str) -> list[str]:
    return [line for line in pyfiglet.figlet_format(word, font=BANNER_FONT).splitlines() if line.strip()]


def print_banner() -> None:
    """PIT parked next to an F1 car, WALL staggered below, between chequered strips."""
    pit, wall, car = _figlet("PIT"), _figlet("WALL"), CAR.splitlines()
    # Here I bottom align PIT with the car, as if the car pulls out of the pit box
    pit = [""] * (len(car) - len(pit)) + pit
    pit_w = max(map(len, pit))
    width = pit_w + len(SPEED_LINES[3]) + max(map(len, car))
    flag = ("class:flag", "▀▄" * (width // 2) + "\n")
    fragments = [flag]
    for i, (p, c) in enumerate(zip(pit, car)):
        fragments += [("class:pit", p.ljust(pit_w)), ("class:speed", SPEED_LINES.get(i, " " * 8)), ("class:car", c + "\n")]
    fragments.append(("", "\n"))
    for w, tag in zip(wall, TAGLINES):
        fragments += [("class:wall", " " * WALL_INDENT + w), ("class:tag", tag + "\n")]
    fragments.append(flag)
    print_formatted_text(FormattedText(fragments), style=BANNER_STYLE)


def _select(question: str, choices: list) -> object:
    return questionary.select(question, choices=choices, qmark="▶", style=MENU_STYLE).unsafe_ask()


def pick_session() -> tuple[int, str]:
    """Ask season, Grand Prix and race or sprint, return (session_key, title). Ctrl+C raises KeyboardInterrupt."""
    # Here I list seasons newest first, OpenF1 starts in 2023
    seasons = [str(y) for y in range(pd.Timestamp.now(tz="UTC").year, FIRST_SEASON - 1, -1)]
    year = int(_select("Season", seasons))
    events = get_events(year)
    meeting_key, meeting_name = _select(
        "Grand Prix",
        [questionary.Choice(name, value=(key, name)) for name, key in zip(events["meeting_name"], events["meeting_key"])],
    )
    # Then I keep sessions a replay makes sense for, race and sprint have positions and intervals
    sessions = get_sessions(int(meeting_key))
    sessions = sessions[sessions["session_name"].isin(REPLAY_SESSIONS)]
    if sessions.empty:
        raise DataError("No race or sprint session for this Grand Prix yet")
    session_key, session_name = _select(
        "Session",
        [questionary.Choice(name, value=(key, name)) for name, key in zip(sessions["session_name"], sessions["session_key"])],
    )
    return int(session_key), f"{year} {meeting_name} {session_name}"
