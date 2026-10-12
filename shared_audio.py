"""Project-independent BGM/SFX/ambience catalog.

Assets in a project are never moved or relinked: sharing and importing make
separate physical copies. All common media remains in the git-ignored workspace.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
from pathlib import Path

import core

SHAREABLE_KINDS = {"bgm", "sfx", "ambience"}
ID_PATTERN = re.compile(r"shared_[a-f0-9]{16}$")


def root():
    return core.WORKSPACE / "shared_audio"


def index_path():
    return root() / "index.json"


def _index():
    p = index_path()
    if not p.exists():
        return []
    entries = json.loads(p.read_text(encoding="utf-8"))
    if not isinstance(entries, list):
        raise ValueError("共通素材の管理ファイルが不正です")
    return entries


def _save(entries):
    folder = root()
    folder.mkdir(parents=True, exist_ok=True)
    temp = folder / ("index_" + core.uid("tmp") + ".json")
    try:
        temp.write_text(json.dumps(entries, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(temp, index_path())
    finally:
        temp.unlink(missing_ok=True)


def shared_path(entry):
    uid = entry.get("id", "")
    kind = entry.get("kind")
    filename = entry.get("filename", "")
    if not isinstance(uid, str) or not ID_PATTERN.fullmatch(uid):
        raise ValueError("共通素材IDが不正です")
    if kind not in SHAREABLE_KINDS or not isinstance(filename, str):
        raise ValueError("共通素材の種類が不正です")
    if not re.fullmatch(re.escape(uid) + r"\.(wav|mp3|flac|ogg|m4a|aac|opus)", filename):
        raise ValueError("共通素材のファイル名が不正です")
    return root() / filename


def list_shared():
    with core.LOCK:
        entries = _index()
        return [{k: e[k] for k in ("id", "name", "kind", "duration") if k in e}
                for e in entries if isinstance(e, dict)]


def get_shared_file(shared_id):
    if not isinstance(shared_id, str) or not ID_PATTERN.fullmatch(shared_id):
        raise ValueError("共通素材IDが不正です")
    with core.LOCK:
        entry = next((e for e in _index() if e.get("id") == shared_id), None)
        if entry is None:
            raise FileNotFoundError("共通素材が見つかりません")
        p = shared_path(entry)
        if not p.is_file():
            raise FileNotFoundError("共通素材の実体ファイルが見つかりません")
        return p, entry


def _sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def share_assets(pid, asset_ids):
    """Publish BGM/SFX/ambience via independent copies; deduplicate same audio/kind."""
    if not isinstance(asset_ids, list) or not 1 <= len(asset_ids) <= 250:
        raise ValueError("共有登録は1～250件で指定してください")
    if len(asset_ids) != len(set(asset_ids)) or any(not isinstance(aid, str) for aid in asset_ids):
        raise ValueError("共有登録する音声IDが重複または不正です")
    with core.LOCK:
        state = core.load_project(pid)
        by_id = {a["id"]: a for a in state["assets"]}
        entries = _index()
        added = []
        created = []
        reused = 0
        try:
            for aid in asset_ids:
                asset = by_id.get(aid)
                if asset is None or asset.get("kind") not in SHAREABLE_KINDS:
                    raise ValueError("BGM・効果音・環境音だけを共通登録できます")
                src = core.asset_path(pid, asset)
                if not src.is_file():
                    raise FileNotFoundError("登録元の音声ファイルがありません")
                checksum = _sha256(src)
                match = next((e for e in entries if e.get("kind") == asset["kind"] and e.get("sha256") == checksum), None)
                if match is not None:
                    if not shared_path(match).is_file():
                        raise FileNotFoundError("共通素材のファイルが見つかりません")
                    reused += 1
                    continue
                sid = core.uid("shared")
                dest = root() / (sid + src.suffix.lower())
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dest)
                created.append(dest)
                new = {"id": sid, "filename": dest.name, "name": str(asset.get("name") or src.name)[:160],
                       "kind": asset["kind"], "duration": asset.get("duration", 0),
                       "sha256": checksum, "createdAt": core.now()}
                entries.append(new)
                added.append(new)
            if added:
                _save(entries)
        except Exception:
            for p in created:
                p.unlink(missing_ok=True)
            raise
        return {"added": len(added), "existing": reused, "assets": list_shared()}


def import_shared(pid, shared_id, expected_revision=None):
    """Copy shared sound into a project's assets, preserving its own storage/IDs."""
    with core.LOCK:
        src, shared = get_shared_file(shared_id)
        state = core.load_project(pid)
        if expected_revision is not None and int(expected_revision) != state["revision"]:
            raise RuntimeError("編集内容の競合を検出しました。再読み込みしてください。")
        old = next((a for a in state["assets"] if a.get("sharedId") == shared_id), None)
        if old:
            if core.asset_path(pid, old).is_file():
                return {"project": state, "asset": old, "alreadyImported": True}
            raise FileNotFoundError("取り込み済みの共通素材ファイルがありません")
        aid = core.uid("asset")
        dest = core.workdir(pid) / core.asset_subdir(shared["kind"]) / (aid + src.suffix.lower())
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dest)
        copied = {"id": aid, "filename": dest.name, "name": shared["name"],
                  "kind": shared["kind"], "duration": shared["duration"], "sharedId": shared_id}
        try:
            state["assets"].append(copied)
            saved = core.save_project(pid, state, expected_revision=expected_revision, allow_new_assets=True)
        except Exception:
            dest.unlink(missing_ok=True)
            raise
        return {"project": saved, "asset": copied, "alreadyImported": False}
