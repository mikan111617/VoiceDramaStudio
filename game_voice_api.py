"""HTTP endpoints for the isolated game-voice tab."""
from __future__ import annotations
import json
import io
import tempfile
import uuid
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from fastapi import APIRouter, HTTPException, UploadFile, File
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel
from starlette.background import BackgroundTask
import core
import game_voice as game

router=APIRouter()
executor=ThreadPoolExecutor(max_workers=1)

class NewGame(BaseModel):
    title: str='新しいゲーム音声'

class ChangeGame(BaseModel):
    expectedRevision: int
    changes: dict

class GameGenerate(BaseModel):
    voiceIds: list[str]
    numSteps: int=40


def _errors(func,*args,**kwargs):
    try:return func(*args,**kwargs)
    except FileNotFoundError as e:raise HTTPException(404,str(e))
    except RuntimeError as e:raise HTTPException(409,str(e))
    except (ValueError,UnicodeError) as e:raise HTTPException(400,str(e))

@router.get('/game')
def game_home():return FileResponse(core.ROOT/'static'/'game.html')

@router.get('/api/game/projects')
def games():return game.list_games()

@router.post('/api/game/projects')
def create_game(body:NewGame):return _errors(game.create_game,body.title)

@router.get('/api/game/projects/{gid}')
def get_game(gid:str):return _errors(game.get_game,gid)

@router.put('/api/game/projects/{gid}')
def update_game(gid:str,body:ChangeGame):return _errors(game.update_game,gid,body.changes,body.expectedRevision)

@router.post('/api/game/projects/{gid}/import-ks')
async def import_ks(gid:str,expectedRevision:int,files:list[UploadFile]=File(...)):
    if not files or len(files)>30:raise HTTPException(400,'1〜30件のKSを選択してください')
    data=[]
    total=0
    for f in files:
        raw=await f.read(game.KS_MAX_BYTES+1)
        if len(raw)>game.KS_MAX_BYTES:
            raise HTTPException(400,f'{f.filename or "KSファイル"}: 20MBの上限を超えています')
        total+=len(raw)
        if total>game.KS_TOTAL_MAX_BYTES:
            raise HTTPException(400,'KSの合計サイズは60MB以内にしてください')
        data.append((f.filename or '',raw))
    return _errors(game.import_ks,gid,data,expectedRevision)

async def _json_upload(file:UploadFile):
    body=await file.read(3*1024*1024+1)
    if len(body)>3*1024*1024:raise HTTPException(400,'JSONは3MB以内です')
    try:return json.loads(body.decode('utf-8-sig'))
    except (UnicodeError,ValueError):raise HTTPException(400,'有効なUTF-8 JSONを指定してください')

@router.post('/api/game/projects/{gid}/voice-list')
async def import_voice_list(gid:str,expectedRevision:int,file:UploadFile=File(...)):
    return _errors(game.import_voice_list,gid,await _json_upload(file),expectedRevision)

@router.get('/api/game/projects/{gid}/captions.csv')
def export_caption_sheet(gid: str, scenario: str = ''):
    data = _errors(game.export_captions_csv, gid, scenario or None)
    name = 'game_captions.csv'
    return StreamingResponse(io.BytesIO(data), media_type='text/csv; charset=utf-8',
                             headers={'Content-Disposition':f'attachment; filename="{name}"',
                                      'Cache-Control':'no-store'})


@router.post('/api/game/projects/{gid}/captions/preview')
async def preview_caption_sheet(gid: str, expectedRevision: int, file: UploadFile = File(...)):
    data = await file.read(game.CAPTION_CSV_LIMIT + 1)
    return _errors(game.preview_caption_csv, gid, data, expectedRevision)


@router.post('/api/game/projects/{gid}/captions/import')
async def import_caption_sheet(gid: str, expectedRevision: int, file: UploadFile = File(...)):
    data = await file.read(game.CAPTION_CSV_LIMIT + 1)
    return _errors(game.import_caption_csv, gid, data, expectedRevision)


@router.post('/api/game/projects/{gid}/voice-id-state')
async def import_id_state(gid:str,expectedRevision:int,file:UploadFile=File(...)):
    return _errors(game.import_state_number,gid,await _json_upload(file),expectedRevision)

@router.post('/api/game/projects/{gid}/character-sheet')
async def import_character_sheet(gid:str,expectedRevision:int,file:UploadFile=File(...)):
    return _errors(game.import_character_sheet,gid,await _json_upload(file),expectedRevision)

