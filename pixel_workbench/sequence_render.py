"""Local-only battle sequence compositor and exporters.

No AI calls or network access. Coordinates are offsets from stage center, except
backgrounds, which fill the stage and use offsets relative to the top-left.
"""
from __future__ import annotations

import io
import json
import math
import os
import shutil
import subprocess
import tempfile
import zipfile
from functools import lru_cache
from pathlib import Path
from typing import Any

from PIL import Image, ImageSequence, UnidentifiedImageError

TRACK_ORDER = ('background', 'item', 'character', 'effect', 'sound')
MAX_FRAMES = 600
MAX_PIXELS = 960 * 540
MAX_PREVIEW_FRAME = 10000
SUPPORTED_IMAGE_EXT = {'.png', '.jpg', '.jpeg', '.webp', '.gif'}
SUPPORTED_AUDIO_EXT = {'.wav', '.mp3', '.ogg', '.m4a'}


def media_file(asset: dict, root: Path) -> Path | None:
    category, aid = asset.get('category', ''), asset.get('id', '')
    if category not in ('character', 'item', 'effect', 'background', 'sound', 'other'):
        return None
    folder = root / category / aid
    if not folder.is_dir():
        return None
    available = [str(f) for f in asset.get('files', []) if isinstance(f, str) and Path(f).name == f]
    preferred = (['effect.gif', 'preview.gif'] if asset.get('kind') != 'audio' else []) + [
        str(f) for f in available if f.startswith('frame_') and f.endswith('.png')
    ]
    if asset.get('primary_file'):
        preferred += [str(asset['primary_file'])]
    preferred += available
    for file in preferred:
        if file in available and (folder / file).is_file():
            path = folder / file
            if path.suffix.lower() in SUPPORTED_IMAGE_EXT | SUPPORTED_AUDIO_EXT:
                return path
    return None


def source_frames(asset: dict, root: Path) -> list[Path]:
    folder = root / asset.get('category', '') / asset.get('id', '')
    filenames = asset.get('files', [])
    frames = sorted(f for f in filenames if isinstance(f, str) and f.startswith('frame_') and f.endswith('.png') and Path(f).name == f and (folder / f).is_file())
    if len(frames) >= 2:
        return [folder / name for name in frames[:64]]
    return []


@lru_cache(maxsize=96)
def _load_still(path: str, stamp: int) -> Image.Image:
    with Image.open(path) as source:
        source.seek(0)
        return source.convert('RGBA').copy()


@lru_cache(maxsize=32)
def _load_gif(path: str, stamp: int) -> tuple[tuple[Image.Image, ...], tuple[int, ...]]:
    with Image.open(path) as source:
        frames = []
        durations = []
        for frame in ImageSequence.Iterator(source):
            frames.append(frame.convert('RGBA').copy())
            durations.append(max(20, int(frame.info.get('duration', 100))))
            if len(frames) >= 100:
                break
        if not frames:
            raise ValueError('GIFにフレームがありません。')
        return tuple(frames), tuple(durations)


def _open_for_frame(asset: dict, root: Path, offset: int, fps: int) -> Image.Image | None:
    frames = source_frames(asset, root)
    if frames:
        path = frames[offset % len(frames)]
        return _load_still(str(path), path.stat().st_mtime_ns)
    path = media_file(asset, root)
    if path is None or path.suffix.lower() not in SUPPORTED_IMAGE_EXT:
        return None
    try:
        if path.suffix.lower() == '.gif':
            images, ms = _load_gif(str(path), path.stat().st_mtime_ns)
            total = sum(ms)
            t = ((offset / fps) * 1000) % total
            for im, duration in zip(images, ms):
                if t < duration:
                    return im
                t -= duration
            return images[-1]
        return _load_still(str(path), path.stat().st_mtime_ns)
    except (OSError, UnidentifiedImageError):
        return None


def sequence_length(seq: dict) -> int:
    return max([int(c.get('start', 0)) + int(c.get('duration', 1)) for c in seq.get('clips', [])] or [1])


def validate_stage(seq: dict, for_export: bool = False) -> tuple[int, int, int, int]:
    width, height, fps = (int(seq.get('width', 240)), int(seq.get('height', 160)), int(seq.get('fps', 12)))
    length = sequence_length(seq)
    if width < 16 or height < 16 or fps < 1 or fps > 60 or width * height > MAX_PIXELS:
        raise ValueError('プレビュー・書き出しは960×540以下のキャンバスに対応しています。')
    if for_export and (length > MAX_FRAMES or len(seq.get('clips', [])) > 100):
        raise ValueError('書き出しは600フレーム・100クリップ以内です。長い演出は分割してください。')
    return width, height, fps, length


