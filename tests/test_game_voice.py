"""Regression tests for the independent game-voice production workbench."""
import io
import json
import sys
from pathlib import Path
from zipfile import ZipFile

import numpy as np
import pytest
import soundfile as sf
from fastapi.testclient import TestClient
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import core
import game_voice as game
from app import app

@pytest.fixture
def client(tmp_path,monkeypatch):
    monkeypatch.setattr(core,'WORKSPACE',tmp_path)
    return TestClient(app)

def new_game(client):
    result=client.post('/api/game/projects',json={'title':'舞黒館ゲーム音声'})
    assert result.status_code==200,result.text
    return result.json()

def ks_file(client,s,body,name='scene1.ks'):
    return client.post(f'/api/game/projects/{s["id"]}/import-ks?expectedRevision={s["revision"]}',
        files=[('files',(name,body.encode('utf8'),'text/plain'))])

def short_wav():
    buf=io.BytesIO()
    sf.write(buf,np.zeros(16000,dtype='float32'),16000,format='WAV')
    return buf.getvalue()


def test_game_voice_tab_and_separate_storage(client):
    s=new_game(client)
    assert s['id'].startswith('g_')
    assert (core.WORKSPACE/'game_voice'/s['id']/'game.json').exists()
    assert client.get('/game').status_code==200
    assert b'GAME VOICE' in client.get('/game').content
    assert 'ゲーム音声' in client.get('/').text
    assert (core.WORKSPACE / 'game_voice' / s['id']).parent.name == 'game_voice'
    assert client.get('/api/game/projects').json()[0]['id']==s['id']


def test_voice_ids_are_permanent_and_original_ks_is_unmodified(client):
    s=new_game(client)
    req=client.post(f'/api/game/projects/{s["id"]}/voice-id-state?expectedRevision={s["revision"]}',
        files={'file':('state.json',json.dumps({'last_issued':3958}).encode(),'application/json')})
    assert req.status_code==200,req.text
    s=req.json()
    orig='*start\n#mahoru\n「おはよう！」[p]\n#airi\n知らないわ。[p]\n'
    r=ks_file(client,s,orig)
    assert r.status_code==200,r.text
    s=r.json();assert [v['voiceId'] for v in s['voices']]==['v03959','v03960']
    assert s['scenarios'][0]['content']==orig
    assert client.get('/api/projects').json()==[]
    buf=client.get(f'/api/game/projects/{s["id"]}/export.zip')
    assert buf.status_code==200,buf.text[:100]
    with ZipFile(io.BytesIO(buf.content)) as z:
        script=z.read('voiced_scenarios/scene1.ks').decode()
        assert script.count('[voice id="v03959"]')==1
        assert '[voice id="v03960"]' in script
        assert '#mahoru' in script and '「おはよう！」[p]' in script
        assert z.namelist().count('voice_map.json')==1
        assert json.loads(z.read('.voice_id_state.json'))['last_issued']==3960
        assert json.loads(z.read('missing_voices.json'))==['v03959','v03960']
        assert json.loads(z.read('voice_maps/scene1.json'))['v03959']=='voice/mahoru/scene1/v03959.wav'
    # Re-import with added line: exact matches keep IDs.
    added='*start\n#mahoru\n「おはよう！」[p]\n#airi\n知らないわ。[p]\n#mahoru\nここはどこ？[p]\n'
    r=ks_file(client,s,added)
    assert r.status_code==200,r.text
    s=r.json()
    assert [v['voiceId'] for v in s['voices']]==['v03959','v03960','v03961']
    changed='*start\n#mahoru\n「おはよう！！」[p]\n#airi\n知らないわ。[p]\n#mahoru\nここはどこ？[p]\n'
    s=ks_file(client,s,changed).json()
    assert s['voices'][0]['voiceId']=='v03959' # text edit keeps ID


def test_ks_supports_existing_ids_script_blocks_and_shiftjis(client):
    s=new_game(client)
    original='[iscript]\n#ignored\nJavaScript variable\n[endscript]\n*hall\n#mary\n[voice id="v00123"]\n「こんにちは」[l]\n'
    bs=original.replace('\n','\r\n').encode('cp932')
    r=client.post(f'/api/game/projects/{s["id"]}/import-ks?expectedRevision={s["revision"]}',
        files=[('files',('scene2.ks',bs,'text/plain'))])
    assert r.status_code==200,r.text
    s=r.json();assert len(s['voices'])==1 and s['voices'][0]['voiceId']=='v00123'
    raw=client.get(f'/api/game/projects/{s["id"]}/export.zip')
    with ZipFile(io.BytesIO(raw.content)) as z:
        expected=z.read('voiced_scenarios/scene2.ks')
        assert expected.count('v00123'.encode())==1
        assert expected==bs


