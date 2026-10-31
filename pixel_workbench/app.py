"""PixelLab Local Trial - personal local UI for PixelLab API v2.

API keys can be kept encrypted for the local Windows user using DPAPI.
The app binds to 127.0.0.1 only.
"""
from __future__ import annotations

import base64
import io
import json
import logging
import os
import secrets
import threading
import time
import uuid
import webbrowser
from urllib.parse import urlsplit
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx
from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from PIL import Image, ImageDraw, UnidentifiedImageError
from sequence_render import render_png, validate_stage, save_media, package_project, project_manifest, media_file, sequence_length
from credential_store import read_credentials, write_credentials, supported as credential_storage_supported
from pydantic import BaseModel, Field

ROOT = Path(__file__).resolve().parent
DATA = ROOT / 'data'
OUTPUT = DATA / 'outputs'
ASSETS = DATA / 'assets'
EXPORTS = DATA / 'exports'
STATE_FILE = DATA / 'state.json'
STATIC = ROOT / 'static'
BASE_URL = 'https://api.pixellab.ai/v2'
OLLAMA_URL = 'http://127.0.0.1:11434'
DATA.mkdir(parents=True, exist_ok=True)
OUTPUT.mkdir(parents=True, exist_ok=True)
ASSETS.mkdir(parents=True, exist_ok=True)
EXPORTS.mkdir(parents=True, exist_ok=True)
logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
log = logging.getLogger('pixellab-local')
app = FastAPI(title='PixelLab + GPT Local Workbench', version='0.7.0', docs_url=None, redoc_url=None)
app.mount('/static', StaticFiles(directory=str(STATIC)), name='static')
_lock = threading.RLock()
_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix='art-job')
_saved_tokens: dict[str, str] = {}
_credential_store_warning = False
try:
    _saved_tokens = read_credentials()
except RuntimeError:
    # Do not reveal ciphertext, plaintext credentials, or operating-system details.
    _credential_store_warning = True
    log.warning('Stored API credentials could not be loaded; they may need replacing.')
_pixellab_env = os.environ.get('PIXELLAB_API_KEY', '').strip() or os.environ.get('PIXELLAB_SECRET', '').strip()
_openai_env = os.environ.get('OPENAI_API_KEY', '').strip()
_token = _pixellab_env or _saved_tokens.get('pixellab', '')
_openai_token = _openai_env or _saved_tokens.get('openai', '')


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def month() -> str:
    return datetime.now().strftime('%Y-%m')


def initial_state() -> dict:
    return {'budget_usd': 2.0, 'jobs': [], 'assets': [], 'sequences': []}


def load_state() -> dict:
    try:
        state = json.loads(STATE_FILE.read_text(encoding='utf-8'))
        if isinstance(state, dict) and isinstance(state.get('jobs'), list):
            state.setdefault('assets', [])
            state.setdefault('sequences', [])
            return state
        return initial_state()
    except (FileNotFoundError, ValueError, OSError):
        return initial_state()


_state = load_state()
# Jobs interrupted during shutdown cannot safely be retried; API may have already charged.
for _job in _state['jobs']:
    if _job.get('status') in ('queued', 'processing'):
        _job['status'] = 'unknown'
        _job['error'] = '前回のアプリ終了により結果が不明です。PixelLabの残高をご確認ください。'


def save_state() -> None:
    with _lock:
        temp = STATE_FILE.with_suffix('.tmp')
        temp.write_text(json.dumps(_state, ensure_ascii=False, indent=2), encoding='utf-8')
        temp.replace(STATE_FILE)


save_state()


def require_token() -> str:
    if not _token:
        raise RuntimeError('APIキーが未設定です。先に「API設定」で登録してください。')
    return _token


def pixel_request(method: str, route: str, payload: dict | None = None, timeout: int = 75) -> dict:
    token = require_token()
    url = BASE_URL + route
    with httpx.Client(timeout=httpx.Timeout(timeout, connect=15), follow_redirects=False) as client:
        response = client.request(method, url, headers={'Authorization': f'Bearer {token}', 'Content-Type': 'application/json'}, json=payload)
    try:
        value = response.json()
    except ValueError:
        raise RuntimeError(f'PixelLab HTTP {response.status_code}: JSON以外の応答')
    if response.is_error:
        detail = value.get('detail', value) if isinstance(value, dict) else value
        # Do not return auth headers or secrets.
        raise RuntimeError(f'PixelLab HTTP {response.status_code}: {str(detail)[:650]}')
    if not isinstance(value, dict):
        raise RuntimeError('PixelLabから予想外の応答形式が返りました。')
    return value


def balance_data() -> dict:
    result = pixel_request('GET', '/balance', timeout=30)
    return {'credits': result.get('credits', {}), 'subscription': result.get('subscription', {}), 'fetched_at': now()}


def number(value: Any) -> float | None:
    try:
        n = float(value)
        if n == n and abs(n) != float('inf'):
            return n
    except (TypeError, ValueError, OverflowError):
        pass
    return None


def credits_usd(balance: dict | None) -> float | None:
    try:
        return number(balance['credits']['usd'])
    except (TypeError, KeyError):
        return None


def generations_left(balance: dict | None) -> float | None:
    try:
        return number(balance['subscription']['generations'])
    except (TypeError, KeyError):
        return None


def estimate_usd(mode: str, size: int, frames: int = 8, provider: str='pixellab', model: str='standard', quality: str='low', gpt_size: str='1024x1024') -> float | None:
    """Known examples only, never a guaranteed quote. None -> ask for explicit reservation."""
    if provider == 'openai':
        # Official GPT Image 2 OUTPUT cost examples; inputs extra (especially edits).
        if model == 'gpt-image-2':
            price = {'1024x1024':{'low':.006,'medium':.053,'high':.211},
                     '1024x1536':{'low':.005,'medium':.041,'high':.165},
                     '1536x1024':{'low':.005,'medium':.041,'high':.165}}
            return price[gpt_size][quality]
        return None  # 2.5 output cost depends on generated image tokens.
    if model == 'pro':
        return None  # Pro GPU costs vary; do not fake a per-call amount.
    if mode == 'vfx':
        return None  # VFX is quoted/billed in PixelLab generations, not fixed USD.
    if mode == 'redraw':
        return None  # Pro reference-guided creation is a variable-price endpoint.
    if mode == 'text':
        pairs = {32:0.0070,64:0.00718,96:0.0076,128:0.00793,192:0.0086,256:0.0089,512:0.0169}
        return pairs.get(size)
    if mode == 'convert':
        return {32:.006,64:.006,96:.0064,128:.00666,192:.009,256:.01164,512:None}.get(size)
    if mode == 'animate':
        if frames not in (4,8,16): return None  # No reliable fixed USD quote for intermediate counts.
        if size <= 64 and frames <= 4: return .0221
        if size <= 256 and frames <= 8: return .0302
        return .0424
    raise ValueError('不明なモードです')


def local_stats() -> dict:
    with _lock:
        jobs = [j.copy() for j in _state['jobs'] if j.get('month') == month() and not j.get('demo') and j.get('provider') != 'chatgpt']
        confirmed = sum(j.get('usd_charged') or 0.0 for j in jobs if j.get('usage_status') == 'confirmed')
        equivalent = sum(j.get('estimate_usd') or 0.0 for j in jobs if j.get('status') != 'failed')
        used_gens = sum(j.get('generations_used') or 0.0 for j in jobs)
        # Budget guard reserves even completed jobs with unknown cost.
        locked = sum((j.get('reservation_usd') if j.get('reservation_usd') is not None else max(j.get('usd_charged') or 0, (j.get('estimate_usd') or 0.0)*1.5)) for j in jobs if j.get('status') != 'failed')
        pending = sum(j.get('reservation_usd', (j.get('estimate_usd') or 0.0)*1.5) or 0 for j in jobs if j.get('status') in ('queued','processing','unknown'))
        unknown = sum(1 for j in jobs if j.get('status') == 'unknown' or (j.get('status') == 'completed' and j.get('usage_status') == 'unknown'))
        return {'usd_confirmed': round(confirmed, 6), 'usd_equivalent_estimate': round(equivalent, 6), 'generations_used': round(used_gens, 3), 'pending_reserved': round(pending, 6), 'budget_reserved': round(locked, 6), 'unknown_count': unknown, 'calls': len(jobs), 'month': month(), 'budget_usd': _state['budget_usd']}


def image_payload(blob: bytes) -> tuple[dict, tuple[int, int]]:
    if len(blob) > 12 * 1024 * 1024:
        raise ValueError('画像が大きすぎます。12MB以下にしてください。')
    try:
        with Image.open(io.BytesIO(blob)) as img:
            img.load()
            if img.width > 6000 or img.height > 6000:
                raise ValueError('入力画像は長辺6000px以下にしてください。')
            clean = img.convert('RGBA')
            buff = io.BytesIO()
            clean.save(buff, format='PNG', optimize=True)
            return {'type': 'base64', 'base64': base64.b64encode(buff.getvalue()).decode('ascii'), 'format': 'png'}, clean.size
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError):
        raise ValueError('PNGまたはJPEGなどの通常の画像を選択してください。')


def sized_image(blob: bytes, limit: int) -> tuple[dict, tuple[int, int]]:
    with Image.open(io.BytesIO(blob)) as source:
        img = source.convert('RGBA')
        img.thumbnail((limit, limit), Image.Resampling.LANCZOS)
        buff = io.BytesIO()
        img.save(buff, 'PNG')
        return image_payload(buff.getvalue())