def clip_position(clip: dict, frame: int) -> tuple[int, int]:
    """Evaluate the local motion path, in stage-relative pixels, at a global frame.

    Old clips have no end coordinates and remain stationary. A moving clip
    reaches its end point on its last visible frame. This function is shared
    by the PNG preview, animated GIF export and MP4 renderer.
    """
    x, y = int(clip.get('x', 0)), int(clip.get('y', 0))
    end_x = clip.get('end_x')
    end_y = clip.get('end_y')
    if end_x is None and end_y is None:
        return x, y
    dx = x if end_x is None else int(end_x)
    dy = y if end_y is None else int(end_y)
    duration = max(1, int(clip.get('duration', 1)))
    offset = max(0, min(duration - 1, frame - int(clip.get('start', 0))))
    t = offset / (duration - 1) if duration > 1 else 1.0
    if clip.get('easing') == 'ease_in_out':
        t = t * t * (3 - 2 * t)
    elif clip.get('easing') == 'ease_out':
        t = 1 - (1 - t) * (1 - t)
    return round(x + (dx - x) * t), round(y + (dy - y) * t)


def render_frame(seq: dict, assets: dict[str, dict], root: Path, frame: int) -> Image.Image:
    w, h, fps, length = validate_stage(seq)
    if frame < 0 or frame > min(MAX_PREVIEW_FRAME, length):
        raise ValueError('フレーム番号が範囲外です。')
    canvas = Image.new('RGBA', (w, h), (0, 0, 0, 0))
    clips = [c for c in seq.get('clips', []) if c.get('track') != 'sound' and int(c.get('start', 0)) <= frame < int(c.get('start', 0)) + int(c.get('duration', 0))]
    clips.sort(key=lambda c: (TRACK_ORDER.index(c.get('track')) if c.get('track') in TRACK_ORDER else 2, int(c.get('start', 0))))
    for clip in clips:
        asset = assets.get(clip.get('asset_id'))
        if not asset:
            continue
        image = _open_for_frame(asset, root, frame - int(clip.get('start', 0)), fps)
        if image is None:
            continue
        if image.width * image.height > 4096 * 4096:
            continue
        x, y = clip_position(clip, frame)
        if clip.get('flip_x') and clip.get('track') != 'background':
            image = image.transpose(Image.Transpose.FLIP_LEFT_RIGHT)
        if clip.get('track') == 'background':
            # Make backgrounds cover the stage; a separate crop is needed for larger compositions.
            image = image.resize((w, h), Image.Resampling.NEAREST)
            pos = (x, y)
        else:
            pos = ((w - image.width) // 2 + x, (h - image.height) // 2 + y)
        overlay = Image.new('RGBA', (w, h), (0, 0, 0, 0))
        overlay.paste(image, pos)
        canvas = Image.alpha_composite(canvas, overlay)
    return canvas


def render_png(seq: dict, assets: dict[str, dict], root: Path, frame: int) -> bytes:
    buf = io.BytesIO()
    render_frame(seq, assets, root, frame).save(buf, format='PNG')
    return buf.getvalue()


def find_ffmpeg() -> str | None:
    try:
        import imageio_ffmpeg
        binary = imageio_ffmpeg.get_ffmpeg_exe()
        if binary and Path(binary).is_file():
            return binary
    except (ImportError, OSError, RuntimeError):
        pass
    return shutil.which('ffmpeg')


def _save_gif(seq: dict, assets: dict[str, dict], root: Path, destination: Path) -> None:
    width, height, fps, length = validate_stage(seq, for_export=True)
    if width * height * length > 40_000_000:
        raise ValueError('GIFの合成画素数が多すぎます。画面を縮小するか、MP4を利用してください。')
    # PIL palette GIF transparency is only 1-bit. Per-frame duration is rounded to GIF's 10ms tick.
    frames: list[Image.Image] = []
    for i in range(length):
        frame = render_frame(seq, assets, root, i)
        # A transparent GIF is important for game effect overlays.
        alpha = frame.getchannel('A')
        pal = frame.convert('RGB').quantize(colors=255, method=Image.Quantize.FASTOCTREE)
        mask = alpha.point(lambda a: 255 if a < 128 else 0)
        pal.paste(255, mask)
        pal.putpalette(pal.getpalette()[:768])
        pal.info['transparency'] = 255
        frames.append(pal)
    frames[0].save(destination, save_all=True, append_images=frames[1:], duration=max(20, round(1000 / fps / 10) * 10), loop=0, disposal=2, optimize=False, transparency=255)


def _save_mp4(seq: dict, assets: dict[str, dict], root: Path, destination: Path) -> None:
    _, _, fps, length = validate_stage(seq, for_export=True)
    binary = find_ffmpeg()
    if not binary:
        raise RuntimeError('MP4書き出しにはFFmpegが必要です。requirementsのimageio-ffmpegを導入してください。')
    duration = length / fps
    with tempfile.TemporaryDirectory(prefix='pixelbattle_') as temp_dir:
        temp = Path(temp_dir)
        for frame_num in range(length):
            rgba = render_frame(seq, assets, root, frame_num)
            # MP4 cannot contain alpha, use opaque dark stage background.
            rgb = Image.new('RGB', rgba.size, '#101822')
            rgb.paste(rgba, (0, 0), rgba)
            rgb.save(temp / f'{frame_num:05d}.png')
        args = [binary, '-hide_banner', '-loglevel', 'error', '-nostdin', '-y', '-framerate', str(fps), '-i', str(temp / '%05d.png')]
        audio_filters = []
        count = 0
        for clip in seq.get('clips', []):
            if clip.get('track') != 'sound':
                continue
            asset = assets.get(clip.get('asset_id'))
            path = media_file(asset, root) if asset else None
            if not path or path.suffix.lower() not in SUPPORTED_AUDIO_EXT:
                continue
            args.extend(['-i', str(path)])
            start_ms = max(0, int(clip.get('start', 0)) * 1000 // fps)
            seconds = max(0.01, min(int(clip.get('duration', 1)) / fps, duration - start_ms / 1000))
            volume = min(2.0, max(0.0, float(clip.get('volume', 1))))
            count += 1
            audio_filters.append(f'[{count}:a]atrim=0:{seconds:.4f},asetpts=PTS-STARTPTS,volume={volume:.3f},adelay={start_ms}:all=1[a{count}]')
        args += ['-map', '0:v:0', '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-vf', 'pad=ceil(iw/2)*2:ceil(ih/2)*2']
        if count:
            audio_filters.append(''.join(f'[a{i}]' for i in range(1, count + 1)) + f'amix=inputs={count}:duration=longest:dropout_transition=0[aout]')
            args += ['-filter_complex', ';'.join(audio_filters), '-map', '[aout]', '-c:a', 'aac', '-b:a', '160k']
        else:
            args += ['-an']
        args += ['-t', f'{duration:.6f}', '-movflags', '+faststart', str(destination)]
        try:
            proc = subprocess.run(args, capture_output=True, text=True, timeout=150, check=False)
        except subprocess.TimeoutExpired:
            raise RuntimeError('MP4書き出しが時間制限を超えました。')
        if proc.returncode or not destination.is_file() or destination.stat().st_size == 0:
            raise RuntimeError('FFmpegでMP4を作成できませんでした。' + (proc.stderr or '')[-350:])


def save_media(seq: dict, assets: dict[str, dict], root: Path, destination: Path, kind: str) -> None:
    validate_stage(seq, for_export=True)
    if kind == 'gif':
        _save_gif(seq, assets, root, destination)
    elif kind == 'mp4':
        _save_mp4(seq, assets, root, destination)
    else:
        raise ValueError('書き出し形式が不正です。')


def project_manifest(seq: dict, assets: dict[str, dict]) -> dict[str, Any]:
    unique_ids = list(dict.fromkeys(c.get('asset_id') for c in seq.get('clips', [])))
    included = []
    for asset_id in unique_ids:
        a = assets.get(asset_id)
        if a:
            included.append({**a, 'relative_folder': f"assets/{a['category']}/{a['id']}"})
    return {'format': 'PixelLabBattleSequence', 'format_version': 1,
            'time_unit': 'frames', 'coordinates': 'center offsets for sprites; top-left for backgrounds',
            'timeline': seq, 'assets': included,
            'notes': 'GIF has no audio. MP4 mixes audio clips. Images are nearest-neighbor at their original size.'}


def package_project(seq: dict, assets: dict[str, dict], root: Path, dest: Path) -> None:
    manifest = project_manifest(seq, assets)
    with zipfile.ZipFile(dest, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr('project.json', json.dumps(manifest, ensure_ascii=False, indent=2))
        for asset in manifest['assets']:
            for name in asset.get('files', []):
                if not isinstance(name, str) or Path(name).name != name:
                    continue
                p = root / asset['category'] / asset['id'] / name
                if p.is_file():
                    archive.write(p, f"assets/{asset['category']}/{asset['id']}/{name}")
