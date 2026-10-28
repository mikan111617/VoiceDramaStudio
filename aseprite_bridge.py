"""Aseprite (Steam edition) bridge: native sprite/layer/frame creation via CLI + Lua.

The server only accepts bounded drawing instructions; it never runs user Lua.
All state remains in VDS_WORKSPACE/aseprite_studio, outside Git.
"""
from __future__ import annotations

import io
import json
import os
import re
import shutil
import subprocess
import tempfile
import threading
import uuid
from pathlib import Path
from urllib import request as urlrequest

from fastapi import APIRouter, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from PIL import Image, ImageDraw, UnidentifiedImageError
from pydantic import BaseModel, Field

ROOT = Path(__file__).resolve().parent
WORKSPACE = Path(os.environ.get("VDS_WORKSPACE", ROOT / "workspace")).resolve()
DATA = WORKSPACE / "aseprite_studio"
LOCK = threading.RLock()
router = APIRouter(tags=["Aseprite"])
SPRITE_ID = re.compile(r"^[0-9a-f]{32}$")
COLOR = re.compile(r"^#[0-9a-fA-F]{6}(?:[0-9a-fA-F]{2})?$")
MAX_UPLOAD = 16 * 1024 * 1024


class AsepriteConfig(BaseModel):
    executable: str = Field(min_length=1, max_length=2048)


class AiPrompt(BaseModel):
    prompt: str = Field(min_length=4, max_length=1000)
    model: str = Field(default="gemma3:4b", min_length=1, max_length=80)
    width: int = Field(default=48, ge=16, le=256)
    height: int = Field(default=48, ge=16, le=256)
    frames: int = Field(default=1, ge=1, le=12)


def _config_path():
    return DATA / "config.json"


