"""Local pixel-art conversion for images generated elsewhere. Pillow only, no network.

Pipeline per image:
  1. shrink to a working size (long side <= WORK_MAX) with premultiplied alpha
  2. remove a flat background by flood-filling from the border (unless already transparent)
  3. trim empty margins
  4. reduce colours to a palette (one palette can be shared by several images)
  5. downscale to the target height: "crisp" takes the most common colour of each
     block, "smooth" averages the block and snaps it to the palette
  6. optional clean-up of single stray pixels and a 1px outline
"""
from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass, field

from PIL import Image, ImageDraw, ImageOps

WORK_MAX = 512
SENTINEL = (1, 254, 3)


@dataclass
class PixelizeSettings:
    height: int = 48                 # output height in pixels (width keeps the aspect ratio)
    colors: int = 16                 # palette size, outline colour not included
    background: str = 'auto'         # auto | keep
    tolerance: int = 48              # background colour tolerance (sum of RGB differences)
    method: str = 'crisp'            # crisp | smooth
    cleanup: bool = True             # remove stray single pixels
    outline: str = 'none'            # none | auto | #rrggbb
    rgb555: bool = False             # snap colours to the GBA 15-bit colour grid
    trim: bool = True                # crop empty margins before scaling
    extra: dict = field(default_factory=dict)


@dataclass
class Prepared:
    image: Image.Image               # RGBA working image, background removed, trimmed
    transparent_input: bool


# ---------------------------------------------------------------- preparation

def _shrink(image: Image.Image) -> Image.Image:
    image = ImageOps.exif_transpose(image).convert('RGBA')
    long_side = max(image.size)
    if long_side <= WORK_MAX:
        return image
    scale = WORK_MAX / long_side
    size = (max(1, round(image.width * scale)), max(1, round(image.height * scale)))
    return image.convert('RGBa').resize(size, Image.Resampling.LANCZOS).convert('RGBA')


def has_transparency(image: Image.Image) -> bool:
    alpha = image.getchannel('A')
    if alpha.getextrema()[0] >= 250:
        return False
    hist = alpha.histogram()
    return sum(hist[:128]) > image.width * image.height * 0.01


def _border_pixels(image: Image.Image, step: int) -> list[tuple[int, int]]:
    w, h = image.size
    points = set()
    for x in range(0, w, step):
        points.update({(x, 0), (x, h - 1)})
    for y in range(0, h, step):
        points.update({(0, y), (w - 1, y)})
    points.update({(w - 1, 0), (0, h - 1), (w - 1, h - 1)})
    return sorted(points)


def _close(a: tuple[int, ...], b: tuple[int, ...], tolerance: int) -> bool:
    return abs(a[0] - b[0]) + abs(a[1] - b[1]) + abs(a[2] - b[2]) <= tolerance


