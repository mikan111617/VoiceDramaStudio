"""Extra exporters for battle sequences: APNG, transparent WebM, Unity and GBA packages.

Local only (no AI calls). Every exporter renders through sequence_render.render_frame,
so the output matches the preview exactly.
"""
from __future__ import annotations

import io
import json
import math
import re
import subprocess
import tempfile
import zipfile
from pathlib import Path
from typing import Any, Iterator

from PIL import Image

from sequence_render import SUPPORTED_AUDIO_EXT, find_ffmpeg, media_file, render_frame, validate_stage

TEMPLATES = Path(__file__).resolve().parent / 'export_templates'
APNG_PIXEL_LIMIT = 60_000_000  # width * height * frames kept in memory by the APNG writer
UNITY_SHEET_LIMIT = 8192
GBA_SIZE = (240, 160)
GBA_TRANSPARENT = (248, 0, 248)  # conventional magenta for palette index 0


def frames(seq: dict, assets: dict[str, dict], root: Path) -> Iterator[Image.Image]:
    _, _, _, length = validate_stage(seq, for_export=True)
    for index in range(length):
        yield render_frame(seq, assets, root, index)


def safe_name(name: str, fallback: str = 'sequence') -> str:
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', str(name or '')).strip(' .')
    return cleaned[:60] or fallback


def sound_cues(seq: dict, assets: dict[str, dict], root: Path) -> list[dict[str, Any]]:
    """Sound clips with their source file, in timeline order."""
    fps = int(seq.get('fps', 12))
    cues = []
    for clip in sorted((c for c in seq.get('clips', []) if c.get('track') == 'sound'), key=lambda c: int(c.get('start', 0))):
        asset = assets.get(clip.get('asset_id'))
        path = media_file(asset, root) if asset else None
        if not path or path.suffix.lower() not in SUPPORTED_AUDIO_EXT:
            continue
        start = int(clip.get('start', 0))
        cues.append({'path': path, 'start_frame': start, 'time': round(start / fps, 4),
                     'duration_frames': int(clip.get('duration', 1)),
                     'volume': round(min(2.0, max(0.0, float(clip.get('volume', 1)))), 3),
                     'label': clip.get('label') or (asset or {}).get('name', '')})
    return cues


def sound_file(archive: zipfile.ZipFile, path: Path, folder: str, written: dict[Path, str]) -> str:
    """Add each source sound once; return its file name inside `folder`."""
    if path in written:
        return written[path]
    stem, suffix = safe_name(path.stem, 'sound'), path.suffix.lower()
    filename, number = f'{stem}{suffix}', 2
    while filename in written.values():
        filename, number = f'{stem}_{number}{suffix}', number + 1
    archive.write(path, f'{folder}/{filename}')
    written[path] = filename
    return filename


def check_scale(seq: dict, scale: int) -> int:
    scale = 1 if scale is None else int(scale)
    if scale < 1 or scale > 6:
        raise ValueError('拡大倍率は1〜6倍で指定してください。')
    if int(seq.get('width', 240)) * scale > 3840 or int(seq.get('height', 160)) * scale > 2160:
        raise ValueError('拡大後の動画が3840×2160を超えます。倍率を下げてください。')
    return scale


# ---------------------------------------------------------------- APNG

def save_apng(seq: dict, assets: dict[str, dict], root: Path, destination: Path) -> None:
    width, height, fps, length = validate_stage(seq, for_export=True)
    if width * height * length > APNG_PIXEL_LIMIT:
        raise ValueError('APNGの合成画素数が多すぎます。画面を縮小するか、フレーム数を減らすか、WebMを利用してください。')
    rendered = frames(seq, assets, root)
    first = next(rendered)
    first.save(destination, format='PNG', save_all=True, append_images=list(rendered),
               duration=1000 / fps, loop=0)


# ---------------------------------------------------------------- WebM (VP9 with alpha)

def _encoders(binary: str) -> str:
    try:
        return subprocess.run([binary, '-hide_banner', '-encoders'], capture_output=True, text=True,
                              timeout=20, check=False).stdout
    except (OSError, subprocess.TimeoutExpired):
        return ''


