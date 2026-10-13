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
AUDIO_FORMATS = ('wav', 'mp3')
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
             'localCasts':{}, 'localCastMode':{}, 'voiceAssets':[],
             'characterSheet':{}, 'referenceCharacters':{}, 'nonCharacterSpeakers':[],
             'speakerFilterEnabled':False, 'storyContext':{}, 'format':'wav', 'voicePrefix':'voice',
             'pendingKS':[], 'lastIdRemaps':[],
             'pronunciationDictionary':{}, 'prosodyDictionary':{}}
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



# KS files contain UI and instruction blocks like #TIPS in addition to real
# dialogue. A sheet provides an explicit speaker allow-list; scripts without
# a sheet still avoid the known UI-only TIPS speaker.
DEFAULT_NON_VOICE_SPEAKERS = frozenset({'tips'})


def _sheet_active(state):
    # Old projects imported a character sheet before this flag existed.
    return bool(state.get('speakerFilterEnabled') or state.get('characterSheet'))


def _speaker_profile(state, speaker):
    """Return (canonical sheet ID, profile) for a KS speaker or sheet alias."""
    normalized=str(speaker).strip().casefold()
    for ident, profile in [*state.get('characterSheet',{}).items(),
                           *state.get('referenceCharacters',{}).items()]:
        names=[ident,profile.get('display_name',''),*(profile.get('aliases',[]) or [])]
        if normalized in {str(value).strip().casefold() for value in names if value}:
            return ident, profile
    return None, None


def _speaker_allowed(state, speaker):
    normalized=str(speaker).strip().casefold()
    if not normalized or normalized in DEFAULT_NON_VOICE_SPEAKERS:
        return False
    excluded={str(x).strip().casefold() for x in state.get('nonCharacterSpeakers', [])}
    if normalized in excluded:
        return False
    if not _sheet_active(state):
        return True
    _, profile=_speaker_profile(state,speaker)
    return profile is not None


def _filter_ks_rows(state, rows):
    kept=[]
    ignored=defaultdict(int)
    for row in rows:
        if _speaker_allowed(state,row['speaker']):
            kept.append(row)
        else:
            ignored[row['speaker']]+=1
    return kept,dict(sorted(ignored.items()))


def _profile(raw):
    return {
        'display_name':str(raw.get('display_name','')),
        'aliases':[str(x) for x in raw.get('aliases',[])[:30]] if isinstance(raw.get('aliases'),list) else [],
        'reading':str(raw.get('reading','')),
        'baseline_tone':str(raw.get('baseline_tone','')),
        'speech_style':raw.get('speech_style',[]) if isinstance(raw.get('speech_style',[]),list) else [],
        'stable_traits':raw.get('stable_traits',[]) if isinstance(raw.get('stable_traits',[]),list) else [],
        'acting_notes':raw.get('acting_notes',[]) if isinstance(raw.get('acting_notes',[]),list) else [],
        'arc':raw.get('arc',[]) if isinstance(raw.get('arc',[]),list) else [],
    }


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
            # Explicit KS IDs are authoritative, but reusing an ID previously
            # misattributed to #TIPS must not transfer TIPS audio/captions.
            vid=explicit_id
            reuse=old_by_id.get(vid)
            if reuse and reuse.get('speaker') != row['speaker']:
                reuse=None
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


def _read_ks_uploads(uploads):
    """Decode/validate uploads without touching the persistent project state."""
    if not uploads or len(uploads)>30:
        raise ValueError('一度に読み込めるKSは1〜30件です')
    staged=[]; keys=set(); total=0
    for raw_name,body in uploads:
        name=Path(str(raw_name).replace('\\','/')).name
        if not name.lower().endswith('.ks'):
            raise ValueError('KSファイルのみ選択してください')
        key=_clean_name(name[:-3])
        if key in keys:
            raise ValueError(f'同じ名前のKSが複数選択されています: {name}')
        keys.add(key)
        total+=len(body)
        if len(body)>KS_MAX_BYTES or total>KS_TOTAL_MAX_BYTES:
            raise ValueError(f'{name}: KSのサイズ上限を超えています')
        try:
            content,encoding=_decode_ks(body)
            parse_ks(content)
        except ValueError as exc:
            raise ValueError(f'{name}: {exc}') from exc
        staged.append({'key':key,'filename':name,'format':'ks','content':content,
                       'encoding':encoding,'newline':'\r\n' if '\r\n' in content else '\n'})
    return staged


