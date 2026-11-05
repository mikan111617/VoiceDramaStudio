"""Stage direction layers for battle sequences: camera, cut-ins and drawn effects.

Everything here is drawn locally with Pillow (no AI calls). All drawing is
non-antialiased so the output stays pixel-art friendly.

Clip fields used by this module (all optional; old clips keep working):
  camera : zoom, end_zoom, x/y -> end_x/end_y (view-centre pan), easing, shake
  cutin  : asset_id (portrait, optional), text, side, text_speed, color
  fx     : fx_type (flash|speedlines|hlines|beam|bolt|explosion|hitstop), color, intensity,
           beam_width, layer (auto|back|front), x/y -> end_x/end_y, flip_x
  background : scroll_x, scroll_y (pixels per frame, the image tiles)
"""
from __future__ import annotations

import math
import os
import zlib
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

FX_TYPES = ('flash', 'speedlines', 'hlines', 'beam', 'bolt', 'explosion', 'hitstop')
FX_LABELS = {
    'flash': '画面フラッシュ', 'speedlines': '集中線', 'hlines': 'スピード線',
    'beam': 'ビーム', 'bolt': '光弾', 'explosion': '爆発', 'hitstop': 'ヒットストップ',
}
BACK_BY_DEFAULT = ('speedlines', 'hlines')
FONT_DIR = Path(__file__).resolve().parent / 'fonts'
FONT_CANDIDATES = (
    # Windows
    'C:/Windows/Fonts/msgothic.ttc', 'C:/Windows/Fonts/BIZ-UDGothicR.ttc',
    'C:/Windows/Fonts/meiryo.ttc', 'C:/Windows/Fonts/YuGothM.ttc',
    # macOS
    '/System/Library/Fonts/ヒラギノ角ゴシック W3.ttc', '/System/Library/Fonts/Hiragino Sans GB.ttc',
    # Linux
    '/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc',
    '/usr/share/fonts/noto-cjk/NotoSansCJK-Regular.ttc',
    '/usr/share/fonts/truetype/fonts-japanese-gothic.ttf',
)


# ---------------------------------------------------------------- helpers

def _active(clip: dict, frame: int) -> bool:
    start = int(clip.get('start', 0))
    return start <= frame < start + int(clip.get('duration', 0))


def _progress(clip: dict, frame: int) -> float:
    duration = max(1, int(clip.get('duration', 1)))
    offset = max(0, min(duration - 1, frame - int(clip.get('start', 0))))
    return offset / (duration - 1) if duration > 1 else 1.0


def ease(t: float, mode: str | None) -> float:
    if mode == 'ease_in_out':
        return t * t * (3 - 2 * t)
    if mode == 'ease_out':
        return 1 - (1 - t) * (1 - t)
    return t


def hex_rgb(value: str | None, default: tuple[int, int, int] = (255, 255, 255)) -> tuple[int, int, int]:
    text = str(value or '').lstrip('#')
    if len(text) != 6:
        return default
    try:
        return int(text[0:2], 16), int(text[2:4], 16), int(text[4:6], 16)
    except ValueError:
        return default


class _Rand:
    """Small deterministic generator so previews and exports are identical."""

    def __init__(self, *seed_parts) -> None:
        self.state = zlib.crc32(repr(seed_parts).encode('utf-8')) or 1

    def next(self) -> float:
        self.state = (self.state * 1103515245 + 12345) & 0x7FFFFFFF
        return self.state / 0x7FFFFFFF

    def range(self, low: float, high: float) -> float:
        return low + (high - low) * self.next()


def fx_layer(clip: dict) -> str:
    layer = clip.get('layer') or 'auto'
    if layer in ('back', 'front'):
        return layer
    return 'back' if clip.get('fx_type') in BACK_BY_DEFAULT else 'front'


# ---------------------------------------------------------------- hit stop

def world_frame(seq: dict, frame: int) -> int:
    """Map a timeline frame to the frame the world (sprites, backgrounds) shows.

    During a hit-stop clip the world freezes on the clip's first frame. Clips
    keep their timeline positions, so the edit stays what-you-see-is-what-you-get;
    the frozen frames simply replace those frames of motion.
    """
    for clip in seq.get('clips', []):
        if clip.get('track') == 'fx' and clip.get('fx_type') == 'hitstop' and _active(clip, frame):
            return int(clip.get('start', 0))
    return frame