def remove_background(image: Image.Image, tolerance: int) -> Image.Image:
    """Make the flat area connected to the border transparent."""
    rgb = image.convert('RGB')
    seeds = _border_pixels(rgb, max(4, min(rgb.size) // 32))
    samples = [rgb.getpixel(p) for p in seeds]
    dominant = Counter(tuple(v // 16 for v in c) for c in samples).most_common(1)[0][0]
    reference = next(c for c in samples if tuple(v // 16 for v in c) == dominant)
    work = rgb.copy()
    for point, color in zip(seeds, samples):
        if work.getpixel(point) == SENTINEL or not _close(color, reference, tolerance):
            continue
        ImageDraw.floodfill(work, point, SENTINEL, thresh=tolerance)
    raw, mark = work.tobytes(), bytes(SENTINEL)
    mask = Image.frombytes('L', work.size, bytes(0 if raw[i:i + 3] == mark else 255 for i in range(0, len(raw), 3)))
    result = image.copy()
    result.putalpha(Image.composite(image.getchannel('A'), Image.new('L', image.size, 0), mask))
    return result


def prepare(image: Image.Image, settings: PixelizeSettings) -> Prepared:
    work = _shrink(image)
    transparent = has_transparency(work)
    if settings.background == 'auto' and not transparent:
        work = remove_background(work, settings.tolerance)
    if settings.trim:
        box = work.getchannel('A').point(lambda a: 255 if a >= 128 else 0).getbbox()
        if box:
            work = work.crop(box)
    return Prepared(work, transparent)


# ---------------------------------------------------------------- palette

def _opaque_rgb(images: list[Image.Image]) -> Image.Image | None:
    data = bytearray()
    for image in images:
        raw = image.tobytes()
        for i in range(0, len(raw), 4):
            if raw[i + 3] >= 128:
                data += raw[i:i + 3]
    count = len(data) // 3
    if not count:
        return None
    return Image.frombytes('RGB', (count, 1), bytes(data))


def snap555(color: tuple[int, int, int]) -> tuple[int, int, int]:
    return tuple(v & 0xF8 for v in color)


def build_palette(images: list[Image.Image], colors: int, rgb555: bool = False) -> list[tuple[int, int, int]]:
    """A palette of at most `colors` colours fitted to the opaque pixels of all images."""
    strip = _opaque_rgb(images)
    if strip is None:
        return [(0, 0, 0)]
    if rgb555:
        strip = strip.point(lambda v: v & 0xF8)
    unique = strip.getcolors(maxcolors=colors)
    if unique:
        palette = [c for _, c in sorted(unique, reverse=True)]
    else:
        # Median cut fits the large areas; the remaining slots go to small accents
        # (eyes, lights, weapons) that median cut would blend into their surroundings.
        base = max(2, colors - max(1, colors // 4))
        reduced = strip.quantize(colors=base, method=Image.Quantize.MEDIANCUT, kmeans=3, dither=Image.Dither.NONE)
        palette = representative_colors(strip, reduced)
        palette += accent_colors(strip, palette, colors - len(palette))
    if rgb555:
        palette = list(dict.fromkeys(snap555(c) for c in palette))
    return palette


def _distance(a: tuple[int, int, int], b: tuple[int, int, int]) -> int:
    return 2 * (a[0] - b[0]) ** 2 + 4 * (a[1] - b[1]) ** 2 + 3 * (a[2] - b[2]) ** 2


def representative_colors(strip: Image.Image, reduced: Image.Image) -> list[tuple[int, int, int]]:
    """Replace each median-cut colour with the most common real colour among its pixels.

    Median cut averages; when a box mixes, say, a red light and a dark gun, the average is a
    muddy colour that matches neither. Taking the dominant real colour keeps palettes clean.
    """
    rgb, idx = strip.tobytes(), reduced.tobytes()
    step = max(1, len(idx) // 200000)
    groups: dict[int, Counter] = {}
    sums: dict[tuple[int, int], list[int]] = {}
    for k in range(0, len(idx), step):
        r, g, b = rgb[k * 3], rgb[k * 3 + 1], rgb[k * 3 + 2]
        key = (r >> 4) << 8 | (g >> 4) << 4 | (b >> 4)
        groups.setdefault(idx[k], Counter())[key] += 1
        entry = sums.setdefault((idx[k], key), [0, 0, 0, 0])
        entry[0] += 1
        entry[1] += r
        entry[2] += g
        entry[3] += b
    palette = []
    for index in sorted(groups):
        key = groups[index].most_common(1)[0][0]
        n, r, g, b = sums[(index, key)]
        palette.append((round(r / n), round(g / n), round(b / n)))
    return list(dict.fromkeys(palette))


def accent_colors(strip: Image.Image, palette: list[tuple[int, int, int]], slots: int) -> list[tuple[int, int, int]]:
    """Up to `slots` colours that are far from the palette and used by enough pixels to matter."""
    if slots <= 0:
        return []
    counts = strip.getcolors(maxcolors=1 << 24) or []
    total = sum(n for n, _ in counts)
    bins: dict[tuple[int, int, int], list[int]] = {}
    for n, (r, g, b) in counts:  # group near-identical colours so noise does not count as an accent
        key = (r >> 4, g >> 4, b >> 4)
        entry = bins.setdefault(key, [0, 0, 0, 0])
        entry[0] += n
        entry[1] += r * n
        entry[2] += g * n
        entry[3] += b * n
    minimum = max(4, total * 0.0004)
    candidates = [(n, (round(r / n), round(g / n), round(b / n))) for n, r, g, b in bins.values() if n >= minimum]
    chosen = list(palette)
    added: list[tuple[int, int, int]] = []
    nearest = {color: min(_distance(color, p) for p in chosen) for _, color in candidates}
    weight = {color: n ** 0.25 for n, color in candidates}
    while len(added) < slots and nearest:
        color = max(nearest, key=lambda c: weight[c] * nearest[c])
        if nearest[color] < 2400:  # roughly 20 levels apart: already represented
            break
        added.append(color)
        for other in nearest:
            nearest[other] = min(nearest[other], _distance(other, color))
    return added


def _palette_image(palette: list[tuple[int, int, int]]) -> Image.Image:
    image = Image.new('P', (1, 1))
    image.putpalette([v for c in palette + [palette[0]] * (256 - len(palette)) for v in c])
    return image


def _indices(image: Image.Image, palette: list[tuple[int, int, int]]) -> bytes:
    """Nearest palette index for every pixel (unused slots fold back to index 0)."""
    mapped = image.convert('RGB').quantize(palette=_palette_image(palette), dither=Image.Dither.NONE)
    count = len(palette)
    return bytes(i if i < count else 0 for i in mapped.tobytes())


# ---------------------------------------------------------------- scaling

def target_size(width: int, height: int, target_height: int) -> tuple[int, int]:
    th = max(1, int(target_height))
    return max(1, round(width * th / height)), th


def _downscale_crisp(index_bytes: bytes, alpha_bytes: bytes, size: tuple[int, int],
                     out: tuple[int, int]) -> tuple[list[int], list[bool]]:
    sw, sh = size
    tw, th = out
    indices, opaque = [], []
    for ty in range(th):
        y0, y1 = ty * sh // th, max(ty * sh // th + 1, (ty + 1) * sh // th)
        for tx in range(tw):
            x0, x1 = tx * sw // tw, max(tx * sw // tw + 1, (tx + 1) * sw // tw)
            counts: Counter = Counter()
            visible = total = 0
            for y in range(y0, y1):
                row = y * sw
                for x in range(x0, x1):
                    total += 1
                    if alpha_bytes[row + x] >= 128:
                        visible += 1
                        counts[index_bytes[row + x]] += 1
            if visible * 2 >= total and counts:
                indices.append(counts.most_common(1)[0][0])
                opaque.append(True)
            else:
                indices.append(0)
                opaque.append(False)
    return indices, opaque


def _downscale_smooth(image: Image.Image, palette: list[tuple[int, int, int]],
                      out: tuple[int, int]) -> tuple[list[int], list[bool]]:
    small = image.convert('RGBa').resize(out, Image.Resampling.BOX).convert('RGBA')
    opaque = [a >= 128 for a in small.getchannel('A').tobytes()]
    return list(_indices(small, palette)), opaque


# ---------------------------------------------------------------- clean-up and outline

def remove_stray_pixels(indices: list[int], opaque: list[bool], w: int, h: int) -> None:
    """A pixel whose four neighbours all agree with each other but not with it takes their value."""
    source_i, source_o = indices[:], opaque[:]
    for y in range(h):
        for x in range(w):
            k = y * w + x
            neighbours = [(x + dx, y + dy) for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1))
                          if 0 <= x + dx < w and 0 <= y + dy < h]
            if len(neighbours) < 3:
                continue
            states = {(source_o[ny * w + nx], source_i[ny * w + nx] if source_o[ny * w + nx] else -1) for nx, ny in neighbours}
            if len(states) != 1:
                continue
            state_opaque, state_index = next(iter(states))
            if state_opaque != source_o[k] or (state_opaque and state_index != source_i[k]):
                opaque[k] = state_opaque
                indices[k] = state_index if state_opaque else 0


def add_outline(indices: list[int], opaque: list[bool], w: int, h: int, outline_index: int) -> tuple[list[int], list[bool], int, int]:
    """Grow the canvas by 1px and draw a 1px outline around opaque pixels."""
    nw, nh = w + 2, h + 2
    grown_i, grown_o = [0] * (nw * nh), [False] * (nw * nh)
    for y in range(h):
        for x in range(w):
            grown_i[(y + 1) * nw + x + 1] = indices[y * w + x]
            grown_o[(y + 1) * nw + x + 1] = opaque[y * w + x]
    result_i, result_o = grown_i[:], grown_o[:]
    for y in range(nh):
        for x in range(nw):
            k = y * nw + x
            if grown_o[k]:
                continue
            if any(0 <= x + dx < nw and 0 <= y + dy < nh and grown_o[(y + dy) * nw + x + dx]
                   for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1))):
                result_i[k], result_o[k] = outline_index, True
    return result_i, result_o, nw, nh


def outline_color(setting: str, palette: list[tuple[int, int, int]]) -> tuple[int, int, int] | None:
    if setting == 'none':
        return None
    if setting.startswith('#') and len(setting) == 7:
        return tuple(int(setting[i:i + 2], 16) for i in (1, 3, 5))
    darkest = min(palette, key=lambda c: 299 * c[0] + 587 * c[1] + 114 * c[2])
    return tuple(round(v * 0.45) for v in darkest)


# ---------------------------------------------------------------- public API

def pixelize_prepared(prepared: Prepared, palette: list[tuple[int, int, int]],
                      settings: PixelizeSettings) -> Image.Image:
    image = prepared.image
    with_outline = settings.outline != 'none'
    # The outline adds 1px on each side; shrink the body so the total height stays as asked.
    out = target_size(image.width, image.height, max(1, settings.height - 2) if with_outline else settings.height)
    if settings.method == 'smooth':
        indices, opaque = _downscale_smooth(image, palette, out)
    else:
        indices, opaque = _downscale_crisp(_indices(image, palette), image.getchannel('A').tobytes(), image.size, out)
    w, h = out
    if settings.cleanup and w >= 3 and h >= 3:
        remove_stray_pixels(indices, opaque, w, h)
    colors = list(palette)
    edge = outline_color(settings.outline, palette)
    if edge is not None:
        if settings.rgb555:
            edge = snap555(edge)
        colors.append(edge)
        indices, opaque, w, h = add_outline(indices, opaque, w, h, len(colors) - 1)
    raw = bytearray()
    for index, visible in zip(indices, opaque):
        raw += bytes(colors[index]) + (b'\xff' if visible else b'\x00')
    return Image.frombytes('RGBA', (w, h), bytes(raw))


def pixelize_many(images: list[Image.Image], settings: PixelizeSettings,
                  shared_palette: bool = True) -> tuple[list[Image.Image], list[list[tuple[int, int, int]]]]:
    """Convert several images. With shared_palette all results use one palette."""
    prepared = [prepare(image, settings) for image in images]
    if shared_palette:
        palette = build_palette([p.image for p in prepared], settings.colors, settings.rgb555)
        palettes = [palette] * len(prepared)
    else:
        palettes = [build_palette([p.image], settings.colors, settings.rgb555) for p in prepared]
    results = [pixelize_prepared(p, pal, settings) for p, pal in zip(prepared, palettes)]
    return results, palettes


def used_colors(image: Image.Image) -> int:
    colors = image.getcolors(maxcolors=1 << 16) or []
    return len({c[:3] for _, c in colors if c[3]})


def palette_hex(palette: list[tuple[int, int, int]]) -> list[str]:
    return ['#%02x%02x%02x' % c for c in palette]