def _rebuild_all_ks(s, incoming=()):
    """Two-pass project-wide allocator: reserve EVERY tagged KS ID first.

    Explicit tags are authoritative, even if a previous import mistakenly
    issued that number to an untagged line from another scenario. Only the
    latter is renumbered; its caption and audio path are preserved. New IDs
    are always above the historical issued number and all observed IDs.
    """
    original_voices=list(s['voices'])
    old_by_scene=defaultdict(list)
    for voice in original_voices:
        old_by_scene[voice['scenario']].append(voice)
    scenarios=list(s['scenarios'])
    for sc in incoming:
        scenarios=[old for old in scenarios if old['key']!=sc['key']]+[dict(sc)]
    staged=[];explicit={};all_tags=set()
    for sc in scenarios:
        if sc.get('format')!='ks':
            continue
        try:
            parsed=parse_ks(sc['content'])
            rows,ignored=_filter_ks_rows(s,parsed)
        except ValueError as exc:
            raise ValueError(f'{sc.get("filename",sc["key"])}: {exc}') from exc
        for row in parsed:
            if row.get('existingId'):
                all_tags.add(row['existingId'])
        for row in rows:
            vid=row.get('existingId')
            if not vid:
                continue
            location=f'{sc.get("filename",sc["key"])} の {row.get("voiceTagLine") or row.get("sourceLine")}行目'
            if vid in explicit:
                raise ValueError(f'ボイスID {vid} がKS内で重複しています: {explicit[vid]} と {location}。'
                                 '元シナリオに同じボイスIDが2つあります')
            explicit[vid]=location
        staged.append((sc,rows,ignored))
    reserved=set(explicit)|all_tags
    high=max([s.get('lastIssued',0),_issued(original_voices),
              _issued([{'voiceId':v} for v in reserved])])
    used=set(reserved)
    changes=[]
    def allocate():
        nonlocal high
        while True:
            high+=1
            vid=f'v{high:05d}'
            if vid not in used:
                used.add(vid)
                return vid
    rebuilt=[]
    # JSON voice-list IDs are internally assigned (not embedded KS tags).
    # Keep them unless an incoming KS asserts the same ID.
    for voice in original_voices:
        if any(sc['key']==voice['scenario'] for sc,_,_ in staged):
            continue
        v=dict(voice)
        if v['voiceId'] in used:
            old=v['voiceId'];v['voiceId']=allocate()
            changes.append({'scenario':v['scenario'],'speaker':v['speaker'],
                            'oldId':old,'newId':v['voiceId']})
        else:
            used.add(v['voiceId'])
        rebuilt.append(v)
    for sc,rows,ignored in staged:
        previous=old_by_scene[sc['key']]
        old_by_id={v['voiceId']:v for v in previous}
        old_sig=[(v['speaker'],v.get('label',''),v.get('text','')) for v in previous]
        new_sig=[(v['speaker'],v.get('label',''),v.get('text','')) for v in rows]
        reused={}
        for op,i1,i2,j1,j2 in difflib.SequenceMatcher(
                a=old_sig,b=new_sig,autojunk=False).get_opcodes():
            if op=='equal':
                for i,j in zip(range(i1,i2),range(j1,j2)):
                    reused[j]=previous[i]
            elif op=='replace' and i2-i1==j2-j1:
                for i,j in zip(range(i1,i2),range(j1,j2)):
                    if previous[i]['speaker']==rows[j]['speaker']:
                        reused[j]=previous[i]
        # Already-claimed IDs in used include every explicit KS tag. For an
        # explicit line that is its rightful owner, it is allowed by design.
        for i,row in enumerate(rows):
            explicit_id=row.get('existingId')
            candidate=old_by_id.get(explicit_id) if explicit_id else reused.get(i)
            if candidate and candidate.get('speaker')!=row['speaker']:
                candidate=None
            if explicit_id:
                vid=explicit_id
            elif candidate and candidate['voiceId'] not in used:
                vid=candidate['voiceId']
                used.add(vid)
            else:
                vid=allocate()
                if candidate:
                    changes.append({'scenario':sc['key'],'speaker':row['speaker'],
                                    'oldId':candidate['voiceId'],'newId':vid})
            # Source tags, speaker, script and stable previous metadata all
            # survive; an ID remap only changes the game's identity field.
            unchanged=bool(candidate and candidate.get('text')==row['text'])
            item={'voiceId':vid,'speaker':row['speaker'],'scenario':sc['key'],'text':row['text'],
                  'ttsText':candidate.get('ttsText',row['text']) if unchanged else row['text'],
                  'caption':candidate.get('caption','') if candidate else '',
                  'label':row.get('label',''),'sourceLine':row.get('sourceLine',0),
                  'sourceEnd':row.get('sourceEnd',0),'voiceTagLine':row.get('voiceTagLine',0),
                  'sourceId':candidate.get('sourceId','') if candidate else '',
                  'audio':candidate.get('audio','') if candidate else '',
                  'stale':bool(candidate and (candidate.get('stale') or
                          (not unchanged and candidate.get('audio'))))}
            rebuilt.append(item)
        sc['count']=len(rows)
        sc['ignoredSpeakers']=ignored
    if len({v['voiceId'] for v in rebuilt})!=len(rebuilt):
        raise ValueError('割り当て後のボイスIDが重複しています。データを変更せず中止しました')
    s['voices']=rebuilt
    s['scenarios']=scenarios
    s['lastIssued']=high
    s['lastIdRemaps']=changes[:1000]
    return s