# ---------------------------------------------------------------- background scroll

def scrolled_background(image: Image.Image, w: int, h: int, clip: dict, local_frame: int) -> Image.Image:
    """Fill the stage with a background, tiled and shifted when it scrolls."""
    base = image.resize((w, h), Image.Resampling.NEAREST)
    sx, sy = int(clip.get('scroll_x', 0) or 0), int(clip.get('scroll_y', 0) or 0)
    ox = (int(clip.get('x', 0)) + sx * local_frame) % w
    oy = (int(clip.get('y', 0)) + sy * local_frame) % h
    if not sx and not sy:
        layer = Image.new('RGBA', (w, h), (0, 0, 0, 0))
        layer.paste(base, (int(clip.get('x', 0)), int(clip.get('y', 0))))
        return layer
    layer = Image.new('RGBA', (w, h), (0, 0, 0, 0))
    for tx in (ox - w, ox):
        for ty in (oy - h, oy):
            layer.paste(base, (tx, ty))
    return layer


# ---------------------------------------------------------------- drawn effects

def draw_fx(canvas: Image.Image, clip: dict, frame: int) -> Image.Image:
    """Draw one fx clip onto the canvas and return the result."""
    kind = clip.get('fx_type')
    if kind == 'speedlines':
        return _speedlines(canvas, clip, frame)
    if kind == 'hlines':
        return _hlines(canvas, clip, frame)
    if kind == 'beam':
        return _beam(canvas, clip, frame)
    if kind == 'bolt':
        return _bolt(canvas, clip, frame)
    if kind == 'explosion':
        return _explosion(canvas, clip, frame)
    if kind == 'flash':
        return _flash(canvas, clip, frame)
    return canvas


