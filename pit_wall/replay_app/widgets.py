"""Replay window components: track map, leaderboard, driver cards, info, weather, timeline, controls. Drawing only."""

import arcade
import numpy as np
from arcade.types import LBWH, Color, Rect

from pit_wall.core.replay import REPLAY_HZ
from pit_wall.replay_app import icons
from pit_wall.replay_app import theme as t
from pit_wall.replay_app.scene import Scene

TRACK_EDGE = Color(30, 36, 64)
PIT_BADGE = Color(40, 110, 230)
DRS_BADGE = Color(0, 200, 90)
MAX_ROW_H = 28
CARD_H = 150
BUTTON_W, BUTTON_H, BUTTON_GAP, GROUP_GAP, SPEED_LABEL_W = 46, 34, 6, 22, 60
# Row centres of a driver card as shares of its inner height: speed, gear, lap and tyre, gaps
CARD_ROWS = [0.18, 0.42, 0.64, 0.88]
# Liberation Sans cap height in px per font point (0.73 em at 96 dpi)
CAP_HEIGHT = 0.73 * 96 / 72
STATUS_LABELS = {"GREEN": "TRACK CLEAR", "SC": "SAFETY CAR", "VSC": "VIRTUAL SC", "RED": "RED FLAG"}
LEGEND = [
    ("YELLOW", t.TRACK_STATUS_COLOURS["YELLOW"]),
    ("SC", t.TRACK_STATUS_COLOURS["SC"]),
    ("VSC", t.TRACK_STATUS_COLOURS["VSC"]),
    ("RED", t.TRACK_STATUS_COLOURS["RED"]),
]
CONTROLS = [
    ("SPACE", "Play / pause"),
    ("← →", "Seek 10 s"),
    ("SHIFT", "+ arrows 60 s"),
    ("↑ ↓", "Speed"),
    ("R", "Restart"),
    ("G", "Interval / gap"),
    ("CLICK", "Driver / seek"),
    ("ESC", "Quit"),
]


def _lerp(scene: Scene, field: str, f: float) -> np.ndarray:
    # Here I blend two 4 Hz frames so cars glide at 60 fps instead of jumping
    i = int(f)
    j = min(i + 1, scene.n_frames - 1)
    a = f - i
    values = scene.num[field]
    return values[i] * (1 - a) + values[j] * a


def _fit(outline: np.ndarray, points: np.ndarray, rect: Rect, margin: float = 30) -> np.ndarray:
    # Here I scale points like the circuit fitted in the rect, aspect ratio kept and centred
    lo, hi = np.nanmin(outline, axis=0), np.nanmax(outline, axis=0)
    scale = min((rect.width - 2 * margin) / (hi[0] - lo[0]), (rect.height - 2 * margin) / (hi[1] - lo[1]))
    return (points - (lo + hi) / 2) * scale + np.array([rect.x, rect.y])


def _gap_label(scene: Scene, slot: int, frame: int, to_leader: bool) -> str:
    status = scene.txt["status"][frame, slot]
    if status == "OUT":
        return "OUT"
    if scene.num["position"][frame, slot] == 1:
        return "LEADER"
    laps_down = scene.num["laps_down"][frame, slot]
    if laps_down > 0:
        return f"+{laps_down:.0f} LAP"
    gap = scene.num["gap_to_leader" if to_leader else "interval"][frame, slot]
    return "" if np.isnan(gap) else f"+{gap:.3f}"


def _tyre_badge(x: float, y: float, compound: str, text: t.TextPool, key: str, radius: float = 9) -> None:
    # Here a filled compound disc with a dark letter, readable on every compound colour
    if not compound:
        return
    arcade.draw_circle_filled(x, y, radius, t.TYRE_COLOURS.get(compound, t.DIM))
    arcade.draw_circle_outline(x, y, radius, t.BG, 2)
    # Pyglet centres on the full line height, I place the baseline half a cap height under the centre instead
    size = round(radius * 0.8)
    text.put(key, compound[0], x, y - CAP_HEIGHT * size / 2, t.BG, size=size, anchor_x="center")