def stage_ks(pid,uploads,revision):
    """Store all source scripts first; do not generate any new voice IDs."""
    incoming=_read_ks_uploads(uploads)
    with GAME_LOCK:
        s=_load(pid)
        if s['revision']!=revision:raise RuntimeError('編集の競合を検出しました')
        pending=list(s.get('pendingKS',[]))
        for entry in incoming:
            pending=[old for old in pending if old['key']!=entry['key']]
            pending.append(entry)
        if len(pending)>30:
            raise ValueError('仮登録できるKSは最大30件です')
        if sum(len(x['content'].encode(x.get('encoding','utf-8'))) for x in pending)>KS_TOTAL_MAX_BYTES:
            raise ValueError('仮登録中のKSの合計が60MBを超えています')
        s['pendingKS']=pending
        return _save(pid,s,revision)


def finalize_ks(pid,revision):
    """Issue global IDs only after the user has finished registering scripts."""
    with GAME_LOCK:
        s=_load(pid)
        if s['revision']!=revision:raise RuntimeError('編集の競合を検出しました')
        pending=s.get('pendingKS',[])
        if not pending:raise ValueError('先にKSを仮登録してください')
        _rebuild_all_ks(s,pending)
        s['pendingKS']=[]
        return _save(pid,s,revision)


def import_ks(pid,uploads,revision):
    """Backward-compatible immediate batch import, with global ID arbitration."""
    incoming=_read_ks_uploads(uploads)
    with GAME_LOCK:
        s=_load(pid)
        if s['revision']!=revision:raise RuntimeError('編集の競合を検出しました')
        _rebuild_all_ks(s,incoming)
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
    if not isinstance(body.get('reference_characters',{}),dict):
        raise ValueError('reference_charactersはオブジェクトで指定してください')
    if not isinstance(body.get('non_character_speakers',{}),(dict,list)):
        raise ValueError('non_character_speakersはオブジェクトまたは配列で指定してください')
    with GAME_LOCK:
        s=_load(pid)
        if s['revision']!=revision:raise RuntimeError('編集の競合を検出しました')
        s['characterSheet']={str(k):_profile(v) for k,v in body['characters'].items() if isinstance(v,dict)}
        s['referenceCharacters']={str(k):_profile(v) for k,v in body.get('reference_characters',{}).items()
                                  if isinstance(v,dict)}
        if not s['characterSheet'] and not s['referenceCharacters']:
            raise ValueError('キャラクターシートに音声化するcharactersがありません')
        blocked=body.get('non_character_speakers',{})
        s['nonCharacterSpeakers']=list(blocked)[:1000]
        s['speakerFilterEnabled']=True
        # A changed sheet can uncover characters in previously stored KS.
        # Revalidate and rebuild all scenes with one global ID reservation.
        _rebuild_all_ks(s)
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
            profiles={v['speaker']:(_speaker_profile(s,v['speaker'])[1] or {}) for v in chunk}
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


# Pronunciation and prosody dictionaries remain separate from the actual KS
# text and per-line user-entered caption. Both dictionaries are game-project-local.
MAX_DICTIONARY_ENTRIES = 1000


def _validate_pronunciation_dictionary(value):
    if not isinstance(value,dict) or len(value)>MAX_DICTIONARY_ENTRIES:
        raise ValueError('読み方辞書は最大1000語のJSONオブジェクトで指定してください')
    result={}
    for surface,reading in value.items():
        if not isinstance(surface,str) or not (1<=len(surface)<=100) or any(ord(c)<32 for c in surface):
            raise ValueError('読み方辞書の単語は1〜100文字にしてください')
        if not isinstance(reading,str) or not (1<=len(reading.strip())<=120) or any(ord(c)<32 for c in reading):
            raise ValueError(f'{surface} の読みは1〜120文字で指定してください')
        result[surface]=reading.strip()
    return result


def _validate_prosody_dictionary(value):
    if not isinstance(value,dict) or len(value)>MAX_DICTIONARY_ENTRIES:
        raise ValueError('アクセント辞書は最大1000語のJSONオブジェクトで指定してください')
    result={}
    for surface,item in value.items():
        if not isinstance(surface,str) or not (1<=len(surface)<=100) or any(ord(c)<32 for c in surface):
            raise ValueError('アクセント辞書の単語は1〜100文字にしてください')
        if not isinstance(item,dict):raise ValueError(f'{surface} のアクセント設定はJSONオブジェクトが必要です')
        aliases=item.get('aliases',[])
        if not isinstance(aliases,list) or len(aliases)>30 or any(not isinstance(x,str) or len(x)>100 for x in aliases):
            raise ValueError(f'{surface} の別名は30件以内・各100文字以内にしてください')
        if item.get('enabled',True) not in (True,False):
            raise ValueError(f'{surface} の有効設定はtrue/falseで指定してください')
        entry={}
        for key,limit in (('reading',120),('accent',80),('instruction',500),('note',500)):
            v=item.get(key,'')
            if not isinstance(v,str) or len(v)>limit or any(ord(c)<32 and c not in '\n\t' for c in v):
                raise ValueError(f'{surface} の {key} は{limit}文字以内にしてください')
            entry[key]=v
        entry['aliases']=aliases
        entry['enabled']=item.get('enabled',True)
        result[surface]=entry
    return result


