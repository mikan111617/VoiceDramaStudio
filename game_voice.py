"""Isolated game-voice workbench: KS and voice-list import, stable IDs, TTS, game ZIP.

Unlike VoiceDrama projects, no changes are made to an imported script or its
original voice assets. Projects live in workspace/game_voice/g_<hex>.
"""
from __future__ import annotations
import copy
import csv
import difflib
import io
import json
import os
import re
import shutil
import subprocess
import tempfile
import threading
import uuid
import urllib.request
import zipfile
from collections import defaultdict
from pathlib import Path

import core

GAME_LOCK = threading.RLock()
GAME_JOBS: dict[str, dict] = {}
GAME_EXECUTOR = None
VOICE_RE = re.compile(r'^\s*\[voice\s+[^\]]*\bid="([^"]+)"[^\]]*\]\s*$', re.I)
VOICE_OLD_RE = re.compile(r'^\s*;\s*@voice\s+id="([^"]+)"', re.I)
ID_RE = re.compile(r'^v([0-9]+)$', re.I)
SAFE_KEY = re.compile(r'^[\w-]+$', re.UNICODE)
AUDIO_FORMATS = ('.wav', '.mp3')
KS_MAX_BYTES = 20 * 1024 * 1024  # Up to 20 MiB per source file.
KS_TOTAL_MAX_BYTES = 60 * 1024 * 1024  # Prevent oversized multi-file batches.


def _base():
    return core.WORKSPACE / 'game_voice'


def _folder(pid):
    if not re.fullmatch(r'g_[0-9a-f]{16}', str(pid)):
        raise ValueError('不正なゲームプロジェクトIDです')
    return _base() / pid


def _statefile(pid):
    return _folder(pid) / 'game.json'


def _load(pid):
    p = _statefile(pid)
    if not p.is_file():
        raise FileNotFoundError('ゲームプロジェクトが見つかりません')
    return json.loads(p.read_text(encoding='utf8'))


def _write(pid, state):
    p = _statefile(pid)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix('.tmp')
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding='utf8')
    os.replace(tmp, p)
    return state


def list_games():
    _base().mkdir(parents=True, exist_ok=True)
    results = []
    for p in _base().glob('g_*/game.json'):
        try:
            s=json.loads(p.read_text(encoding='utf8'))
            results.append({'id':s['id'],'title':s['title'],'scenarios':len(s['scenarios']),
                            'voices':len(s['voices'])})
        except (ValueError, KeyError, OSError):
            continue
    return results


def create_game(title='新しいゲーム音声'):
    with GAME_LOCK:
        pid = 'g_' + uuid.uuid4().hex[:16]
        s = {'id':pid, 'title':str(title).strip()[:120] or 'ゲーム音声', 'revision':1,
             'lastIssued':0, 'scenarios':[], 'voices':[], 'bindings':{},
             'characterSheet':{}, 'storyContext':{}, 'format':'wav', 'voicePrefix':'voice'}
        return _write(pid,s)


def get_game(pid):
    with GAME_LOCK:
        return _load(pid)


def _save(pid,state,revision):
    prior=_load(pid)
    if int(prior['revision']) != int(revision):
        raise RuntimeError('別の画面で編集されています。再読み込みしてください')
    state['revision']=int(prior['revision'])+1
    return _write(pid,state)


def _clean_name(name):
    # Stem or folder component, never an arbitrary relative path.
    name = str(name).strip()
    if not name or len(name)>128 or not SAFE_KEY.fullmatch(name):
        raise ValueError('ファイル名・話者IDに使用できない文字が含まれています（空白・パス記号不可）')
    return name


def _decode_ks(content):
    if len(content)>KS_MAX_BYTES:
        raise ValueError('KSファイルは20MB以内にしてください')
    for encoding in ('utf-8-sig','utf-8','cp932'):
        try:
            return content.decode(encoding),encoding
        except UnicodeDecodeError:
            continue
    raise ValueError('KSの文字コードを読み取れません（UTF-8かShift_JISで保存してください）')


def _strip_tags(text):
    # Strip Tyrano tags while respecting quoted ] attributes.
    out=[];i=0
    while i<len(text):
        if text[i]!='[':
            out.append(text[i]);i+=1;continue
        j=i+1;quote=None;escape=False
        while j<len(text):
            c=text[j]
            if escape:escape=False
            elif quote and c=='\\':escape=True
            elif quote and c==quote:quote=None
            elif not quote and c in ('"',"'"):quote=c
            elif not quote and c==']':break
            j+=1
        if j>=len(text):out.append(text[i]);i+=1
        else:i=j+1
    return ''.join(out).replace('「','').replace('」','').replace('『','').replace('』','').strip()


