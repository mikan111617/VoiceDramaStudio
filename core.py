"""Portable, project-scoped data store and Excel interchange for VoiceDrama Studio."""
from __future__ import annotations
import copy
import json
import os
import re
import shutil
import threading
import uuid
from datetime import datetime
from pathlib import Path
from difflib import SequenceMatcher

import openpyxl

ROOT = Path(__file__).resolve().parent
WORKSPACE = Path(os.getenv("VDS_WORKSPACE", str(ROOT / "workspace"))).resolve()
LOCK = threading.RLock()


def uid(prefix="id"):
    return prefix + "_" + uuid.uuid4().hex[:16]


def now():
    return datetime.now().astimezone().isoformat(timespec="seconds")


def workdir(project_id):
    if not re.fullmatch(r"p_[a-f0-9]{16}", str(project_id)):
        raise ValueError("Invalid project ID")
    return WORKSPACE / project_id


def state_path(project_id):
    return workdir(project_id) / "project.json"


def default_state(title, project_id):
    scene = uid('scene')
    return {
        "id": project_id, "title": title.strip() or "無題の作品", "updatedAt": now(), "revision": 0,
        "scenes": [{"id": scene, "name": "シーン1"}], "lines": [],
        "tracks": [
            {"id":"dialogue", "name":"台詞", "kind":"dialogue"},
            {"id":"sfx", "name":"効果音", "kind":"sfx"},
            {"id":"bgm", "name":"BGM", "kind":"bgm"},
            {"id":"ambience", "name":"環境音", "kind":"ambience"},
        ],
        "clips": [], "assets": [], "voices": [],
        "settings": {"irodori_dir": "D:\\Irodori-TTS", "irodori_python": "", "qwen_python": "", "defaultGap":0.65},
    }


def list_projects():
    WORKSPACE.mkdir(parents=True, exist_ok=True)
    out=[]
    for path in WORKSPACE.glob("p_*/project.json"):
        try:
            s=json.loads(path.read_text(encoding="utf-8"))
            out.append({"id":s["id"], "title":s.get("title", ""), "updatedAt":s.get("updatedAt", "")})
        except (OSError,ValueError,KeyError):
            pass
    return sorted(out,key=lambda p:p["updatedAt"],reverse=True)


def create_project(title):
    with LOCK:
        project_id=uid('p')
        state=default_state(title,project_id)
        workdir(project_id).mkdir(parents=True)
        (workdir(project_id)/"assets").mkdir()
        state=save_project(project_id,state)
        return state


def load_project(project_id):
    p=state_path(project_id)
    if not p.is_file():
        raise FileNotFoundError("Project not found")
    return json.loads(p.read_text(encoding="utf-8"))


def save_project(project_id, state, expected_revision=None, *, allow_new_assets=False):
    with LOCK:
        path=state_path(project_id)
        if str(state.get("id"))!=project_id:
            raise ValueError("Project ID mismatch")
        previous=json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None
        if previous and expected_revision is not None and int(previous["revision"]) != int(expected_revision):
            raise RuntimeError("編集内容の競合を検出しました。再読み込みしてください。")
        state=copy.deepcopy(state)
        validate_state(state, previous, allow_new_assets=allow_new_assets)
        state["revision"]=(int(previous.get("revision",0))+1) if previous else 1
        state["updatedAt"]=now()
        path.parent.mkdir(parents=True,exist_ok=True)
        temp=path.with_suffix(".tmp")
        temp.write_text(json.dumps(state,ensure_ascii=False,indent=2),encoding="utf-8")
        os.replace(temp,path)
        return state


def validate_state(s, prev, *, allow_new_assets=False):
    for k in ("scenes","lines","clips","tracks","assets","voices"):
        if not isinstance(s.get(k),list):
            raise ValueError(f"{k} must be a list")
    for k in ("lines","scenes","clips","assets"):
        vals=[v.get("id") for v in s[k]]
        if len(vals)!=len(set(vals)) or not all(vals):
            raise ValueError(f"Duplicate or missing IDs in {k}")
    scene_ids={v["id"] for v in s["scenes"]}
    asset_ids={v["id"] for v in s["assets"]}
    for l in s["lines"]:
        if l.get("sceneId") not in scene_ids: raise ValueError("Line scene mismatch")
        if len(str(l.get("text","")))>10000: raise ValueError("Line too long")
        for take in l.get("takes",[]):
            if take.get("assetId") not in asset_ids: raise ValueError("Take asset missing")
    track_ids={x["id"] for x in s["tracks"]}
    for c in s["clips"]:
        if c.get("sceneId") not in scene_ids or c.get("trackId") not in track_ids:
            raise ValueError("Clip track/scene mismatch")
        if c.get("assetId") not in asset_ids: raise ValueError("Clip asset missing")
    # Asset filename is server-controlled: clients cannot replace or invent disk paths.
    if prev:
        originals={a["id"]:a for a in prev["assets"]}
        for a in s["assets"]:
            if (a["id"] not in originals and not allow_new_assets) or (a["id"] in originals and a.get("filename") != originals[a["id"]].get("filename")):
                raise ValueError("Assets can be added only through the upload API")