def find_images(payload: Any) -> list[bytes]:
    """PixelLab images and OpenAI Images API response (base64 only)."""
    out: list[bytes] = []
    def decode(value: Any) -> None:
        if not isinstance(value, str) or len(value) > 34_000_000:
            return
        if value.startswith('data:'):
            value = value.partition(',')[2]
        try:
            blob = base64.b64decode(value, validate=True)
            if blob.startswith((b'\x89PNG', b'\xff\xd8', b'RIFF')):
                out.append(blob)
        except Exception:
            return
    def descend(item: Any, depth: int=0) -> None:
        if depth > 8 or len(out) >= 64:
            return
        if isinstance(item, dict):
            for key in ('base64', 'b64_json'):
                if key in item:
                    decode(item[key])
                    if out:
                        return
            for key in ('data','images','image','frames','last_response','result','output','animation'):
                if key in item:
                    descend(item[key], depth+1)
        elif isinstance(item, list):
            for v in item:
                descend(v, depth+1)
    descend(payload)
    return out


def extract_usage(payload: Any) -> tuple[float | None, float | None]:
    candidate = payload.get('usage') if isinstance(payload, dict) else None
    if not candidate and isinstance(payload.get('last_response'), dict):
        candidate = payload['last_response'].get('usage')
    if isinstance(candidate, dict):
        if candidate.get('type') == 'usd':
            return number(candidate.get('usd')), 0.0
        if candidate.get('type') == 'generations':
            return 0.0, number(candidate.get('generations'))
        return number(candidate.get('usd')), number(candidate.get('generations'))
    return None, None


def installed_ollama_models() -> list[str]:
    """Only contact Ollama on this computer. Never accept an arbitrary server URL."""
    with httpx.Client(timeout=httpx.Timeout(4.0, connect=2.0), trust_env=False) as client:
        response = client.get(OLLAMA_URL + '/api/tags')
        response.raise_for_status()
        body = response.json()
    if not isinstance(body, dict) or not isinstance(body.get('models'), list):
        raise ValueError('Ollamaのモデル一覧の形式が不明です。')
    return [m['name'] for m in body['models'] if isinstance(m, dict) and isinstance(m.get('name'), str) and m['name'] and not m['name'].lower().endswith(':cloud')][:100]


def ollama_chat(model: str, task: str, prompt: str, mode: str, fidelity: str, reference: bytes | None) -> str:
    """Local text/vision assistant; never calls the paid image providers."""
    if task not in ('refine', 'describe'):
        raise ValueError('未対応の補助タスクです。')
    if task == 'describe' and not reference:
        raise ValueError('画像の特徴抽出には参照画像を選択してください。')
    available = installed_ollama_models()
    if model not in available:
        raise ValueError('選択したOllamaモデルが見つかりません。モデル一覧を更新してください。')
    guidelines = (
        'You are a precision-focused pixel-art art director. '
        'Do not invent observable details or silently redesign a character. '
        'Preserve identity, head-to-body ratio, age impression, face, pose, silhouette, hair volume, glasses, clothing layers and small accessories. '
        'If an image is supplied, it is the most authoritative visual reference. '
        'No verbose introductions, no markdown code fences.\n'
    )
    if task == 'describe':
        task_text = ('List the actual visible attributes of the supplied reference in concise Japanese bullet points. '
                     'Distinguish facial proportions, hair, pose, clothing layers, small objects and original body proportions. '
                     'Never guess hidden details. Do NOT claim to have generated a new image.')
    elif mode == 'vfx':
        task_text = ('Write a single focused English prompt for an animated pixel-art VFX only. '
                     'Specify the effect, color, progression and end, and whether it should loop. '
                     'Do not add a character unless one appears in the reference. Output only the prompt.')
    else:
        task_text = (
            'Write ONE improved English image-generation prompt, 300-1500 characters long, using the user intent and image if provided. '
            'Retain exact known details. Request crisp pixel clusters, legibility at a small scale, and faithful identity. '
            'Avoid chibi proportions and invented accessories unless explicitly requested. '
            'For a reference-based conversion prioritize the image rather than fabricating more details. '
            'Output ONLY the final prompt.'
        )
    text = ('Operation: '+ mode + '\nFidelity: ' + fidelity + '\nUser request: ' + (prompt or '(no extra instructions)') + '\nTask: ' + task_text)
    msg: dict[str, Any] = {'role':'user', 'content':text}
    if reference:
        clean, _ = sized_image(reference, 1024)
        msg['images'] = [clean['base64']]
    payload = {'model': model, 'messages': [{'role':'system','content':guidelines}, msg],
               'stream':False, 'options': {'temperature':0.25, 'num_predict':800}}
    with httpx.Client(timeout=httpx.Timeout(150.0, connect=4.0), trust_env=False) as client:
        response = client.post(OLLAMA_URL + '/api/chat', json=payload)
        if response.is_error:
            try:
                info = response.json().get('error', '不明なエラー')
            except ValueError:
                info = '予想外の応答'
            raise RuntimeError(f'Ollama HTTP {response.status_code}: {str(info)[:300]}（画像解析にはVisionモデルが必要です）')
        result = response.json()
    content = result.get('message', {}).get('content') if isinstance(result, dict) else None
    if not isinstance(content, str) or not content.strip():
        raise RuntimeError('Ollamaから文章が返りませんでした。')
    return content.strip()[:3000]


OPENAI_MODELS = ('gpt-image-2', 'gpt-image-2.5-flare', 'gpt-image-2.5-sunburst')
GPT_SIZES = ('1024x1024', '1024x1536', '1536x1024')
PIXEL_SIZES = (32,64,96,128,192,256,512)

ASSET_CATEGORIES = ('character','item','effect','background','sound','ui','other')
ASSET_LABELS = {
    'character':'キャラクター','item':'アイテム','effect':'エフェクト','background':'背景','sound':'効果音・音声','ui':'UI・ウィンドウ枠','other':'その他'
}

def normalize_category(value: str | None, mode: str='text') -> str:
    if value in ASSET_CATEGORIES:
        return str(value)
    defaults = {'vfx':'effect','animate':'character','convert':'character','text':'other'}
    return defaults.get(mode, 'other')

def asset_dir(category: str, asset_id: str) -> Path:
    d = ASSETS / category / asset_id
    d.mkdir(parents=True, exist_ok=True)
    return d

def guess_asset_kind(filename: str) -> str:
    lower = filename.lower()
    if lower.endswith(('.mp3','.wav','.ogg','.m4a')):
        return 'audio'
    if lower.endswith('.gif'):
        return 'animation'
    if lower.endswith('.json'):
        return 'data'
    return 'image'

def unique_asset_name(name: str, category: str) -> str:
    base = (name or 'asset').strip()[:80] or 'asset'
    existing = {a.get('name') for a in _state.get('assets', []) if a.get('category') == category}
    if base not in existing:
        return base
    n = 2
    while f'{base} {n}' in existing:
        n += 1
    return f'{base} {n}'

def ensure_generated_asset(job: dict) -> None:
    asset_id = job['id']
    category = normalize_category(job.get('category'), job.get('mode','text'))
    files = job.get('files') or []
    if not files:
        return
    with _lock:
        existing = next((a for a in _state.get('assets', []) if a.get('id') == asset_id), None)
        if existing:
            existing.update({'category': category, 'files': files, 'updated_at': now()})
            save_state()
            return
    out_dir = OUTPUT / asset_id
    if not out_dir.exists():
        return
    target = asset_dir(category, asset_id)
    copied = []
    for name in files:
        src = out_dir / name
        if src.exists():
            dst = target / name
            if not dst.exists():
                dst.write_bytes(src.read_bytes())
            copied.append(name)
    primary = next((f for f in copied if f not in ('reference.png','spritesheet.json')), copied[0] if copied else None)
    asset = {'id': asset_id, 'name': unique_asset_name(job.get('asset_name') or job.get('prompt') or job.get('mode') or 'generated', category), 'category': category, 'kind': guess_asset_kind(primary or ''), 'created_at': job.get('created_at', now()), 'updated_at': now(), 'source': 'generated', 'source_job_id': asset_id, 'provider': job.get('provider'), 'mode': job.get('mode'), 'files': copied, 'primary_file': primary}
    with _lock:
        _state.setdefault('assets', []).append(asset)
        save_state()

def asset_summary(asset: dict) -> dict:
    item = asset.copy()
    item['category_label'] = ASSET_LABELS.get(item.get('category'), item.get('category', ''))
    return item

def sequence_summary(seq: dict) -> dict:
    item = seq.copy()
    item['clip_count'] = len(item.get('clips', []))
    return item


def request_openai_image(mode: str, model: str, prompt: str, quality: str, gpt_size: str, transparent: bool, reference: bytes | None) -> dict:
    if not _openai_token:
        raise RuntimeError('OpenAI APIキーがありません。接続・料金設定で登録してください。')
    if mode == 'animate':
        raise ValueError('GPT画像APIはこのツールのアニメーション機能に未対応です。')
    headers = {'Authorization': 'Bearer ' + _openai_token}
    data = {'model': model, 'prompt': prompt, 'quality': quality, 'size': gpt_size, 'output_format': 'png', 'background': 'transparent' if transparent else 'opaque'}
    # JSON images inputs are also available, but multipart is broadly supported by Images/edits.
    url = 'https://api.openai.com/v1/images/edits' if reference else 'https://api.openai.com/v1/images/generations'
    with httpx.Client(timeout=httpx.Timeout(300.0, connect=15), follow_redirects=False) as client:
        if reference:
            clean, _ = image_payload(reference)
            blob = base64.b64decode(clean['base64'])
            response = client.post(url, headers=headers, data=data, files=[('image[]', ('reference.png', blob, 'image/png'))])
        else:
            response = client.post(url, headers=headers, json=data)
    try:
        content = response.json()
    except ValueError:
        raise RuntimeError(f'OpenAI HTTP {response.status_code}: 不明な応答')
    if response.is_error:
        error = content.get('error', content) if isinstance(content, dict) else content
        if isinstance(error, dict):
            error = error.get('message', str(error))
        raise RuntimeError(f'OpenAI HTTP {response.status_code}: {str(error)[:480]}')
    return content


