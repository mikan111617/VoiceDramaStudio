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


def caption_sheet_rows(content):
    import csv
    reader=csv.DictReader(io.StringIO(content.decode('utf-8-sig'),newline=''))
    return list(reader)


def encode_caption_rows(rows, encoding='utf-8-sig'):
    import csv
    output=io.StringIO(newline='')
    writer=csv.DictWriter(output,fieldnames=['ボイスID','シナリオ','キャラ名','セリフ','キャプション'],lineterminator='\r\n')
    writer.writeheader();writer.writerows(rows)
    return output.getvalue().encode(encoding)


def send_caption_sheet(client, s, content, action='import'):
    return client.post(f'/api/game/projects/{s["id"]}/captions/{action}?expectedRevision={s["revision"]}',
                       files={'file':('game_captions.csv',content,'text/csv')})


def test_caption_csv_export_import_changes_captions_only_and_marks_audio_stale(client):
    s=new_game(client)
    s=ks_file(client,s,'#真歩流\n「サブレが、ない！」[p]\n#愛理\n「そうなの？\n」[p]\n').json()
    first=s['voices'][0];second=s['voices'][1]
    loaded=game._load(s['id'])
    loaded['voices'][0]['audio']='真歩流/scene1/'+first['voiceId']+'.wav'
    loaded['voices'][0]['caption']='もともとの演技指示'
    game._write(s['id'],loaded)
    s=game.get_game(s['id'])
    original=scenarios_before=s['scenarios']
    data=client.get(f'/api/game/projects/{s["id"]}/captions.csv')
    assert data.status_code==200 and data.content.startswith(b'\xef\xbb\xbf')
    assert 'attachment' in data.headers['content-disposition']
    rows=caption_sheet_rows(data.content)
    assert len(rows)==2
    assert list(rows[0])==['ボイスID','シナリオ','キャラ名','セリフ','キャプション']
    assert rows[0]['ボイスID']==first['voiceId']
    assert rows[0]['キャプション']=='もともとの演技指示'
    rows[0]['キャプション']='困惑しながら、声を大きく上げる'
    rows[1]['キャプション']=''
    encoded=encode_caption_rows(rows)
    preview=send_caption_sheet(client,s,encoded,'preview')
    assert preview.status_code==200,preview.text
    assert preview.json()['rows']==2 and preview.json()['updated']==1 and preview.json()['blank']==1
    # Preview does not save anything.
    assert game.get_game(s['id'])['revision']==s['revision']
    result=send_caption_sheet(client,s,encoded)
    assert result.status_code==200,result.text
    newer=result.json()['project'];voices=newer['voices']
    assert voices[0]['caption']=='困惑しながら、声を大きく上げる'
    assert voices[0]['stale'] is True
    assert voices[0]['audio']==loaded['voices'][0]['audio']
    assert voices[1]['caption']==''
    assert voices[1]['ttsText']==second['ttsText']
    assert newer['scenarios']==original and [v['voiceId'] for v in voices]==[first['voiceId'],second['voiceId']]
    assert newer['revision']==s['revision']+1
    again=send_caption_sheet(client,newer,encoded)
    assert again.status_code==200
    assert again.json()['project']['revision']==newer['revision'] # No revision for no-op


def test_caption_csv_exports_current_scenario_or_all(client):
    s=new_game(client)
    s=ks_file(client,s,'#mahoru\nひとつめ。[p]\n',name='scene1.ks').json()
    s=ks_file(client,s,'#airi\nふたつめ。[p]\n',name='scene2.ks').json()
    base=f'/api/game/projects/{s["id"]}/captions.csv'
    assert len(caption_sheet_rows(client.get(base).content))==2
    one=caption_sheet_rows(client.get(base+'?scenario=scene2').content)
    assert len(one)==1 and one[0]['シナリオ']=='scene2'
    assert client.get(base+'?scenario=missing').status_code==400
    one[0]['キャプション']='強く言い切る'
    res=send_caption_sheet(client,s,encode_caption_rows(one))
    assert res.status_code==200
    captions={v['scenario']:v['caption'] for v in res.json()['project']['voices']}
    assert captions=={'scene1':'','scene2':'強く言い切る'}