@router.get('/api/game/cast-projects')
def cast_projects():
    result=[]
    for p in core.list_projects():
        try:
            voices=core.load_project(p['id']).get('voices',[])
            result.append({'id':p['id'],'title':p['title'],
                           'casts':[{'name':v.get('name',''),'engine':v.get('engine','irodori'),
                                     'model':v.get('model','')} for v in voices]})
        except (OSError,ValueError):continue
    return result

@router.post('/api/game/projects/{gid}/story-context')
async def import_story_context(gid:str,expectedRevision:int,file:UploadFile=File(...)):
    return _errors(game.import_story_context,gid,await _json_upload(file),expectedRevision)

class GameAnalyze(BaseModel):
    voiceIds: list[str]
    model: str='gemma4:12b'

@router.post('/api/game/projects/{gid}/analyze')
def analyze(gid:str,body:GameAnalyze):
    if not 1<=len(body.voiceIds)<=200 or len(set(body.voiceIds))!=len(body.voiceIds):
        raise HTTPException(400,'解析する台詞は1〜200件です')
    if not set(body.voiceIds).issubset({v['voiceId'] for v in _errors(game.get_game,gid)['voices']}):
        raise HTTPException(400,'不明なボイスIDがあります')
    jid='ga_'+uuid.uuid4().hex[:16]
    job={'id':jid,'status':'running','message':'Ollamaの準備中','total':0,'completed':0,'errors':[]}
    game.GAME_JOBS[jid]=job
    executor.submit(game.analyze_captions,gid,body.voiceIds,body.model,job)
    return job

@router.post('/api/game/projects/{gid}/generate')
def generate(gid:str,body:GameGenerate):
    if len(body.voiceIds)>500 or not body.voiceIds or len(set(body.voiceIds))!=len(body.voiceIds):
        raise HTTPException(400,'生成するボイスを1〜500件選択してください')
    if not 1<=body.numSteps<=80:raise HTTPException(400,'ステップ数は1〜80です')
    state=_errors(game.get_game,gid)
    known={v['voiceId'] for v in state['voices']}
    if not set(body.voiceIds).issubset(known):raise HTTPException(400,'不明なボイスIDがあります')
    _errors(game.generation_groups,gid,set(body.voiceIds))
    jid='gj_'+uuid.uuid4().hex[:16]
    job={'id':jid,'status':'running','message':'生成の準備中','completed':0,'total':0,'errors':[]}
    game.GAME_JOBS[jid]=job
    executor.submit(game.generate_game_audio,gid,body.voiceIds,body.numSteps,job)
    return job

@router.get('/api/game/jobs/{jid}')
def job_status(jid:str):
    if jid not in game.GAME_JOBS:raise HTTPException(404,'ジョブが見つかりません')
    return game.GAME_JOBS[jid]

@router.post('/api/game/jobs/{jid}/cancel')
def cancel_job(jid:str):
    if jid not in game.GAME_JOBS:raise HTTPException(404,'ジョブが見つかりません')
    game.GAME_JOBS[jid]['cancel']=True
    return {'ok':True}

@router.get('/api/game/projects/{gid}/audio/{vid}')
def game_audio(gid:str,vid:str):
    state=_errors(game.get_game,gid)
    v=next((x for x in state['voices'] if x['voiceId']==vid),None)
    if not v or not v.get('audio'):raise HTTPException(404,'生成済み音声がありません')
    p=_errors(game._current_audio_path,gid,v)
    if not p or not p.is_file():raise HTTPException(404,'音声ファイルが見つかりません')
    return FileResponse(p,media_type='audio/wav',filename=vid+'.wav',content_disposition_type='inline')

@router.get('/api/game/projects/{gid}/export.zip')
def export_game(gid:str):
    fd,path=tempfile.mkstemp(prefix='vds_game_export_',suffix='.zip')
    import os
    os.close(fd)
    path=Path(path)
    try:
        result=_errors(game.make_export_zip,gid,path)
        return FileResponse(path,filename='game_voice_export.zip',media_type='application/zip',
                            headers={'X-Voice-Total':str(result['total']),'X-Voice-Missing':str(result['missing'])},
                            background=BackgroundTask(lambda: path.unlink(missing_ok=True)))
    except Exception:
        path.unlink(missing_ok=True)
        raise
