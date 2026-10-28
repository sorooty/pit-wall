"""PS2 era look: palette, fonts (pixel titles, clean body), bevelled panels, keycaps, text pool."""

import arcade
import pyglet
from arcade.types import LBWH, Color, Rect

BG = Color(8, 10, 22)
SCANLINE = Color(0, 0, 0, 38)
PANEL = Color(18, 24, 46)
PANEL_EDGE = Color(70, 90, 150)
PANEL_LIGHT = Color(120, 150, 220)
TITLE_BAR = Color(30, 40, 78)
TEXT = Color(230, 236, 255)
DIM = Color(160, 172, 210)
KEYCAP = Color(44, 56, 100)
ACCENT = Color(225, 6, 0)
HIGHLIGHT = Color(255, 255, 255, 28)
THROTTLE = Color(0, 230, 140)
BRAKE = Color(255, 50, 60)
BAR_OFF = Color(40, 48, 80)

TRACK_STATUS_COLOURS = {
    "GREEN": Color(140, 150, 185),
    "YELLOW": Color(255, 210, 0),
    "SC": Color(255, 140, 0),
    "VSC": Color(255, 180, 60),
    "RED": Color(230, 20, 30),
    "CHEQUERED": Color(245, 245, 245),
}
TYRE_COLOURS = {
    "SOFT": Color(230, 30, 40),
    "MEDIUM": Color(255, 210, 0),
    "HARD": Color(240, 240, 240),
    "INTERMEDIATE": Color(40, 180, 70),
    "WET": Color(30, 110, 230),
}
FALLBACK_TEAM = Color(150, 150, 150)

# Pixel font for titles and big numbers (PS2 feel), clean bold sans for small text so it stays readable
FONT_PIXEL = "Kenney Mini Square"
FONT_BODY = "Liberation Sans"
TITLE_H = 24


def load_fonts() -> None:
    """Register Arcade's bundled Kenney pixel fonts and Liberation Sans Bold."""
    arcade.resources.load_kenney_fonts()
    arcade.load_font(":system:fonts/ttf/Liberation/Liberation_Sans_Bold.ttf")


def draw_scanlines(width: int, height: int) -> None:
    """CRT scanlines over the whole window, one dark line every 3 px."""
    # Here I send all lines in one draw call, a pair of points per line
    points = [(x, y) for y in range(0, height, 3) for x in (0, width)]
    arcade.draw_lines(points, SCANLINE)


def draw_panel(rect: Rect, title: str, text: "TextPool", accent: Color = TITLE_BAR) -> Rect:
    """Bevelled panel with an uppercase title bar, returns the inner content rect."""
    arcade.draw_rect_filled(rect, PANEL)
    # Here a light top/left edge and a dark bottom/right edge give the PS2 bevel
    arcade.draw_line(rect.left, rect.top, rect.right, rect.top, PANEL_LIGHT, 2)
    arcade.draw_line(rect.left, rect.bottom, rect.left, rect.top, PANEL_LIGHT, 2)
    arcade.draw_line(rect.left, rect.bottom, rect.right, rect.bottom, BG, 2)
    arcade.draw_line(rect.right, rect.bottom, rect.right, rect.top, BG, 2)
    bar = LBWH(rect.left + 3, rect.top - TITLE_H - 3, rect.width - 6, TITLE_H)
    arcade.draw_rect_filled(bar, accent)
    arcade.draw_rect_filled(LBWH(bar.left, bar.bottom, 4, bar.height), ACCENT)
    # Dark title on light team colours so it stays readable
    luma = 0.299 * accent.r + 0.587 * accent.g + 0.114 * accent.b
    ink = BG if luma > 150 else TEXT
    text.put(
        f"title:{rect.left:.0f}:{rect.top:.0f}", title.upper(), bar.left + 12, bar.y, ink, size=12, anchor_y="center", font=FONT_PIXEL
    )
    return LBWH(rect.left + 8, rect.bottom + 6, rect.width - 16, bar.bottom - rect.bottom - 10)


def draw_keycap(x: float, y: float, key: str, text: "TextPool", slot: str) -> float:
    """Keyboard key drawn as a bevelled cap, (x, y) is its left middle. Returns its right edge."""
    w = max(26, 9 * len(key) + 12)
    cap = LBWH(x, y - 11, w, 22)
    arcade.draw_rect_filled(cap, KEYCAP)
    arcade.draw_line(cap.left, cap.top, cap.right, cap.top, PANEL_LIGHT, 2)
    arcade.draw_line(cap.left, cap.bottom, cap.right, cap.bottom, BG, 2)
    # Arrow glyphs are small in Liberation Sans, I draw them bigger
    size = 15 if any(c in key for c in "←→↑↓") else 10
    text.put(slot, key, cap.x, y, TEXT, size=size, anchor_x="center", anchor_y="center")
    return cap.right


def draw_segment_bar(rect: Rect, value: float, colour: Color, segments: int = 10) -> None:
    """Vertical segmented bar filled from the bottom, value 0 to 1."""
    gap = 2
    seg_h = (rect.height - gap * (segments - 1)) / segments
    lit = round(max(0.0, min(1.0, value)) * segments)
    for i in range(segments):
        arcade.draw_rect_filled(
            LBWH(rect.left, rect.bottom + i * (seg_h + gap), rect.width, seg_h), colour if i < lit else BAR_OFF
        )


class TextPool:
    """Reusable arcade.Text objects keyed by slot, one batch draw. Unused slots are hidden."""

    def __init__(self) -> None:
        self._batch = pyglet.graphics.Batch()
        self._texts: dict[str, arcade.Text] = {}
        self._used: set[str] = set()

    def put(
        self,
        key: str,
        value: str,
        x: float,
        y: float,
        colour: Color = TEXT,
        size: int = 11,
        anchor_x: str = "left",
        anchor_y: str = "baseline",
        font: str = FONT_BODY,
    ) -> None:
        """Show value at (x, y) this frame. Pyglet only relayouts when a property really changes."""
        label = self._texts.get(key)
        if label is None:
            label = arcade.Text(
                value, x, y, colour, size, font_name=font, bold=font == FONT_BODY,
                anchor_x=anchor_x, anchor_y=anchor_y, batch=self._batch,
            )
            self._texts[key] = label
        else:
            # Here I assign only on change, every setter triggers a layout update
            if label.text != value:
                label.text = value
            if label.position != (x, y):
                label.position = (x, y)
            if label.color != colour:
                label.color = colour
            label.visible = True
        self._used.add(key)

    def draw(self) -> None:
        """Hide slots not put since the last draw, then draw the batch."""
        for key, label in self._texts.items():
            if key not in self._used and label.visible:
                label.visible = False
        self._used.clear()
        self._batch.draw()
