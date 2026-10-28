"""Replay window: layout, playback clock, keyboard and mouse controls."""

import arcade
from arcade.types import LBWH, Rect

from pit_wall.core.replay import REPLAY_HZ
from pit_wall.replay_app import theme as t
from pit_wall.replay_app import widgets as w
from pit_wall.replay_app.scene import Scene

SPEEDS = [0.5, 1, 2, 4, 8, 16, 32, 64]
DEFAULT_SPEED = 4
SEEK_S, BIG_SEEK_S = 10, 60
MAX_SELECTED = 3
MARGIN, SIDE_W, INFO_H, WEATHER_H, CONTROLS_H, TIMELINE_H = 10, 340, 130, 108, 150, 92


class ReplayWindow(arcade.Window):
    """Race replay: track map, leaderboard, driver cards, info panel and timeline."""

    def __init__(self, scene: Scene) -> None:
        super().__init__(1600, 900, f"PIT WALL  {scene.title}", resizable=True, vsync=True)
        t.load_fonts()
        self.scene = scene
        self.text = t.TextPool()
        self.frame = 0.0
        self.speed_idx = SPEEDS.index(DEFAULT_SPEED)
        self.paused = False
        self.to_leader = False
        # Here I follow the podium of the first frame by default
        self.selected = [int(s) for s in scene.order(0)[:MAX_SELECTED]]
        self.rows: list[tuple[Rect, int]] = []
        self.seek_bar: Rect | None = None
        self.background_color = t.BG

    def _layout(self) -> dict[str, Rect]:
        # Here I split the window: left column info, weather and cards, right column leaderboard and controls,
        # timeline at the bottom
        width, height = self.width, self.height
        body_bottom = MARGIN * 2 + TIMELINE_H
        body_h = height - body_bottom - MARGIN
        top = body_bottom + body_h
        right_x = width - SIDE_W - MARGIN
        return {
            "info": LBWH(MARGIN, top - INFO_H, SIDE_W, INFO_H),
            "weather": LBWH(MARGIN, top - INFO_H - MARGIN - WEATHER_H, SIDE_W, WEATHER_H),
            "cards": LBWH(MARGIN, body_bottom, SIDE_W, body_h - INFO_H - WEATHER_H - 2 * MARGIN),
            "board": LBWH(right_x, body_bottom + CONTROLS_H + MARGIN, SIDE_W, body_h - CONTROLS_H - MARGIN),
            "controls": LBWH(right_x, body_bottom, SIDE_W, CONTROLS_H),
            "track": LBWH(SIDE_W + 2 * MARGIN, body_bottom, width - 2 * SIDE_W - 4 * MARGIN, body_h),
            "timeline": LBWH(MARGIN, MARGIN, width - 2 * MARGIN, TIMELINE_H),
        }

    def on_draw(self) -> None:
        self.clear()
        box = self._layout()
        frame = int(self.frame)
        w.draw_track(self.scene, self.frame, box["track"], self.text, self.selected)
        w.draw_info(self.scene, self.frame, box["info"], self.text, SPEEDS[self.speed_idx], self.paused)
        w.draw_weather(self.scene, frame, box["weather"], self.text)
        w.draw_controls(box["controls"], self.text)
        for i, (slot, rect) in enumerate(zip(self.selected, w.card_rects(box["cards"], len(self.selected)))):
            w.draw_driver_card(self.scene, frame, slot, rect, self.text, f"card{i}")
        self.rows = w.draw_leaderboard(self.scene, frame, box["board"], self.text, self.selected, self.to_leader)
        self.seek_bar = w.draw_timeline(self.scene, self.frame, box["timeline"], self.text)
        self.text.draw()
        t.draw_scanlines(self.width, self.height)

    def on_update(self, delta_time: float) -> None:
        if self.paused:
            return
        # Here the clock advances in replay frames, speed is a multiple of real time
        self.frame += delta_time * REPLAY_HZ * SPEEDS[self.speed_idx]
        if self.frame >= self.scene.n_frames - 1:
            self.frame = self.scene.n_frames - 1
            self.paused = True

    def _seek(self, seconds: float) -> None:
        self.frame = min(max(self.frame + seconds * REPLAY_HZ, 0), self.scene.n_frames - 1)

    def on_key_press(self, symbol: int, modifiers: int) -> None:
        step = BIG_SEEK_S if modifiers & arcade.key.MOD_SHIFT else SEEK_S
        match symbol:
            case arcade.key.SPACE:
                self.paused = not self.paused
            case arcade.key.LEFT:
                self._seek(-step)
            case arcade.key.RIGHT:
                self._seek(step)
            case arcade.key.UP:
                self.speed_idx = min(self.speed_idx + 1, len(SPEEDS) - 1)
            case arcade.key.DOWN:
                self.speed_idx = max(self.speed_idx - 1, 0)
            case arcade.key.R:
                self.frame, self.paused = 0.0, False
            case arcade.key.G:
                self.to_leader = not self.to_leader
            case arcade.key.ESCAPE:
                self.close()

    def _seek_to(self, x: float) -> None:
        bar = self.seek_bar
        if bar is not None:
            share = min(max((x - bar.left) / bar.width, 0), 1)
            self.frame = share * (self.scene.n_frames - 1)

    def on_mouse_press(self, x: int, y: int, button: int, modifiers: int) -> None:
        # Here a click on the timeline seeks, a click on a leaderboard row toggles that driver card
        if self._on_bar(x, y):
            self._seek_to(x)
            return
        for row, slot in self.rows:
            if row.point_in_rect((x, y)):
                if slot in self.selected:
                    self.selected.remove(slot)
                else:
                    self.selected = (self.selected + [slot])[-MAX_SELECTED:]
                return

    def _on_bar(self, x: float, y: float) -> bool:
        # The bar is thin, I accept clicks a few pixels around it
        bar = self.seek_bar
        return bar is not None and bar.left <= x <= bar.right and bar.bottom - 8 <= y <= bar.top + 8

    def on_mouse_drag(self, x: int, y: int, dx: int, dy: int, buttons: int, modifiers: int) -> None:
        if self._on_bar(x, y):
            self._seek_to(x)