def _speech_text(original, dictionary):
    """Replace only the longest original surface once; never recursively."""
    if not dictionary:return original
    tokens=sorted(dictionary,key=lambda x:(-len(x),x))
    pattern=re.compile('|'.join(re.escape(x) for x in tokens))
    def replacement(match):
        key=match.group(0)
        # An ASCII token such as 2F must not match inside 12F or 2Floor.
        if key.isascii() and any(c.isalnum() for c in key):
            begin,end=match.span()
            if begin and original[begin-1].isascii() and original[begin-1].isalnum():return key
            if end<len(original) and original[end].isascii() and original[end].isalnum():return key
        return dictionary[key]
    return pattern.sub(replacement,original)


def _speech_inputs(state,voice):
    """Effective worker inputs, without mutating KS, captions, or ttsText."""
    original=voice['ttsText']
    text=_speech_text(original,state.get('pronunciationDictionary',{}))
    notes=[]
    for name,item in state.get('prosodyDictionary',{}).items():
        if not item.get('enabled',True):continue
        terms=[name,*item.get('aliases',[])]
        if not any(term and term in original for term in terms):continue
        # A prosody entry may provide a reading not present in the separate
        # pronunciation dictionary. The dedicated pronunciation dictionary wins.
        if name not in state.get('pronunciationDictionary',{}) and item.get('reading'):
            text=_speech_text(text,{name:item['reading']})
        instruct=item.get('instruction','').strip()
        if not instruct and item.get('accent'):
            instruct=f'「{name}」は「{item["accent"]}」にアクセントを置く。'
        if instruct:notes.append(instruct)
    return text, '\n'.join(notes)


def _mark_changed_dictionary_voices(before,after):
    for v in after['voices']:
        if not v.get('audio'):continue
        if _speech_inputs(before,v)!=_speech_inputs(after,v):v['stale']=True


def import_speech_dictionary(pid,kind,payload,revision):
    if kind=='pronunciation':
        key='pronunciationDictionary'
        entries=_validate_pronunciation_dictionary(payload)
    elif kind=='prosody':
        key='prosodyDictionary'
        if not isinstance(payload,dict) or not isinstance(payload.get('entries'),dict):
            raise ValueError('アクセント辞書は version / entries のあるJSONを選択してください')
        entries=_validate_prosody_dictionary(payload['entries'])
    else:raise ValueError('辞書の種類が正しくありません')
    with GAME_LOCK:
        state=_load(pid)
        if state['revision']!=revision:raise RuntimeError('編集の競合を検出しました')
        before=copy.deepcopy(state)
        state[key]=entries
        _mark_changed_dictionary_voices(before,state)
        return _save(pid,state,revision)


def update_game(pid,changes,revision):
    with GAME_LOCK:
        s=_load(pid)
        if s['revision']!=revision:raise RuntimeError('編集の競合を検出しました')
        if 'title' in changes:
            title=str(changes['title']).strip()
            if not title or len(title)>120:raise ValueError('作品名が正しくありません')
            s['title']=title
        if 'format' in changes:
            # The browser sends 'wav'/'mp3' without a leading dot.
            output_format = str(changes['format']).lower().lstrip('.')
            if output_format not in AUDIO_FORMATS:raise ValueError('音声形式はwavかmp3です')
            s['format']=output_format
        if 'pronunciationDictionary' in changes or 'prosodyDictionary' in changes:
            before=copy.deepcopy(s)
            if 'pronunciationDictionary' in changes:
                s['pronunciationDictionary']=_validate_pronunciation_dictionary(changes['pronunciationDictionary'])
            if 'prosodyDictionary' in changes:
                s['prosodyDictionary']=_validate_prosody_dictionary(changes['prosodyDictionary'])
            _mark_changed_dictionary_voices(before,s)
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
        changed_cast_keys=set()
        if 'localCastMode' in changes:
            flags=changes['localCastMode']
            if not isinstance(flags,dict) or len(flags)>250 or any(
                not isinstance(k,str) or not SAFE_KEY.fullmatch(k) or v not in ('local','shared') for k,v in flags.items()):
                raise ValueError('声設定の切り替え値が正しくありません')
            for key in set(flags)|set(s.get('localCastMode',{})):
                if flags.get(key,'shared')!=s.get('localCastMode',{}).get(key,'shared'):
                    changed_cast_keys.add(key)
            s['localCastMode']=flags
        if 'localCasts' in changes:
            local=changes['localCasts']
            if not isinstance(local,dict) or len(local)>250:raise ValueError('個別キャスト設定が不正です')
            active={speaker for v in s['voices'] for speaker in (v['speaker'], _speaker_profile(s,v['speaker'])[0]) if speaker}
            active.update(s.get('characterSheet',{}))
            active.update(s.get('referenceCharacters',{}))
            for key,cast in local.items():
                _clean_name(key)
                if key not in active:raise ValueError(f'シナリオに存在しないキャストです: {key}')
                local[key]=_validate_local_cast(cast,s.get('voiceAssets',[]))
            for key in set(local)|set(s.get('localCasts',{})):
                if local.get(key)!=s.get('localCasts',{}).get(key):
                    changed_cast_keys.add(key)
            s['localCasts']=local
        for key in changed_cast_keys:
            _mark_voice_takes_stale_for_cast(s,key)
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
    # If a non-character line contains an old voice tag in the source,
    # exclude the tag from the GAME copy too, or it would refer to missing audio.
    # The original imported text is not modified.
    remove_tags=set()
    for row in parse_ks(content):
        if row.get('voiceTagLine') and not _speaker_allowed(s,row['speaker']):
            remove_tags.add(row['voiceTagLine'])
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
        if n in remove_tags:continue
        if n in insert:built.append(insert[n])
        built.append(replace.get(n,line))
    text=scenario['newline'].join(built)+scenario['newline']
    return text.encode(scenario['encoding'])