def parse_ks(text):
    """Read KS dialogue and source locations; never evaluate JS/KS commands."""
    lines=text.splitlines(); rows=[];speaker=None; label=''; start=None;buf=''; pending='';tag_line=0;in_script=False
    def flush(end):
        nonlocal buf,start,pending,tag_line
        body=_strip_tags(buf)
        if body and speaker:
            rows.append({'speaker':speaker,'text':body,'label':label,'sourceLine':start or end,
                         'sourceEnd':end,'existingId':pending,'voiceTagLine':tag_line})
            pending='';tag_line=0
        buf='';start=None
    for i,raw in enumerate(lines,1):
        val=raw.strip();lower=val.lower()
        m=VOICE_RE.fullmatch(val) or VOICE_OLD_RE.match(val)
        if m:
            flush(i-1);pending=m.group(1).strip();tag_line=i;continue
        if lower.startswith(('[iscript','@iscript')):
            flush(i);in_script=True;continue
        if in_script:
            if lower.startswith(('[endscript','@endscript')):in_script=False
            continue
        if val.startswith(';') or not val:continue
        if val.startswith('*'):
            flush(i-1);label=val[1:].strip();continue
        if val.startswith('#'):
            flush(i-1);speaker=val[1:].strip();continue
        if val.startswith('@'):continue
        # Stop before unrelated commands that appear on their own line.
        if val.startswith('[') and re.fullmatch(r'\[[^\]]+\]',val):continue
        parts=re.split(r'(\[l\]|\[p\])',raw)
        for part in parts:
            if part in ('[l]','[p]'):flush(i)
            else:
                if part and _strip_tags(part) and start is None:start=i
                buf+=part
    flush(len(lines))
    places=defaultdict(int)
    for row in rows:
        places[row['sourceLine']]+=1
    repeated_line = next((n for n, count in places.items() if count > 1), None)
    if repeated_line is not None:
        raise ValueError(f'{repeated_line}行目に複数の発話があります。'
                         'ボイスIDを安全に割り当てるため、発話ごとに行を分けてください')
    return rows


def _issued(voices):
    return max((int(m.group(1)) for v in voices if (m:=ID_RE.fullmatch(str(v.get('voiceId',''))))), default=0)


def _assign(s,scenario_key,rows,old_rows):
    """Sequence match before allocating new IDs; survives insertions and text changes."""
    old_sig=[(x['speaker'],x.get('label',''),x.get('text','')) for x in old_rows]
    new_sig=[(x['speaker'],x.get('label',''),x.get('text','')) for x in rows]
    matcher=difflib.SequenceMatcher(a=old_sig,b=new_sig,autojunk=False)
    reused={}
    for op,i1,i2,j1,j2 in matcher.get_opcodes():
        if op=='equal':
            for i,j in zip(range(i1,i2),range(j1,j2)):reused[j]=old_rows[i]
        elif op=='replace' and i2-i1==j2-j1:
            for i,j in zip(range(i1,i2),range(j1,j2)):
                if old_rows[i]['speaker']==rows[j]['speaker']:
                    reused[j]=old_rows[i]
    # Reserve *all* IDs explicitly embedded in the incoming KS before using
    # sequence matching. Otherwise an earlier untagged line may inherit an ID
    # that belongs to a later [voice id="..."] line in the same file.
    other_owners={v['voiceId']:v['scenario'] for v in s['voices']
                  if v['scenario']!=scenario_key}
    old_by_id={v['voiceId']:v for v in old_rows}
    explicit_locations={}
    for row in rows:
        vid=row.get('existingId')
        if not vid:
            continue
        if vid in explicit_locations:
            raise ValueError(f'{row.get("voiceTagLine") or row.get("sourceLine") or "不明"}行目: '
                             f'ボイスID {vid} が同じKS内で重複しています'
                             f'（最初は{explicit_locations[vid]}行目）')
        explicit_locations[vid]=row.get('voiceTagLine') or row.get('sourceLine') or '不明'
        if vid in other_owners:
            raise ValueError(f'{row.get("voiceTagLine") or row.get("sourceLine") or "不明"}行目: '
                             f'ボイスID {vid} は別シナリオ「{other_owners[vid]}」で使用中です。'
                             '既存のゲーム音声プロジェクト内のシナリオを確認してください')
    reserved=set(explicit_locations)
    occupied=set(other_owners) | set(old_by_id) | reserved
    s['lastIssued']=max(s['lastIssued'],_issued(s['voices']),
                        _issued([{'voiceId':r['existingId']} for r in rows]))
    assigned=[];local=set()
    for i,row in enumerate(rows):
        explicit_id=row.get('existingId')
        if explicit_id:
            # A tagged ID is authoritative; never borrow a different old
            # voice's audio/caption because a fuzzy match happened to align.
            vid=explicit_id
            reuse=old_by_id.get(vid)
        else:
            candidate=reused.get(i)
            candidate_id=candidate.get('voiceId') if candidate else None
            # An inferred ID must not steal an explicit ID elsewhere in this KS
            # nor an ID already assigned by a previous row.
            if candidate_id and candidate_id in old_by_id and candidate_id not in reserved and candidate_id not in local:
                vid=candidate_id
                reuse=candidate
            else:
                vid=None
                reuse=None
        if not vid:
            while True:
                s['lastIssued']+=1
                vid=f"v{s['lastIssued']:05d}"
                if vid not in occupied and vid not in local:break
        local.add(vid)
        item={'voiceId':vid,'speaker':row['speaker'],'scenario':scenario_key,'text':row['text'],
              'ttsText':(reuse.get('ttsText') if reuse and reuse.get('text')==row['text'] else row['text']),
              'caption':reuse.get('caption','') if reuse else '',
              'label':row.get('label',''),'sourceLine':row.get('sourceLine',0),
              'sourceEnd':row.get('sourceEnd',0),'voiceTagLine':row.get('voiceTagLine',0),
              'sourceId':reuse.get('sourceId','') if reuse else '',
              'audio':(reuse.get('audio','') if reuse else ''),
              'stale':bool(reuse and reuse.get('text')!=row['text'] and reuse.get('audio'))}
        assigned.append(item)
    return assigned