def new_line(scene_id, speaker="", text="", caption="", after=0.65):
    return {"id":uid("line"), "sceneId":scene_id, "speaker":speaker,
            "text":text, "caption":caption,"after":after,
            "status":"missing", "takes":[], "selectedTakeId":None,
            "engine":"irodori", "model":"Aratako/Irodori-TTS-v4.1-Small", "mode":"clone", "language":"Japanese"}


def attach_take(s, line, asset, generator=None, *, reflow=True):
    take={"id":uid("take"),"assetId":asset["id"],"createdAt":now(),"engine":generator or "import",
          "signature":generation_signature(line, s)}
    line.setdefault("takes",[]).append(take)
    line["selectedTakeId"]=take["id"]
    line["status"]="ready"
    cl=next((c for c in s["clips"] if c.get("lineId")==line["id"] and c["trackId"]=="dialogue"),None)
    if cl is None:
        cl={"id":uid("clip"),"sceneId":line["sceneId"],"trackId":"dialogue","lineId":line["id"],
            "start":0.0, "gain":1.0,"loop":False,"fadeIn":0.0,"fadeOut":0.0,"trimStart":0.0,"pinned":False}
        s["clips"].append(cl)
    cl.update({"assetId":asset["id"],"duration":asset["duration"],"trimStart":0.0})
    if reflow:
        reflow_dialogue(s,line["sceneId"])
    return take


def reflow_dialogue(s,scene_id):
    cursor=0.0
    for line in (x for x in s["lines"] if x["sceneId"]==scene_id):
        clip=next((c for c in s["clips"] if c.get("lineId")==line["id"] and c.get("trackId")=="dialogue"),None)
        if clip:
            if not clip.get("pinned"):
                old_start=float(clip.get("start",0))
                new_start=max(0,cursor)
                clip["start"]=round(new_start,4)
                delta=new_start-old_start
                for related in s["clips"]:
                    if related.get("anchorLineId")==line["id"] and related is not clip:
                        related["start"]=round(max(0,new_start+float(related.get("anchorOffset",0))),4)
            cursor=max(cursor,float(clip["start"])+float(clip.get("duration",0)))
        cursor+=max(0,float(line.get("after",0.65)))


def generation_signature(line,s):
    import hashlib
    voice=next((v for v in s["voices"] if v["name"]==line.get("speaker")),{})
    data={k:line.get(k) for k in ("text","caption","engine","model","mode","language")}
    data["profile"]={k:voice.get(k) for k in ("refs","description","refText","language","model","engine","mode")}
    return hashlib.sha256(json.dumps(data,ensure_ascii=False,sort_keys=True).encode()).hexdigest()[:24]


def refresh_staleness(s):
    for line in s["lines"]:
        take=next((t for t in line.get("takes",[]) if t.get("id")==line.get("selectedTakeId")),None)
        if not take: line["status"]="missing"
        elif take.get("signature") and take["signature"]!=generation_signature(line,s):
            line["status"]="stale"
        else:line["status"]="ready"


HEADERS={
    "id":{"台詞id","セリフid","voice_id","line_id","id","音声id"},
    "scene":{"シーン","scene","scene_name","場面"},
    "speaker":{"話者","キャラクター","キャラ","speaker","name","人物","発話者"},
    "text":{"台詞","セリフ","本文","text","dialogue","発話"},
    "caption":{"キャプション","演技指示","指示","caption","emotion","ト書き","演出"},
    "after":{"間","間(秒)","間（秒）","次までの間","pause","gap","gap_after","after"},
}

def norm(s):
    return re.sub(r"\s+","",str(s or "").strip()).lower()