def test_voice_list_import_keeps_game_filename_ids(client):
    s=new_game(client)
    # Mini fixture preserves the uploaded voice-list schema, without shipping
    # private game dialogue or relying on /mnt/data being available on Windows.
    sample={'batch_name':'spinoff_en_all','engine':'qwen3',
        'output_dir':'../detective_minigame/data/voice_en','output_format':'wav',
        'path_template':'{speaker}/{id}.{ext}','speakers':[
            {'speaker':'mahoru' if i==0 else f'other{i}',
             'items':[{'id':'doubt_0' if i==0 and j==0 else f'place_calm_{j}',
                       'text':f'Hello {i}-{j}'} for j in range(11 if i<6 else 10)]}
            for i in range(13)]}
    
    r=client.post(f'/api/game/projects/{s["id"]}/voice-list?expectedRevision={s["revision"]}',
        files={'file':('voices.json',json.dumps(sample).encode('utf8'),'application/json')})
    assert r.status_code==200,r.text
    s=r.json();assert len(s['voices'])==136
    assert len({v['voiceId'] for v in s['voices']})==136
    mahoru=next(v for v in s['voices'] if v['speaker']=='mahoru')
    assert mahoru['sourceId']=='doubt_0'
    assert game._game_audio_relative(mahoru)=="mahoru/doubt_0.wav"
    r=client.get(f'/api/game/projects/{s["id"]}/export.zip')
    assert r.status_code==200,r.text[:80]
    with ZipFile(io.BytesIO(r.content)) as z:
        manifest=json.loads(z.read('voice_map.json'))
        assert manifest[mahoru['voiceId']]=='voice_en/mahoru/doubt_0.wav'
        assert 'voice_list/spinoff_en_all.json' in z.namelist()
        assert len(json.loads(z.read('missing_voices.json')))==136


def test_character_sheet_and_id_state_import(client):
    s=new_game(client)
    data=json.dumps({'meta':{'title':'sample'},'characters':{
        'mahoru':{'display_name':'真白真歩流','reading':'ましろ まほる',
                  'aliases':['真歩流'],'baseline_tone':'元気で親しみやすい'}}},
         ensure_ascii=False).encode('utf8')
    r=client.post(f'/api/game/projects/{s["id"]}/character-sheet?expectedRevision={s["revision"]}',
       files={'file':('sheet.json',data,'application/json')})
    assert r.status_code==200,r.text
    s=r.json()
    assert 'mahoru' in s['characterSheet']
    assert s['characterSheet']['mahoru']['reading']=='ましろ まほる'



def test_ks_bad_format_or_duplicate_is_atomic(client):
    s=new_game(client)
    form={'files':('normal.ks',b'#mahoru\nHello[p]Hi[p]', 'text/plain')}
    r=client.post(f'/api/game/projects/{s["id"]}/import-ks?expectedRevision={s["revision"]}',files=form)
    assert r.status_code==400
    after=client.get('/api/game/projects/'+s['id']).json()
    assert after['voices']==[] and after['revision']==s['revision']
    old=ks_file(client,s,'#mahoru\nおはよう。[p]\n')
    assert old.status_code==200
    r=ks_file(client,s,'#mahoru\nおはよう。[p]\n')
    assert r.status_code==409


def test_cast_binding_tts_wav_and_zip(client,monkeypatch):
    s=new_game(client)
    s=ks_file(client,s,'#mahoru\nこんにちは。[p]\n').json()
    p=client.post('/api/projects',json={'title':'キャストの作品'}).json()
    aid=client.post(f'/api/projects/{p["id"]}/assets',files={'file':('sample.wav',short_wav(),'audio/wav')},data={'kind':'reference'}).json()['asset']['id']
    p=client.get(f'/api/projects/{p["id"]}').json()
    p['voices'].append({'name':'真歩流','engine':'irodori','model':'phasefield-audio/Irodori-TTS-v4.1-Anime',
                       'mode':'clone','language':'Japanese','refs':[aid]})
    p=client.put(f'/api/projects/{p["id"]}',json={'state':p,'expectedRevision':p['revision']}).json()
    r=client.put(f'/api/game/projects/{s["id"]}',json={'expectedRevision':s['revision'],
        'changes':{'bindings':{'mahoru':{'projectId':p['id'],'castName':'真歩流'}}}})
    assert r.status_code==200,r.text
    s=r.json()
    plan=game.generation_groups(s['id'],{s['voices'][0]['voiceId']})
    assert plan[0][2].endswith('Anime')
    import app as studio
    monkeypatch.setattr(studio,'python_for',lambda engine,state:sys.executable)
    class CP:returncode=0;stdout='';stderr=''
    def fake(args,**kwargs):
        request=json.loads(Path(args[2]).read_text(encoding='utf8'))
        assert request['model'].endswith('Anime')
        assert request['items'][0]['refs']
        for item in request['items']:
            Path(item['output']).write_bytes(short_wav())
        Path(args[3]).write_text(json.dumps({'ok':True,'items':[{'ok':True,'id':x['id'],'output':x['output']} for x in request['items']]}),encoding='utf8')
        return CP()
    monkeypatch.setattr(game.subprocess,'run',fake)
    job={'status':'running','completed':0,'total':0,'message':'','errors':[]}
    game.generate_game_audio(s['id'],[s['voices'][0]['voiceId']],40,job)
    assert job['status']=='done',job
    voice=client.get('/api/game/projects/'+s['id']).json()['voices'][0]
    assert voice['audio']=='mahoru/scene1/'+voice['voiceId']+'.wav'
    assert client.get(f'/api/game/projects/{s["id"]}/audio/{voice["voiceId"]}').status_code==200
    package=client.get(f'/api/game/projects/{s["id"]}/export.zip')
    with ZipFile(io.BytesIO(package.content)) as z:
        assert 'audio/'+voice['audio'] in z.namelist()
        assert json.loads(z.read('missing_voices.json'))==[]
    # Caption updates mark existing audio stale, and export doesn't ship stale files.
    old=client.get(f'/api/game/projects/{s["id"]}').json()
    resp=client.put(f'/api/game/projects/{s["id"]}',json={'expectedRevision':old['revision'],
        'changes':{'voices':[{'voiceId':voice['voiceId'],'caption':'焦り気味で'}]}})
    assert resp.status_code==200
    revised=resp.json()['voices'][0]
    assert revised['stale'] is True
    with ZipFile(io.BytesIO(client.get(f'/api/game/projects/{s["id"]}/export.zip').content)) as z:
        assert 'audio/'+revised['audio'] not in z.namelist()