def import_ks(pid,uploads,revision):
    """Batch .ks import is atomic (invalid file causes no mutation)."""
    with GAME_LOCK:
        s=_load(pid)
        if s['revision']!=revision:raise RuntimeError('編集の競合を検出しました')
        if not uploads or len(uploads)>30:raise ValueError('一度に読み込めるKSは1〜30件です')
        target_keys=set()
        staged=[]
        for raw_name,body in uploads:
            name=Path(str(raw_name).replace('\\','/')).name
            if not name.lower().endswith('.ks'):raise ValueError('KSファイルのみ選択してください')
            key=_clean_name(name[:-3]);
            if key in target_keys:raise ValueError('同じ名前のシナリオが選択されています')
            target_keys.add(key)
            try:
                decoded,encoding=_decode_ks(body)
                new_rows=parse_ks(decoded)
            except ValueError as exc:
                raise ValueError(f'{name}: {exc}') from exc
            staged.append((key,name,decoded,encoding,new_rows,'\r\n' if '\r\n' in decoded else '\n'))
        for key,name,decoded,encoding,new_rows,newline in staged:
            previous=[x for x in s['voices'] if x['scenario']==key]
            try:
                new_voices=_assign(s,key,new_rows,previous)
            except ValueError as exc:
                raise ValueError(f'{name}: {exc}') from exc
            s['voices']=[v for v in s['voices'] if v['scenario']!=key]+new_voices
            s['scenarios']=[sc for sc in s['scenarios'] if sc['key']!=key]+[{
                'key':key,'filename':name,'format':'ks','content':decoded,
                'encoding':encoding,'newline':newline,'count':len(new_voices)}]
        return _save(pid,s,revision)


def import_voice_list(pid,payload,revision):
    if not isinstance(payload,dict) or not isinstance(payload.get('speakers'),list):
        raise ValueError('speakers配列のある音声リストJSONを選択してください')
    with GAME_LOCK:
        s=_load(pid)
        if s['revision']!=revision:raise RuntimeError('編集の競合を検出しました')
        batch=_clean_name(str(payload.get('batch_name','voice_list')))
        key=batch
        if payload.get('output_format') in ('wav','mp3'):
            s['format']=payload['output_format']
        old=[v for v in s['voices'] if v['scenario']==key]
        old_lookup={(v['speaker'],v.get('sourceId')):v for v in old}
        s['lastIssued']=max(s['lastIssued'],_issued(s['voices']))
        all_voices=[];used=set()
        for group in payload['speakers']:
            speaker=_clean_name(group.get('speaker',''))
            items=group.get('items',[])
            if not isinstance(items,list):raise ValueError('itemsは配列で指定してください')
            for item in items:
                src_id=_clean_name(item.get('id',''))
                pair=speaker,src_id
                if pair in used:raise ValueError('同じ話者に重複した音声IDがあります')
                used.add(pair)
                value=str(item.get('text','')).strip()
                if not value:continue
                prev=old_lookup.get(pair)
                if prev:vid=prev['voiceId']
                else:
                    s['lastIssued']+=1;vid=f"v{s['lastIssued']:05d}"
                all_voices.append({'voiceId':vid,'speaker':speaker,'scenario':key,'text':value,
                    'ttsText':prev.get('ttsText',value) if prev and prev['text']==value else value,
                    'caption':prev.get('caption','') if prev else '',
                    'sourceId':src_id,'label':'','sourceLine':0,'sourceEnd':0,'voiceTagLine':0,
                    'audio':prev.get('audio','') if prev and prev['text']==value else '',
                    'stale':bool(prev and prev['text']!=value and prev.get('audio'))})
        if len(all_voices)>2000:raise ValueError('音声リストは2000件以内です')
        s['voices']=[v for v in s['voices'] if v['scenario']!=key]+all_voices
        s['scenarios']=[sc for sc in s['scenarios'] if sc['key']!=key]+[{
            'key':key,'filename':key+'.json','format':'voice-list','content':payload,
            'encoding':'utf-8','newline':'\n','count':len(all_voices),
            'outputPrefix':Path(str(payload.get('output_dir','voice')).replace('\\','/')).name or 'voice'}]
        return _save(pid,s,revision)