def make_export_zip(pid,destination,format_override=None):
    with GAME_LOCK:
        s=_load(pid)
        if s.get('pendingKS'):
            raise ValueError('仮登録中のKSがあります。先にボイスIDを一括確定してください')
        if not s['scenarios']:raise ValueError('シナリオを取り込んでください')
        fmt=str(format_override if format_override is not None else s.get('format','wav')).lower().lstrip('.')
        if fmt not in AUDIO_FORMATS:raise ValueError('不正な音声形式です')
        scenario_data={sc['key']:sc for sc in s['scenarios']}
        if len(set(v['voiceId'] for v in s['voices']))!=len(s['voices']):
            raise ValueError('ボイスIDが重複しています')
        maps={};files=[];missing=[]
        for v in s['voices']:
            rel=_game_audio_relative(v,fmt)
            # Game maps always reference the chosen export format, not the source WAV.
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
            z.writestr('id_remaps.json',json.dumps(s.get('lastIdRemaps',[]),ensure_ascii=False,indent=2))
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
        return {'total':len(s['voices']),'audio':len(files),'missing':len(missing),'format':fmt}


# Game-specific voice configurations share the VoiceDrama TTS worker and
# model names, but live entirely inside a game project and have private refs.
GAME_TTS_MODELS = {
    'irodori': (
        'Aratako/Irodori-TTS-v4.1-Small',
        'Aratako/Irodori-TTS-v4-Large',
        'Aratako/Irodori-TTS-v4-Large-Quantized/int8-weight-only',
        'Aratako/Irodori-TTS-v4-Large-Quantized/int4-weight-only',
        'phasefield-audio/Irodori-TTS-v4.1-Anime',
        'phasefield-audio/Irodori-TTS-v4.1-Anime/int8-weight-only',
    ),
    'qwen': (
        'Qwen/Qwen3-TTS-12Hz-1.7B-Base',
        'Qwen/Qwen3-TTS-12Hz-0.6B-Base',
        'Qwen/Qwen3-TTS-12Hz-1.7B-VoiceDesign',
    ),
}
REFERENCE_EXTENSIONS=frozenset({'.wav','.mp3','.flac','.ogg','.m4a','.aac','.opus'})
MAX_REFERENCE_BYTES=20*1024*1024


def default_local_cast(speaker):
    return {'name':speaker,'engine':'irodori','model':GAME_TTS_MODELS['irodori'][0],
            'mode':'clone','language':'Japanese','description':'','refText':'','refs':[]}


def _validate_local_cast(spec, assets):
    if not isinstance(spec,dict):raise ValueError('キャストの設定が正しくありません')
    engine=spec.get('engine','irodori')
    if engine not in GAME_TTS_MODELS:raise ValueError('TTSエンジンが不正です')
    mode=spec.get('mode','clone')
    if mode not in ('clone','design'):raise ValueError('生成方式が不正です')
    model=spec.get('model') or GAME_TTS_MODELS[engine][0]
    if model not in GAME_TTS_MODELS[engine]:raise ValueError('未対応の音声モデルです')
    if engine=='qwen' and (('VoiceDesign' in model) != (mode=='design')):
        raise ValueError('Qwen Baseは参照音声方式、VoiceDesignはVoice Design方式を選択してください')
    language=spec.get('language','Japanese')
    if language not in ('Japanese','English','Chinese','Auto'):raise ValueError('言語が不正です')
    desc=spec.get('description','');reftext=spec.get('refText','')
    if not isinstance(desc,str) or len(desc)>2000 or not isinstance(reftext,str) or len(reftext)>4000:
        raise ValueError('声の説明・参照文字起こしが長すぎます')
    refs=spec.get('refs',[])
    if not isinstance(refs,list) or len(refs)>10 or len(refs)!=len(set(refs)):
        raise ValueError('参照音声は最大10本です')
    known={a['id'] for a in assets}
    if any(not isinstance(x,str) or x not in known for x in refs):
        raise ValueError('ゲーム音声の参照音声IDが不正です')
    return {'name':str(spec.get('name',''))[:128],'engine':engine,'model':model,
            'mode':mode,'language':language,'description':desc,'refText':reftext,'refs':refs}


