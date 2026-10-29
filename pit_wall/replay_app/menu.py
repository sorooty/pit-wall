"""CLI menu before the replay window: season, Grand Prix, race or sprint, with back and exit."""

import pandas as pd
import questionary
from prompt_toolkit import print_formatted_text
from prompt_toolkit.formatted_text import FormattedText
from prompt_toolkit.styles import Style

from pit_wall.core.ingestion import DataError, get_events, get_sessions

FIRST_SEASON = 2023
REPLAY_SESSIONS = ["Race", "Sprint"]
HINT = "↑↓ move · Enter select · Ctrl+C quit"
BACK, EXIT = "back", "exit"
WARN_STYLE = Style.from_dict({"warn": "#ffd200"})
MENU_STYLE = questionary.Style([
    ("qmark", "fg:#e10600 bold"),
    ("question", "bold"),
    ("pointer", "fg:#00e68c bold"),
    ("highlighted", "fg:#00e68c bold"),
    ("answer", "fg:#00e68c bold"),
    ("instruction", "fg:#a0acd2"),
])


def _warn(message: str) -> None:
    print_formatted_text(FormattedText([("class:warn", f"  ! {message}")]), style=WARN_STYLE)


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