def draw_track(scene: Scene, f: float, rect: Rect, text: t.TextPool, selected: list[int]) -> None:
    """Circuit coloured by track status, finish line, cars as team coloured dots."""
    frame = int(f)
    line = _fit(scene.outline, np.vstack([scene.outline, scene.outline[:1]]), rect)
    status = scene.track_status[frame]
    arcade.draw_line_strip(line, TRACK_EDGE, 18)
    arcade.draw_line_strip(line, t.TRACK_STATUS_COLOURS.get(status, t.DIM), 6)
    # Then the finish line, a white tick across the track at the first outline point
    p0, p1 = line[0], line[1]
    d = (p1 - p0) / (np.hypot(*(p1 - p0)) or 1)
    normal = np.array([-d[1], d[0]]) * 14
    arcade.draw_line(*(p0 - normal), *(p0 + normal), t.TEXT, 3)
    xy = _fit(scene.outline, np.column_stack([_lerp(scene, "x", f), _lerp(scene, "y", f)]), rect)
    # Leader drawn last so it stays on top, retired cars are hidden
    for slot in scene.order(frame)[::-1]:
        car_status = scene.txt["status"][frame, slot]
        if car_status == "OUT" or np.isnan(xy[slot, 0]):
            continue
        x, y = xy[slot]
        colour = scene.colours[slot]
        if car_status == "PIT":
            colour = Color(colour.r, colour.g, colour.b, 110)
        if slot in selected:
            arcade.draw_circle_filled(x, y, 10, t.TEXT)
            text.put(f"map:{slot}", scene.acronyms[slot], x + 13, y + 6, t.TEXT, size=11)
        arcade.draw_circle_filled(x, y, 7, colour)
        arcade.draw_circle_outline(x, y, 7, t.BG, 1)


def draw_leaderboard(
    scene: Scene, frame: int, rect: Rect, text: t.TextPool, selected: list[int], to_leader: bool
) -> list[tuple[Rect, int]]:
    """Classification with team stripe, tyre, gap and badges. Returns row rects with their slot for clicks."""
    inner = t.draw_panel(rect, "Gap to leader" if to_leader else "Interval", text)
    order = scene.order(frame)
    row_h = min(MAX_ROW_H, inner.height / len(order))
    rows = []
    for rank, slot in enumerate(order):
        top = inner.top - rank * row_h
        row = LBWH(inner.left, top - row_h, inner.width, row_h)
        rows.append((row, int(slot)))
        status = scene.txt["status"][frame, slot]
        out = status == "OUT"
        cy = row.y
        if slot in selected:
            arcade.draw_rect_filled(row, t.HIGHLIGHT)
        elif rank % 2:
            arcade.draw_rect_filled(row, Color(255, 255, 255, 8))
        colour = t.DIM if out else t.TEXT
        text.put(f"lb:pos:{rank}", str(rank + 1), row.left + 24, cy, colour, size=12, anchor_x="right", anchor_y="center", font=t.FONT_PIXEL)
        arcade.draw_rect_filled(LBWH(row.left + 30, row.bottom + 4, 5, row_h - 8), scene.colours[slot])
        text.put(f"lb:acr:{rank}", scene.acronyms[slot], row.left + 44, cy, colour, size=12, anchor_y="center")
        _tyre_badge(row.left + 112, cy, scene.txt["compound"][frame, slot], text, f"lb:tyre:{rank}")
        gap = _gap_label(scene, int(slot), frame, to_leader)
        text.put(f"lb:gap:{rank}", gap, row.right - 48, cy, colour, size=11, anchor_x="right", anchor_y="center")
        # Badges on the right edge: pit, finish or DRS open
        badge = {"PIT": ("PIT", PIT_BADGE), "FIN": ("FIN", t.TEXT)}.get(status)
        if badge is None and scene.num["drs"][frame, slot] == 1:
            badge = ("DRS", DRS_BADGE)
        if badge:
            label, badge_colour = badge
            arcade.draw_rect_filled(LBWH(row.right - 40, row.bottom + 4, 40, row_h - 8), badge_colour)
            text.put(f"lb:badge:{rank}", label, row.right - 20, cy, t.BG, size=10, anchor_x="center", anchor_y="center")
    return rows