def _speedlines(canvas: Image.Image, clip: dict, frame: int) -> Image.Image:
    w, h = canvas.size
    intensity = int(clip.get('intensity', 50))
    cx, cy = w / 2 + int(clip.get('x', 0)), h / 2 + int(clip.get('y', 0))
    color = hex_rgb(clip.get('color'))
    rnd = _Rand('speedlines', clip.get('id'), frame // 2)
    count = 10 + round(intensity * 0.5)
    outer = math.hypot(w, h)
    clear = min(w, h) * (0.45 - intensity * 0.0025)
    overlay = Image.new('RGBA', (w, h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    for _ in range(count):
        angle = rnd.range(0, math.tau)
        spread = rnd.range(0.006, 0.022)
        inner = clear * rnd.range(0.85, 1.35)
        points = [
            (cx + math.cos(angle - spread) * outer, cy + math.sin(angle - spread) * outer),
            (cx + math.cos(angle + spread) * outer, cy + math.sin(angle + spread) * outer),
            (cx + math.cos(angle) * inner, cy + math.sin(angle) * inner),
        ]
        draw.polygon(points, fill=color + (255,))
    return Image.alpha_composite(canvas, overlay)


def _hlines(canvas: Image.Image, clip: dict, frame: int) -> Image.Image:
    w, h = canvas.size
    intensity = int(clip.get('intensity', 50))
    color = hex_rgb(clip.get('color'))
    rnd = _Rand('hlines', clip.get('id'))
    count = 6 + round(intensity * 0.3)
    speed = max(4, round(w * (0.08 + intensity * 0.002)))
    local = frame - int(clip.get('start', 0))
    direction = 1 if clip.get('flip_x') else -1
    overlay = Image.new('RGBA', (w, h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    for _ in range(count):
        length = round(rnd.range(0.15, 0.6) * w)
        y = round(rnd.range(0, h - 1))
        thick = 1 if rnd.next() < 0.7 else 2
        span = w + length
        base = rnd.range(0, span)
        x = (base + direction * speed * local) % span - length
        draw.rectangle([round(x), y, round(x) + length, y + thick - 1], fill=color + (255,))
    return Image.alpha_composite(canvas, overlay)


def _beam(canvas: Image.Image, clip: dict, frame: int) -> Image.Image:
    w, h = canvas.size
    x0, y0 = int(clip.get('x', 0)), int(clip.get('y', 0))
    # Without an end point the beam fires half a screen to the right.
    x1 = x0 + w // 2 if clip.get('end_x') is None else int(clip['end_x'])
    y1 = y0 if clip.get('end_y') is None else int(clip['end_y'])
    sx, sy, ex, ey = w / 2 + x0, h / 2 + y0, w / 2 + x1, h / 2 + y1
    t = _progress(clip, frame)
    width = max(1, int(clip.get('beam_width', 6) or 6))
    if t < 0.3:  # the tip travels to the target
        reach, scale = t / 0.3, 1.0
    elif t < 0.8:  # full beam
        reach, scale = 1.0, 1.0
    else:  # thins out
        reach, scale = 1.0, max(0.15, (1 - t) / 0.2)
    flicker = 1 if (frame % 2) else 0
    outer = max(1, round(width * scale) + flicker)
    core = max(1, round(outer / 3))
    tx, ty = sx + (ex - sx) * reach, sy + (ey - sy) * reach
    color = hex_rgb(clip.get('color'), (110, 220, 255))
    overlay = Image.new('RGBA', (w, h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    draw.line([(sx, sy), (tx, ty)], fill=color + (255,), width=outer)
    r = outer / 2
    draw.ellipse([tx - r, ty - r, tx + r, ty + r], fill=color + (255,))
    draw.ellipse([sx - r, sy - r, sx + r, sy + r], fill=color + (255,))
    draw.line([(sx, sy), (tx, ty)], fill=(255, 255, 255, 255), width=core)
    return Image.alpha_composite(canvas, overlay)


def _endpoints(clip: dict, w: int, h: int) -> tuple[float, float, float, float]:
    x0, y0 = int(clip.get('x', 0)), int(clip.get('y', 0))
    x1 = x0 + w // 2 if clip.get('end_x') is None else int(clip['end_x'])
    y1 = y0 if clip.get('end_y') is None else int(clip['end_y'])
    return w / 2 + x0, h / 2 + y0, w / 2 + x1, h / 2 + y1


def _bolt(canvas: Image.Image, clip: dict, frame: int) -> Image.Image:
    """A short energy shot travelling from the muzzle (x, y) to the target (end_x, end_y)."""
    w, h = canvas.size
    sx, sy, ex, ey = _endpoints(clip, w, h)
    t = _progress(clip, frame)
    width = max(1, int(clip.get('beam_width', 4) or 4))
    color = hex_rgb(clip.get('color'), (110, 220, 255))
    dist = math.hypot(ex - sx, ey - sy) or 1.0
    ux, uy = (ex - sx) / dist, (ey - sy) / dist
    head = dist * t
    tail = max(0.0, head - max(10.0, w * 0.09))
    hx, hy = sx + ux * head, sy + uy * head
    tx, ty = sx + ux * tail, sy + uy * tail
    overlay = Image.new('RGBA', (w, h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    if frame == int(clip.get('start', 0)):  # muzzle flash
        r = width * 1.6
        draw.ellipse([sx - r, sy - r, sx + r, sy + r], fill=color + (255,))
        draw.ellipse([sx - r / 2, sy - r / 2, sx + r / 2, sy + r / 2], fill=(255, 255, 255, 255))
    if t >= 1.0:  # impact spark
        r = width * 2.2
        for dx, dy in ((1, 0), (0, 1), (0.7, 0.7), (0.7, -0.7)):
            draw.line([(ex - dx * r, ey - dy * r), (ex + dx * r, ey + dy * r)], fill=color + (255,), width=max(1, width // 2))
        draw.ellipse([ex - width, ey - width, ex + width, ey + width], fill=(255, 255, 255, 255))
    else:
        draw.line([(tx, ty), (hx, hy)], fill=color + (255,), width=width)
        draw.line([(tx, ty), (hx, hy)], fill=(255, 255, 255, 255), width=max(1, width // 3))
    return Image.alpha_composite(canvas, overlay)


def _explosion(canvas: Image.Image, clip: dict, frame: int) -> Image.Image:
    """A drawn explosion centred on (x, y): fireball, white core, puffs and debris."""
    w, h = canvas.size
    cx, cy = w / 2 + int(clip.get('x', 0)), h / 2 + int(clip.get('y', 0))
    t = _progress(clip, frame)
    big = min(w, h) * 0.16 * (0.5 + int(clip.get('intensity', 50)) / 100)
    color = hex_rgb(clip.get('color'), (255, 160, 48))
    dark = tuple(max(0, c // 2) for c in color)
    alpha = 255 if t < 0.6 else round(255 * (1 - t) / 0.4)
    if alpha <= 0:
        return canvas
    rnd = _Rand('explosion', clip.get('id'))
    overlay = Image.new('RGBA', (w, h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    grow = ease(min(1.0, t * 1.8), 'ease_out')
    # secondary puffs appear around the centre
    for i in range(3 + int(clip.get('intensity', 50)) // 25):
        angle, reach = rnd.range(0, math.tau), rnd.range(0.4, 0.9)
        delay = rnd.range(0.05, 0.35)
        if t < delay:
            continue
        pr = big * 0.55 * ease(min(1.0, (t - delay) * 2.2), 'ease_out')
        px, py = cx + math.cos(angle) * big * reach, cy + math.sin(angle) * big * reach
        draw.ellipse([px - pr, py - pr, px + pr, py + pr], fill=dark + (alpha,))
    r = big * grow
    draw.ellipse([cx - r, cy - r, cx + r, cy + r], fill=color + (alpha,))
    core = r * 0.6 * max(0.0, 1 - t * 1.3)
    if core >= 1:
        draw.ellipse([cx - core, cy - core, cx + core, cy + core], fill=(255, 250, 220, alpha))
    for _ in range(8 + int(clip.get('intensity', 50)) // 8):
        angle, speed = rnd.range(0, math.tau), rnd.range(0.8, 1.6)
        d = big * speed * ease(t, 'ease_out') * 1.3
        dx, dy = cx + math.cos(angle) * d, cy + math.sin(angle) * d
        size = 2 if rnd.next() < 0.6 else 3
        draw.rectangle([round(dx), round(dy), round(dx) + size - 1, round(dy) + size - 1], fill=color + (alpha,))
    return Image.alpha_composite(canvas, overlay)


def _flash(canvas: Image.Image, clip: dict, frame: int) -> Image.Image:
    peak = int(clip.get('intensity', 80)) / 100
    t = _progress(clip, frame) if int(clip.get('duration', 1)) > 1 else 0.0
    alpha = round(255 * peak * (1 - t))
    if alpha <= 0:
        return canvas
    color = hex_rgb(clip.get('color'))
    overlay = Image.new('RGBA', canvas.size, color + (alpha,))
    return Image.alpha_composite(canvas, overlay)


# ---------------------------------------------------------------- camera

def camera_state(seq: dict, frame: int) -> tuple[float, int, int, int, int] | None:
    """Return (zoom, pan_x, pan_y, shake_dx, shake_dy) of the newest active camera clip."""
    cams = [c for c in seq.get('clips', []) if c.get('track') == 'camera' and _active(c, frame)]
    if not cams:
        return None
    clip = max(cams, key=lambda c: int(c.get('start', 0)))
    t = ease(_progress(clip, frame), clip.get('easing'))
    z0 = float(clip.get('zoom', 1.0) or 1.0)
    z1 = clip.get('end_zoom')
    z1 = z0 if z1 is None else float(z1)
    zoom = max(0.25, min(8.0, z0 + (z1 - z0) * t))
    x0, y0 = int(clip.get('x', 0)), int(clip.get('y', 0))
    x1 = x0 if clip.get('end_x') is None else int(clip['end_x'])
    y1 = y0 if clip.get('end_y') is None else int(clip['end_y'])
    pan_x, pan_y = round(x0 + (x1 - x0) * t), round(y0 + (y1 - y0) * t)
    shake = int(clip.get('shake', 0) or 0)
    dx = dy = 0
    if shake:
        amp = max(1, round(shake * (1 - 0.7 * _progress(clip, frame))))
        rnd = _Rand('shake', clip.get('id'), frame)
        dx = round(rnd.range(-amp, amp))
        dy = round(rnd.range(-amp, amp))
    return zoom, pan_x, pan_y, dx, dy


def apply_camera(world: Image.Image, state: tuple[float, int, int, int, int] | None) -> Image.Image:
    if state is None:
        return world
    zoom, pan_x, pan_y, dx, dy = state
    w, h = world.size
    view = world
    if zoom != 1.0 or pan_x or pan_y:
        vw, vh = w / zoom, h / zoom
        cx, cy = w / 2 + pan_x, h / 2 + pan_y
        if zoom >= 1:  # keep a zoomed-in view inside the stage so no empty edge shows
            cx = min(max(cx, vw / 2), w - vw / 2)
            cy = min(max(cy, vh / 2), h - vh / 2)
        box = (round(cx - vw / 2), round(cy - vh / 2), round(cx + vw / 2), round(cy + vh / 2))
        view = world.crop(box).resize((w, h), Image.Resampling.NEAREST)
    if dx or dy:
        shaken = Image.new('RGBA', (w, h), (0, 0, 0, 0))
        shaken.paste(view, (dx, dy))
        view = shaken
    return view


# ---------------------------------------------------------------- cut-in

def font_path() -> str | None:
    custom = os.environ.get('PIXEL_WORKBENCH_FONT')
    if custom and Path(custom).is_file():
        return custom
    if FONT_DIR.is_dir():
        for pattern in ('*.ttf', '*.otf', '*.ttc'):
            found = sorted(FONT_DIR.glob(pattern))
            if found:
                return str(found[0])
    for candidate in FONT_CANDIDATES:
        if Path(candidate).is_file():
            return candidate
    return None


@lru_cache(maxsize=16)
def _font(size: int) -> ImageFont.ImageFont | ImageFont.FreeTypeFont:
    path = font_path()
    if path:
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            pass
    try:
        return ImageFont.load_default(size)
    except TypeError:
        return ImageFont.load_default()


def _wrap(draw: ImageDraw.ImageDraw, text: str, font, max_width: int) -> list[str]:
    lines: list[str] = []
    for paragraph in text.split('\n'):
        current = ''
        for ch in paragraph:
            if current and draw.textlength(current + ch, font=font) > max_width:
                lines.append(current)
                current = ch
            else:
                current += ch
        lines.append(current)
    return lines


def _fit_portrait(image: Image.Image, box: int) -> Image.Image:
    scale = min(box / image.width, box / image.height)
    if scale >= 1:
        scale = math.floor(scale)
    size = (max(1, round(image.width * scale)), max(1, round(image.height * scale)))
    return image.resize(size, Image.Resampling.NEAREST)


def draw_cutin(canvas: Image.Image, clip: dict, frame: int, portrait: Image.Image | None) -> Image.Image:
    w, h = canvas.size
    local = frame - int(clip.get('start', 0))
    panel_h = max(28, round(h * 0.34))
    margin = max(2, panel_h // 16)
    right = clip.get('side') == 'right'
    slide = min(1.0, (local + 1) / 3)  # slides in over three frames
    shift = round((1 - ease(slide, 'ease_out')) * w) * (1 if right else -1)
    top = h - panel_h - margin
    bg = hex_rgb(clip.get('color'), (16, 24, 40))
    panel = Image.new('RGBA', (w, panel_h), bg + (235,))
    draw = ImageDraw.Draw(panel)
    draw.fontmode = '1'
    edge = (230, 236, 245, 255)
    draw.line([(0, 0), (w, 0)], fill=edge)
    draw.line([(0, panel_h - 1), (w, panel_h - 1)], fill=edge)
    box = panel_h - margin * 2
    face_x = w - margin - box if right else margin
    draw.rectangle([face_x - 1, margin - 1, face_x + box, margin + box], outline=edge)
    if portrait is not None:
        face = _fit_portrait(portrait, box)
        panel.alpha_composite(face, (face_x + (box - face.width) // 2, margin + (box - face.height) // 2))
    text = str(clip.get('text') or '')
    speed = int(clip.get('text_speed', 2) or 0)
    if speed > 0:
        text = text[:max(0, (local + 1) * speed)]
    size = max(10, min(32, round(panel_h * 0.26)))
    font = _font(size)
    text_x = margin * 2 if right else face_x + box + margin * 2
    text_w = (face_x - margin * 3) if right else (w - text_x - margin * 2)
    y = margin + 1
    for line in _wrap(draw, text, font, max(8, text_w)):
        if y + size > panel_h - margin:
            break
        draw.text((text_x, y), line, font=font, fill=(255, 255, 255, 255))
        y += size + max(1, size // 5)
    layer = Image.new('RGBA', (w, h), (0, 0, 0, 0))
    layer.paste(panel, (shift, top))
    return Image.alpha_composite(canvas, layer)
