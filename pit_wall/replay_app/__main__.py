"""Entry point: python -m pit_wall.replay_app"""

import sys

import arcade

from pit_wall.core.ingestion import DataError
from pit_wall.core.replay import build_replay
from pit_wall.replay_app.menu import pick_session, print_banner
from pit_wall.replay_app.scene import Scene
from pit_wall.replay_app.window import ReplayWindow


def main() -> int:
    """Menu, then load the race and open the replay window. Exit code 1 on data errors."""
    try:
        print_banner()
        picked = pick_session()
        if picked is None:
            return 0
        session_key, title = picked
        # Here the first load of a race can take a couple of minutes (OpenF1 rate limit), then it comes from the cache
        print("Loading replay, first load of a race can take ~2 min...")
        scene = Scene(build_replay(session_key), title)
    except KeyboardInterrupt:
        return 0
    except DataError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    ReplayWindow(scene)
    arcade.run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