def _game_ref_path(pid, asset):
    filename=str(asset.get('filename',''))
    if not re.fullmatch(r'ref_[0-9a-f]{16}\.(?:wav|mp3|flac|ogg|m4a|aac|opus)',filename):
        raise ValueError('参照音声ファイル名が不正です')
    return _folder(pid)/'reference_audio'/filename


def _mark_voice_takes_stale_for_cast(state, speaker):
    # A changed cast setting must not silently export speech created with an
    # older model/reference. Keep all audio files, but require regeneration.
    for voice in state.get('voices',[]):
        canonical,_=_speaker_profile(state,voice['speaker'])
        if (speaker==voice['speaker'] or speaker==canonical) and voice.get('audio'):
            voice['stale']=True


def upload_game_reference(pid,speaker,filename,body,revision):
    if not isinstance(body,bytes) or not body or len(body)>MAX_REFERENCE_BYTES:
        raise ValueError('参照音声は20MB以内のファイルを指定してください')
    basename=Path(str(filename).replace('\\','/')).name
    ext=Path(basename).suffix.lower()
    if ext not in REFERENCE_EXTENSIONS:raise ValueError('WAV / MP3 / FLAC / OGG / M4A / AAC / OPUSのみ対応')
    speaker=_clean_name(speaker)
    with GAME_LOCK:
        s=_load(pid)
        if s['revision']!=revision:raise RuntimeError('編集の競合を検出しました')
        if speaker not in s.get('localCasts',{}):raise ValueError('先に「ゲーム専用の声設定」を有効にしてください')
        if len(s.get('voiceAssets',[]))>=250:raise ValueError('参照音声は250本以内です')
        if len(s['localCasts'][speaker].get('refs',[]))>=10:
            raise ValueError('1キャラクターに登録できる参照音声は10本までです')
        import io
        from render import duration_of
        aid='ref_'+uuid.uuid4().hex[:16]
        asset={'id':aid,'filename':aid+ext,'name':basename[:160]}
        target=_game_ref_path(pid,asset);target.parent.mkdir(parents=True,exist_ok=True)
        try:
            target.write_bytes(body)
            duration=duration_of(target)
            asset['duration']=round(duration,3)
            s.setdefault('voiceAssets',[]).append(asset)
            cast=s['localCasts'][speaker]
            cast.setdefault('refs',[]).append(aid)
            _mark_voice_takes_stale_for_cast(s,speaker)
            _save(pid,s,revision)
        except Exception:
            target.unlink(missing_ok=True)
            raise
        return _load(pid)


def remove_game_reference(pid,speaker,aid,revision):
    speaker=_clean_name(speaker)
    with GAME_LOCK:
        s=_load(pid)
        if s['revision']!=revision:raise RuntimeError('編集の競合を検出しました')
        cast=s.get('localCasts',{}).get(speaker)
        asset=next((a for a in s.get('voiceAssets',[]) if a['id']==aid),None)
        if not cast or not asset or aid not in cast.get('refs',[]):
            raise ValueError('このキャラクターの参照音声が見つかりません')
        cast['refs'].remove(aid)
        _mark_voice_takes_stale_for_cast(s,speaker)
        # Other local casts may share the same ID. Keep the underlying audio.
        still_used=any(aid in c.get('refs',[]) for c in s['localCasts'].values())
        if not still_used:
            s['voiceAssets']=[a for a in s['voiceAssets'] if a['id']!=aid]
        result=_save(pid,s,revision)
        if not still_used:_game_ref_path(pid,asset).unlink(missing_ok=True)
        return result