# CSV interchange for ChatGPT/Claude. IDs and original dialogue are immutable.
CAPTION_CSV_FIELDS = ('ボイスID', 'シナリオ', 'キャラ名', 'セリフ', 'キャプション')
CAPTION_CSV_LIMIT = 8 * 1024 * 1024


def export_captions_csv(pid, scenario_key=None):
    """Export original dialogue with immutable identity columns; no TTS required."""
    with GAME_LOCK:
        s = _load(pid)
        if scenario_key and not any(sc['key'] == scenario_key for sc in s['scenarios']):
            raise ValueError('指定したシナリオが見つかりません')
        out = io.StringIO(newline='')
        writer = csv.writer(out, lineterminator='\r\n')
        writer.writerow(CAPTION_CSV_FIELDS)
        scenarios = [sc['key'] for sc in s['scenarios'] if not scenario_key or sc['key'] == scenario_key]
        for key in scenarios:
            for voice in s['voices']:
                if voice['scenario'] != key:
                    continue
                writer.writerow([voice['voiceId'], key, voice['speaker'], voice['text'], voice.get('caption', '')])
        # BOM: Excel on Windows opens Japanese text without mojibake.
        return out.getvalue().encode('utf-8-sig')


def _parse_caption_csv(body, state):
    if not isinstance(body, bytes) or not body or len(body) > CAPTION_CSV_LIMIT:
        raise ValueError('CSVは8MB以内にしてください')
    try:
        decoded = body.decode('utf-8-sig')
    except UnicodeDecodeError:
        # Excel may re-export a CSV as Shift_JIS on Windows.
        try:
            decoded = body.decode('cp932')
        except UnicodeDecodeError:
            raise ValueError('CSVはUTF-8またはShift_JISで保存してください')
    try:
        reader = csv.DictReader(io.StringIO(decoded, newline=''), restkey='__extra__')
        if not reader.fieldnames or any(x not in reader.fieldnames for x in CAPTION_CSV_FIELDS):
            raise ValueError('CSVの必須列は「ボイスID・シナリオ・キャラ名・セリフ・キャプション」です')
        if len(reader.fieldnames) != len(set(reader.fieldnames)):
            raise ValueError('CSVに重複した列名があります')
        voices = {v['voiceId']: v for v in state['voices']}
        seen = set()
        changes = []
        count = blank = unchanged = 0
        for row in reader:
            count += 1
            if count > 10000:
                raise ValueError('CSVは10000行以内にしてください')
            if row.get('__extra__') is not None or any(row.get(key) is None for key in CAPTION_CSV_FIELDS):
                raise ValueError(f'CSVの{count + 1}行目の列数が正しくありません')
            vid = row['ボイスID'].strip()
            if not vid or vid in seen:
                raise ValueError(f'CSVに重複または空のボイスIDがあります（{count + 1}行目）')
            seen.add(vid)
            voice = voices.get(vid)
            if voice is None:
                raise ValueError(f'不明なボイスIDです: {vid}（{count + 1}行目）')
            # Never trust AI-edited rows based only on a numeric ID.
            if (row['シナリオ'] != voice['scenario'] or row['キャラ名'] != voice['speaker']
                    or row['セリフ'] != voice['text']):
                raise ValueError(f'{vid}: シナリオ・キャラ名・台詞が元データと一致しません。キャプション列だけ編集してください')
            caption = row['キャプション']
            if not caption.strip():
                blank += 1
                continue
            if len(caption) > 800:
                raise ValueError(f'{vid}: キャプションは800文字以内にしてください')
            if caption == voice.get('caption', ''):
                unchanged += 1
            else:
                changes.append((vid, caption))
        if not count:
            raise ValueError('CSVに台詞がありません')
        return {'rows': count, 'updated': len(changes), 'blank': blank, 'unchanged': unchanged,
                'examples': [{'voiceId': vid, 'caption': cap[:100]} for vid, cap in changes[:6]]}, changes
    except csv.Error as e:
        raise ValueError(f'CSVの解析に失敗しました: {e}')


