"""Small vector icons drawn with Arcade shapes, centred on (x, y), about 24 px tall."""

import math

import arcade
from arcade.types import LBWH, Color

from pit_wall.replay_app.theme import BG, TEXT

WATER = Color(70, 160, 255)
CLOUD = Color(170, 180, 205)
SUN = Color(255, 200, 40)


def thermometer(x: float, y: float, colour: Color) -> None:
    """Stem with a bulb, the fill colour tells which temperature it is."""
    arcade.draw_rect_filled(LBWH(x - 3, y - 4, 6, 16), TEXT)
    arcade.draw_circle_filled(x, y - 7, 6, TEXT)
    arcade.draw_rect_filled(LBWH(x - 1.5, y - 4, 3, 10), colour)
    arcade.draw_circle_filled(x, y - 7, 4, colour)


def droplet(x: float, y: float) -> None:
    """Water drop: a circle under a triangle tip."""
    arcade.draw_circle_filled(x, y - 4, 7, WATER)
    arcade.draw_triangle_filled(x - 6.5, y - 2, x + 6.5, y - 2, x, y + 11, WATER)
    arcade.draw_circle_filled(x - 2.5, y - 5, 2, Color(200, 230, 255))


def wind_arrow(x: float, y: float, direction_deg: float) -> None:
    """Arrow pointing where the wind blows to, OpenF1 gives where it comes from (0 = north)."""
    # Here I turn the compass bearing into a screen vector, north is up
    to = math.radians(direction_deg + 180)
    dx, dy = math.sin(to), math.cos(to)
    tail, head = (x - dx * 11, y - dy * 11), (x + dx * 11, y + dy * 11)
    arcade.draw_line(*tail, *head, TEXT, 3)
    nx, ny = -dy, dx
    arcade.draw_triangle_filled(
        head[0] + dx * 3, head[1] + dy * 3,
        head[0] - dx * 6 + nx * 6, head[1] - dy * 6 + ny * 6,
        head[0] - dx * 6 - nx * 6, head[1] - dy * 6 - ny * 6,
        TEXT,
    )


def weather_sky(x: float, y: float, raining: bool) -> None:
    """Cloud with rain drops when wet, sun when dry."""
    if not raining:
        for i in range(8):
            a = i * math.pi / 4
            arcade.draw_line(x + math.cos(a) * 9, y + math.sin(a) * 9, x + math.cos(a) * 13, y + math.sin(a) * 13, SUN, 2)
        arcade.draw_circle_filled(x, y, 7, SUN)
        return
    for dx in (-6, 0, 6):
        arcade.draw_line(x + dx, y - 6, x + dx - 3, y - 13, WATER, 2)
    arcade.draw_circle_filled(x - 6, y + 2, 6, CLOUD)
    arcade.draw_circle_filled(x + 2, y + 5, 8, CLOUD)
    arcade.draw_circle_filled(x + 8, y + 1, 5, CLOUD)
    arcade.draw_rect_filled(LBWH(x - 12, y - 4, 25, 6), CLOUD)
    arcade.draw_line(x - 12, y - 4, x + 13, y - 4, BG, 1)