def design_game_reference(pid,speaker,text,job):
    try:
        import app as studio
        speaker=_clean_name(speaker)
        if not 1<=len(text.strip())<=500:raise ValueError('サンプル台詞は1〜500文字です')
        with GAME_LOCK:
            s=_load(pid)
            cast=s.get('localCasts',{}).get(speaker)
            if not cast:raise ValueError('ゲーム専用の声設定を登録してください')
            cast=_validate_local_cast(cast,s.get('voiceAssets',[]))
        engine=cast['engine']
        model=('Qwen/Qwen3-TTS-12Hz-1.7B-VoiceDesign' if engine=='qwen' else cast['model'])
        job['total']=1;job['message']=f'{speaker} の参照音声を作成中'
        with tempfile.TemporaryDirectory(prefix='vds_game_design_') as temp:
            folder=Path(temp);output=folder/'sample.wav';reply=folder/'response.json'
            payload={'engine':engine,'model':model,
              'irodori_dir':core.load_global_settings()['irodori_dir'],
              'items':[{'id':speaker,'text':text.strip(),
                'caption':cast['description'] or '自然で聞き取りやすい話し方',
                'description':cast['description'],'mode':'design','refs':[],
                'language':cast['language'],'num_steps':40,'output':str(output)}]}
            request=folder/'request.json';request.write_text(json.dumps(payload,ensure_ascii=False),encoding='utf8')
            cp=subprocess.run([studio.python_for(engine,None),str(core.ROOT/'tts_worker.py'),str(request),str(reply)],
                 cwd=str(core.ROOT),capture_output=True,text=True,encoding='utf8',errors='replace',timeout=1200)
            if not reply.exists():raise RuntimeError('Voice Design応答なし: '+(cp.stderr+cp.stdout)[-500:])
            result=json.loads(reply.read_text(encoding='utf8'))
            if not result.get('ok'):raise RuntimeError(result.get('error','生成失敗'))
            item=result['items'][0]
            if not item.get('ok'):raise RuntimeError(item.get('error','生成失敗'))
            if not output.is_file():raise RuntimeError('音声が生成されませんでした')
            with GAME_LOCK:
                latest=_load(pid)
                current=latest.get('localCasts',{}).get(speaker)
                if not current:raise ValueError('生成中にキャストが削除されました')
                aid='ref_'+uuid.uuid4().hex[:16]
                asset={'id':aid,'filename':aid+'.wav','name':f'{speaker} - Voice Design.wav'}
                dest=_game_ref_path(pid,asset);dest.parent.mkdir(parents=True,exist_ok=True)
                shutil.copy2(output,dest)
                try:
                    from render import duration_of
                    asset['duration']=round(duration_of(dest),3)
                    latest.setdefault('voiceAssets',[]).append(asset)
                    current.setdefault('refs',[]).append(aid)
                    current['refText']=text.strip();current['mode']='clone'
                    if engine=='qwen':current['model']='Qwen/Qwen3-TTS-12Hz-1.7B-Base'
                    _mark_voice_takes_stale_for_cast(latest,speaker)
                    _save(pid,latest,latest['revision'])
                except Exception:
                    dest.unlink(missing_ok=True);raise
            job['referenceAssetId']=aid;job['completed']=1
        job['status']='done';job['message']='参照音声を生成し、ゲーム専用キャストに登録しました'
    except Exception as exc:
        job['status']='error';job['message']=str(exc)


def generation_groups(pid,ids):
    s=_load(pid)
    if s.get('pendingKS'):
        raise ValueError('仮登録中のKSがあります。先にボイスIDを一括確定してください')
    targets=[v for v in s['voices'] if v['voiceId'] in ids]
    plans=[]
    linked_projects={}
    linked_assets={}
    local_assets={a['id']:a for a in s.get('voiceAssets',[])}
    for v in targets:
        sheet_id,_=_speaker_profile(s,v['speaker'])
        # Local game-specific cast overrides a VoiceDrama shared cast. Aliases
        # such as #arther resolve to canonical arthur_king local settings.
        active_local=(s.get('localCastMode',{}).get(v['speaker'])=='local'
                      or bool(sheet_id and s.get('localCastMode',{}).get(sheet_id)=='local'))
        local=(s.get('localCasts',{}).get(v['speaker'])
               or (s.get('localCasts',{}).get(sheet_id) if sheet_id else None)) if active_local else None
        if active_local and local is None:
            raise ValueError(f'{v["speaker"]} のゲーム専用設定がありません')
        if local is not None:
            cast=_validate_local_cast(local,s.get('voiceAssets',[]))
            engine=cast['engine'];model=cast['model']
            refs=[]
            for aid in cast['refs']:
                p=_game_ref_path(pid,local_assets[aid])
                if not p.is_file():raise ValueError(f'{v["speaker"]}の参照音声ファイルがありません: {local_assets[aid]["name"]}')
                refs.append(str(p))
        else:
            binding=(s['bindings'].get(v['speaker'])
                     or (s['bindings'].get(sheet_id) if sheet_id else None))
            if not binding or not binding.get('castName'):
                raise ValueError(f"キャストが未設定です: {v['speaker']}")
            project_id=binding['projectId']
            if project_id not in linked_projects:
                linked_projects[project_id]=core.load_project(project_id)
                linked_assets[project_id]={a['id']:a for a in linked_projects[project_id]['assets']}
            source=linked_projects[project_id]
            cast=next((x for x in source['voices'] if x['name']==binding['castName']),None)
            if cast is None:raise ValueError('キャストが見つかりません: '+v['speaker'])
            engine=cast.get('engine','irodori')
            model=cast.get('model') or ('Qwen/Qwen3-TTS-12Hz-1.7B-Base' if engine=='qwen' else 'Aratako/Irodori-TTS-v4.1-Small')
            refs=[]
            for aid in cast.get('refs',[]):
                a=linked_assets[project_id].get(aid)
                if a:refs.append(str(core.asset_path(source['id'],a)))
        if engine=='qwen' and cast.get('mode','clone')!='design' and not refs:
            raise ValueError(f"{v['speaker']}のQwen用参照音声が登録されていません")
        plans.append((v,engine,model,cast,refs))
    return plans