def preview_caption_csv(pid, body, revision):
    with GAME_LOCK:
        state = _load(pid)
        if int(state['revision']) != int(revision):
            raise RuntimeError('編集の競合を検出しました。再読み込みしてください')
        summary, _ = _parse_caption_csv(body, state)
        return summary


def import_caption_csv(pid, body, revision):
    """Atomic update: captions only; unchanged or blank rows preserve existing data."""
    with GAME_LOCK:
        state = _load(pid)
        if int(state['revision']) != int(revision):
            raise RuntimeError('編集の競合を検出しました。再読み込みしてください')
        summary, updates = _parse_caption_csv(body, state)
        if not updates:
            return {'project': state, 'summary': summary}
        voices = {v['voiceId']: v for v in state['voices']}
        for vid, caption in updates:
            voice = voices[vid]
            voice['caption'] = caption
            if voice.get('audio'):
                voice['stale'] = True
        return {'project': _save(pid, state, revision), 'summary': summary}


def import_state_number(pid,body,revision):
    if not isinstance(body,dict) or not isinstance(body.get('last_issued'),int) or body['last_issued']<0 or body['last_issued']>99999999:
        raise ValueError('last_issuedが整数のJSONを指定してください')
    with GAME_LOCK:
        s=_load(pid)
        if s['revision']!=revision:raise RuntimeError('編集の競合を検出しました')
        s['lastIssued']=max(s['lastIssued'],body['last_issued'],_issued(s['voices']))
        return _save(pid,s,revision)


def import_character_sheet(pid,body,revision):
    if not isinstance(body,dict) or not isinstance(body.get('characters'),dict):
        raise ValueError('charactersを含むキャラクターシートJSONを指定してください')
    with GAME_LOCK:
        s=_load(pid)
        if s['revision']!=revision:raise RuntimeError('編集の競合を検出しました')
        s['characterSheet']={str(k):{
            'display_name':str(v.get('display_name','')),
            'aliases':[str(x) for x in v.get('aliases',[])[:30]] if isinstance(v.get('aliases'),list) else [],
            'reading':str(v.get('reading','')),
            'baseline_tone':str(v.get('baseline_tone','')),
            'speech_style':v.get('speech_style',[]) if isinstance(v.get('speech_style',[]),list) else [],
            'stable_traits':v.get('stable_traits',[]) if isinstance(v.get('stable_traits',[]),list) else [],
            'acting_notes':v.get('acting_notes',[]) if isinstance(v.get('acting_notes',[]),list) else [],
            'arc':v.get('arc',[]) if isinstance(v.get('arc',[]),list) else [],
        } for k,v in body['characters'].items() if isinstance(v,dict)}
        return _save(pid,s,revision)


def import_story_context(pid,body,revision):
    if not isinstance(body,dict) or len(json.dumps(body,ensure_ascii=False))>40000:
        raise ValueError('作品コンテキストは40KB以内のJSONオブジェクトにしてください')
    with GAME_LOCK:
        s=_load(pid)
        if s['revision']!=revision:raise RuntimeError('編集の競合を検出しました')
        s['storyContext']=body
        return _save(pid,s,revision)


