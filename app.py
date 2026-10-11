"""Local-only VoiceDrama Studio HTTP API. Python 3.10+."""
from __future__ import annotations
import copy
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from starlette.background import BackgroundTask
from urllib.parse import quote

from fastapi import FastAPI, HTTPException, UploadFile, File, Form, Body
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import core
from core import WORKSPACE, LOCK
from render import render_project, ffmpeg_bin

app=FastAPI(title="VoiceDrama Studio",version="0.3.10")
app.mount('/static',StaticFiles(directory=str(core.ROOT/'static')),name='static')
THREADS=ThreadPoolExecutor(max_workers=1)
JOBS={}

class ProjectCreate(BaseModel):
    title:str="無題の作品"
class StateSave(BaseModel):
    state:dict
    expectedRevision:int | None=None

@app.get('/')
def home():return FileResponse(core.ROOT/'static'/'index.html')

@app.get('/api/status')
def status():return {'ok':True,'ffmpeg':bool(ffmpeg_bin()),'workspace':str(WORKSPACE),'python':sys.version.split()[0]}

class GlobalSettingsUpdate(BaseModel):
    irodori_dir: str
    irodori_python: str
    qwen_python: str
    expectedRevision: int | None = None

@app.get('/api/settings/global')
def global_settings_get():
    try:
        return core.load_global_settings()
    except (ValueError, OSError) as exc:
        raise HTTPException(500, str(exc))

@app.put('/api/settings/global')
def global_settings_put(body: GlobalSettingsUpdate):
    try:
        return core.save_global_settings({
            "irodori_dir": body.irodori_dir,
            "irodori_python": body.irodori_python,
            "qwen_python": body.qwen_python,
        }, body.expectedRevision)
    except RuntimeError as exc:
        raise HTTPException(409, str(exc))
    except (ValueError, OSError) as exc:
        raise HTTPException(400, str(exc))

@app.get('/api/projects')
def projects():return core.list_projects()

@app.post('/api/projects')
def project_create(v:ProjectCreate):return core.create_project(v.title)

class CastDeleteRequest(BaseModel):
    name: str
    expectedRevision: int


@app.post('/api/projects/{pid}/casts/delete')
def cast_delete(pid: str, body: CastDeleteRequest):
    try:
        return core.delete_cast(pid, body.name, body.expectedRevision)
    except RuntimeError as exc:
        raise HTTPException(409, str(exc))
    except FileNotFoundError as exc:
        raise HTTPException(404, str(exc))
    except ValueError as exc:
        raise HTTPException(400, str(exc))


class CastCopyRequest(BaseModel):
    targetProjectId: str
    castNames: list[str]
    conflict: str = "skip"
    expectedSourceRevision: int | None = None
    expectedTargetRevision: int | None = None


@app.post('/api/projects/{pid}/casts/copy')
def cast_copy(pid: str, body: CastCopyRequest):
    try:
        return core.copy_casts(pid, body.targetProjectId, body.castNames,
                               conflict=body.conflict,
                               expected_source_revision=body.expectedSourceRevision,
                               expected_target_revision=body.expectedTargetRevision)
    except RuntimeError as e:
        raise HTTPException(409, str(e))
    except (ValueError, FileNotFoundError) as e:
        raise HTTPException(400, str(e))


@app.get('/api/projects/{pid}')
def get_project(pid:str):
    try:
        project=core.load_project(pid)
        core.refresh_staleness(project)
        return project
    except (ValueError,FileNotFoundError) as e:raise HTTPException(404,str(e))

@app.put('/api/projects/{pid}')
def project_save(pid:str,data:StateSave):
    try:
        core.refresh_staleness(data.state)
        return core.save_project(pid,data.state,data.expectedRevision)
    except RuntimeError as e:raise HTTPException(409,str(e))
    except (ValueError,FileNotFoundError) as e:raise HTTPException(400,str(e))

class ScriptClearRequest(BaseModel):
    expectedRevision: int