@pytest.mark.parametrize('mutation',[
    lambda a: a[0].update({'ボイスID':'v99999'}),
    lambda a: a[0].update({'キャラ名':'変更したキャラ'}),
    lambda a: a[0].update({'セリフ':'違う台詞'}),
    lambda a: a[0].update({'シナリオ':'another'}),
    lambda a: a[1].update({'ボイスID':a[0]['ボイスID']}),
    lambda a: a[0].update({'キャプション':'a'*801}),
])
def test_caption_csv_import_rejects_bad_or_ai_rewritten_rows_atomically(client, mutation):
    s=new_game(client)
    s=ks_file(client,s,'#mahoru\nこんにちは。[p]\n#airi\nこんばんは。[p]\n').json()
    rows=caption_sheet_rows(client.get(f'/api/game/projects/{s["id"]}/captions.csv').content)
    rows[0]['キャプション']='先頭を更新する'
    rows[1]['キャプション']='2番目を更新する'
    mutation(rows)
    res=send_caption_sheet(client,s,encode_caption_rows(rows))
    assert res.status_code==400,res.text
    unchanged=game.get_game(s['id'])
    assert unchanged['revision']==s['revision']
    assert [v['caption'] for v in unchanged['voices']]==['','']


def test_caption_csv_import_revision_conflict_and_shift_jis(client):
    s=new_game(client)
    s=ks_file(client,s,'#真歩流\nこんにちは。[p]\n').json()
    rows=caption_sheet_rows(client.get(f'/api/game/projects/{s["id"]}/captions.csv').content)
    rows[0]['キャプション']='楽しげに'
    encoded=encode_caption_rows(rows,'cp932')
    assert send_caption_sheet(client,s,encoded,'preview').status_code==200
    result=send_caption_sheet(client,s,encoded)
    assert result.status_code==200,result.text
    assert result.json()['project']['voices'][0]['caption']=='楽しげに'
    assert send_caption_sheet(client,s,encoded).status_code==409


def test_caption_csv_ui_has_export_import_and_copy_prompt():
    root=Path(__file__).resolve().parents[1]
    html=(root/'static'/'game.html').read_text(encoding='utf-8')
    js=(root/'static'/'game.js').read_text(encoding='utf-8')
    for elem in ('exportCaptionsScene','exportCaptionsAll','importCaptions','captionPicker','copyCaptionPrompt'):
        assert 'id="'+elem+'"' in html
    assert '/captions/preview' in js and '/captions/import' in js
    assert 'navigator.clipboard.writeText' in js
    assert '/captions.csv' in js
    assert 'game.js?v=031' in html


def test_ks_reimport_keeps_explicit_ids_when_speakers_reordered_and_text_changed(client):
    state=new_game(client)
    original=('#mahoru\n[voice id="v00211"]\n初めの台詞。[p]\n'
              '#airi\n[voice id="v00212"]\n二つ目。[p]\n')
    saved=ks_file(client,state,original)
    assert saved.status_code==200,saved.text
    state=saved.json()
    result=client.put(f'/api/game/projects/{state["id"]}',json={
        'expectedRevision':state['revision'],
        'changes':{'voices':[{'voiceId':'v00211','caption':'穏やかに話す'}]}})
    assert result.status_code==200,result.text
    state=result.json()
    reordered=('#airi\n[voice id="v00212"]\n二つ目！[p]\n'
               '#mahoru\n[voice id="v00211"]\n初めの台詞も変わった。[p]\n'
               '#mahoru\n追加の台詞。[p]\n')
    result=ks_file(client,state,reordered)
    assert result.status_code==200,result.text
    fresh=result.json()
    assert [v['voiceId'] for v in fresh['voices']]==['v00212','v00211','v00213']
    assert next(v for v in fresh['voices'] if v['voiceId']=='v00211')['caption']=='穏やかに話す'
    assert next(v for v in fresh['voices'] if v['voiceId']=='v00211')['ttsText']=='初めの台詞も変わった。'


def test_ks_accepts_scenario_larger_than_old_five_megabyte_cap(client):
    state=new_game(client)
    source=';'+'注'*2_200_000+'\n#mahoru\nこんにちは。[p]\n'
    assert len(source.encode('utf8'))>5*1024*1024
    assert len(source.encode('utf8'))<20*1024*1024
    result=ks_file(client,state,source,name='long_scene.ks')
    assert result.status_code==200,result.text[:500]
    assert result.json()['voices'][0]['text']=='こんにちは。'