def _read_config():
    try:
        value = json.loads(_config_path().read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (FileNotFoundError, ValueError):
        return {}


def discover_executable():
    candidates = [os.environ.get("ASEPRITE_EXE"), _read_config().get("executable"),
                  shutil.which("aseprite"), shutil.which("aseprite.exe")]
    for env in ("PROGRAMFILES(X86)", "PROGRAMFILES"):
        if os.environ.get(env):
            candidates.append(str(Path(os.environ[env]) / "Steam/steamapps/common/Aseprite/Aseprite.exe"))
    for value in candidates:
        if value and Path(value).is_file() and Path(value).name.lower() in ("aseprite", "aseprite.exe"):
            return str(Path(value).resolve())
    return None


def configure(executable):
    path = Path(executable).expanduser().resolve()
    if path.name.lower() not in ("aseprite", "aseprite.exe") or not path.is_file():
        raise ValueError("Aseprite.exeの実行ファイルを指定してください")
    with LOCK:
        DATA.mkdir(parents=True, exist_ok=True)
        _config_path().write_text(json.dumps({"executable": str(path)}, ensure_ascii=False), encoding="utf-8")
    return {"executable": str(path)}


def status():
    executable = discover_executable()
    return {"available": bool(executable), "executable": executable,
            "workspace": str(DATA), "mcp_command": "python aseprite_mcp_server.py",
            "ollama_optional": True}


def _cli(args, timeout=75):
    executable = discover_executable()
    if not executable:
        raise RuntimeError("Steam版Aseprite.exeの場所を登録してください")
    try:
        result = subprocess.run([executable, "--batch", "--noinapp", *map(str, args)],
                                cwd=str(ROOT), capture_output=True, text=True,
                                encoding="utf-8", errors="replace", timeout=timeout,
                                shell=False)
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError("Asepriteがタイムアウトしました") from exc
    if result.returncode:
        raise RuntimeError("Asepriteの実行に失敗: " + (result.stderr or result.stdout)[-500:])


def _int(command, key, minimum, maximum, default=0):
    value = command.get(key, default)
    if type(value) is not int or not minimum <= value <= maximum:
        raise ValueError(f"{key}は{minimum}～{maximum}の整数にしてください")
    return value


def _draw(width, height, instructions):
    if not isinstance(instructions, list) or len(instructions) > 2500:
        raise ValueError("描画指示は配列・最大2500件です")
    image = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    pen = ImageDraw.Draw(image)
    for shape in instructions:
        if not isinstance(shape, dict) or shape.get("type") not in ("pixel", "rect", "ellipse", "line"):
            raise ValueError("描画命令はpixel/rect/ellipse/lineのみです")
        color = shape.get("color")
        if not isinstance(color, str) or not COLOR.fullmatch(color):
            raise ValueError("色は#RRGGBBまたは#RRGGBBAAで指定してください")
        x, y = _int(shape, "x", -width, 2 * width), _int(shape, "y", -height, 2 * height)
        typ = shape["type"]
        if typ == "pixel":
            if 0 <= x < width and 0 <= y < height:
                pen.point((x, y), fill=color)
        elif typ == "line":
            x2 = _int(shape, "x2", -width, 2 * width)
            y2 = _int(shape, "y2", -height, 2 * height)
            pen.line((x, y, x2, y2), fill=color, width=1)
        else:
            w, h = _int(shape, "w", 1, 2 * width, 1), _int(shape, "h", 1, 2 * height, 1)
            bounds = (x, y, x + w - 1, y + h - 1)
            filled = shape.get("fill", True) is not False
            paint = pen.rectangle if typ == "rect" else pen.ellipse
            paint(bounds, fill=color if filled else None, outline=None if filled else color)
    return image


def _layers(spec, count):
    value = spec.get("layers")
    if value is None:
        value = [{"name": "AI Layer", "frames": [spec.get("commands", [])]}]
    if not isinstance(value, list) or not 1 <= len(value) <= 12:
        raise ValueError("レイヤー数は1～12です")
    result = []
    for index, layer in enumerate(value):
        if not isinstance(layer, dict) or not isinstance(layer.get("frames", []), list):
            raise ValueError("レイヤー定義が不正です")
        frames = layer.get("frames", [])
        if len(frames) > count:
            raise ValueError("フレーム数を超えています")
        result.append((str(layer.get("name") or f"Layer {index + 1}")[:60],
                       [frames[n] if n < len(frames) else [] for n in range(count)]))
    return result


def _database():
    try:
        value = json.loads((DATA / "index.json").read_text(encoding="utf-8"))
        return value if isinstance(value, list) else []
    except (OSError, ValueError):
        return []


def _write_database(items):
    DATA.mkdir(parents=True, exist_ok=True)
    tmp = DATA / "index.tmp"
    tmp.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(DATA / "index.json")


def list_sprites():
    with LOCK:
        return [item for item in _database()
                if (DATA / (item["id"] + ".aseprite")).is_file()]


def _require(sprite_id):
    if not isinstance(sprite_id, str) or not SPRITE_ID.fullmatch(sprite_id):
        raise ValueError("スプライトIDが不正です")
    with LOCK:
        item = next((x for x in _database() if x.get("id") == sprite_id), None)
    if item is None or not (DATA / (sprite_id + ".aseprite")).is_file():
        raise FileNotFoundError("スプライトが見つかりません")
    return item


def _quoted(value):
    return json.dumps(str(value), ensure_ascii=False)


def _create(spec, source=None, target_id=None):
    if not isinstance(spec, dict):
        raise ValueError("描画内容はJSONオブジェクトで指定してください")
    width = _int(spec, "width", 8, 256, 48)
    height = _int(spec, "height", 8, 256, 48)
    frames = _int(spec, "frames", 1, 12, 1)
    layers = _layers(spec, frames)
    sprite_id = target_id or uuid.uuid4().hex
    DATA.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="vds_aseprite_") as temp_dir:
        temp = Path(temp_dir)
        lines = ([f"local sprite = app.open({_quoted(source)})",
                  'if sprite == nil then error("Could not open source sprite") end']
                 if source else [f"local sprite = Sprite({width}, {height}, ColorMode.RGB)"])
        lines.append(f"while #sprite.frames < {frames} do sprite:newEmptyFrame() end")
        for layer_index, (name, drawings) in enumerate(layers):
            lines.append(f"local layer{layer_index} = sprite:newLayer()")
            lines.append(f"layer{layer_index}.name = {_quoted(name)}")
            for frame_index, commands in enumerate(drawings, start=1):
                png = temp / f"layer{layer_index}_frame{frame_index}.png"
                _draw(width, height, commands).save(png)
                lines.append(f"local picture = Image{{fromFile={_quoted(png)}}}")
                lines.append(f"sprite:newCel(layer{layer_index}, {frame_index}, picture)")
        staged = temp / "result.aseprite"
        lines.append(f"sprite:saveCopyAs({_quoted(staged)})")
        script = temp / "script.lua"
        script.write_text("\n".join(lines) + "\n", encoding="utf-8")
        _cli(["--script", script])
        if not staged.is_file():
            raise RuntimeError("Asepriteファイルの作成に失敗しました")
        preview = temp / "preview.png"
        _cli(["--oneframe", staged, "--save-as", preview])
        if not preview.is_file():
            raise RuntimeError("プレビュー作成に失敗しました")
        with LOCK:
            index = _database()
            if target_id:
                entry = next((x for x in index if x["id"] == target_id), None)
                if entry is None:
                    raise ValueError("スプライトが削除されました")
                entry["layers_added"] = entry.get("layers_added", 0) + len(layers)
            else:
                entry = {"id": sprite_id, "name": str(spec.get("name") or "無題のスプライト")[:80],
                         "width": width, "height": height, "frames": frames,
                         "layers_added": len(layers)}
                index.append(entry)
            staged.replace(DATA / (sprite_id + ".aseprite"))
            preview.replace(DATA / (sprite_id + ".png"))
            _write_database(index)
        return entry