@app.post('/api/projects/{pid}/script/clear')
def clear_script(pid: str, body: ScriptClearRequest):
    try:
        return core.clear_script(pid, body.expectedRevision)
    except RuntimeError as exc:
        raise HTTPException(409, str(exc))
    except FileNotFoundError as exc:
        raise HTTPException(404, str(exc))
    except ValueError as exc:
        raise HTTPException(400, str(exc))


class DestructiveRevisionRequest(BaseModel):
    expectedRevision: int


@app.post('/api/projects/{pid}/scenes/{sid}/delete')
def delete_scene(pid: str, sid: str, body: DestructiveRevisionRequest):
    try:
        return core.delete_scene(pid, sid, body.expectedRevision)
    except RuntimeError as exc:
        raise HTTPException(409, str(exc))
    except FileNotFoundError as exc:
        raise HTTPException(404, str(exc))
    except ValueError as exc:
        raise HTTPException(400, str(exc))


@app.post('/api/projects/{pid}/lines/{lineid}/takes/{takeid}/delete')
def delete_take(pid: str, lineid: str, takeid: str, body: DestructiveRevisionRequest):
    try:
        return core.delete_take_to_trash(pid, lineid, takeid, body.expectedRevision)
    except RuntimeError as exc:
        raise HTTPException(409, str(exc))
    except FileNotFoundError as exc:
        raise HTTPException(404, str(exc))
    except ValueError as exc:
        raise HTTPException(400, str(exc))


@app.post('/api/projects/{pid}/excel/import')
def excel_import(pid:str,file:UploadFile=File(...),mode:str=Form('merge'),expectedRevision:int|None=Form(None)):
    if not file.filename.lower().endswith('.xlsx'):raise HTTPException(400,'Excel .xlsx ファイルを指定してください')
    try:
        return core.import_excel(pid,file.file,mode=mode,expected_revision=expectedRevision)
    except RuntimeError as e:
        raise HTTPException(409,str(e))
    except Exception as e:
        raise HTTPException(400,str(e))