def analyze_captions(pid,voice_ids,model,job):
    """Optional local Ollama analysis, without altering original game text.

    A lightweight story-aware mode. Keeps recent utterances plus the character
    sheet and available story context. The original v28 advanced cache/quality
    pipeline is not embedded or executed here.
    """
    try:
        model=str(model).strip()
        if not model or len(model)>100 or any(ord(c)<32 for c in model):
            raise ValueError('Ollamaモデル名を指定してください')
        with GAME_LOCK:
            s=_load(pid)
        chosen=set(voice_ids)
        selected=[v for v in s['voices'] if v['voiceId'] in chosen]
        if len(selected)!=len(chosen):raise ValueError('不明なボイスIDが含まれています')
        job['total']=len(selected)
        for chunk_start in range(0,len(selected),5):
            if job.get('cancel'):break
            chunk=selected[chunk_start:chunk_start+5]
            voices_by_scene={sc['key']:[v for v in s['voices'] if v['scenario']==sc['key']] for sc in s['scenarios']}
            context=[]
            for v in chunk:
                scene=voices_by_scene.get(v['scenario'],[])
                offset=next((i for i,x in enumerate(scene) if x['voiceId']==v['voiceId']),0)
                context.extend({'speaker':x['speaker'],'text':x['text']} for x in scene[max(0,offset-4):offset])
            profiles={v['speaker']:s['characterSheet'].get(v['speaker'],{}) for v in chunk}
            # Never include hidden_truth or future story developments as acting guidance.
            for v in chunk:
                profile=profiles[v['speaker']]
                profile['arc']=[a for a in profile.get('arc',[]) if isinstance(a,dict)
                      and v['scenario'] in str(a.get('range',''))][:1]
            instruction={
                'task':'以下のゲーム台詞について、原文にない発話を追加せず、感情と声の出し方だけを日本語で指示する。',
                'policy':'これまで未開示の真相を声に出さない。台詞ごとのcaptionは80文字以内。JSONのみ返答。',
                'format':{'captions':[{'voiceId':'v00001','caption':'声の演技指示'}]},
                'scene':chunk[0]['scenario'],
                'story_context':s.get('storyContext',{}),
                'characters':profiles,
                'recent_dialogue':context[-12:],
                'targets':[{'voiceId':v['voiceId'],'speaker':v['speaker'],'text':v['text'],
                            'existing_caption':v.get('caption','')} for v in chunk]}
            data=json.dumps({'model':model,'prompt':json.dumps(instruction,ensure_ascii=False)[:15000],
                'stream':False,'format':'json','options':{'temperature':0.2}},ensure_ascii=False).encode('utf8')
            request=urllib.request.Request('http://127.0.0.1:11434/api/generate',data=data,
                headers={'Content-Type':'application/json'},method='POST')
            job['message']=f'Ollamaで演技指示を解析中（{job["completed"]}/{job["total"]}）'
            with urllib.request.urlopen(request,timeout=240) as reply:
                response=json.loads(reply.read().decode('utf8'))
            content=response.get('response','')
            payload=json.loads(content)
            captions=payload.get('captions',[])
            if not isinstance(captions,list):raise ValueError('Ollamaが想定外のJSONを返しました')
            results={str(item.get('voiceId','')):str(item.get('caption','')).strip()[:800]
                for item in captions if isinstance(item,dict)}
            for voice in chunk:
                cap=results.get(voice['voiceId'],'')
                if not cap:
                    job['errors'].append({'id':voice['voiceId'],'error':'演技指示が返りませんでした'})
                    job['completed']+=1;continue
                with GAME_LOCK:
                    latest=_load(pid)
                    candidate=next((v for v in latest['voices'] if v['voiceId']==voice['voiceId']),None)
                    if candidate:
                        if candidate.get('caption')!=cap:
                            candidate['caption']=cap
                            if candidate.get('audio'):candidate['stale']=True
                        _save(pid,latest,latest['revision'])
                job['completed']+=1
        job['status']='cancelled' if job.get('cancel') else 'done'
        job['message']='演技指示の解析が完了しました'
    except Exception as e:
        job['status']='error';job['message']=str(e)


def update_game(pid,changes,revision):
    with GAME_LOCK:
        s=_load(pid)
        if s['revision']!=revision:raise RuntimeError('編集の競合を検出しました')
        if 'title' in changes:
            title=str(changes['title']).strip()
            if not title or len(title)>120:raise ValueError('作品名が正しくありません')
            s['title']=title
        if 'format' in changes:
            if changes['format'] not in AUDIO_FORMATS:raise ValueError('音声形式はwavかmp3です')
            s['format']=changes['format']
        if 'bindings' in changes:
            bindings=changes['bindings']
            if not isinstance(bindings,dict):raise ValueError('キャスト設定が正しくありません')
            for speaker,config in bindings.items():
                _clean_name(speaker)
                if not isinstance(config,dict):raise ValueError('キャスト設定が正しくありません')
                project_id=str(config.get('projectId',''))
                cast_name=str(config.get('castName',''))
                if project_id or cast_name:
                    orig=core.load_project(project_id)
                    if not any(c.get('name')==cast_name for c in orig['voices']):raise ValueError('指定したキャストが見つかりません')
            s['bindings']=bindings
        if 'voices' in changes:
            vmap={v['voiceId']:v for v in s['voices']}
            items=changes['voices']
            if not isinstance(items,list) or len(items)>2000:raise ValueError('台詞の指定が正しくありません')
            for item in items:
                if not isinstance(item,dict) or item.get('voiceId') not in vmap:
                    raise ValueError('存在しないボイスIDです')
                original=vmap[item['voiceId']]
                if 'caption' in item:
                    cap=str(item['caption'])
                    if len(cap)>800:raise ValueError('演技指示が長すぎます')
                    if cap!=original['caption']:
                        original['caption']=cap
                        if original.get('audio'):original['stale']=True
                if 'ttsText' in item:
                    val=str(item['ttsText']).strip()
                    if not val or len(val)>4000:raise ValueError('読み上げテキストが正しくありません')
                    if val!=original['ttsText']:
                        original['ttsText']=val
                        if original.get('audio'):original['stale']=True
        return _save(pid,s,revision)