def create_sprite(spec):
    return _create(spec)


def add_layers(sprite_id, spec):
    previous = _require(sprite_id)
    if not isinstance(spec, dict):
        raise ValueError("レイヤー指定が不正です")
    return _create({**spec, "width": previous["width"], "height": previous["height"],
                    "frames": previous["frames"]},
                   source=DATA / (sprite_id + ".aseprite"), target_id=sprite_id)


def export_sprite(sprite_id, kind="png"):
    _require(sprite_id)
    if kind not in ("png", "gif", "sheet"):
        raise ValueError("png/gif/sheetを指定してください")
    base = DATA / sprite_id
    source = base.with_suffix(".aseprite")
    target = (base.with_suffix(".png") if kind == "png"
              else base.with_suffix(".gif") if kind == "gif"
              else DATA / (sprite_id + ".sheet.png"))
    if kind == "png":
        return target
    with LOCK:
        _cli([source, "--save-as", target] if kind == "gif"
             else [source, "--sheet", target])
    if not target.is_file():
        raise RuntimeError("書き出しに失敗しました")
    return target


def import_png(name, blob):
    if len(blob) > MAX_UPLOAD:
        raise ValueError("画像は16MB以内です")
    try:
        with Image.open(io.BytesIO(blob)) as image:
            image.load()
            if image.format not in ("PNG", "GIF", "WEBP") or not (8 <= image.width <= 256 and 8 <= image.height <= 256):
                raise ValueError("8～256pxのPNG/GIF/WebPを指定してください")
            w, h = image.size
            with tempfile.TemporaryDirectory(prefix="vds_ase_import_") as temp_dir:
                src = Path(temp_dir) / "sprite.png"
                image.convert("RGBA").save(src)
                sprite_id = uuid.uuid4().hex
                DATA.mkdir(parents=True, exist_ok=True)
                destination = DATA / (sprite_id + ".aseprite")
                _cli([src, "--save-as", destination])
                if not destination.is_file():
                    raise RuntimeError("画像の変換に失敗しました")
                shutil.copy2(src, DATA / (sprite_id + ".png"))
                entry = {"id": sprite_id, "name": str(name)[:80], "width": w,
                         "height": h, "frames": 1, "layers_added": 1}
                with LOCK:
                    index = _database()
                    index.append(entry)
                    _write_database(index)
                return entry
    except (UnidentifiedImageError, OSError) as exc:
        raise ValueError("画像を読み取れませんでした") from exc