def draw_driver_card(scene: Scene, frame: int, slot: int, rect: Rect, text: t.TextPool, key: str) -> None:
    """Live telemetry of one driver: speed, gear, DRS, tyre, gaps around, throttle and brake bars."""
    num = {f: v[frame, slot] for f, v in scene.num.items()}
    position = "--" if np.isnan(num["position"]) else f"P{num['position']:.0f}"
    inner = t.draw_panel(rect, f"{position}  {scene.acronyms[slot]}  #{scene.numbers[slot]}", text, scene.colours[slot])
    # Here the four rows are spread on the card height, so a smaller window shrinks the card without overlaps
    left = inner.left + 4
    rows = [inner.top - inner.height * share for share in CARD_ROWS]
    # A retired car has stale telemetry, I only show when it stopped
    if scene.txt["status"][frame, slot] == "OUT":
        text.put(f"{key}:speed", "RETIRED", left, rows[0], t.DIM, size=18, anchor_y="center", font=t.FONT_PIXEL)
        text.put(f"{key}:gear", f"Stopped on lap {num['lap']:.0f}", left, rows[1], t.DIM, size=11, anchor_y="center")
        return
    speed = 0 if np.isnan(num["speed"]) else num["speed"]
    text.put(f"{key}:speed", f"{speed:.0f}", left, rows[0], t.TEXT, size=22, anchor_y="center", font=t.FONT_PIXEL)
    text.put(f"{key}:kmh", "KM/H", left + 76, rows[0], t.DIM, size=10, anchor_y="center")
    gear = "N" if np.isnan(num["n_gear"]) or num["n_gear"] == 0 else f"{num['n_gear']:.0f}"
    text.put(f"{key}:gear", f"GEAR {gear}", left, rows[1], t.TEXT, size=12, anchor_y="center")
    drs_open = num["drs"] == 1
    drs = LBWH(left + 90, rows[1] - 9, 48, 18)
    arcade.draw_rect_filled(drs, DRS_BADGE if drs_open else t.BAR_OFF)
    text.put(f"{key}:drs", "DRS", drs.x, drs.y, t.BG if drs_open else t.DIM, size=10, anchor_x="center", anchor_y="center")
    text.put(f"{key}:lap", f"LAP {num['lap']:.0f}", left, rows[2], t.TEXT, size=12, anchor_y="center")
    compound = scene.txt["compound"][frame, slot]
    _tyre_badge(left + 100, rows[2], compound, text, f"{key}:tyrebadge")
    if compound and not np.isnan(num["tyre_age"]):
        age = int(num["tyre_age"])
        text.put(f"{key}:tyre", f"{age} LAP{'' if age == 1 else 'S'}", left + 114, rows[2], t.TEXT, size=11, anchor_y="center")
    # Here ahead is my own interval, behind is the interval of the car one place back, both on one row
    order = list(scene.order(frame))
    rank = order.index(slot)
    ahead = _gap_label(scene, slot, frame, to_leader=False)
    behind = _gap_label(scene, int(order[rank + 1]), frame, to_leader=False) if rank + 1 < len(order) else ""
    gaps = f"AHEAD {ahead or '-'}    BEHIND {behind.replace('+', '-') or '-'}"
    text.put(f"{key}:gaps", gaps, left, rows[3], t.DIM, size=10, anchor_y="center")
    bar_h = inner.height - 36
    for i, (label, value, colour) in enumerate([
        ("THR", (0 if np.isnan(num["throttle"]) else num["throttle"]) / 100, t.THROTTLE),
        ("BRK", 0 if np.isnan(num["brake"]) else num["brake"], t.BRAKE),
    ]):
        x = inner.right - 54 + i * 28
        t.draw_segment_bar(LBWH(x, inner.bottom + 32, 20, bar_h), value, colour)
        text.put(f"{key}:{label}", label, x + 10, inner.bottom + 20, t.DIM, size=9, anchor_x="center")