def test_game_ui_contains_separate_tab_controls():
    base=Path(__file__).resolve().parents[1]
    html=(base/'static'/'game.html').read_text(encoding='utf8')
    js=(base/'static'/'game.js').read_text(encoding='utf8')
    assert 'ボイスドラマ' in html and 'ゲーム音声' in html
    for id in ('importKs','importJson','importState','importSheet','sourceViewer','voiceList','saveLine','generateSelected','exportZip'):
        if id!='saveLine':assert f'id="{id}"' in html
        else:assert "id=\\\"saveLine\\\"" in js or 'id="saveLine"' in js
    assert 'voiceId' in js and 'castBindings' in js

def test_ollama_caption_helper_preserves_ids_and_source(client,monkeypatch):
    s=new_game(client)
    s=ks_file(client,s,'#mahoru\nこんにちは。[p]\n#airi\nおはよう。[p]\n').json()
    original=s['scenarios'][0]['content']
    ids=[v['voiceId'] for v in s['voices']]
    import urllib.request
    class FakeAnswer:
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def read(self):
            return json.dumps({'response':json.dumps({'captions':[
                {'voiceId':ids[0],'caption':'元気よく笑顔で'},
                {'voiceId':ids[1],'caption':'少し困惑しながら'}]})}).encode()
    def fake_request(req,timeout):
        body=json.loads(req.data)
        assert body['model']=='gemma4:12b'
        assert 'recent_dialogue' in body['prompt']
        return FakeAnswer()
    monkeypatch.setattr(urllib.request,'urlopen',fake_request)
    job={'status':'running','message':'','total':0,'completed':0,'errors':[]}
    game.analyze_captions(s['id'],ids,'gemma4:12b',job)
    assert job['status']=='done',job
    updated=game.get_game(s['id'])
    assert updated['scenarios'][0]['content']==original
    assert [v['voiceId'] for v in updated['voices']]==ids
    assert [v['caption'] for v in updated['voices']]==['元気よく笑顔で','少し困惑しながら']


def test_source_edit_keeps_previous_audio_as_stale(client):
    s=new_game(client)
    s=ks_file(client,s,'#mahoru\nこんにちは。[p]\n').json()
    v=s['voices'][0]
    source=s['scenarios'][0]['content']
    state=game._load(s['id'])
    state['voices'][0]['audio']='mahoru/scene1/'+v['voiceId']+'.wav'
    game._write(s['id'],state)
    updated=ks_file(client,game.get_game(s['id']),'#mahoru\nこんばんは。[p]\n')
    assert updated.status_code==200,updated.text
    voice=updated.json()['voices'][0]
    assert voice['voiceId']==v['voiceId'] and voice['stale'] is True
    assert voice['audio'].endswith(v['voiceId']+'.wav')


def test_multi_ks_rejects_cross_file_voice_id_collision_without_mutation(client):
    s=new_game(client)
    r=client.post(f'/api/game/projects/{s["id"]}/import-ks?expectedRevision={s["revision"]}',files=[
        ('files',('a.ks',b'#mahoru\n[voice id="v00007"]\nA[p]','text/plain')),
        ('files',('b.ks',b'#airi\n[voice id="v00007"]\nB[p]','text/plain'))])
    assert r.status_code==400,r.text
    loaded=game.get_game(s['id'])
    assert loaded['voices']==[] and loaded['revision']==s['revision']

def test_japanese_speaker_and_scene_names_are_supported(client):
    s=new_game(client)
    r=ks_file(client,s,'#真歩流\nサブレがない。[p]\n',name='食堂.ks')
    assert r.status_code==200,r.text
    v=r.json()['voices'][0]
    assert v['scenario']=='食堂' and v['speaker']=='真歩流'
    assert game._game_audio_relative(v)=='真歩流/食堂/'+v['voiceId']+'.wav'