def generate_game_audio(pid,voice_ids,steps,job):
    """Sequential bounded TTS batches; track whole-project progress without data loss."""
    try:
        import app as studio
        if not isinstance(voice_ids,list) or not 1<=len(voice_ids)<=10000:
            raise ValueError('生成対象は1〜10000件です')
        with GAME_LOCK:
            groups=generation_groups(pid,set(voice_ids))
            current_state=_load(pid)
        if not groups:raise ValueError('生成する台詞がありません')
        buckets=defaultdict(list)
        for v,engine,model,cast,refs in groups:
            buckets[(engine,model)].append((v,cast,refs))
        job['total']=len(groups)
        batch_size=40
        for (engine,model),all_items in buckets.items():
            if job.get('cancel'):break
            interpreter=studio.python_for(engine,None)
            for offset in range(0,len(all_items),batch_size):
                if job.get('cancel'):break
                items=all_items[offset:offset+batch_size]
                with tempfile.TemporaryDirectory(prefix='vds_game_tts_') as temp:
                    d=Path(temp)
                    request=[]
                    for v,cast,refs in items:
                        pronunciation_text,prosody_notes=_speech_inputs(current_state,v)
                        base_caption=v['caption'] or cast.get('description','')
                        caption=(base_caption+'\n'+prosody_notes).strip() if prosody_notes else base_caption
                        request.append({'id':v['voiceId'],'text':pronunciation_text,'caption':caption,
                             'description':cast.get('description',''),'refs':refs,'mode':cast.get('mode','clone'),
                             'language':cast.get('language','Japanese'),'ref_text':cast.get('refText',''),
                             'num_steps':steps,'output':str(d/(v['voiceId']+'.wav')),
                             'schedule':'linear','sway_coeff':-1.0})
                    job_file=d/'request.json';result_file=d/'result.json'
                    job_file.write_text(json.dumps({'engine':engine,'model':model,
                        'irodori_dir':core.load_global_settings()['irodori_dir'],
                        'items':request},ensure_ascii=False),encoding='utf8')
                    job['message']=f'{model}: {job["completed"]}/{job["total"]}件完了・次の{len(items)}件を生成中'
                    cp=subprocess.run([interpreter,str(core.ROOT/'tts_worker.py'),str(job_file),str(result_file)],
                        cwd=str(core.ROOT),capture_output=True,text=True,encoding='utf-8',
                        errors='replace',timeout=3600)
                    if not result_file.exists():
                        raise RuntimeError('TTSの応答がありません: '+(cp.stderr+cp.stdout)[-1000:])
                    result=json.loads(result_file.read_text(encoding='utf8'))
                    if not result.get('ok'):raise RuntimeError(result.get('error','TTS失敗'))
                    rows={row.get('id'):row for row in result.get('items',[])}
                    for v,_,_ in items:
                        item=rows.get(v['voiceId'])
                        if item and item.get('ok') and Path(item.get('output','')).is_file():
                            try:
                                with GAME_LOCK:
                                    state=_load(pid)
                                    target=next((line for line in state['voices'] if line['voiceId']==v['voiceId']),None)
                                    if target:
                                        rel=_game_audio_relative(target,'wav')
                                        dest=_folder(pid)/'audio'/rel
                                        dest.parent.mkdir(parents=True,exist_ok=True)
                                        staging=Path(str(dest)+'.tmp')
                                        try:
                                            shutil.copy2(item['output'],staging)
                                            os.replace(staging,dest)
                                        finally:
                                            staging.unlink(missing_ok=True)
                                        target['audio']=rel
                                        # A changed script should remain marked as needing regeneration.
                                        target['stale']=bool(target.get('ttsText')!=v.get('ttsText')
                                            or target.get('caption')!=v.get('caption'))
                                        _save(pid,state,state['revision'])
                                    else:
                                        job['errors'].append({'id':v['voiceId'],'error':'台詞が見つかりません'})
                            except (OSError,ValueError,RuntimeError) as exc:
                                job['errors'].append({'id':v['voiceId'],'error':str(exc)})
                        else:
                            job['errors'].append({'id':v['voiceId'],
                                'error':item.get('error','生成結果がありません') if item else '生成結果がありません'})
                        job['completed']+=1
        job['status']='cancelled' if job.get('cancel') else ('done_with_errors' if job['errors'] else 'done')
        job['message']='生成を停止しました' if job.get('cancel') else '音声生成が完了しました'
    except Exception as exc:
        job['status']='error';job['message']=str(exc)