def test_ks_import_reports_file_and_line_and_rejects_oversized(client):
    state=new_game(client)
    bad=ks_file(client,state,'#mahoru\nこんにちは。[p]またね。[p]',name='scene7.ks')
    assert bad.status_code==400,bad.text
    assert 'scene7.ks' in bad.json()['detail']
    assert '2行目' in bad.json()['detail']
    oversized=client.post(f'/api/game/projects/{state["id"]}/import-ks?expectedRevision={state["revision"]}',
         files=[('files',('huge.ks',b'0'*(20*1024*1024+1),'text/plain'))])
    assert oversized.status_code==400
    assert 'huge.ks' in oversized.json()['detail']
    assert len(client.get(f'/api/game/projects/{state["id"]}').json()['voices'])==0


def test_game_ks_ui_shows_persistent_import_error_and_larger_limit():
    root=Path(__file__).resolve().parents[1]
    js=(root/'static'/'game.js').read_text(encoding='utf8')
    html=(root/'static'/'game.html').read_text(encoding='utf8')
    assert 'id="importError"' in html
    assert 'showImportError(e.message)' in js
    assert '20*1024*1024' in js
    assert 'game.js?v=031' in html


def test_ks_reserves_explicit_id_before_reusing_old_id(client):
    s=new_game(client)
    # A previously imported ID (v03812) was associated with an earlier line.
    # The new KS explicitly assigns it to a *later* line, while the earlier
    # untagged dialogue remains eligible for SequenceMatcher reuse.
    original='#eruku\n以前の台詞。[p]\n#eruku\n後ろの台詞。[p]\n'
    res=ks_file(client,s,original,name='scene4.ks')
    assert res.status_code==200,res.text
    s=res.json()
    id_early=s['voices'][0]['voiceId']
    id_late=s['voices'][1]['voiceId']
    # Simulate an old game workspace that recorded v03812 on the earlier line.
    state=game.get_game(s['id'])
    state['voices'][0]['voiceId']='v03812'
    state['voices'][1]['voiceId']=id_late
    state['lastIssued']=3958
    game._write(s['id'],state)
    changed=('#eruku\n以前の台詞。[p]\n'
             '#eruku\n[voice id="v03812"]\n新しく書いた台詞。[p]\n')
    res=ks_file(client,state,changed,name='scene4.ks')
    assert res.status_code==200,res.text
    newer=res.json()
    assert newer['voices'][0]['voiceId']=='v03959'
    assert newer['voices'][1]['voiceId']=='v03812'
    assert newer['voices'][1]['text']=='新しく書いた台詞。'
    assert len({v['voiceId'] for v in newer['voices']})==2


def test_ks_explicit_id_elsewhere_is_not_reused_and_reports_owner(client):
    s=new_game(client)
    s=ks_file(client,s,'#mahoru\n[voice id="v03812"]\n既に使っている。[p]\n',name='scene3.ks').json()
    res=ks_file(client,s,'#eruku\n[voice id="v03812"]\n新しい台詞。[p]\n',name='scene4.ks')
    assert res.status_code==400
    assert 'scene4.ks' in res.json()['detail']
    assert 'scene3' in res.json()['detail']
    assert 'v03812' in res.json()['detail']
    assert game.get_game(s['id'])['revision']==s['revision']


def test_ks_duplicate_tag_reports_two_line_numbers(client):
    s=new_game(client)
    text=('#eruku\n[voice id="v03812"]\n初め。[p]\n'
          '#eruku\n[voice id="v03812"]\n次。[p]\n')
    res=ks_file(client,s,text,name='scene4.ks')
    assert res.status_code==400
    assert '2行目' in res.json()['detail']
    assert '5行目' in res.json()['detail']


def test_ks_explicit_id_does_not_reuse_unrelated_caption_or_audio(client):
    s=new_game(client)
    s=ks_file(client,s,'#eruku\n最初。[p]\n#eruku\n次。[p]\n',name='scene4.ks').json()
    previous=s['voices'][1]['voiceId']
    first=s['voices'][0]['voiceId']
    change={'voices':[{'voiceId':first,'caption':'元の第一台詞の演技指示'}]}
    result=client.put(f'/api/game/projects/{s["id"]}',json={'expectedRevision':s['revision'],'changes':change})
    assert result.status_code==200
    s=result.json()
    new_source='#eruku\n[voice id="v03812"]\n最初。[p]\n#eruku\n次。[p]\n'
    result=ks_file(client,s,new_source,name='scene4.ks')
    assert result.status_code==200,result.text
    newer=result.json()
    assert newer['voices'][0]['voiceId']=='v03812'
    assert newer['voices'][0]['caption']==''  # Never inherit first voice's caption.
    assert newer['voices'][1]['voiceId']==previous