def _game_audio_relative(voice,fmt='wav'):
    speaker=_clean_name(voice['speaker'])
    scenario=_clean_name(voice['scenario'])
    # The list format supplies per-speaker speech keys; KS uses durable voice IDs.
    ident=_clean_name(voice.get('sourceId') or voice['voiceId'])
    filename=ident+('.'+fmt)
    if voice.get('sourceId'):return f'{speaker}/{filename}'
    return f'{speaker}/{scenario}/{filename}'


def _current_audio_path(pid,voice):
    rel=voice.get('audio','')
    if not rel:return None
    actual=(_folder(pid)/'audio'/rel).resolve()
    if not actual.is_relative_to((_folder(pid)/'audio').resolve()):
        raise ValueError('保存されている音声パスが不正です')
    return actual


def _quoted_script(s,scenario):
    content=scenario['content'];lines=content.splitlines()
    voices=sorted([v for v in s['voices'] if v['scenario']==scenario['key']],key=lambda v:v['sourceLine'])
    insert={};replace={}
    for v in voices:
        source_line=int(v['sourceLine'])
        if not 1<=source_line<=len(lines):raise ValueError('シナリオの行番号が不正です')
        if v['voiceTagLine']:
            index=v['voiceTagLine']
            if not 1<=index<=len(lines):raise ValueError('ボイスIDタグの行番号が不正です')
            match=VOICE_RE.match(lines[index-1].strip()) or VOICE_OLD_RE.match(lines[index-1].strip())
            if not match:raise ValueError('既存のボイスIDタグを確認できません')
            if match.group(1)!=v['voiceId']:
                replace[index]=f'[voice id="{v["voiceId"]}"]'
        else:
            if source_line in insert:raise ValueError('1行に複数の声が含まれています')
            insert[source_line]=f'[voice id="{v["voiceId"]}"]'
    built=[]
    for n,line in enumerate(lines,1):
        if n in insert:built.append(insert[n])
        built.append(replace.get(n,line))
    text=scenario['newline'].join(built)+scenario['newline']
    return text.encode(scenario['encoding'])


def make_export_zip(pid,destination):
    with GAME_LOCK:
        s=_load(pid)
        if not s['scenarios']:raise ValueError('シナリオを取り込んでください')
        fmt=s.get('format','wav').lstrip('.')
        if fmt not in ('wav','mp3'):raise ValueError('不正な音声形式です')
        scenario_data={sc['key']:sc for sc in s['scenarios']}
        if len(set(v['voiceId'] for v in s['voices']))!=len(s['voices']):
            raise ValueError('ボイスIDが重複しています')
        maps={};files=[];missing=[]
        for v in s['voices']:
            rel=_game_audio_relative(v,fmt)
            # KS game runtime accesses sound/voice/<speaker>/<scenario>/<id>.wav.
            scenario=scenario_data.get(v['scenario'],{})
            prefix=scenario.get('outputPrefix') if v.get('sourceId') else s.get('voicePrefix','voice')
            prefix=_clean_name(prefix or 'voice')
            maps[v['voiceId']]=prefix+'/'+rel
            p=_current_audio_path(pid,v)
            if p and p.is_file() and not v.get('stale'):
                files.append((p,rel,v['voiceId']))
            else:missing.append(v['voiceId'])
        scene_maps={}
        for v in s['voices']:
            scene_maps.setdefault(v['scenario'],{})[v['voiceId']]=maps[v['voiceId']]
        with zipfile.ZipFile(destination,'w',zipfile.ZIP_DEFLATED,compresslevel=3,allowZip64=True) as z:
            for sc in s['scenarios']:
                if sc['format']=='ks':
                    z.writestr('voiced_scenarios/'+sc['filename'],_quoted_script(s,sc))
                else:
                    z.writestr('voice_list/'+sc['filename'],json.dumps(sc['content'],ensure_ascii=False,indent=2))
                z.writestr('voice_maps/'+sc['key']+'.json',json.dumps(scene_maps.get(sc['key'],{}),ensure_ascii=False,indent=2))
            z.writestr('voice_id_registry.json',json.dumps({'version':1,'last_issued':s['lastIssued'],
                'voices':[{k:v.get(k) for k in ('voiceId','scenario','speaker','sourceLine','text','sourceId')} for v in s['voices']]},ensure_ascii=False,indent=2))
            z.writestr('.voice_id_state.json',json.dumps({'last_issued':s['lastIssued']},indent=2))
            z.writestr('voice_map.json',json.dumps(maps,ensure_ascii=False,indent=2))
            z.writestr('missing_voices.json',json.dumps(missing,ensure_ascii=False,indent=2))
            for p,rel,vid in files:
                if fmt=='wav':
                    z.write(p,'audio/'+rel)
                else:
                    with tempfile.TemporaryDirectory(prefix='vds_game_mp3_') as temp:
                        output=Path(temp)/f'{vid}.mp3'
                        cp=subprocess.run(['ffmpeg','-v','error','-y','-i',str(p),'-codec:a','libmp3lame','-b:a','128k',str(output)],capture_output=True,text=True,timeout=180)
                        if cp.returncode!=0 or not output.is_file():
                            raise ValueError('MP3出力にはFFmpegが必要です: '+cp.stderr[-200:])
                        z.write(output,'audio/'+rel)
        return {'total':len(s['voices']),'audio':len(files),'missing':len(missing)}