def ai_create(prompt, model, width, height, frames):
    if not re.fullmatch(r"[a-zA-Z0-9_.:/-]{1,80}", model):
        raise ValueError("Ollamaモデル名に使えない文字があります")
    instruction = (
        "Return ONLY a JSON object describing pixel art drawing shapes. "
        f"Canvas {width}x{height}; frames {frames}. "
        'Example {"name":"Knight","width":48,"height":48,"frames":1,'
        '"layers":[{"name":"Body","frames":[[{"type":"rect","x":10,"y":10,'
        '"w":12,"h":8,"color":"#445577"}]]}]}. '
        "Allowed shape types: pixel,rect,line,ellipse. Only integer coordinates and "
        "#RRGGBB or #RRGGBBAA colors. Up to 200 shapes, up to 6 layers. "
        "Retain consistent silhouette across frames. No code or markdown. "
        "User request (Japanese): " + prompt)
    body = json.dumps({"model": model, "prompt": instruction, "stream": False,
                       "format": "json", "options": {"temperature": 0.3}},
                      ensure_ascii=False).encode("utf-8")
    req = urlrequest.Request("http://127.0.0.1:11434/api/generate", data=body,
                             headers={"Content-Type": "application/json"})
    try:
        with urlrequest.urlopen(req, timeout=95) as reply:
            result = json.load(reply)
        spec = json.loads(result["response"])
    except Exception as exc:
        raise RuntimeError("Ollama接続または描画JSONの解析に失敗しました: " + str(exc)[:120]) from exc
    if not isinstance(spec, dict):
        raise ValueError("AI応答はJSONオブジェクトで指定してください")
    spec.update(width=width, height=height, frames=frames)
    return {"sprite": create_sprite(spec), "plan": spec}


def _safe(call, *args):
    try:
        return call(*args)
    except (ValueError, FileNotFoundError) as exc:
        raise HTTPException(400, str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.get("/aseprite/")
def editor():
    return FileResponse(ROOT / "static" / "aseprite.html")


@router.get("/api/aseprite/status")
def api_status():
    return status()


@router.post("/api/aseprite/config")
def api_config(body: AsepriteConfig):
    return _safe(configure, body.executable)


@router.get("/api/aseprite/sprites")
def api_list():
    return {"sprites": list_sprites()}


@router.post("/api/aseprite/sprites")
def api_create(spec: dict):
    return _safe(create_sprite, spec)


@router.post("/api/aseprite/sprites/{sprite_id}/layers")
def api_layers(sprite_id: str, spec: dict):
    return _safe(add_layers, sprite_id, spec)


@router.post("/api/aseprite/ai")
def api_ai(body: AiPrompt):
    return _safe(ai_create, body.prompt, body.model, body.width, body.height, body.frames)


@router.post("/api/aseprite/import")
async def api_import(file: UploadFile = File(...)):
    blob = await file.read(MAX_UPLOAD + 1)
    return _safe(import_png, file.filename or "import", blob)


@router.get("/api/aseprite/sprites/{sprite_id}/download/{kind}")
def api_download(sprite_id: str, kind: str):
    if kind not in ("aseprite", "png", "gif", "sheet"):
        raise HTTPException(400, "出力形式が正しくありません")
    _safe(_require, sprite_id)
    path = DATA / (sprite_id + ".aseprite") if kind == "aseprite" else _safe(export_sprite, sprite_id, kind)
    if kind == "aseprite":
        return FileResponse(path, filename=f"{sprite_id}.aseprite",
                            media_type="application/octet-stream")
    return FileResponse(path, media_type="image/gif" if kind == "gif" else "image/png",
                        headers={"Cache-Control": "no-store"})
