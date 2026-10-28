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
TAGLINES = [("tag", ""), ("tag", "  RACE REPLAY"), ("tag", "  OPENF1 · 2023+"), ("tag", ""), ("author", "  by Sensey"), ("tag", "")]
HINT = "↑↓ move · Enter select · Ctrl+C quit"
BACK, EXIT = "back", "exit"
BANNER_STYLE = Style.from_dict({
    "flag": "#e6ecff",
    "pit": "#e10600 bold",
    "car": "#e10600",
    "speed": "#00e68c",
    "wall": "#e6ecff bold",
    "tag": "#a0acd2 italic",
    "author": "#00e68c bold italic",
    "hint": "#a0acd2",
    "warn": "#ffd200",
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
    for w, (tag_style, tag) in zip(wall, TAGLINES):
        fragments += [("class:wall", " " * WALL_INDENT + w), (f"class:{tag_style}", tag + "\n")]
    fragments += [flag, ("class:hint", "  Pick a season, a Grand Prix and a session. Back returns one step, Exit or Ctrl+C quits.\n")]
    print_formatted_text(FormattedText(fragments), style=BANNER_STYLE)


def _warn(message: str) -> None:
    print_formatted_text(FormattedText([("class:warn", f"  ! {message}")]), style=BANNER_STYLE)


def _select(question: str, choices: list, first_step: bool) -> object:
    """Arrow key menu ending with Back (one step up), or Exit on the first step."""
    last = questionary.Choice("✕  Exit", value=EXIT) if first_step else questionary.Choice("←  Back", value=BACK)
    return questionary.select(
        question, choices=[*choices, questionary.Separator(), last], qmark="▶", style=MENU_STYLE, instruction=HINT
    ).unsafe_ask()


def _choices(names: pd.Series, keys: pd.Series) -> list[questionary.Choice]:
    return [questionary.Choice(name, value=(int(key), name)) for name, key in zip(names, keys)]


def pick_session() -> tuple[int, str] | None:
    """Ask season, Grand Prix and race or sprint, return (session_key, title), None on Exit.

    Back returns to the previous step. Ctrl+C raises KeyboardInterrupt.
    """
    # Here I walk the three steps as a small state machine, Back moves one step up, no recursion
    seasons = [str(y) for y in range(pd.Timestamp.now(tz="UTC").year, FIRST_SEASON - 1, -1)]
    step, year, meeting = 0, 0, (0, "")
    events = sessions = pd.DataFrame()
    while True:
        if step == 0:
            answer = _select("Season", seasons, first_step=True)
            if answer == EXIT:
                return None
            year = int(answer)
            try:
                events = get_events(year)
            except DataError as exc:
                _warn(str(exc))
                continue
            step = 1
        elif step == 1:
            answer = _select(f"Grand Prix  {year}", _choices(events["meeting_name"], events["meeting_key"]), first_step=False)
            if answer == BACK:
                step = 0
                continue
            meeting = answer
            # Then I keep sessions a replay makes sense for, race and sprint have positions and intervals
            sessions = get_sessions(meeting[0])
            sessions = sessions[sessions["session_name"].isin(REPLAY_SESSIONS)]
            if sessions.empty:
                _warn("No race or sprint session for this Grand Prix yet")
                continue
            step = 2
        else:
            answer = _select(f"Session  {meeting[1]}", _choices(sessions["session_name"], sessions["session_key"]), first_step=False)
            if answer == BACK:
                step = 1
                continue
            session_key, session_name = answer
            return session_key, f"{year} {meeting[1]} {session_name}"