def generation_groups(pid,ids):
    s=_load(pid)
    targets=[v for v in s['voices'] if v['voiceId'] in ids]
    plans=[]
    for v in targets:
        binding=s['bindings'].get(v['speaker'])
        if not binding or not binding.get('castName'):
            raise ValueError(f"キャストが未設定です: {v['speaker']}")
        source=core.load_project(binding['projectId'])
        cast=next((x for x in source['voices'] if x['name']==binding['castName']),None)
        if cast is None:raise ValueError('キャストが見つかりません: '+v['speaker'])
        engine=cast.get('engine','irodori')
        model=cast.get('model') or ('Qwen/Qwen3-TTS-12Hz-1.7B-Base' if engine=='qwen' else 'Aratako/Irodori-TTS-v4.1-Small')
        refs=[]
        for aid in cast.get('refs',[]):
            a=next((a for a in source['assets'] if a['id']==aid),None)
            if a:refs.append(str(core.asset_path(source['id'],a)))
        if engine=='qwen' and not refs:
            raise ValueError(f"{v['speaker']}のQwen用参照音声が登録されていません")
        plans.append((v,engine,model,cast,refs))
    return plans


def generate_game_audio(pid,voice_ids,steps,job):
    """Runs in background using the existing TTS worker and global environment."""
    try:
        import app as studio
        if not isinstance(voice_ids,list) or not voice_ids or len(voice_ids)>500:
            raise ValueError('生成対象は1〜500件です')
        with GAME_LOCK:
            groups=generation_groups(pid,set(voice_ids))
        if not groups:raise ValueError('生成する台詞がありません')
        buckets=defaultdict(list)
        for v,engine,model,cast,refs in groups:
            buckets[(engine,model)].append((v,cast,refs))
        job['total']=len(groups)
        for (engine,model),items in buckets.items():
            if job.get('cancel'):break
            interpreter=studio.python_for(engine,None)
            with tempfile.TemporaryDirectory(prefix='vds_game_tts_') as temp:
                d=Path(temp)
                request=[]
                for v,cast,refs in items:
                    request.append({'id':v['voiceId'],'text':v['ttsText'],'caption':v['caption'] or cast.get('description',''),
                         'description':cast.get('description',''),'refs':refs,'mode':'clone',
                         'language':cast.get('language','Japanese'),'ref_text':cast.get('refText',''),
                         'num_steps':steps,'output':str(d/(v['voiceId']+'.wav')),
                         'schedule':'linear','sway_coeff':-1.0})
                job_file=d/'request.json';result_file=d/'result.json'
                job_file.write_text(json.dumps({'engine':engine,'model':model,'irodori_dir':core.load_global_settings()['irodori_dir'],
                    'items':request},ensure_ascii=False),encoding='utf8')
                job['message']=f'{model} で {len(items)}件生成中'
                cp=subprocess.run([interpreter,str(core.ROOT/'tts_worker.py'),str(job_file),str(result_file)],
                    cwd=str(core.ROOT),capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=3600)
                if not result_file.exists():raise RuntimeError('TTSの応答がありません: '+(cp.stderr+cp.stdout)[-1000:])
                result=json.loads(result_file.read_text(encoding='utf8'))
                if not result.get('ok'):raise RuntimeError(result.get('error','TTS失敗'))
                for item in result['items']:
                    if item.get('ok') and Path(item['output']).is_file():
                        with GAME_LOCK:
                            s=_load(pid)
                            v=next((v for v in s['voices'] if v['voiceId']==item['id']),None)
                            if v:
                                rel=_game_audio_relative(v,'wav')
                                dest=_folder(pid)/'audio'/rel
                                dest.parent.mkdir(parents=True,exist_ok=True)
                                shutil.copy2(item['output'],str(dest)+'.tmp')
                                os.replace(str(dest)+'.tmp',dest)
                                v['audio']=rel;v['stale']=False
                                _save(pid,s,s['revision'])
                    else:job['errors'].append({'id':item.get('id'),'error':item.get('error','生成失敗')})
                    job['completed']+=1
        job['status']='cancelled' if job.get('cancel') else 'done'
        job['message']='音声生成が完了しました'
    except Exception as exc:
        job['status']='error';job['message']=str(exc)