def import_excel(project_id, stream):
    """ID aware non-destructive merge; id-less rows match only exact text + speaker + scene."""
    wb=openpyxl.load_workbook(stream, read_only=True, data_only=True)
    with LOCK:
        s=load_project(project_id)
        sc_map={x["name"]:x["id"] for x in s["scenes"]}
        old_by_id={l["id"]:l for l in s["lines"]}
        seen=set()
        warnings=[]
        added=updated=matched=0
        imported=[]
        for ws in wb.worksheets:
            header_row=keys=None
            for row in list(ws.iter_rows(min_row=1,max_row=12,values_only=True)):
                fields={k:i for i,v in enumerate(row) for k,labels in HEADERS.items() if norm(v) in labels}
                if "text" in fields:
                    header_row=list(ws.iter_rows(min_row=1,max_row=12,values_only=True)).index(row)+1
                    keys=fields
                    break
            if not keys:
                warnings.append(f"{ws.title}: 台詞列が見つからずスキップしました")
                continue
            def cell(row,key):
                i=keys.get(key)
                return row[i] if i is not None and i<len(row) else None
            for row_no,row in enumerate(ws.iter_rows(min_row=header_row+1,values_only=True),start=header_row+1):
                txt=str(cell(row,"text") or "").strip()
                if not txt: continue
                speaker=str(cell(row,"speaker") or "ナレーション").strip()
                scene_name=str(cell(row,"scene") or ws.title).strip()
                if scene_name not in sc_map:
                    sid=uid("scene")
                    s["scenes"].append({"id":sid,"name":scene_name})
                    sc_map[scene_name]=sid
                sid=sc_map[scene_name]
                external_id=str(cell(row,"id") or "").strip()
                line=None
                if external_id:
                    line=old_by_id.get(external_id)
                    if line and line["id"] in seen:
                        warnings.append(f"{ws.title} {row_no}行目: ID重複のためスキップ ({external_id})")
                        continue
                    if line and line["sceneId"]!=sid:
                        # moving a line between scenes is allowed
                        for c in s["clips"]:
                            if c.get("lineId")==line["id"]:c["sceneId"]=sid
                if not line and not external_id:
                    possibilities=[x for x in s["lines"] if x["id"] not in seen and x["sceneId"]==sid and x["speaker"]==speaker and x["text"]==txt]
                    if len(possibilities)==1:line=possibilities[0];matched+=1
                if not line:
                    if external_id and external_id in old_by_id:
                        warnings.append(f"{ws.title} {row_no}行目: IDの照合に失敗")
                    line=new_line(sid)
                    if external_id and re.fullmatch(r"line_[a-f0-9]{16}",external_id):line["id"]=external_id
                    s["lines"].append(line)
                    old_by_id[line["id"]]=line
                    added+=1
                else:updated+=1
                line["speaker"]=speaker
                line["text"]=txt
                line["sceneId"]=sid
                if "caption" in keys:line["caption"]=str(cell(row,"caption") or "")
                if "after" in keys and cell(row,"after") not in (None,""):
                    try:line["after"]=max(0,float(cell(row,"after")))
                    except (TypeError,ValueError):warnings.append(f"{row_no}行目: 間の数値を読み取れません")
                seen.add(line["id"])
                imported.append(line["id"])
        # Put imported lines in workbook order, preserve non-imported entries afterward.
        order={line_id:i for i,line_id in enumerate(imported)}
        s["lines"].sort(key=lambda ln:(0,order[ln["id"]]) if ln["id"] in order else (1,0))
        # Imported speakers become editable cast entries automatically.
        known={v.get("name") for v in s["voices"]}
        for ln in s["lines"]:
            speaker=ln.get("speaker","")
            if speaker and speaker not in known:
                s["voices"].append({"name":speaker,"engine":"irodori",
                    "model":"Aratako/Irodori-TTS-v4.1-Small","mode":"clone",
                    "description":"","language":"Japanese","refText":"","refs":[]})
                known.add(speaker)
        for scene in s["scenes"]:reflow_dialogue(s,scene["id"])
        refresh_staleness(s)
        s=save_project(project_id,s)
        return {"project":s,"summary":{"added":added,"updated":updated,"matched":matched,"warnings":warnings}}


def export_excel(project_id):
    from io import BytesIO
    s=load_project(project_id)
    wb=openpyxl.Workbook()
    wb.remove(wb.active)
    for scene in s["scenes"]:
        # Excel sheet names must be unique and <=31 chars.
        name=re.sub(r'[\\/*?:\[\]]','_',scene["name"])[:28] or "Scene"
        base=name;i=1
        while name in wb.sheetnames:
            i+=1;name=base[:25]+f"_{i}"
        ws=wb.create_sheet(name)
        ws.append(["台詞ID","シーン","話者","台詞","キャプション","間(秒)"])
        for line in (l for l in s["lines"] if l["sceneId"]==scene["id"]):
            ws.append([line["id"],scene["name"],line["speaker"],line["text"],line.get("caption",""),line.get("after",0.65)])
        ws.freeze_panes="A2"
        from openpyxl.styles import Font,PatternFill,Alignment
        for c in ws[1]:c.font=Font(color="FFFFFF",bold=True);c.fill=PatternFill("solid",fgColor="1C3347")
        for col,width in {"A":24,"B":20,"C":18,"D":64,"E":56,"F":14}.items():ws.column_dimensions[col].width=width
        for row in ws.iter_rows(min_row=2):
            for c in row:c.alignment=Alignment(vertical="top",wrap_text=True)
    buf=BytesIO();wb.save(buf);buf.seek(0);return buf


