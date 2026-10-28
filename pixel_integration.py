"""Mounted PixelLab workbench and read-only VoiceDrama scene -> battle timeline bridge.

The VoiceDrama project and audio files are never changed by this module. Only
copies of rendered clips are stored in pixel_workbench/data/assets.
"""
from __future__ import annotations

import importlib.util
import math
import shutil
import sys
import uuid
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

ROOT = Path(__file__).resolve().parent
PIXEL = ROOT / "pixel_workbench"


class SceneImport(BaseModel):
    project_id: str = Field(min_length=1, max_length=100)
    scene_id: str = Field(min_length=1, max_length=100)
    sequence_id: str | None = None
    fps: int = Field(default=12, ge=1, le=60)
    width: int = Field(default=240, ge=16, le=960)
    height: int = Field(default=160, ge=16, le=540)


def _load_pixel_module():
    # PixelLab's sequence_render.py uses a local absolute import. Restrict this
    # path to our bundled module, never to a user-provided directory.
    if str(PIXEL) not in sys.path:
        sys.path.insert(0, str(PIXEL))
    module_name = "vds_mounted_pixel_app"
    if module_name in sys.modules:
        return sys.modules[module_name]
    spec = importlib.util.spec_from_file_location(module_name, PIXEL / "app.py")
    if spec is None or spec.loader is None:
        raise RuntimeError("pixel_workbench/app.py を読み込めません")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def _resolve_scene(core, pid, scene_id):
    try:
        state = core.load_project(pid)
    except (ValueError, FileNotFoundError) as exc:
        raise HTTPException(404, "ボイスドラマの作品が見つかりません") from exc
    scene = next((sc for sc in state.get("scenes", []) if sc.get("id") == scene_id), None)
    if scene is None:
        raise HTTPException(404, "シーンが見つかりません")
    return state, scene