def draw_info(scene: Scene, f: float, rect: Rect, text: t.TextPool, speed: float, paused: bool) -> None:
    """Race header: lap, race clock, playback state and track status."""
    frame = int(f)
    inner = t.draw_panel(rect, scene.title, text)
    left, top = inner.left + 4, inner.top - 6
    lap = scene.num["lap"][frame][scene.order(frame)[0]]
    text.put("info:lap", f"LAP {lap:.0f}/{scene.total_laps}", left, top - 24, t.TEXT, size=22, font=t.FONT_PIXEL)
    seconds = int(f / REPLAY_HZ)
    clock = f"{seconds // 3600:01d}:{seconds % 3600 // 60:02d}:{seconds % 60:02d}"
    text.put("info:clock", f"RACE  {clock}", left, top - 50, t.TEXT, size=13)
    play = "PAUSED" if paused else f"PLAY x{speed:g}"
    text.put("info:play", play, inner.right - 4, top - 50, t.DIM if paused else t.THROTTLE, size=13, anchor_x="right")
    status = scene.track_status[frame]
    band = LBWH(left, top - 84, inner.width - 8, 24)
    arcade.draw_rect_filled(band, t.TRACK_STATUS_COLOURS.get(status, t.DIM))
    label = STATUS_LABELS.get(status, status)
    text.put("info:status", label, band.x, band.y, t.BG, size=13, anchor_x="center", anchor_y="center", font=t.FONT_PIXEL)


def draw_weather(scene: Scene, frame: int, rect: Rect, text: t.TextPool) -> None:
    """Weather tiles with icons: track and air temperature, humidity, wind, rain."""
    inner = t.draw_panel(rect, "Weather", text)
    w = scene.weather.iloc[frame]
    raining = bool(w["rainfall"])
    tiles = [
        (lambda x, y: icons.thermometer(x, y, t.BRAKE), f"{w['track_temperature']:.0f}°", "TRACK"),
        (lambda x, y: icons.thermometer(x, y, icons.WATER), f"{w['air_temperature']:.0f}°", "AIR"),
        (icons.droplet, f"{w['humidity']:.0f}%", "HUMIDITY"),
        (lambda x, y: icons.wind_arrow(x, y, w["wind_direction"]), f"{w['wind_speed']:.1f}", "WIND M/S"),
        (lambda x, y: icons.weather_sky(x, y, raining), "WET" if raining else "DRY", "RAIN"),
    ]
    tile_w = inner.width / len(tiles)
    for i, (draw_icon, value, label) in enumerate(tiles):
        cx = inner.left + tile_w * (i + 0.5)
        draw_icon(cx, inner.top - 18)
        text.put(f"wx:value:{i}", value, cx, inner.top - 46, t.TEXT, size=14, anchor_x="center", anchor_y="center")
        text.put(f"wx:label:{i}", label, cx, inner.top - 64, t.DIM, size=9, anchor_x="center", anchor_y="center")


def draw_timeline(scene: Scene, f: float, rect: Rect, text: t.TextPool) -> Rect:
    """Progress bar with track status runs, sector yellows, lap marks, cursor and colour legend. Returns the seek bar."""
    inner = t.draw_panel(rect, "Timeline", text)
    # Colour legend on the right of the title bar
    x = inner.right
    for label, colour in reversed(LEGEND):
        x -= 9 * len(label) + 30
        arcade.draw_rect_filled(LBWH(x, inner.top + 10, 12, 10), colour)
        text.put(f"tl:legend:{label}", label, x + 16, inner.top + 15, t.TEXT, size=10, anchor_y="center")
    bar = LBWH(inner.left, inner.top - 32, inner.width, 22)
    per_frame = bar.width / max(scene.n_frames - 1, 1)
    arcade.draw_rect_filled(bar, t.BAR_OFF)
    for start, end, status in scene.status_runs:
        if status != "GREEN":
            run = LBWH(bar.left + start * per_frame, bar.bottom, max((end - start) * per_frame, 2), bar.height)
            arcade.draw_rect_filled(run, t.TRACK_STATUS_COLOURS.get(status, t.DIM))
    # Sector yellows as a thin strip above the bar
    for start, end in scene.yellows:
        strip = LBWH(bar.left + start * per_frame, bar.top + 2, max((end - start) * per_frame, 2), 5)
        arcade.draw_rect_filled(strip, t.TRACK_STATUS_COLOURS["YELLOW"])
    arcade.draw_rect_filled(LBWH(bar.left, bar.bottom, f * per_frame, bar.height), Color(255, 255, 255, 40))
    # Lap marks: a tick per lap, a number every 5 laps
    for frame, lap in zip(scene.lap_marks["frame"], scene.lap_marks["lap"]):
        x = bar.left + frame * per_frame
        major = lap % 5 == 0 or lap == 1
        arcade.draw_line(x, bar.bottom - (7 if major else 3), x, bar.bottom, t.DIM, 1)
        if major:
            text.put(f"tl:lap:{lap}", str(lap), x, bar.bottom - 18, t.DIM, size=10, anchor_x="center")
    cursor = bar.left + f * per_frame
    arcade.draw_line(cursor, bar.bottom - 4, cursor, bar.top + 9, t.TEXT, 3)
    return bar