def register_asset(project_id,src_path,name,kind="other"):
    with LOCK:
        s=load_project(project_id)
        ext=src_path.suffix.lower()
        if ext not in (".wav",".mp3",".flac",".ogg",".m4a",".aac",".opus"):
            raise ValueError("WAV / MP3 / FLAC / OGG / M4A / AAC / OPUSのみ対応")
        asset_id=uid("asset")
        dest=workdir(project_id)/"assets"/(asset_id+ext)
        dest.parent.mkdir(parents=True,exist_ok=True)
        shutil.copy2(src_path,dest)
        from render import duration_of
        try:duration=duration_of(dest)
        except Exception:
            dest.unlink(missing_ok=True)
            raise
        meta={"id":asset_id,"filename":dest.name,"name":name[:160],"kind":kind,"duration":round(duration,4)}
        s["assets"].append(meta)
        s=save_project(project_id,s,allow_new_assets=True)
        return {"asset":meta,"project":s}


SUPPORTED_AUDIO_EXTS = (".wav", ".mp3", ".flac", ".ogg", ".m4a", ".aac", ".opus")


def import_audio_batch(project_id, items, kind="voice", line_ids=None, cast_name=""):
    """Import multiple uploaded audio files in one project revision.

    `items`: list of (temporary file path, original display name), in UI order.
    `line_ids`: one existing line ID or None for each item (None means library only).
    Existing takes are kept; new assets get unique IDs. Invalid requests do not
    change project metadata and uploaded/copied files are cleaned up on error.
    """
    if not 1 <= len(items) <= 250:
        raise ValueError("一度に取り込める音声は1〜250件です")
    if kind not in {"voice", "reference", "sfx", "bgm", "ambience", "other"}:
        raise ValueError("音声の種類が不正です")
    if line_ids is None:
        line_ids = [None] * len(items)
    if not isinstance(line_ids, list) or len(line_ids) != len(items):
        raise ValueError("音声と割り当て先の件数が一致しません")
    line_ids = [x or None for x in line_ids]
    if len([x for x in line_ids if x]) != len(set(x for x in line_ids if x)):
        raise ValueError("同じ台詞に複数の音声を一括割り当てできません")
    if any(not Path(name.replace('\\', '/')).suffix.lower() in SUPPORTED_AUDIO_EXTS for _, name in items):
        raise ValueError("WAV / MP3 / FLAC / OGG / M4A / AAC / OPUSのみ対応")
    # Parse duration and verify all files before placing anything in workspace.
    from render import duration_of
    durations = []
    for path, _ in items:
        duration = duration_of(path)
        if not 0 < duration <= 7200:
            raise ValueError("音声ファイルの長さが不正です（2時間以内）")
        durations.append(duration)

    with LOCK:
        state = load_project(project_id)
        lines = {line['id']: line for line in state['lines']}
        for line_id in line_ids:
            if line_id and line_id not in lines:
                raise ValueError(f"割り当て先の台詞が見つかりません: {line_id}")
        voice = None
        if cast_name:
            if kind != 'reference':
                raise ValueError("キャストへの登録には参照音声モードを指定してください")
            voice = next((v for v in state['voices'] if v['name'] == cast_name), None)
            if voice is None:
                raise ValueError(f"キャストが見つかりません: {cast_name}")
        if kind == 'reference' and any(line_ids):
            raise ValueError("参照音声は台詞へ直接割り当てできません")
        new_assets = []
        copied = []
        scenes = set()
        try:
            for (path, name), duration, line_id in zip(items, durations, line_ids):
                ext = Path(name.replace('\\', '/')).suffix.lower()
                aid = uid('asset')
                dest = workdir(project_id) / 'assets' / (aid + ext)
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(path, dest)
                copied.append(dest)
                asset = {"id": aid, "filename": dest.name, "name": Path(name.replace('\\','/')).name[:160],
                         "kind": kind, "duration": round(duration, 4)}
                new_assets.append(asset)
                state['assets'].append(asset)
                if line_id:
                    line = lines[line_id]
                    attach_take(state, line, asset, reflow=False)
                    scenes.add(line['sceneId'])
                if voice is not None:
                    voice.setdefault('refs', []).append(aid)
            for scene_id in scenes:
                reflow_dialogue(state, scene_id)
            state = save_project(project_id, state, allow_new_assets=True)
        except Exception:
            for dest in copied:
                dest.unlink(missing_ok=True)
            raise
    return {"project": state, "assets": new_assets,
            "summary": {"imported": len(new_assets), "assigned": sum(bool(x) for x in line_ids),
                        "references": len(new_assets) if cast_name else 0}}