def save_webm(seq: dict, assets: dict[str, dict], root: Path, destination: Path, scale: int = 1) -> None:
    _, _, fps, length = validate_stage(seq, for_export=True)
    scale = check_scale(seq, scale)
    binary = find_ffmpeg()
    if not binary:
        raise RuntimeError('WebM書き出しにはFFmpegが必要です。requirementsのimageio-ffmpegを導入してください。')
    encoders = _encoders(binary)
    if 'libvpx-vp9' not in encoders:
        raise RuntimeError('お使いのFFmpegはVP9（libvpx-vp9）に対応していないため、透過WebMを作れません。')
    audio_codec = 'libopus' if 'libopus' in encoders else ('libvorbis' if 'libvorbis' in encoders else None)
    duration = length / fps
    with tempfile.TemporaryDirectory(prefix='pixelbattle_webm_') as temp_dir:
        temp = Path(temp_dir)
        for index, image in enumerate(frames(seq, assets, root)):
            image.save(temp / f'{index:05d}.png')
        args = [binary, '-hide_banner', '-loglevel', 'error', '-nostdin', '-y',
                '-framerate', str(fps), '-i', str(temp / '%05d.png')]
        filters = []
        cues = sound_cues(seq, assets, root) if audio_codec else []
        for number, cue in enumerate(cues, 1):
            args += ['-i', str(cue['path'])]
            start_ms = cue['start_frame'] * 1000 // fps
            seconds = max(0.01, min(cue['duration_frames'] / fps, duration - start_ms / 1000))
            filters.append(f"[{number}:a]atrim=0:{seconds:.4f},asetpts=PTS-STARTPTS,volume={cue['volume']:.3f},adelay={start_ms}:all=1[a{number}]")
        video_filter = f'scale=iw*{scale}:ih*{scale}:flags=neighbor,format=yuva420p'
        if cues:
            filters.append(''.join(f'[a{i}]' for i in range(1, len(cues) + 1)) +
                           f'amix=inputs={len(cues)}:duration=longest:dropout_transition=0[aout]')
            filters.append(f'[0:v]{video_filter}[vout]')
            args += ['-filter_complex', ';'.join(filters), '-map', '[vout]', '-map', '[aout]',
                     '-c:a', audio_codec, '-b:a', '128k']
        else:
            args += ['-vf', video_filter, '-an']
        args += ['-c:v', 'libvpx-vp9', '-pix_fmt', 'yuva420p', '-b:v', '0', '-crf', '24',
                 '-auto-alt-ref', '0', '-row-mt', '1', '-t', f'{duration:.6f}', str(destination)]
        try:
            proc = subprocess.run(args, capture_output=True, text=True, timeout=300, check=False)
        except subprocess.TimeoutExpired:
            raise RuntimeError('WebM書き出しが時間制限を超えました。')
        if proc.returncode or not destination.is_file() or destination.stat().st_size == 0:
            raise RuntimeError('FFmpegでWebMを作成できませんでした。' + (proc.stderr or '')[-350:])


# ---------------------------------------------------------------- sprite sheets