def draw_controls(rect: Rect, text: t.TextPool) -> None:
    """Keyboard and mouse help as keycaps, two columns."""
    inner = t.draw_panel(rect, "Controls", text)
    rows = (len(CONTROLS) + 1) // 2
    row_h = inner.height / rows
    col_w = inner.width / 2
    for i, (key, action) in enumerate(CONTROLS):
        x = inner.left + (i // rows) * col_w
        y = inner.top - row_h * (i % rows + 0.5)
        right = t.draw_keycap(x, y, key, text, f"ctl:key:{i}")
        text.put(f"ctl:action:{i}", action, right + 8, y, t.TEXT, size=11, anchor_y="center")


def draw_transport(
    rect: Rect, paused: bool, speed: float, text: t.TextPool, mouse: tuple[float, float]
) -> list[tuple[Rect, str]]:
    """Clickable playback bar centred in rect: restart, seek back, play or pause, seek forward, speed down and up.

    Returns button rects with their action for clicks.
    """
    buttons = [
        ("restart", icons.restart),
        ("back", lambda x, y, s, c: icons.double_triangle(x, y, s, c, left=True)),
        ("play", icons.triangle if paused else icons.pause),
        ("forward", icons.double_triangle),
        ("slower", icons.minus),
        ("speed", None),
        ("faster", icons.plus),
    ]
    widths = [SPEED_LABEL_W if action == "speed" else BUTTON_W for action, _ in buttons]
    # Here the speed group sits apart from the transport group
    total = sum(widths) + BUTTON_GAP * (len(buttons) - 1) + GROUP_GAP
    x = rect.x - total / 2
    hits = []
    for (action, draw_icon), width in zip(buttons, widths):
        if action == "slower":
            x += GROUP_GAP
        button = LBWH(x, rect.y - BUTTON_H / 2, width, BUTTON_H)
        x += width + BUTTON_GAP
        if draw_icon is None:
            label = f"x{speed:g}"
            text.put("transport:speed", label, button.x, button.y, t.TEXT, size=14, anchor_x="center", anchor_y="center", font=t.FONT_PIXEL)
            continue
        hovered = button.point_in_rect(mouse)
        arcade.draw_rect_filled(button, t.PANEL_LIGHT if hovered else t.KEYCAP)
        arcade.draw_line(button.left, button.top, button.right, button.top, t.PANEL_LIGHT, 2)
        arcade.draw_line(button.left, button.bottom, button.right, button.bottom, t.BG, 2)
        draw_icon(button.x, button.y, 8, t.BG if hovered else t.TEXT)
        hits.append((button, action))
    return hits


def card_rects(column: Rect, count: int, gap: float = 8) -> list[Rect]:
    """Stack count card rects top down in a column, height capped so cards keep their layout."""
    if count == 0:
        return []
    h = min(CARD_H, (column.height - gap * (count - 1)) / count)
    return [LBWH(column.left, column.top - (i + 1) * h - i * gap, column.width, h) for i in range(count)]