def _import_voice_scene(pixel, core, render_project, req):
    state, scene = _resolve_scene(core, req.project_id, req.scene_id)
    clips = [c for c in state.get("clips", []) if c.get("sceneId") == req.scene_id and float(c.get("duration", 0)) > 0]
    if not clips:
        raise HTTPException(422, "このシーンには音声クリップがありません")
    if len(clips) > 100:
        raise HTTPException(422, "このシーンは音声クリップが100件を超えます。分割してください")

    with pixel._lock:
        original = next((s for s in pixel._state.get("sequences", []) if s["id"] == req.sequence_id), None) if req.sequence_id else None
        if req.sequence_id and not original:
            raise HTTPException(404, "取り込み先の戦闘シーケンスが見つかりません")
        fps = int(original["fps"] if original else req.fps)
        width = int(original["width"] if original else req.width)
        height = int(original["height"] if original else req.height)
        if width * height > 960 * 540:
            raise HTTPException(422, "画面サイズは最大960×540です")
        if original and any(c.get("source_vds_project") == req.project_id and c.get("source_vds_scene") == req.scene_id for c in original.get("clips", [])):
            raise HTTPException(409, "同じシーンの音声は取り込み済みです。重複登録を避けるため処理を止めました")
        existing = len(original.get("clips", [])) if original else 0
        if existing + len(clips) > 100:
            raise HTTPException(422, "タイムラインは100クリップ以内です")

    end = max(max(0, float(c.get("start", 0))) + max(0, float(c.get("duration", 0))) for c in clips)
    if math.ceil(end * fps) > 600:
        raise HTTPException(422, f"このシーンは約{end:.1f}秒です。{fps}fpsでは600フレームを超えるため、シーンを分割するかFPSを下げてください")
    asset_by_id = {a["id"]: a for a in state.get("assets", [])}
    track_names = {x["id"]: x.get("name", x["id"]) for x in state.get("tracks", [])}
    lines = {x["id"]: x for x in state.get("lines", [])}
    pending_assets, pending_clips, directories = [], [], []
    skipped = []
    try:
        for clip in clips:
            source = asset_by_id.get(clip.get("assetId"))
            if not source:
                skipped.append(str(clip.get("id", "missing asset")))
                continue
            try:
                audio = core.asset_path(req.project_id, source)
            except (ValueError, FileNotFoundError):
                skipped.append(source.get("name", "missing file"))
                continue
            if not audio.is_file():
                skipped.append(source.get("name", "missing file"))
                continue
            asset_id = str(uuid.uuid4())
            folder = pixel.ASSETS / "sound" / asset_id
            folder.mkdir(parents=True, exist_ok=False)
            directories.append(folder)
            # Render one clip at t=0. VDS's existing mixer applies trim,
            # looping, fade-in/out and gain, so Pixel won't play raw material.
            trimmed = dict(clip)
            trimmed["start"] = 0.0
            isolated = dict(state)
            isolated["clips"] = [trimmed]
            try:
                render_project(isolated, core.workdir(req.project_id), folder / "asset.wav", format_name="wav", scene_id=req.scene_id)
            except Exception as exc:
                raise RuntimeError(f"音声「{source.get('name', '不明')}」の合成に失敗しました: {str(exc)[:180]}") from exc
            line = lines.get(clip.get("lineId"), {})
            kind = track_names.get(clip.get("trackId"), "音声")
            snippet = str(line.get("text", "")).strip().replace("\n", " ")[:22]
            label = (f"{kind}: {line.get('speaker','')} {snippet}" if snippet else f"{kind}: {source.get('name','音声')}")[:120]
            start = max(0, round(float(clip.get("start", 0)) * fps))
            length = max(1, round(float(clip.get("duration", 0)) * fps))
            pending_assets.append({
                "id": asset_id, "name": label, "category": "sound", "kind": "audio",
                "created_at": pixel.now(), "updated_at": pixel.now(), "source": "voicedrama-copy",
                "source_vds_project": req.project_id, "source_vds_scene": req.scene_id,
                "source_vds_clip_id": clip.get("id"), "files": ["asset.wav"],
                "primary_file": "asset.wav"
            })
            pending_clips.append({
                "id": str(uuid.uuid4()), "asset_id": asset_id, "asset_name": label,
                "asset_category": "sound", "track": "sound", "start": start,
                "duration": length, "volume": 1.0, "x": 0, "y": 0, "label": label,
                "created_at": pixel.now(), "source_vds_project": req.project_id,
                "source_vds_scene": req.scene_id, "source_vds_clip_id": clip.get("id"),
                "source_vds_track": clip.get("trackId")
            })
        if not pending_clips:
            raise HTTPException(422, "取り込める音声ファイルがありません。VoiceDrama側の素材を確認してください")
        with pixel._lock:
            seq = next((s for s in pixel._state.get("sequences", []) if s["id"] == req.sequence_id), None) if req.sequence_id else None
            if req.sequence_id and not seq:
                raise HTTPException(409, "操作中に取り込み先が変更されました")
            if seq and len(seq.get("clips", [])) + len(pending_clips) > 100:
                raise HTTPException(409, "取り込み中にシーケンスの上限が変わりました")
            created = seq is None
            if created:
                seq = {"id": str(uuid.uuid4()), "name": f"{state.get('title', '作品')} / {scene.get('name', 'シーン')}", "fps": fps,
                       "width": width, "height": height, "created_at": pixel.now(), "updated_at": pixel.now(), "clips": []}
            seq["clips"].extend(pending_clips)
            seq["clips"].sort(key=lambda x: (x.get("track", ""), int(x.get("start", 0))))
            seq["updated_at"] = pixel.now()
            if created:
                pixel._state.setdefault("sequences", []).append(seq)
            pixel._state.setdefault("assets", []).extend(pending_assets)
            try:
                pixel.save_state()
            except Exception:
                # Roll back in-memory mutations if the disk is full etc.
                for asset in pending_assets:
                    pixel._state["assets"].remove(asset)
                for c in pending_clips:
                    seq["clips"].remove(c)
                if created:
                    pixel._state["sequences"].remove(seq)
                raise
        return {"sequence_id": seq["id"], "clips_added": len(pending_clips),
                "skipped": skipped, "name": seq["name"], "fps": fps,
                "note": "VoiceDramaの音声素材をコピーして配置しました。元の台本・音声・クリップは変更していません。元の編集後は再取り込みが必要です。"}
    except Exception:
        for directory in directories:
            shutil.rmtree(directory, ignore_errors=True)
        raise


def register_pixel_workbench(vds_app):
    """Attach the v0.7 editor at /pixel without colliding with VDS /api."""
    import core
    from render import render_project

    pixel = _load_pixel_module()
    router = APIRouter(prefix="/api/vds", tags=["VoiceDrama bridge"])

    @router.get("/projects")
    def projects():
        return {"projects": core.list_projects()}

    @router.get("/projects/{pid}/scenes")
    def scenes(pid: str):
        try:
            project = core.load_project(pid)
        except (ValueError, FileNotFoundError) as exc:
            raise HTTPException(404, "作品が見つかりません") from exc
        return {"title": project.get("title"), "scenes": project.get("scenes", [])}

    @router.post("/import-scene")
    def import_scene(req: SceneImport):
        try:
            return _import_voice_scene(pixel, core, render_project, req)
        except HTTPException:
            raise
        except (OSError, ValueError, RuntimeError) as exc:
            raise HTTPException(422, str(exc)[:250]) from exc

    pixel.app.include_router(router)
    vds_app.mount("/pixel", pixel.app, name="pixel")
    return pixel.app