def gpt_prompt(user_prompt: str, mode: str, fidelity: str) -> str:
    pixel = 'Create actual readable pixel art with crisp, consistent pixel clusters, limited-color game-sprite shading and distinct silhouettes. Avoid blurry faux pixel textures and avoid smooth raster painting.'
    if mode in ('text','redraw'):
        return pixel + '\n' + user_prompt
    base = ('Convert the attached reference artwork into high-quality pixel art. Preserve the same character identity, original pose, facial features, eyewear, hairstyle, clothing layers, accessories, proportions, colors and overall composition. No redesign, no chibi proportions, no different face. ' if fidelity=='faithful' else 'Convert the attached reference artwork into polished pixel art while retaining its main composition and recognizable details. ')
    if fidelity == 'creative':
        base = 'Reinterpret the attached image as a game-ready pixel art sprite, maintaining its essential identity. '
    return pixel + '\n' + base + ('\nAdditional instructions: ' + user_prompt if user_prompt else '')

def demo_images(mode: str, size: int, frames: int, width: int | None=None, height: int | None=None) -> list[bytes]:
    import math
    count = frames if mode == 'animate' else 1
    result = []
    canvas_size = min(max(size, 32), 256)
    for frame in range(count):
        img = Image.new('RGBA', (canvas_size, canvas_size), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        u = max(2, canvas_size // 16)
        cx = canvas_size // 2
        bob = int(math.sin(frame * 0.7) * u) if mode == 'animate' else 0
        def rect(x1, y1, x2, y2, color):
            draw.rectangle((cx + x1*u, canvas_size//2 + (y1*u) + bob, cx + x2*u-1, canvas_size//2 + (y2*u)+bob-1), fill=color)
        rect(-3, -5, 3, -1, '#413059')
        rect(-2, -4, 2, -2, '#d0bdff')
        rect(-2, -2, 2, 2, '#e9a86d')
        rect(-1, -1, 0, 0, '#292040')
        rect(1, -1, 2, 0, '#292040')
        rect(-3, 2, 3, 5, '#5c87b9')
        rect(-3, 5, -1, 7, '#423c51')
        rect(1, 5, 3, 7, '#423c51')
        if mode in ('text','redraw') and width is not None and height is not None:
            # Demo only: retain exact requested dimensions without calling the service.
            resized = img.copy()
            resized.thumbnail((width, height), Image.Resampling.NEAREST)
            canvas = Image.new('RGBA', (width, height), (0, 0, 0, 0))
            canvas.paste(resized, ((width - resized.width)//2, (height - resized.height)//2), resized)
            img = canvas
        buff = io.BytesIO()
        img.save(buff, 'PNG')
        result.append(buff.getvalue())
    return result


def make_exports(job_id: str, blobs: list[bytes], animated: bool) -> list[str]:
    directory = OUTPUT / job_id
    directory.mkdir(exist_ok=True)
    frames = []
    files = []
    for i, blob in enumerate(blobs[:32]):
        with Image.open(io.BytesIO(blob)) as im:
            image = im.convert('RGBA')
            filename = f'frame_{i:02d}.png' if animated else f'image_{i:02d}.png'
            image.save(directory / filename)
            files.append(filename)
            frames.append(image)
    if animated and frames:
        width = max(i.width for i in frames)
        height = max(i.height for i in frames)
        sheet = Image.new('RGBA', (width * len(frames), height), (0, 0, 0, 0))
        for index, im in enumerate(frames):
            sheet.paste(im, (index * width, 0), im)
        sheet.save(directory / 'spritesheet.png')
        files.append('spritesheet.png')
        # GIF is an 8-bit format and necessarily quantizes colors/transparency.
        frames[0].save(directory / 'preview.gif', save_all=True, append_images=frames[1:], duration=140, loop=0, disposal=2)
        files.append('preview.gif')
    return files


def update_job(job_id: str, **fields: Any) -> None:
    with _lock:
        job = next(j for j in _state['jobs'] if j['id'] == job_id)
        job.update(fields)
        save_state()


def vfx_quote_generations(artist: str, reference: bytes | None) -> int:
    """PixelLab published VFX *initial quote*, NOT a hard charge ceiling."""
    if artist not in ('lite', 'pro'):
        raise ValueError('不明なVFXアーティストです。')
    if reference is None:
        return 1 if artist == 'lite' else 6
    with Image.open(io.BytesIO(reference)) as im:
        longest = max(im.width, im.height)
    if longest > 512:
        raise ValueError('VFXに重ねる画像は512px以下にしてください。')
    if artist == 'lite':
        return 5 if longest > 256 else 3 if longest > 128 else 1
    return 28 if longest > 256 else 14 if longest > 128 else 8


def vfx_file_urls(payload: dict) -> list[tuple[str, str]]:
    """Find declared exports (GIF, spritesheet, JSON, optional game ZIP)."""
    src = payload.get('last_response', payload)
    if not isinstance(src, dict):
        return []
    raw = src.get('files') or payload.get('files') or []
    out = []
    if isinstance(raw, dict):
        for kind, val in raw.items():
            if isinstance(val, str): out.append((str(kind), val))
            if isinstance(val, dict):
                url = val.get('url') or val.get('href') or val.get('download_url')
                if isinstance(url, str): out.append((str(val.get('name') or kind), url))
    elif isinstance(raw, list):
        for i, item in enumerate(raw):
            if not isinstance(item, dict): continue
            url = item.get('url') or item.get('href') or item.get('download_url')
            if isinstance(url,str): out.append((str(item.get('name') or item.get('filename') or item.get('kind') or i), url))
    return out


def safe_vfx_url(url: str) -> None:
    """Do not let a response turn a local process into a general URL fetcher."""
    parsed = urlsplit(url)
    host = (parsed.hostname or '').lower()
    if parsed.scheme != 'https' or parsed.username or parsed.password or not (host == 'pixellab.ai' or host.endswith('.pixellab.ai')):
        raise ValueError('生成ファイルが信頼済みのPixelLabドメイン外にあります。安全のため自動取得しません。')


def save_vfx_files(job_id: str, response: dict) -> list[str]:
    """Store actual VFX exports, never rebuild a guessed animation from a sheet."""
    directory = OUTPUT/job_id
    directory.mkdir(exist_ok=True)
    saved=[]
    fetched = vfx_file_urls(response)
    if not fetched:
        raise RuntimeError('VFX生成が完了しましたが、ファイルURLが確認できません。再送せずPixelLabのMy effectsで確認してください。')
    kinds = {'.gif':'effect.gif', '.png':'spritesheet.png', '.json':'spritesheet.json', '.zip':'game-pack.zip'}
    with httpx.Client(timeout=httpx.Timeout(45, connect=10), follow_redirects=False) as client:
        for name,url in fetched:
            try:
                safe_vfx_url(url)
                path = urlsplit(url).path.lower()
                ext = next((x for x in kinds if path.endswith(x)), None)
                if not ext:
                    key = name.lower()
                    ext = next((x for x in kinds if x[1:] in key), None)
                if not ext: continue
                if ext == '.png' and ('normal' in name.lower() or 'normal' in path): file='normals.png'
                else: file=kinds[ext]
                if file in saved:continue
                with client.stream('GET',url) as res:
                    res.raise_for_status()
                    size=0; chunks=[]
                    for chunk in res.iter_bytes():
                        size+=len(chunk)
                        if size>40*1024*1024: raise RuntimeError('VFXファイルが40MBを超えたため保存を中止しました。')
                        chunks.append(chunk)
                data=b''.join(chunks)
                if ext=='.png' and not data.startswith(b'\x89PNG'): raise ValueError('PNGとして不正な応答')
                if ext=='.gif' and not data.startswith((b'GIF87a',b'GIF89a')): raise ValueError('GIFとして不正な応答')
                if ext=='.zip' and not data.startswith(b'PK'): raise ValueError('ZIPとして不正な応答')
                if ext=='.json': json.loads(data)
                (directory/file).write_bytes(data)
                saved.append(file)
            except (ValueError,httpx.HTTPError,OSError) as exc:
                log.warning('VFX export unavailable: %s (%s)',name, type(exc).__name__)
    if not saved:
        raise RuntimeError('VFX生成は完了しましたが、ファイルの取得に失敗しました。PixelLabのMy effectsで確認してください。')
    return saved


def demo_vfx(size: int, frames: int=12) -> list[bytes]:
    """Deterministic offline lightning sample, obviously labelled demo."""
    import math
    sz=min(max(size,64),256); out=[]
    for i in range(frames):
        img=Image.new('RGBA',(sz,sz));draw=ImageDraw.Draw(img)
        progress=i/(frames-1)
        if 0.1 < progress < 0.75:
            points=[(sz//2,0),(sz//2-sz//10,sz//4),(sz//2+sz//15,sz//3),(sz//2-sz//9,sz//2),(sz//2+sz//12,3*sz//4),(sz//2,sz-6)]
            end = max(2,min(len(points), int(progress*len(points)*2)))
            draw.line(points[:end],fill='#376edf',width=max(5,sz//22),joint='curve')
            draw.line(points[:end],fill='#ddfdff',width=max(2,sz//60),joint='curve')
            if progress>0.4:
                cx,cy=points[-1]
                r=max(2,int(sz//4*(.8-progress)))
                draw.ellipse((cx-r,cy-r//2,cx+r,cy+r//2),outline='#b7eaff',width=max(1,sz//100))
        if progress>=0.75:
            rad=int((1-progress)*sz//2)
            draw.ellipse((sz//2-rad,sz-12-rad//3,sz//2+rad,sz-12+rad//3),outline='#81ddff',width=max(2,sz//60))
        buf=io.BytesIO();img.save(buf,format='PNG');out.append(buf.getvalue())
    return out


def execute_job(job_id: str, mode: str, prompt: str, size: int, transparent: bool, frames: int, reference: bytes | None, demo: bool, provider: str='pixellab', model: str='standard', fidelity: str='balanced', quality: str='low', gpt_size: str='1024x1024', width: int | None=None, height: int | None=None, category: str='other') -> None:
    update_job(job_id, status='processing', started_at=now())
    before, after, dispatched = None, None, False
    try:
        if demo:
            time.sleep(.15)
            blobs = demo_vfx(size) if mode == 'vfx' else demo_images(mode, size, frames, width, height)
            result: dict = {}
        elif provider == 'openai':
            dispatched = True
            result = request_openai_image(mode, model, gpt_prompt(prompt, mode, fidelity), quality, gpt_size, transparent, reference)
            blobs = find_images(result)
            if not blobs: raise RuntimeError('GPT画像APIから画像を取得できませんでした。自動再送しません。')
        else:
            try: before = balance_data()
            except Exception as e: log.warning('Preflight balance not available: %s', e)
            dispatched = True
            if mode == 'vfx':
                vfx_data={'description':prompt,'artist':'pro' if model == 'pro' else 'lite','name':('Effect '+now())[:80],'game_pack':True}
                if reference is not None:
                    sprite, dims = image_payload(reference)
                    if max(dims) > 512:raise ValueError('VFX画像は512px以下にしてください。')
                    vfx_data['sprite']=sprite
                result=pixel_request('POST','/vfx',vfx_data,timeout=180)
                update_job(job_id, quoted_generations=result.get('quoted_generations'))
            elif mode == 'text':
                if model == 'pro' and category == 'ui':
                    # UI-specific Pro endpoint: panels, buttons and dialogue frames.
                    data = {'description': prompt, 'image_size': {'width': width or size, 'height': height or size}, 'no_background': transparent}
                    result = pixel_request('POST', '/generate-ui-v2', data, timeout=180)
                elif model == 'pro':
                    data = {'description': prompt, 'image_size': {'width': width or size, 'height': height or size}, 'no_background': transparent}
                    result = pixel_request('POST', '/generate-image-v2', data, timeout=180)
                else:
                    result = pixel_request('POST', '/create-image-pixen', {'description': prompt, 'image_size': {'width': width or size, 'height': height or size}, 'no_background': transparent}, timeout=180)
            elif mode == 'redraw':
                assert reference is not None
                img, dimensions = sized_image(reference, 1024)
                guidance = {
                    'image': img,
                    'size': {'width': dimensions[0], 'height': dimensions[1]},
                    'usage_description': 'Use the subject identity, hair, outfit, distinctive colors and accessories as design guidance. Redraw intentionally at the target pixel resolution; do not simply shrink or convert the source pixels.'
                }
                result = pixel_request('POST', '/generate-image-v2', {
                    'description': prompt,
                    'image_size': {'width': width or size, 'height': height or size},
                    'reference_images': [guidance],
                    'no_background': transparent
                }, timeout=200)
            elif mode == 'convert':
                assert reference is not None
                if model == 'pro':
                    img, _ = sized_image(reference, 2048)
                    data = {'image': img}
                    if prompt.strip(): data['description'] = prompt.strip()
                    result = pixel_request('POST', '/image-to-pixelart-pro', data, timeout=200)
                else:
                    img, dimensions = sized_image(reference, 2048)
                    w, h = dimensions
                    factor = size / max(w, h)
                    out_w = max(16, min(512, round(w*factor)))
                    out_h = max(16, min(512, round(h*factor)))
                    data = {'image':img, 'image_size': {'width':w,'height':h},'output_size':{'width':out_w,'height':out_h}}
                    if fidelity == 'faithful':
                        data.update({'fixer': True, 'init_image_strength': 230})
                    elif fidelity == 'creative':
                        data.update({'fixer': False, 'init_image_strength': 0})
                    result = pixel_request('POST','/image-to-pixelart',data,timeout=200)
            elif mode == 'animate':
                assert reference is not None
                img, dims = sized_image(reference,size)
                if dims[0]*dims[1]*frames > 524288:
                    raise ValueError('画像サイズとフレーム数の合計が上限を超えています。')
                result = pixel_request('POST','/animate-with-text-v3',{'first_frame':img,'action':prompt,'frame_count':frames,'no_background':transparent},timeout=200)
            else: raise ValueError('未対応の機能です。')
            if result.get('background_job_id') and not (vfx_file_urls(result) if mode == 'vfx' else find_images(result)):
                external_id = str(result['background_job_id'])
                update_job(job_id, provider_job_id=external_id)
                for _ in range(80):
                    time.sleep(5)
                    result = pixel_request('GET', f'/background-jobs/{external_id}', timeout=45)
                    if result.get('status') in ('failed','cancelled','canceled'):
                        raise RuntimeError('PixelLab生成失敗: '+str(result.get('error') or result.get('status'))[:350])
                    if result.get('status')=='completed': break
                else:
                    raise TimeoutError('ジョブ完了を確認できませんでした。二重課金を防ぐため再送しません。')
            if mode=='vfx':
                blobs=[]
                vfx_files=save_vfx_files(job_id,result)
            else:
                blobs=find_images(result)
                if not blobs:
                    raise RuntimeError('PixelLabから画像データを取得できませんでした。自動再送しません。')
            try: after = balance_data()
            except Exception as e: log.warning('Postflight balance not available: %s',e)
        files = vfx_files if (mode=='vfx' and not demo) else make_exports(job_id, blobs, mode in ('animate','vfx'))
        if reference:
            directory = OUTPUT/job_id
            img, _ = image_payload(reference)
            (directory/'reference.png').write_bytes(base64.b64decode(img['base64']))
            files.append('reference.png')
        usd, gens = extract_usage(result)
        if demo:
            usd, gens = 0.0, 0.0
        elif provider == 'pixellab':
            before_usd, after_usd = credits_usd(before), credits_usd(after)
            before_gen, after_gen = generations_left(before), generations_left(after)
            if usd is None and before_usd is not None and after_usd is not None:
                usd = round(max(0.,before_usd-after_usd),6)
            if gens is None and before_gen is not None and after_gen is not None:
                gens = round(max(0.,before_gen-after_gen),3)
        else:
            # Images API may not provide token usage (especially recent models); don't invent a USD bill.
            usd, gens = None, None
        extra = {}
        if mode == 'vfx' and not demo:
            meta=result.get('last_response',result)
            if isinstance(meta,dict):
                extra={'effect_id':meta.get('effect_id'),'effect_edit_url':meta.get('edit_url'),'vfx_fps':meta.get('fps'),'vfx_frames':meta.get('frames')}
        update_job(job_id,status='completed',completed_at=now(),files=files,usd_charged=usd,generations_used=gens,**extra,
                   usage_status='confirmed' if demo or usd is not None or gens is not None else 'unknown',file_count=len(blobs) if mode!='vfx' else (12 if demo else (extra.get('vfx_frames') or 0)))
        with _lock:
            completed_job = next((j.copy() for j in _state['jobs'] if j['id'] == job_id), None)
        if completed_job:
            ensure_generated_asset(completed_job)
    except Exception as exc:
        log.exception('Job failed: %s', job_id)
        # Before provider dispatch: a definite local failure; after dispatch: billing may have occurred.
        http_rejected = any(f'HTTP {n}' in str(exc) for n in (400,401,402,403,404,422,429))
        update_job(job_id,status='unknown' if dispatched and not demo and not http_rejected else 'failed',completed_at=now(),error=str(exc)[:700])


class TokenRequest(BaseModel):
    token: str = Field(min_length=8, max_length=1024)
    provider: str = 'pixellab'
    remember: bool = True


class SettingsRequest(BaseModel):
    budget_usd: float = Field(ge=0.1, le=10000)


class RatingRequest(BaseModel):
    rating: str


class EstimateRequest(BaseModel):
    mode: str
    size: int
    frames: int = 8
    provider: str = 'pixellab'
    model: str = 'standard'
    quality: str = 'low'
    gpt_size: str = '1024x1024'
    reservation_usd: float = 0.50
    width: int | None = None
    height: int | None = None
    category: str = "other"

class SequenceCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    fps: int = Field(default=12, ge=1, le=60)
    width: int = Field(default=240, ge=16, le=4096)
    height: int = Field(default=160, ge=16, le=4096)


class SequenceUpdateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    fps: int = Field(default=12, ge=1, le=60)
    width: int = Field(default=240, ge=16, le=4096)
    height: int = Field(default=160, ge=16, le=4096)


class ClipCreateRequest(BaseModel):
    asset_id: str = Field(min_length=1, max_length=120)
    track: str = Field(default='character', min_length=1, max_length=40)
    start: int = Field(default=0, ge=0, le=999999)
    duration: int = Field(default=6, ge=1, le=999999)
    volume: float = Field(default=1.0, ge=0.0, le=2.0)
    x: int = Field(default=0, ge=-9999, le=9999)
    y: int = Field(default=0, ge=-9999, le=9999)
    end_x: int | None = Field(default=None, ge=-9999, le=9999)
    end_y: int | None = Field(default=None, ge=-9999, le=9999)
    easing: str = Field(default='linear', pattern='^(linear|ease_in_out|ease_out)$')
    flip_x: bool = False
    label: str = Field(default='', max_length=120)


class ClipUpdateRequest(BaseModel):
    start: int = Field(ge=0, le=999999)
    duration: int = Field(ge=1, le=999999)
    volume: float = Field(ge=0.0, le=2.0)
    x: int = Field(ge=-9999, le=9999)
    y: int = Field(ge=-9999, le=9999)
    end_x: int | None = Field(default=None, ge=-9999, le=9999)
    end_y: int | None = Field(default=None, ge=-9999, le=9999)
    easing: str = Field(default='linear', pattern='^(linear|ease_in_out|ease_out)$')
    flip_x: bool = False
    label: str = Field(default='', max_length=120)
    track: str = 'character'


class AttackComboRequest(BaseModel):
    """Three consecutive motions plus optional impact overlays."""
    run_asset_id: str = Field(min_length=1, max_length=120)
    attack_asset_id: str = Field(min_length=1, max_length=120)
    effect_asset_id: str | None = None
    sound_asset_id: str | None = None
    reaction_asset_id: str | None = None
    start: int = Field(default=0, ge=0, le=999999)
    approach_frames: int = Field(default=8, ge=2, le=120)
    attack_frames: int = Field(default=8, ge=1, le=120)
    return_frames: int = Field(default=8, ge=2, le=120)
    hit_offset: int = Field(default=4, ge=0, le=119)
    origin_x: int = Field(default=-65, ge=-9999, le=9999)
    origin_y: int = Field(default=0, ge=-9999, le=9999)
    approach_x: int = Field(default=30, ge=-9999, le=9999)
    approach_y: int = Field(default=0, ge=-9999, le=9999)
    enemy_x: int = Field(default=68, ge=-9999, le=9999)
    enemy_y: int = Field(default=0, ge=-9999, le=9999)
    easing: str = Field(default='ease_in_out', pattern='^(linear|ease_in_out|ease_out)$')
    return_flip_x: bool = True


@app.middleware('http')
async def local_origin_guard(request: Request, call_next):
    if request.url.path.startswith('/api/') and request.method in ('POST', 'PUT', 'DELETE', 'PATCH'):
        origin = request.headers.get('origin', '')
        if origin and origin not in (f'http://{request.headers.get("host", "")}', f'https://{request.headers.get("host", "")}'):
            from fastapi.responses import JSONResponse
            return JSONResponse({'detail': '外部サイトからの操作は許可されていません。'}, status_code=403)
    return await call_next(request)


@app.get('/')
def homepage():
    return FileResponse(STATIC / 'index.html')


@app.get('/api/ollama/models')
def ollama_models():
    try:
        names = installed_ollama_models()
        return {'connected': True, 'models': names, 'message': 'ローカルのOllamaに接続しました。'}
    except Exception as exc:
        return {'connected': False, 'models': [], 'message': 'Ollamaに接続できません。起動状態を確認してください。', 'detail': str(exc)[:180]}


@app.post('/api/ollama/assist')
async def assist_prompt(model: str = Form(...), task: str = Form('refine'),
                        prompt: str = Form(''), mode: str = Form('convert'),
                        fidelity: str = Form('faithful'), image: UploadFile | None = File(None)):
    if len(model) > 150 or len(prompt) > 2000 or mode not in ('text','convert','animate','vfx') or fidelity not in ('faithful','balanced','creative'):
        raise HTTPException(422, '設定またはプロンプトが不正です。')
    if task not in ('refine','describe'):
        raise HTTPException(422, '未対応の補助処理です。')
    blob = None
    if image is not None:
        blob = await image.read(12*1024*1024+1)
        try:
            image_payload(blob)
        except ValueError as exc:
            raise HTTPException(422,str(exc))
    try:
        result = ollama_chat(model, task, prompt, mode, fidelity, blob)
    except (ValueError,RuntimeError,httpx.HTTPError) as exc:
        raise HTTPException(502, str(exc)[:550])
    return {'text': result, 'task': task, 'used_image': blob is not None, 'provider': 'local-ollama', 'paid_api_calls': 0}


@app.post('/api/ollama/compare')
def compare_generated_images(data: dict[str,str]):
    job_id = data.get('job_id', '')
    model = data.get('model', '')
    if len(job_id) > 100 or len(model) > 150:
        raise HTTPException(422, '指定値が不正です。')
    with _lock:
        job = next((j.copy() for j in _state['jobs'] if j['id'] == job_id), None)
    if not job or job.get('status') != 'completed' or 'reference.png' not in job.get('files', []):
        raise HTTPException(422, '元画像付きの完了した履歴を選択してください。')
    output_file = next((f for f in job['files'] if f.startswith('image_') and f.endswith('.png')), None)
    if not output_file:
        output_file = next((f for f in job['files'] if f.startswith('frame_') and f.endswith('.png')), None)
    if not output_file:
        raise HTTPException(422, '比較する生成画像がありません。')
    try:
        available = installed_ollama_models()
        if model not in available:
            raise ValueError('選択したOllamaモデルが見つかりません。')
        a, _ = sized_image((OUTPUT/job_id/'reference.png').read_bytes(), 1024)
        b, _ = sized_image((OUTPUT/job_id/output_file).read_bytes(), 1024)
        payload = {
          'model':model, 'stream':False, 'options':{'temperature':0.1,'num_predict':900},
          'messages':[{'role':'system','content':'You are an expert game-sprite art reviewer. Never invent visual facts. Compare concrete features. Respond in concise Japanese.'},
                      {'role':'user','content':('画像1は元画像、画像2はドット絵の生成結果です。顔つき、年齢印象、頭身、髪、ポーズ、衣装、小物、配色を実際に比較して、'
                       '一致した点と異なる点を分けて指摘してください。特にキャラクター同一性を損なう違いを優先し、最後に次回生成で試す英語の追加指示を1つだけ提案してください。'
                       '不明な点は不明と記載。画像を生成したと主張しないでください。'),
                       'images':[a['base64'],b['base64']]}]
        }
        with httpx.Client(timeout=httpx.Timeout(150,connect=4),trust_env=False) as client:
            resp = client.post(OLLAMA_URL+'/api/chat',json=payload)
            if resp.is_error:
                try: reason=resp.json().get('error','不明なエラー')
                except ValueError: reason='不明なエラー'
                raise RuntimeError(f'Ollama HTTP {resp.status_code}: {str(reason)[:350]}（複数画像のVision対応モデルが必要です）')
            answer=resp.json().get('message',{}).get('content','')
        if not isinstance(answer,str) or not answer.strip():
            raise ValueError('Ollamaから有効な比較結果を取得できませんでした。')
        return {'text':answer.strip()[:4000], 'provider':'local-ollama', 'paid_api_calls':0}
    except (ValueError, RuntimeError, httpx.HTTPError, OSError) as exc:
        raise HTTPException(502, str(exc)[:550])


@app.post('/api/import-chatgpt')
async def import_chatgpt(result_image: UploadFile = File(...), prompt: str = Form(''),
                         mode: str = Form('text'), reference: UploadFile | None = File(None), category: str = Form('other'), asset_name: str = Form('')):
    """Import a manually saved ChatGPT web/app image. No ChatGPT network requests."""
    if mode not in ('text','convert') or len(prompt) > 2000:
        raise HTTPException(422, '取り込み条件が不正です。')
    blob = await result_image.read(12*1024*1024+1)
    try:
        image_payload(blob)
    except ValueError as exc:
        raise HTTPException(422, str(exc))
    ref_blob = None
    if reference is not None:
        ref_blob = await reference.read(12*1024*1024+1)
        try:
            image_payload(ref_blob)
        except ValueError as exc:
            raise HTTPException(422, str(exc))
    job_id = str(uuid.uuid4())
    files = make_exports(job_id, [blob], False)
    if ref_blob:
        image_data, _ = image_payload(ref_blob)
        (OUTPUT/job_id/'reference.png').write_bytes(base64.b64decode(image_data['base64']))
        files.append('reference.png')
    category = normalize_category(category, mode)
    item = {'id':job_id, 'created_at':now(), 'completed_at':now(), 'month':month(),
            'status':'completed','provider':'chatgpt','model':'browser','fidelity':'manual',
            'mode':mode,'prompt':prompt,'size':None,'frames':None,'transparent':False,
            'demo':False,'estimate_usd':None,'reservation_usd':0,'usd_charged':None,
            'generations_used':None,'usage_status':'external','files':files,'file_count':1,
            'external_no_charge':True, 'category': category, 'asset_name': asset_name[:120]}
    with _lock:
        _state['jobs'].append(item)
        save_state()
    ensure_generated_asset(item)
    return {'id':job_id, 'status':'completed','message':'ブラウザで保存した画像をローカルの履歴に追加しました。'}


@app.get('/api/status')
def status():
    return {
        'has_key': bool(_token), 'has_openai_key': bool(_openai_token),
        # Only boolean configuration metadata is returned; never return a secret.
        'saved_keys': {p: bool(_saved_tokens.get(p)) for p in ('pixellab', 'openai')},
        'env_keys': {'pixellab': bool(_pixellab_env), 'openai': bool(_openai_env)},
        'credential_store_supported': credential_storage_supported(),
        'credential_store_warning': _credential_store_warning,
        'stats': local_stats(), 'jobs': list(reversed(_state['jobs'][-60:]))
    }


@app.post('/api/session')
def set_token(data: TokenRequest):
    global _token, _openai_token, _credential_store_warning
    provider = data.provider
    if provider not in ('pixellab', 'openai'):
        raise HTTPException(422, '未対応のAPIです。')
    token = data.token.strip()
    if len(token) < 8:
        raise HTTPException(422, 'APIキーを入力してください。')
    if data.remember and not credential_storage_supported():
        raise HTTPException(422, 'この環境では暗号化保存を利用できません。保存なしで接続してください。')
    with _lock:
        new_saved = dict(_saved_tokens)
        if data.remember:
            new_saved[provider] = token
        else:
            new_saved.pop(provider, None)
        if credential_storage_supported():
            try:
                write_credentials(new_saved)
            except (OSError, RuntimeError) as exc:
                log.warning('Credential persistence failed: %s', type(exc).__name__)
                raise HTTPException(500, 'APIキーの暗号化保存に失敗しました。Windowsの権限をご確認ください。保存なしでの接続も可能です。') from None
        _saved_tokens.clear()
        _saved_tokens.update(new_saved)
        _credential_store_warning = False
        if provider == 'pixellab':
            _token = token
        else:
            _openai_token = token
    return {
        'has_key': True, 'saved': bool(data.remember),
        'message': 'このWindowsユーザー用に暗号化保存しました。次回起動時にも利用できます。' if data.remember else '今回の起動中だけAPIキーを保持します。'
    }


@app.delete('/api/session')
def delete_token(provider: str='pixellab'):
    global _token, _openai_token, _credential_store_warning
    if provider not in ('pixellab', 'openai'):
        raise HTTPException(422, '未対応のAPIです。')
    with _lock:
        new_saved = dict(_saved_tokens)
        new_saved.pop(provider, None)
        if credential_storage_supported():
            try:
                write_credentials(new_saved)
            except (OSError, RuntimeError):
                raise HTTPException(500, '保存済みAPIキーの削除に失敗しました。') from None
        _saved_tokens.clear()
        _saved_tokens.update(new_saved)
        _credential_store_warning = False
        if provider == 'pixellab':
            _token = ''
        else:
            _openai_token = ''
    return {
        'has_key': False, 'saved': False,
        'message': '接続と暗号化済みの保存キーを削除しました。環境変数に設定したキーは別途削除してください。'
    }


@app.get('/api/balance')
def get_balance():
    try:
        return balance_data()
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e)[:500])


@app.post('/api/settings')
def set_settings(data: SettingsRequest):
    with _lock:
        _state['budget_usd'] = round(data.budget_usd, 2)
        save_state()
    return {'budget_usd': _state['budget_usd']}


def validate_options(mode: str, size: int, frames: int, provider: str, model: str, quality: str, gpt_size: str, width: int | None = None, height: int | None = None) -> None:
    if mode not in ('text','redraw','convert','animate','vfx') or size not in PIXEL_SIZES or frames not in (4,6,8,10,12,14,16):
        raise ValueError('生成条件を確認してください。')
    if provider not in ('pixellab','openai'):
        raise ValueError('未対応のプロバイダーです。')
    if width is not None or height is not None:
        if mode not in ('text','redraw') or provider != 'pixellab' or width is None or height is None:
            raise ValueError('幅・高さの個別指定はPixelLab新規生成・参考画像から描き直す場合に使えます。')
        if not (16 <= width <= 512 and 16 <= height <= 512):
            raise ValueError('幅・高さはそれぞれ16〜512pxで指定してください。')
    if mode == 'redraw' and (provider!='pixellab' or model!='pro'):
        raise ValueError('参照画像から描き直す機能はPixelLab Pro専用です。')
    if provider == 'pixellab':
        if model not in ('standard','pro'):
            raise ValueError('PixelLabモデルの指定が不正です。')
        if mode in ('animate','vfx') and size>256:
            raise ValueError('アニメーションは最大256pxです（VFXには出力サイズ指定がありません）。')
        if mode == 'convert' and model == 'standard' and size>512:
            raise ValueError('標準変換は最大512pxです。')
    else:
        if model not in OPENAI_MODELS or gpt_size not in GPT_SIZES or quality not in ('low','medium','high'):
            raise ValueError('GPT画像APIの設定が不正です。')
        if mode in ('animate','vfx'):
            raise ValueError('GPT画像APIのアニメーション/VFXは未対応です。')


@app.post('/api/estimate')
def estimate(data: EstimateRequest):
    try: validate_options(data.mode,data.size,data.frames,data.provider,data.model,data.quality,data.gpt_size,data.width,data.height)
    except ValueError as e: raise HTTPException(422,str(e))
    price=None if data.mode=='redraw' or (data.width is not None and (data.width != data.height or data.width not in PIXEL_SIZES)) or (data.provider=='pixellab' and data.mode=='text' and data.model=='pro' and data.category=='ui') else estimate_usd(data.mode,data.width or data.size,data.frames,data.provider,data.model,data.quality,data.gpt_size)
    if not .01 <= data.reservation_usd <= 500:
        raise HTTPException(422,'予約額は0.01〜500ドルで指定してください。')
    reserve=round(price*1.5,6) if price is not None else round(data.reservation_usd,2)
    vfx_gens=(1 if data.model=='standard' else 6) if data.mode=='vfx' else None
    return {'estimate_usd':price,'budget_reservation_usd':reserve, 'pricing_known':price is not None,'quoted_generations_base':vfx_gens,
            'disclaimer':'事前料金は参考値。Pro/GPT画像2.5は変動し正確な事前額を保証できません。GPT Image 2の表示は画像出力分のみで入力トークンは別料金です。予約額は請求上限ではありません。'}


@app.post('/api/jobs')
async def create_job(mode: str=Form(...), prompt: str=Form(''), size: int=Form(128), transparent: bool=Form(True), frames: int=Form(8), width: int | None=Form(None), height: int | None=Form(None),
                     demo: bool=Form(False), image: UploadFile | None=File(None), provider: str=Form('pixellab'), model: str=Form('standard'),
                     fidelity: str=Form('balanced'), quality: str=Form('low'), gpt_size: str=Form('1024x1024'), reservation_usd: float=Form(.50), category: str=Form('other'), asset_name: str=Form('')):
    try: validate_options(mode,size,frames,provider,model,quality,gpt_size,width,height)
    except ValueError as e: raise HTTPException(422,str(e))
    if fidelity not in ('balanced','faithful','creative'):
        raise HTTPException(422,'忠実度の指定が不正です。')
    if not .01 <= reservation_usd <= 500:
        raise HTTPException(422,'予約額は0.01〜500ドルにしてください。')
    if mode == 'text' and max(width or size,height or size)>256 and transparent and provider=='pixellab' and model=='standard':
        raise HTTPException(422,'Pixenの512px生成では透過背景を指定できません。')
    if mode in ('text','redraw','animate','vfx') and not prompt.strip():
        raise HTTPException(422,'文章を入力してください。')
    if len(prompt)>2000: raise HTTPException(422,'文章は2000字以下にしてください。')
    blob=None
    if mode in ('redraw','convert','animate','vfx') and (mode!='vfx' or image is not None):
        if image is None: raise HTTPException(422,'参照画像を選んでください。')
        blob=await image.read(12*1024*1024+1)
        try:
            image_payload(blob)
            if mode == 'vfx':
                dims=Image.open(io.BytesIO(blob)).size
                if max(dims)>512:raise HTTPException(422,'VFX画像は最大512pxです。')
            if mode == 'animate':
                _, dims=sized_image(blob,size)
                if dims[0]*dims[1]*frames>524288:
                    raise HTTPException(422,'入力サイズとフレーム数の組み合わせが上限を超えています。')
        except ValueError as e: raise HTTPException(422,str(e))
    category = normalize_category(category, mode)
    if category == 'sound':
        raise HTTPException(422,'音声は画像生成カテゴリーに指定できません。音声はアセット管理で取り込んでください。')
    if mode == 'vfx' and category != 'effect':
        raise HTTPException(422,'VFXはエフェクトカテゴリーへ保存してください。')
    if not demo and not (_token if provider=='pixellab' else _openai_token):
        raise HTTPException(401,'選択したサービスのAPIキーを登録するか、デモモードを選んでください。')
    price=None if mode=='redraw' or (width is not None and (width != height or width not in PIXEL_SIZES)) or (provider=='pixellab' and mode=='text' and model=='pro' and category=='ui') else estimate_usd(mode,width or size,frames,provider,model,quality,gpt_size)
    reserve=round(price*1.5,6) if price is not None else round(reservation_usd,2)
    with _lock:
        stats=local_stats()
        active=[j for j in _state['jobs'] if j.get('status') in ('queued','processing') and not j.get('demo')]
        if active and not demo:
            raise HTTPException(409,'別の実API生成が処理中です。完了後にもう一度お試しください。')
        if not demo and stats['budget_reserved']+reserve>_state['budget_usd']+1e-9:
            raise HTTPException(409,'月間予算ガードを超えます。予算または1回の予約額を変更してください。')
        job_id=str(uuid.uuid4())
        category = normalize_category(category, mode)
        job={'id':job_id,'created_at':now(),'month':month(),'status':'queued','provider':provider,'model':model,'fidelity':fidelity,
             'quality':quality,'gpt_size':gpt_size,'mode':mode,'prompt':prompt[:2000], 'size':size, 'width':width if mode in ('text','redraw') else None, 'height':height if mode in ('text','redraw') else None, 'frames':frames if mode=='animate' else None,
             'quoted_generations':vfx_quote_generations('pro' if model=='pro' else 'lite',blob) if mode=='vfx' else None,
             'transparent':transparent,'demo':demo,'estimate_usd':price if not demo else 0,'reservation_usd':reserve if not demo else 0,
             'usd_charged':None,'generations_used':None,'usage_status':'unknown','files':[], 'category':category, 'asset_name':asset_name[:120]}
        _state['jobs'].append(job); save_state()
    _executor.submit(execute_job,job_id,mode,prompt.strip(),size,transparent,frames,blob,demo,provider,model,fidelity,quality,gpt_size,width,height,category)
    return {'id':job_id,'status':'queued'}

@app.get('/api/jobs/{job_id}')
def job_detail(job_id: str):
    with _lock:
        job = next((j for j in _state['jobs'] if j['id'] == job_id), None)
        if not job:
            raise HTTPException(404, 'ジョブが見つかりません。')
        return job.copy()


@app.post('/api/jobs/{job_id}/rating')
def rate_job(job_id: str, data: RatingRequest):
    if data.rating not in ('good', 'bad', 'none'):
        raise HTTPException(422, '評価はgoodまたはbadにしてください。')
    with _lock:
        job = next((j for j in _state['jobs'] if j['id'] == job_id), None)
        if job is None or job.get('status') != 'completed':
            raise HTTPException(404, '完了した生成履歴がありません。')
        job['rating'] = data.rating
        save_state()
    return {'rating': data.rating}


@app.get('/api/jobs/{job_id}/files/{name}')
def job_file(job_id: str, name: str):
    with _lock:
        job = next((j for j in _state['jobs'] if j['id'] == job_id), None)
        if not job or name not in job.get('files', []) or '/' in name or '\\' in name:
            raise HTTPException(404, 'ファイルが見つかりません。')
        path = OUTPUT / job_id / name
        if not path.exists():
            raise HTTPException(404, 'ファイルが見つかりません。')
        return FileResponse(path)  # Inline preview; the browser UI sets download= for saves.


@app.get('/api/assets')
def list_assets(category: str | None = None):
    with _lock:
        assets = [asset_summary(a) for a in reversed(_state.get('assets', []))]
    if category and category in ASSET_CATEGORIES:
        assets = [a for a in assets if a.get('category') == category]
    return {'assets': assets, 'categories': ASSET_LABELS}


@app.post('/api/assets/import')
async def import_asset(file: UploadFile = File(...), category: str = Form('other'), name: str = Form(''), track: str = Form(''), columns: int = Form(1), rows: int = Form(1)):
    category = normalize_category(category)
    blob = await file.read(40*1024*1024 + 1)
    if len(blob) > 40*1024*1024:
        raise HTTPException(422, 'ファイルは40MB以下にしてください。')
    filename = file.filename or 'asset.bin'
    ext = Path(filename).suffix.lower()
    if category != 'sound':
        if ext in ('.png','.jpg','.jpeg','.webp','.gif'):
            try:
                if ext == '.gif':
                    Image.open(io.BytesIO(blob)).verify()
                else:
                    image_payload(blob)
            except Exception:
                raise HTTPException(422, '画像ファイルを確認できません。')
        elif ext not in ('.json',):
            raise HTTPException(422, '対応する画像/GIF/JSONのみ取り込めます。')
    else:
        if ext not in ('.mp3','.wav','.ogg','.m4a'):
            raise HTTPException(422, '音声は mp3 / wav / ogg / m4a を取り込めます。')
    if columns < 1 or rows < 1 or columns * rows > 64:
        raise HTTPException(422, 'スプライトシートの分割数は1〜64です。')
    if columns * rows > 1 and ext not in ('.png', '.jpg', '.jpeg', '.webp'):
        raise HTTPException(422, 'スプライトシート分割はPNG・JPEG・WebPに対応しています。')
    asset_id = str(uuid.uuid4())
    label = unique_asset_name(name or Path(filename).stem, category)
    target = asset_dir(category, asset_id)
    save_name = ('asset' + ext) if ext else filename
    (target / save_name).write_bytes(blob)
    stored_files = [save_name]
    if columns * rows > 1:
        with Image.open(io.BytesIO(blob)) as sheet:
            if sheet.width % columns or sheet.height % rows:
                raise HTTPException(422, 'シートの幅・高さを分割数で割り切れません。')
            sw, sh = sheet.width // columns, sheet.height // rows
            for i in range(columns * rows):
                frame_name = f'frame_{i:02d}.png'
                crop = sheet.crop(((i % columns)*sw, (i // columns)*sh, (i % columns+1)*sw, (i // columns+1)*sh))
                crop.save(target / frame_name)
                stored_files.append(frame_name)
    asset = {'id': asset_id, 'name': label, 'category': category, 'kind': guess_asset_kind(save_name), 'created_at': now(), 'updated_at': now(), 'source': 'imported', 'track_hint': track or category, 'files': stored_files, 'primary_file': save_name, 'sheet_columns': columns, 'sheet_rows': rows}
    with _lock:
        _state.setdefault('assets', []).append(asset)
        save_state()
    return asset_summary(asset)


@app.get('/api/assets/{asset_id}/files/{name}')
def asset_file(asset_id: str, name: str):
    with _lock:
        asset = next((a for a in _state.get('assets', []) if a.get('id') == asset_id), None)
    if not asset or name not in asset.get('files', []) or '/' in name or '\\' in name:
        raise HTTPException(404, 'ファイルが見つかりません。')
    path = asset_dir(asset.get('category', 'other'), asset_id) / name
    if not path.exists():
        raise HTTPException(404, 'ファイルが見つかりません。')
    return FileResponse(path)


@app.get('/api/sequences')
def list_sequences():
    with _lock:
        return {'sequences': [sequence_summary(s) for s in reversed(_state.get('sequences', []))]}


@app.post('/api/sequences')
def create_sequence(data: SequenceCreateRequest):
    seq = {'id': str(uuid.uuid4()), 'name': data.name.strip()[:120], 'fps': data.fps, 'width': data.width, 'height': data.height, 'created_at': now(), 'updated_at': now(), 'clips': []}
    with _lock:
        _state.setdefault('sequences', []).append(seq)
        save_state()
    return seq


@app.get('/api/sequences/{sequence_id}')
def get_sequence(sequence_id: str):
    with _lock:
        seq = next((s for s in _state.get('sequences', []) if s.get('id') == sequence_id), None)
    if not seq:
        raise HTTPException(404, 'シーケンスが見つかりません。')
    return seq


@app.put('/api/sequences/{sequence_id}')
def update_sequence(sequence_id: str, data: SequenceUpdateRequest):
    with _lock:
        seq = next((s for s in _state.get('sequences', []) if s.get('id') == sequence_id), None)
        if not seq:
            raise HTTPException(404, 'シーケンスが見つかりません。')
        seq.update({'name': data.name.strip()[:120], 'fps': data.fps, 'width': data.width, 'height': data.height, 'updated_at': now()})
        save_state()
        return seq


@app.delete('/api/sequences/{sequence_id}')
def delete_sequence(sequence_id: str):
    with _lock:
        before = len(_state.get('sequences', []))
        _state['sequences'] = [s for s in _state.get('sequences', []) if s.get('id') != sequence_id]
        if len(_state['sequences']) == before:
            raise HTTPException(404, 'シーケンスが見つかりません。')
        save_state()
    return {'deleted': True}


@app.post('/api/sequences/{sequence_id}/clips')
def add_clip(sequence_id: str, data: ClipCreateRequest):
    with _lock:
        seq = next((s for s in _state.get('sequences', []) if s.get('id') == sequence_id), None)
        asset = next((a for a in _state.get('assets', []) if a.get('id') == data.asset_id), None)
        if not seq:
            raise HTTPException(404, 'シーケンスが見つかりません。')
        if not asset:
            raise HTTPException(404, 'アセットが見つかりません。')
        if data.track not in ('background','item','character','effect','sound'):
            raise HTTPException(422, '未対応のレーンです。')
        if (data.track == 'sound') != (asset.get('kind') == 'audio'):
            raise HTTPException(422, '音声は効果音レーン、画像は画像レーンを選択してください。')
        clip = {'id': str(uuid.uuid4()), 'asset_id': data.asset_id, 'asset_name': asset.get('name'), 'asset_category': asset.get('category'), 'track': data.track, 'start': data.start, 'duration': data.duration, 'volume': data.volume, 'x': data.x, 'y': data.y, 'end_x': data.end_x, 'end_y': data.end_y, 'easing': data.easing, 'flip_x': data.flip_x, 'label': data.label[:120], 'created_at': now()}
        seq.setdefault('clips', []).append(clip)
        seq['clips'].sort(key=lambda c: (c.get('track',''), c.get('start',0), c.get('asset_name','')))
        seq['updated_at'] = now()
        save_state()
    return clip


@app.post('/api/sequences/{sequence_id}/attack-combo')
def create_attack_combo(sequence_id: str, data: AttackComboRequest):
    """Insert a synchronized approach/strike/return combo as one atomic edit."""
    if data.hit_offset >= data.attack_frames:
        raise HTTPException(422, '命中タイミングは攻撃モーションの長さより小さくしてください。')
    finish = data.start + data.approach_frames + data.attack_frames + data.return_frames
    if finish > 600:
        raise HTTPException(422, '攻撃シーケンスは600フレーム以内で構成してください。')
    attack_start = data.start + data.approach_frames
    retreat_start = attack_start + data.attack_frames
    hit_frame = attack_start + data.hit_offset
    with _lock:
        seq = next((s for s in _state.get('sequences', []) if s.get('id') == sequence_id), None)
        if seq is None:
            raise HTTPException(404, 'シーケンスが見つかりません。')
        asset_lookup = {a.get('id'): a for a in _state.get('assets', [])}
        fields = [
            (data.run_asset_id, '走行アニメ', 'image'),
            (data.attack_asset_id, '攻撃アニメ', 'image'),
            (data.effect_asset_id, '命中エフェクト', 'image'),
            (data.sound_asset_id, '命中効果音', 'audio'),
            (data.reaction_asset_id, '敵の被弾アニメ', 'image')
        ]
        for asset_id, label, expected in fields:
            if asset_id is None:
                continue
            asset = asset_lookup.get(asset_id)
            if not asset or (asset.get('kind') == 'audio') != (expected == 'audio'):
                raise HTTPException(422, f'{label}の素材を確認してください。')
        if len(seq.get('clips', [])) + 3 + sum(bool(x) for x in (
                data.effect_asset_id, data.sound_asset_id, data.reaction_asset_id)) > 100:
            raise HTTPException(422, 'シーケンスのクリップ数は100個以内にしてください。')

        def make_clip(asset_id: str, track: str, start: int, duration: int,
                      label: str, x: int = 0, y: int = 0,
                      end_x: int | None = None, end_y: int | None = None,
                      flip_x: bool = False, easing: str = 'linear') -> dict:
            asset = asset_lookup[asset_id]
            return {
                'id': str(uuid.uuid4()), 'asset_id': asset_id, 'asset_name': asset.get('name'),
                'asset_category': asset.get('category'), 'track': track, 'start': start,
                'duration': duration, 'volume': 1.0, 'x': x, 'y': y,
                'end_x': end_x, 'end_y': end_y, 'easing': easing,
                'flip_x': flip_x, 'label': label, 'created_at': now()
            }

        added = [
            make_clip(data.run_asset_id, 'character', data.start,
                      data.approach_frames, '① 敵へ接近（走行）',
                      data.origin_x, data.origin_y, data.approach_x, data.approach_y,
                      easing=data.easing),
            make_clip(data.attack_asset_id, 'character', attack_start,
                      data.attack_frames, '② 攻撃モーション',
                      data.approach_x, data.approach_y),
            make_clip(data.run_asset_id, 'character', retreat_start,
                      data.return_frames, '③ 元の位置へ帰還（走行）',
                      data.approach_x, data.approach_y, data.origin_x, data.origin_y,
                      flip_x=data.return_flip_x, easing=data.easing)
        ]
        effect_frames = min(6, finish - hit_frame)
        if data.effect_asset_id:
            added.append(make_clip(data.effect_asset_id, 'effect', hit_frame,
                                   effect_frames, '④ 命中エフェクト',
                                   data.enemy_x, data.enemy_y))
        if data.sound_asset_id:
            sound_frames = min(max(1, int(seq.get('fps', 12) // 2)), finish - hit_frame)
            added.append(make_clip(data.sound_asset_id, 'sound', hit_frame,
                                   sound_frames, '⑤ 命中効果音'))
        if data.reaction_asset_id:
            added.append(make_clip(data.reaction_asset_id, 'character', hit_frame,
                                   effect_frames, '⑥ 敵の被弾',
                                   data.enemy_x, data.enemy_y))
        seq.setdefault('clips', []).extend(added)
        seq['clips'].sort(key=lambda c: (c.get('track', ''), c.get('start', 0)))
        seq['updated_at'] = now()
        save_state()
    return {'created': len(added), 'clips': added, 'hit_frame': hit_frame, 'end_frame': finish}


@app.delete('/api/sequences/{sequence_id}/clips/{clip_id}')
def remove_clip(sequence_id: str, clip_id: str):
    with _lock:
        seq = next((s for s in _state.get('sequences', []) if s.get('id') == sequence_id), None)
        if not seq:
            raise HTTPException(404, 'シーケンスが見つかりません。')
        before = len(seq.get('clips', []))
        seq['clips'] = [c for c in seq.get('clips', []) if c.get('id') != clip_id]
        if len(seq['clips']) == before:
            raise HTTPException(404, 'クリップが見つかりません。')
        seq['updated_at'] = now()
        save_state()
    return {'deleted': True}


@app.put('/api/sequences/{sequence_id}/clips/{clip_id}')
def edit_clip(sequence_id: str, clip_id: str, data: ClipUpdateRequest):
    if data.track not in ('background','item','character','effect','sound'):
        raise HTTPException(422, 'レーンが不正です。')
    with _lock:
        seq = next((s for s in _state.get('sequences', []) if s['id'] == sequence_id), None)
        if seq is None:
            raise HTTPException(404, 'シーケンスが見つかりません。')
        clip = next((c for c in seq.get('clips', []) if c['id'] == clip_id), None)
        if clip is None:
            raise HTTPException(404, 'クリップが見つかりません。')
        asset = next((a for a in _state.get('assets', []) if a.get('id') == clip['asset_id']), None)
        if not asset:
            raise HTTPException(404, 'アセットが見つかりません。')
        if (data.track == 'sound') != (asset.get('kind') == 'audio'):
            raise HTTPException(422, '音声と画像のレーンを確認してください。')
        clip.update(data.model_dump())
        seq['clips'].sort(key=lambda c: (c.get('track', ''), c.get('start', 0)))
        seq['updated_at'] = now()
        save_state()
        return clip.copy()


def lookup_sequence(sequence_id: str) -> tuple[dict, dict]:
    with _lock:
        seq = next((s.copy() for s in _state.get('sequences', []) if s.get('id') == sequence_id), None)
        assets = {a['id']: a.copy() for a in _state.get('assets', [])}
    if not seq:
        raise HTTPException(404, 'シーケンスが見つかりません。')
    return seq, assets


@app.get('/api/sequences/{sequence_id}/preview/{frame}')
def preview_sequence_frame(sequence_id: str, frame: int):
    seq, assets = lookup_sequence(sequence_id)
    try:
        data = render_png(seq, assets, ASSETS, frame)
    except ValueError as exc:
        raise HTTPException(422, str(exc))
    return Response(content=data, media_type='image/png', headers={'Cache-Control': 'no-store'})


@app.get('/api/sequences/{sequence_id}/download/{kind}')
def download_sequence(sequence_id: str, kind: str):
    if kind not in ('json', 'zip', 'gif', 'mp4'):
        raise HTTPException(404, '未対応の書き出し形式です。')
    seq, assets = lookup_sequence(sequence_id)
    directory = EXPORTS / sequence_id
    directory.mkdir(parents=True, exist_ok=True)
    filename = f'battle_{uuid.uuid4().hex[:12]}.{kind}'
    dest = directory / filename
    try:
        if kind == 'json':
            dest.write_text(json.dumps(project_manifest(seq, assets), ensure_ascii=False, indent=2), encoding='utf-8')
        elif kind == 'zip':
            package_project(seq, assets, ASSETS, dest)
        else:
            save_media(seq, assets, ASSETS, dest, kind)
    except (ValueError, OSError, RuntimeError) as exc:
        dest.unlink(missing_ok=True)
        raise HTTPException(422, str(exc)[:500])
    media = {'json':'application/json', 'zip':'application/zip', 'gif':'image/gif', 'mp4':'video/mp4'}[kind]
    return FileResponse(dest, media_type=media, filename=f"{seq['name'][:60]}.{kind}")


@app.get('/api/sequences/{sequence_id}/export')
def export_sequence(sequence_id: str):
    with _lock:
        seq = next((s for s in _state.get('sequences', []) if s.get('id') == sequence_id), None)
    if not seq:
        raise HTTPException(404, 'シーケンスが見つかりません。')
    payload = {'sequence': seq, 'assets': []}
    with _lock:
        asset_lookup = {a.get('id'): a for a in _state.get('assets', [])}
    for clip in seq.get('clips', []):
        asset = asset_lookup.get(clip.get('asset_id'))
        if asset:
            payload['assets'].append(asset)
    return payload


if __name__ == '__main__':
    import socket
    import uvicorn
    port = 8765
    for candidate in range(8765, 8775):
        with socket.socket() as s:
            try:
                s.bind(('127.0.0.1', candidate))
                port = candidate
                break
            except OSError:
                continue
    print(f'\nPixelLab + GPT Workbench: http://127.0.0.1:{port}')
    print('終了するにはこのウィンドウで Ctrl+C\n')
    threading.Timer(1.0, lambda: webbrowser.open(f'http://127.0.0.1:{port}')).start()
    uvicorn.run(app, host='127.0.0.1', port=port, log_level='warning')