def sheet_layout(count: int, width: int, height: int, limit: int) -> tuple[int, int] | None:
    """Columns/rows for a roughly square sheet, or None when it would exceed `limit` pixels per side."""
    columns = max(1, math.ceil(math.sqrt(count * height / width)))
    columns = min(columns, max(1, limit // width))
    rows = math.ceil(count / columns)
    if rows * height > limit:
        return None
    return columns, rows


def build_sheet(images: list[Image.Image], columns: int, rows: int) -> Image.Image:
    w, h = images[0].size
    mode = images[0].mode
    sheet = Image.new(mode, (columns * w, rows * h), 0 if mode == 'P' else (0, 0, 0, 0))
    if mode == 'P':
        sheet.putpalette(images[0].getpalette())
    for index, image in enumerate(images):
        sheet.paste(image, ((index % columns) * w, (index // columns) * h))
    return sheet


def _png_bytes(image: Image.Image, **params) -> bytes:
    buf = io.BytesIO()
    image.save(buf, format='PNG', **params)
    return buf.getvalue()


# ---------------------------------------------------------------- Unity

def package_unity(seq: dict, assets: dict[str, dict], root: Path, destination: Path) -> None:
    """ZIP to extract into a Unity project's Assets folder.

    PixelBattle/Scripts is shared by every exported sequence, so extracting a second
    sequence overwrites the same scripts instead of duplicating C# classes.
    """
    width, height, fps, length = validate_stage(seq, for_export=True)
    name = safe_name(seq.get('name', ''))
    base = f'PixelBattle/Sequences/{name}'
    cues = sound_cues(seq, assets, root)
    frame_files = [f'frame_{i:04d}.png' for i in range(length)]
    layout = sheet_layout(length, width, height, UNITY_SHEET_LIMIT)
    sounds: list[dict[str, Any]] = []
    written: dict[Path, str] = {}
    with zipfile.ZipFile(destination, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
        images = []
        for filename, image in zip(frame_files, frames(seq, assets, root)):
            archive.writestr(f'{base}/frames/{filename}', _png_bytes(image))
            if layout:
                images.append(image)
        if layout:
            archive.writestr(f'{base}/spritesheet.png', _png_bytes(build_sheet(images, *layout)))
        for cue in cues:
            filename = sound_file(archive, cue['path'], f'{base}/sounds', written)
            sounds.append({'file': filename, 'start_frame': cue['start_frame'], 'time': cue['time'],
                           'volume': cue['volume'], 'label': cue['label']})
        manifest = {
            'format': 'PixelBattleUnity', 'format_version': 1, 'asset_name': name,
            'fps': fps, 'width': width, 'height': height, 'frame_count': length,
            'loop': False, 'pixels_per_unit': 16, 'frames': frame_files, 'sounds': sounds,
            'spritesheet': ({'file': 'spritesheet.png', 'columns': layout[0], 'rows': layout[1],
                             'frame_width': width, 'frame_height': height, 'origin': 'top-left'}
                            if layout else None),
        }
        archive.writestr(f'{base}/pixelbattle.json', json.dumps(manifest, ensure_ascii=False, indent=2))
        for source, target in (('unity/PixelBattleCuePlayer.cs', 'PixelBattle/Scripts/PixelBattleCuePlayer.cs'),
                               ('unity/PixelBattleImporter.cs', 'PixelBattle/Scripts/Editor/PixelBattleImporter.cs'),
                               ('unity/README_Unity_JP.txt', 'PixelBattle/README_Unity_JP.txt')):
            archive.write(TEMPLATES / source, target)


# ---------------------------------------------------------------- GBA

def _rgb555(image: Image.Image) -> Image.Image:
    """Snap an RGB image to the GBA's 15-bit colours (5 bits per channel)."""
    return image.point(lambda v: v & 0xF8)


def _distance(a: tuple[int, int, int], b: tuple[int, int, int]) -> int:
    return 2 * (a[0] - b[0]) ** 2 + 4 * (a[1] - b[1]) ** 2 + 3 * (a[2] - b[2]) ** 2


def pick_palette(counts: list[tuple[int, tuple[int, int, int]]], slots: int) -> list[tuple[int, int, int]]:
    """Choose `slots` colours that actually occur, without averaging.

    Starts from the most used colour, then repeatedly adds the colour that is far
    from every chosen colour, weighted gently by how often it is used. Small
    accents (eyes, sword edges) survive instead of being blended into big areas.
    """
    candidates = sorted(counts, reverse=True)[:8192]
    chosen = [candidates[0][1]]
    nearest = {color: _distance(color, chosen[0]) for _, color in candidates}
    weight = {color: count ** 0.25 for count, color in candidates}
    while len(chosen) < slots:
        color = max(nearest, key=lambda c: weight[c] * nearest[c])
        if nearest[color] == 0:
            break
        chosen.append(color)
        for other in nearest:
            d = _distance(other, color)
            if d < nearest[other]:
                nearest[other] = d
    return chosen


def gba_frames(seq: dict, assets: dict[str, dict], root: Path, colors: int) -> tuple[list[Image.Image], list[tuple[int, int, int]]]:
    """Render frames as indexed images sharing one palette. Index 0 is transparent."""
    if colors not in (16, 256):
        raise ValueError('GBA書き出しの色数は16色か256色を選んでください。')
    width, height, _, length = validate_stage(seq, for_export=True)
    if (width, height) != GBA_SIZE:
        raise ValueError('GBA書き出しは幅240・高さ160のシーケンスで行ってください。「設定保存」でサイズを変更できます。')
    rgb_frames, masks = [], []
    for image in frames(seq, assets, root):
        mask = image.getchannel('A').point(lambda a: 255 if a >= 128 else 0)
        rgb_frames.append(_rgb555(image.convert('RGB')))
        masks.append(mask)
    strip = Image.new('RGB', (width, height * length))
    strip_mask = Image.new('L', (width, height * length), 0)
    for index, (rgb, mask) in enumerate(zip(rgb_frames, masks)):
        strip.paste(rgb, (0, index * height))
        strip_mask.paste(mask, (0, index * height))
    opaque = Image.new('RGB', strip.size, (1, 2, 3))  # (1,2,3) cannot occur after the 5-bit snap
    opaque.paste(strip, (0, 0), strip_mask)
    counts = [(n, c) for n, c in (opaque.getcolors(maxcolors=1 << 24) or []) if c != (1, 2, 3)]
    slots = colors - 1
    if not counts:
        palette = [(0, 0, 0)]
    elif len(counts) <= slots:
        palette = [c for _, c in sorted(counts, reverse=True)]
    else:
        palette = pick_palette(counts, slots)
    # Unused palette slots repeat colour 0; any pixel mapped to such a slot is folded back to colour 0.
    pal_image = Image.new('P', (1, 1))
    pal_image.putpalette([v for c in palette + [palette[0]] * (256 - len(palette)) for v in c])
    full_palette = [GBA_TRANSPARENT] + palette
    flat = [v for c in full_palette for v in c] + [0, 0, 0] * (256 - len(full_palette))
    lookup = [(i if i < len(palette) else 0) + 1 for i in range(256)]
    result = []
    for rgb, mask in zip(rgb_frames, masks):
        mapped = rgb.quantize(palette=pal_image, dither=Image.Dither.NONE)
        indices = Image.frombytes('L', mapped.size, mapped.tobytes()).point(lookup)
        indices.paste(0, (0, 0), mask.point(lambda a: 255 - a))
        frame = Image.frombytes('P', indices.size, indices.tobytes())
        frame.putpalette(flat)
        frame.info['transparency'] = 0
        result.append(frame)
    return result, full_palette


def _jasc_pal(palette: list[tuple[int, int, int]], size: int) -> str:
    padded = palette + [(0, 0, 0)] * (size - len(palette))
    return 'JASC-PAL\r\n0100\r\n' + f'{size}\r\n' + ''.join(f'{r} {g} {b}\r\n' for r, g, b in padded)


def _gpl(palette: list[tuple[int, int, int]], name: str) -> str:
    return f'GIMP Palette\nName: {name}\nColumns: 16\n#\n' + ''.join(f'{r:3d} {g:3d} {b:3d}\tcolor{i}\n' for i, (r, g, b) in enumerate(palette))


def _bgr555(palette: list[tuple[int, int, int]], size: int) -> bytes:
    padded = palette + [(0, 0, 0)] * (size - len(palette))
    out = bytearray()
    for r, g, b in padded:
        value = (r >> 3) | ((g >> 3) << 5) | ((b >> 3) << 10)
        out += value.to_bytes(2, 'little')
    return bytes(out)


def package_gba(seq: dict, assets: dict[str, dict], root: Path, destination: Path, colors: int = 16) -> None:
    width, height, fps, length = validate_stage(seq, for_export=True)
    indexed, palette = gba_frames(seq, assets, root, colors)
    name = safe_name(seq.get('name', ''))
    base = f'{name}_gba{colors}'
    bpp = 4 if colors == 16 else 8
    layout = sheet_layout(length, width, height, 4096)
    cues = sound_cues(seq, assets, root)
    with zipfile.ZipFile(destination, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
        for index, frame in enumerate(indexed):
            archive.writestr(f'{base}/frames/frame_{index:03d}.png', _png_bytes(frame, transparency=0))
        if layout:
            archive.writestr(f'{base}/spritesheet.png', _png_bytes(build_sheet(indexed, *layout), transparency=0))
        archive.writestr(f'{base}/palette.pal', _jasc_pal(palette, colors))
        archive.writestr(f'{base}/palette.gpl', _gpl(palette, name))
        archive.writestr(f'{base}/palette_bgr555.bin', _bgr555(palette, colors))
        sounds = []
        written: dict[Path, str] = {}
        for cue in cues:
            filename = sound_file(archive, cue['path'], f'{base}/sounds', written)
            sounds.append({'file': filename, 'start_frame': cue['start_frame'], 'time': cue['time'],
                           'gba_vblank': round(cue['start_frame'] * 60 / fps), 'volume': cue['volume'], 'label': cue['label']})
        manifest = {
            'format': 'PixelBattleGBA', 'format_version': 1, 'name': name,
            'width': width, 'height': height, 'fps': fps, 'frame_count': length,
            'gba_vblanks_per_frame': round(60 / fps, 3), 'colors': colors, 'bits_per_pixel': bpp,
            'used_colors': len(palette) - 1, 'transparent_index': 0, 'transparent_color': list(GBA_TRANSPARENT),
            'color_format': 'RGB555 (each channel is a multiple of 8)',
            'bytes_per_frame': width * height * bpp // 8,
            'estimated_total_bytes': width * height * bpp // 8 * length,
            'spritesheet': ({'file': 'spritesheet.png', 'columns': layout[0], 'rows': layout[1]} if layout else None),
            'sounds': sounds,
        }
        archive.writestr(f'{base}/pixelbattle_gba.json', json.dumps(manifest, ensure_ascii=False, indent=2))
        archive.write(TEMPLATES / 'gba' / 'README_GBA_JP.txt', f'{base}/README_GBA_JP.txt')