@app.get('/api/projects/{pid}/excel/export')
def excel_export(pid:str):
    try:result=core.export_excel(pid)
    except Exception as e:raise HTTPException(400,str(e))
    return StreamingResponse(result,media_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',headers={'Content-Disposition':"attachment; filename*=UTF-8''script.xlsx"})

@app.get('/api/projects/{pid}/assets/{aid}/download')
def download_audio_asset(pid: str, aid: str):
    try:
        state = core.load_project(pid)
        audio = next((a for a in state['assets'] if a['id'] == aid), None)
        if audio is None:
            raise FileNotFoundError('音声が見つかりません')
        physical = core.asset_path(pid, audio)
        if not physical.is_file():
            raise FileNotFoundError('音声ファイルが見つかりません')
        name = str(audio.get('name') or audio['filename']).replace('\\', '/').split('/')[-1]
        return FileResponse(physical, filename=name)
    except (ValueError, FileNotFoundError) as exc:
        raise HTTPException(404, str(exc))


@app.get('/api/projects/{pid}/scenes/{sid}/selected-takes.zip')
def download_selected_scene_takes(pid: str, sid: str):
    """Download only selected takes from this scene; no BGM or other scenes."""
    temporary = None
    ready = False
    try:
        with tempfile.NamedTemporaryFile(prefix="vds_voices_", suffix=".zip",
                                         delete=False) as output:
            temporary = Path(output.name)
        count = core.export_scene_selected_takes_zip(pid, sid, temporary)
        ready = True
        return FileResponse(
            temporary, media_type="application/zip",
            filename="selected_scene_voices.zip",
            headers={"X-VDS-Selected-Takes": str(count)},
            background=BackgroundTask(lambda: temporary.unlink(missing_ok=True)))
    except FileNotFoundError as exc:
        raise HTTPException(404, str(exc))
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    finally:
        # The response owns a valid archive; errors must not leave temp ZIPs behind.
        if temporary and not ready:
            temporary.unlink(missing_ok=True)


@app.post('/api/projects/{pid}/assets')
async def asset_upload(pid:str,file:UploadFile=File(...),kind:str=Form('other')):
    if not file.filename:raise HTTPException(400,'ファイルが選択されていません')
    suffix=Path(file.filename).suffix.lower()
    temp_path=None
    try:
        with tempfile.NamedTemporaryFile(delete=False,suffix=suffix) as f:
            temp_path=Path(f.name)
            total=0
            while True:
                chunk=await file.read(1024*1024)
                if not chunk:break
                total+=len(chunk)
                if total>250*1024*1024:raise ValueError('250MBを超えるファイルは取り込めません')
                f.write(chunk)
        return core.register_asset(pid,temp_path,Path(file.filename).name,kind)
    except Exception as e:raise HTTPException(400,str(e))
    finally:
        if temp_path:temp_path.unlink(missing_ok=True)

@app.post('/api/projects/{pid}/assets/batch')
async def batch_assets(pid:str,files:list[UploadFile]=File(...),kind:str=Form('voice'),
                       lineIds:str=Form('[]'),castName:str=Form('')):
    """Atomic bulk asset import; optional one-to-one script assignment."""
    if not 1 <= len(files) <= 250:
        raise HTTPException(400,'一度に取り込めるのは最大250ファイルです')
    try:
        targets=json.loads(lineIds)
        if not isinstance(targets,list) or len(targets)!=len(files) or any(x is not None and not isinstance(x,str) for x in targets):
            raise ValueError('割り当て先の指定が不正です')
    except (TypeError,ValueError) as e:
        raise HTTPException(400,str(e))
    with tempfile.TemporaryDirectory(prefix='vds_batch_') as d:
        uploaded=[]
        overall=0
        try:
            for i,file in enumerate(files):
                filename=Path(str(file.filename or '').replace('\\','/')).name
                suffix=Path(filename).suffix.lower()
                if suffix not in core.SUPPORTED_AUDIO_EXTS:
                    raise ValueError(f'非対応のファイル形式です: {filename}')
                path=Path(d)/f'{i:04d}{suffix}'
                count=0
                with path.open('wb') as out:
                    while True:
                        chunk=await file.read(1024*1024)
                        if not chunk: break
                        count+=len(chunk)
                        overall+=len(chunk)
                        if count>250*1024*1024 or overall>1024*1024*1024:
                            raise ValueError('1ファイル250MB、1回合計1GBが上限です')
                        out.write(chunk)
                if not count: raise ValueError(f'空のファイルです: {filename}')
                uploaded.append((path,filename))
            return core.import_audio_batch(pid,uploaded,kind,targets,castName.strip())
        except (ValueError,FileNotFoundError,OSError,RuntimeError) as e:
            raise HTTPException(400,str(e))

@app.get('/api/projects/{pid}/assets/{aid}')
def asset_stream(pid:str,aid:str):
    try:
        s=core.load_project(pid)
        a=next(x for x in s['assets'] if x['id']==aid)
        p=core.asset_path(pid, a)
        if not p.is_file():raise FileNotFoundError('Audio file missing')
        return FileResponse(p,filename=a['name'],content_disposition_type='inline')
    except (StopIteration,ValueError,FileNotFoundError) as e:raise HTTPException(404,str(e))

class BulkTrashRequest(BaseModel):
    assetIds:list[str]


@app.post('/api/projects/{pid}/assets/bulk/trash')
def bulk_trash_assets(pid:str,body:BulkTrashRequest):
    """Bulk-delete only unused audio, never detach scripts/casts implicitly."""
    ids=body.assetIds
    if not 1<=len(ids)<=250 or len(ids)!=len(set(ids)):
        raise HTTPException(400,'削除する音声は1〜250件、重複なしで指定してください')
    with LOCK:
        try:
            state=core.load_project(pid)
            known={a['id'] for a in state['assets']}
            if any(aid not in known for aid in ids):
                raise ValueError('存在しない音声IDが含まれています')
            used=[aid for aid in ids if any(core.asset_usage(state,aid).values())]
            if used:
                raise RuntimeError(f'{len(used)}件の音声が使用中です。個別に関連付けを確認してください')
            errors=[];removed=[]
            for aid in ids:
                try:
                    core.trash_asset(pid,aid)
                    removed.append(aid)
                except Exception as error:
                    errors.append({'assetId':aid,'error':str(error)})
            return {'project':core.load_project(pid),'trashed':removed,'errors':errors}
        except RuntimeError as e:raise HTTPException(409,str(e))
        except (ValueError,FileNotFoundError) as e:raise HTTPException(400,str(e))


@app.get('/api/projects/{pid}/trash/assets')
def trashed_assets(pid:str):
    try:return core.list_trash(pid)
    except (ValueError,FileNotFoundError) as e:raise HTTPException(404,str(e))


@app.get('/api/projects/{pid}/assets/{aid}/usage')
def asset_usage(pid:str,aid:str):
    try:
        state=core.load_project(pid)
        if not any(a['id']==aid for a in state['assets']):
            raise FileNotFoundError('音声素材が見つかりません')
        return core.asset_usage(state,aid)
    except (ValueError,FileNotFoundError) as e:raise HTTPException(404,str(e))


@app.delete('/api/projects/{pid}/assets/{aid}')
def delete_asset(pid:str,aid:str,detach:bool=False):
    try:return core.trash_asset(pid,aid,detach=detach)
    except RuntimeError as e:raise HTTPException(409,str(e))
    except (ValueError,FileNotFoundError) as e:raise HTTPException(400,str(e))


@app.post('/api/projects/{pid}/trash/assets/{aid}/restore')
def restore_asset(pid:str,aid:str):
    try:return core.restore_asset(pid,aid)
    except RuntimeError as e:raise HTTPException(409,str(e))
    except (ValueError,FileNotFoundError) as e:raise HTTPException(400,str(e))


@app.post('/api/projects/{pid}/lines/{lineid}/attach/{aid}')
def attach_audio(pid:str,lineid:str,aid:str):
    try:
        with LOCK:
            s=core.load_project(pid)
            line=next(l for l in s['lines'] if l['id']==lineid)
            asset=next(a for a in s['assets'] if a['id']==aid)
            core.attach_take(s,line,asset)
            return core.save_project(pid,s)
    except (StopIteration,ValueError,FileNotFoundError) as e:raise HTTPException(400,str(e))

@app.post('/api/projects/{pid}/reflow/{sceneid}')
def reflow(pid:str,sceneid:str):
    with LOCK:
        s=core.load_project(pid)
        core.reflow_dialogue(s,sceneid)
        return core.save_project(pid,s)

class MixRequest(BaseModel):
    sceneId:str|None=None
    format:str='wav'

@app.post('/api/projects/{pid}/render')
def mix(pid:str,body:MixRequest):
    if body.format not in ('wav','mp3'):raise HTTPException(400,'Unsupported output format')
    try:
        with LOCK:s=core.load_project(pid)
        out_dir=core.workdir(pid)/'exports';out_dir.mkdir(exist_ok=True)
        out=out_dir/('mix_'+uuid.uuid4().hex[:10]+'.'+body.format)
        result=render_project(s,core.workdir(pid),out,body.format,body.sceneId)
        return {'url':f'/api/projects/{pid}/exports/{out.name}','file':out.name,'info':result}
    except Exception as e:raise HTTPException(400,str(e))

@app.get('/api/projects/{pid}/exports/{filename}')
def download_mix(pid:str,filename:str):
    if '/' in filename or '\\' in filename or not filename.startswith('mix_'):
        raise HTTPException(400,'Invalid filename')
    p=core.workdir(pid)/'exports'/filename
    if not p.is_file():raise HTTPException(404,'Not found')
    return FileResponse(p,filename=filename)

class Generation(BaseModel):
    lineIds:list[str]
    numSteps:int=40


def python_for(engine,s):
    settings=core.load_global_settings()
    if engine=='irodori':
        directory=Path(str(settings.get('irodori_dir',''))).expanduser()
        if not (directory/'infer.py').is_file():raise ValueError('設定画面でIrodori-TTSのフォルダを指定してください。infer.pyが見つかりません。')
        val=str(settings.get('irodori_python') or directory/'.venv'/'Scripts'/'python.exe')
    else:
        val=str(settings.get('qwen_python') or (core.ROOT/'.venv-qwen3tts'/'Scripts'/'python.exe'))
    if not Path(val).is_file():raise ValueError(f'{engine}用Pythonが見つかりません: {val}')
    return val


def run_generation(job_id,pid,line_ids,steps):
    job=JOBS[job_id]
    try:
        with LOCK:
            state=core.load_project(pid)
            selected=[copy.deepcopy(l) for l in state['lines'] if l['id'] in line_ids and l['text'].strip()]
            asset_map={a['id']:a for a in state['assets']}
            voices={v['name']:v for v in state['voices']}
        if not selected:raise ValueError('生成する台詞がありません')
        filenames={line["id"]:line.get("outputFilename", "") for line in selected}
        groups={}
        for line in selected:
            voice=voices.get(line['speaker'],{})
            resolved=core.resolve_line_tts(line,voice)
            engine,mode,model=resolved['engine'],resolved['mode'],resolved['model']
            # Only the configured engine's native voice references are used.
            refs=[]
            for aid in voice.get('refs',[]):
                asset=asset_map.get(aid)
                if asset:refs.append(str(core.asset_path(pid,asset)))
            descriptor=voice.get('description','')
            spec={'id':line['id'],'text':line['text'],'caption':line.get('caption','') or descriptor,
                  'description':descriptor,'refs':refs,'mode':mode,'language':resolved['language'],
                  'ref_text':voice.get('refText',''),'num_steps':steps,'output':'',
                  'schedule':'linear','sway_coeff':-1.0}
            groups.setdefault((engine,model),[]).append(spec)
        job['total']=len(selected)
        for (engine,model),items in groups.items():
            if job.get('cancel'):break
            with LOCK:latest=core.load_project(pid)
            interp=python_for(engine,latest)
            with tempfile.TemporaryDirectory(prefix='vds_tts_') as fdir:
                folder=Path(fdir)
                for it in items:it['output']=str(folder/(it['id']+'.wav'))
                payload={'engine':engine,'model':model,'irodori_dir':core.load_global_settings()['irodori_dir'],'items':items}
                jobfile=folder/'request.json';resfile=folder/'response.json'
                jobfile.write_text(json.dumps(payload,ensure_ascii=False),encoding='utf-8')
                job['message']=f'{engine}: {model} を実行中 ({len(items)} 件)'
                cp=subprocess.run([interp,str(core.ROOT/'tts_worker.py'),str(jobfile),str(resfile)],
                                  cwd=str(core.ROOT),capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=60*45)
                if not resfile.exists():
                    raise RuntimeError(f'TTSが応答を返しませんでした。\n{(cp.stdout+cp.stderr)[-2000:]}')
                result=json.loads(resfile.read_text(encoding='utf-8'))
                if not result.get('ok'):raise RuntimeError(result.get('error','Unknown error')+'\n'+result.get('traceback','')[-1200:])
                for row in result.get('items',[]):
                    if not row.get('ok'):
                        job['errors'].append({'lineId':row.get('id'),'error':row.get('error','Generation failed')})
                    else:
                        src=Path(row['output'])
                        registered=core.register_asset(pid,src,core.generated_audio_filename({"id":row["id"],"outputFilename":filenames.get(row["id"])},engine),'voice')
                        with LOCK:
                            fresh=core.load_project(pid)
                            line=next((l for l in fresh['lines'] if l['id']==row['id']),None)
                            if line:
                                asset=next(a for a in fresh['assets'] if a['id']==registered['asset']['id'])
                                core.attach_take(fresh,line,asset,generator=f'{engine}:{model}')
                                core.save_project(pid,fresh)
                    job['completed']+=1
        job['message']='生成が完了しました'
        job['status']='cancelled' if job.get('cancel') else ('done_with_errors' if job['errors'] else 'done')
    except Exception as e:
        job['status']='error';job['message']=str(e)

@app.post('/api/projects/{pid}/generate/preview')
def generate_preview(pid: str, body: Generation):
    """Show the exact model grouping before any GPU work starts."""
    try:
        state=core.load_project(pid)
        voices={v['name']:v for v in state['voices']}
        requested=set(body.lineIds)
        groups={}
        for line in state['lines']:
            if line['id'] not in requested or not line.get('text','').strip():
                continue
            config=core.resolve_line_tts(line, voices.get(line.get('speaker'),{}))
            key=(config['engine'],config['model'])
            groups[key]=groups.get(key,0)+1
        return {'total':sum(groups.values()), 'groups':[
            {'engine':engine,'model':model,'count':count}
            for (engine,model),count in groups.items()]}
    except FileNotFoundError as e:
        raise HTTPException(404,str(e))
    except ValueError as e:
        raise HTTPException(400,str(e))


@app.post('/api/projects/{pid}/generate')
def generate(pid:str,body:Generation):
    try:core.load_project(pid)
    except Exception as e:raise HTTPException(404,str(e))
    if not body.lineIds or len(body.lineIds)>500:raise HTTPException(400,'生成する台詞数が不正です')
    jid=core.uid('job')
    JOBS[jid]={'id':jid,'status':'running','total':0,'completed':0,'errors':[],'message':'生成準備中'}
    THREADS.submit(run_generation,jid,pid,body.lineIds,max(1,min(80,body.numSteps)))
    return JOBS[jid]

class VoiceDesignRequest(BaseModel):
    speaker:str
    sampleText:str='こんにちは。今日もよろしくお願いします。'


def run_voice_design(job_id,pid,speaker,text):
    """Design one reference voice and attach it to the cast for subsequent cloning."""
    job=JOBS[job_id]
    try:
        s=core.load_project(pid)
        voice=next((x for x in s['voices'] if x['name']==speaker),None)
        if not voice:raise ValueError('キャストが見つかりません')
        engine=voice.get('engine','irodori')
        model=voice.get('model') or 'Aratako/Irodori-TTS-v4.1-Small'
        if engine=='qwen':model='Qwen/Qwen3-TTS-12Hz-1.7B-VoiceDesign'
        interp=python_for(engine,s)
        job['total']=1
        job['message']=f'{speaker}の新しい声をデザイン中...'
        with tempfile.TemporaryDirectory(prefix='vds_design_') as folder:
            folder=Path(folder);output=folder/'sample.wav'
            payload={'engine':engine,'model':model,'irodori_dir':core.load_global_settings()['irodori_dir'],
                'items':[{'id':speaker,'text':text,'caption':voice.get('description','') or '自然で聞き取りやすい話し方',
                    'description':voice.get('description',''),'output':str(output),
                    'mode':'design','refs':[],'language':voice.get('language','Japanese'),'num_steps':40}]}
            jobfile=folder/'job.json';out=folder/'result.json'
            jobfile.write_text(json.dumps(payload,ensure_ascii=False),encoding='utf-8')
            cp=subprocess.run([interp,str(core.ROOT/'tts_worker.py'),str(jobfile),str(out)],cwd=str(core.ROOT),capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=60*20)
            if not out.exists():raise RuntimeError('Voice Designの応答がありません: '+(cp.stdout+cp.stderr)[-1500:])
            result=json.loads(out.read_text(encoding='utf-8'))
            if not result.get('ok'):raise RuntimeError(result.get('error','VoiceDesign失敗'))
            item=result['items'][0]
            if not item.get('ok'):raise RuntimeError(item.get('error','VoiceDesign失敗'))
            registered=core.register_asset(pid,output,f'{speaker} - Voice Design.wav','reference')
            with LOCK:
                latest=core.load_project(pid)
                dest=next((v for v in latest['voices'] if v['name']==speaker),None)
                if dest:
                    dest.setdefault('refs',[]).append(registered['asset']['id'])
                    dest['refText']=text
                    dest['mode']='clone'
                    if engine=='qwen':dest['model']='Qwen/Qwen3-TTS-12Hz-1.7B-Base'
                    core.save_project(pid,latest)
            job['completed']=1
            job['referenceAssetId']=registered['asset']['id']
            job['message']='声のサンプルを生成してキャストの参照音声に登録しました'
            job['status']='done'
    except Exception as exc:
        job['status']='error';job['message']=str(exc)


@app.post('/api/projects/{pid}/voices/design')
def design_voice(pid:str,body:VoiceDesignRequest):
    if not body.sampleText.strip():raise HTTPException(400,'読み上げるサンプル台詞を入力してください')
    try:core.load_project(pid)
    except Exception as exc:raise HTTPException(404,str(exc))
    jid=core.uid('job')
    JOBS[jid]={'id':jid,'status':'running','total':1,'completed':0,'errors':[],'message':'準備中'}
    THREADS.submit(run_voice_design,jid,pid,body.speaker,body.sampleText)
    return JOBS[jid]


@app.get('/api/jobs/{jid}')
def get_job(jid:str):
    if jid not in JOBS:raise HTTPException(404,'Job not found')
    return JOBS[jid]

@app.post('/api/jobs/{jid}/cancel')
def cancel_job(jid:str):
    if jid not in JOBS:raise HTTPException(404,'Job not found')
    JOBS[jid]['cancel']=True
    return {'ok':True,'message':'現在実行中のモデル処理が終了した時点で停止します'}

class CaptionRequest(BaseModel):
    lineId:str
    model:str='gemma3:4b'

@app.post('/api/projects/{pid}/caption/suggest')
def suggest_caption(pid:str,request:CaptionRequest):
    """Optional Ollama acting note; never writes over the user's script automatically."""
    import urllib.request
    s=core.load_project(pid)
    lines=s['lines']
    idx=next((i for i,x in enumerate(lines) if x['id']==request.lineId),-1)
    if idx<0:raise HTTPException(404,'Line not found')
    line=lines[idx]
    context='\n'.join(f'{x["speaker"]}: {x["text"]}' for x in lines[max(0,idx-3):idx+4])
    prompt=f'以下の会話から対象の発話を演じるための短い日本語の演技指示（声の状態・態度・言い方）を1文で提案してください。台詞本文は変更しないでください。\n会話:\n{context}\n対象:{line["speaker"]}: {line["text"]}'
    body=json.dumps({'model':request.model,'prompt':prompt,'stream':False,'keep_alive':0},ensure_ascii=False).encode('utf-8')
    try:
        http=urllib.request.Request('http://localhost:11434/api/generate',data=body,headers={'Content-Type':'application/json'})
        with urllib.request.urlopen(http,timeout=120) as res:data=json.load(res)
        return {'caption':str(data.get('response','')).strip()[:360]}
    except Exception as e:raise HTTPException(503,'Ollama接続/生成エラー: '+str(e))

# Game voice production is isolated from the VoiceDrama timelines and assets.
from game_voice_api import router as game_router
app.include_router(game_router)

if __name__=='__main__':
    import uvicorn, webbrowser
    threading.Timer(1.3,lambda:webbrowser.open('http://127.0.0.1:'+os.getenv('VDS_PORT','8765'))).start()
    uvicorn.run(app,host='127.0.0.1',port=int(os.getenv('VDS_PORT','8765')))

