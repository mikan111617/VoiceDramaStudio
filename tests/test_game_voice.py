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
    assert 'game.js?v=036' in html


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
    assert 'game.js?v=036' in html


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


# Character sheet speakers are a whitelist for imported KS only.
def _upload_sheet(client, state, allowed=('mahoru',), reference=(), blocked=('TIPS','警察')):
    body={'characters':{x:{'display_name':{'mahoru':'真白真歩流'}.get(x,x),'aliases':['真歩流'] if x=='mahoru' else []} for x in allowed},
          'reference_characters':{x:{'display_name':x} for x in reference},
          'non_character_speakers':{x:{'direction':'音声不要'} for x in blocked}}
    return client.post(f'/api/game/projects/{state["id"]}/character-sheet?expectedRevision={state["revision"]}',
                       files={'file':('character_sheet.json',json.dumps(body,ensure_ascii=False).encode('utf8'),'application/json')})


def test_tips_excluded_without_sheet_and_no_voice_id_issued(client):
    s=new_game(client)
    s=ks_file(client,s,'#mahoru\n最初。[p]\n#TIPS\n説明文。[p]\n#airi\n次の台詞。[p]\n').json()
    assert [v['speaker'] for v in s['voices']]==['mahoru','airi']
    assert [v['voiceId'] for v in s['voices']]==['v00001','v00002']
    assert s['scenarios'][0]['ignoredSpeakers']=={'TIPS':1}
    zipped=client.get(f'/api/game/projects/{s["id"]}/export.zip')
    assert zipped.status_code==200
    with ZipFile(io.BytesIO(zipped.content)) as archive:
        script=archive.read('voiced_scenarios/scene1.ks').decode('utf8')
        assert '#TIPS\n説明文。[p]' in script
        assert script.count('[voice id=')==2


def test_sheet_filters_unknown_system_and_allows_display_names(client):
    s=new_game(client)
    response=_upload_sheet(client,s,reference=('yuduki',))
    assert response.status_code==200,response.text
    s=response.json()
    source=('#mahoru\nおはよう。[p]\n#真歩流\nまたね。[p]\n'
            '#yuduki\nごきげんよう。[p]\n#TIPS\n説明。[p]\n'
            '#警察\n待ちなさい。[p]\n#UNKNOWN\n謎の話者。[p]\n')
    response=ks_file(client,s,source)
    assert response.status_code==200,response.text
    s=response.json()
    assert [v['speaker'] for v in s['voices']]==['mahoru','真歩流','yuduki']
    assert s['scenarios'][0]['ignoredSpeakers']=={'TIPS':1,'UNKNOWN':1,'警察':1}
    assert [v['voiceId'] for v in s['voices']]==['v00001','v00002','v00003']


def test_importing_sheet_later_prunes_old_tips_preserves_actor_id_audio_and_source(client):
    s=new_game(client)
    original=('#mahoru\n声あり。[p]\n#TIPS\n説明。[p]\n#警察\n台詞。[p]\n')
    s=ks_file(client,s,original).json()
    assert [v['speaker'] for v in s['voices']]==['mahoru','警察']
    actor=s['voices'][0]['voiceId']
    # Existing workspace from older versions may still contain TIPS as a voice.
    snapshot=game.get_game(s['id'])
    snapshot['voices'].insert(1,{
        'voiceId':'v03812','speaker':'TIPS','scenario':'scene1','text':'説明。',
        'caption':'旧説明','audio':'TIPS/scene1/v03812.wav',
        'ttsText':'説明。','sourceLine':4,'sourceEnd':4,'voiceTagLine':0})
    snapshot['lastIssued']=3812
    game._write(s['id'],snapshot)
    response=_upload_sheet(client,s)
    assert response.status_code==200,response.text
    s=response.json()
    assert len(s['voices'])==1 and s['voices'][0]['voiceId']==actor
    assert s['scenarios'][0]['content']==original
    assert s['scenarios'][0]['count']==1
    assert s['scenarios'][0]['ignoredSpeakers']=={'TIPS':1,'警察':1}
    assert s['lastIssued']==3812
    # A subsequent KS reimport must preserve the real actor's ID.
    new=ks_file(client,s,original)
    assert new.status_code==200,new.text
    assert new.json()['voices'][0]['voiceId']==actor


def test_old_tips_id_not_duplicated_or_caption_transferred(client):
    s=new_game(client)
    s=ks_file(client,s,'#eruku\n[voice id="v03812"]\n最初の台詞。[p]\n').json()
    snapshot=game.get_game(s['id'])
    snapshot['voices'][0]['speaker']='TIPS'
    snapshot['voices'][0]['caption']='誤って保存した説明文'
    game._write(s['id'],snapshot)
    response=ks_file(client,s,'#eruku\n[voice id="v03812"]\n最初の台詞。[p]\n')
    assert response.status_code==200,response.text
    actor=response.json()['voices'][0]
    assert actor['voiceId']=='v03812' and actor['speaker']=='eruku'
    assert actor['caption']==''


def test_export_removes_ignored_tips_voice_tag_from_copy_only(client):
    s=new_game(client)
    source='#TIPS\n[voice id="v03812"]\nゲームの説明。[p]\n#mahoru\n台詞。[p]\n'
    s=ks_file(client,s,source).json()
    assert [v['speaker'] for v in s['voices']]==['mahoru']
    assert s['lastIssued']>=3812
    zipped=client.get(f'/api/game/projects/{s["id"]}/export.zip')
    with ZipFile(io.BytesIO(zipped.content)) as archive:
        out=archive.read('voiced_scenarios/scene1.ks').decode('utf8')
        assert 'v03812' not in out
        assert 'ゲームの説明。[p]' in out
        assert s['voices'][0]['voiceId'] in out
    assert game.get_game(s['id'])['scenarios'][0]['content']==source


def test_sheet_filter_keeps_explicit_json_voice_list_unmodified(client):
    s=new_game(client)
    s=_upload_sheet(client,s).json()
    payload={'batch_name':'guest_voice','speakers':[{'speaker':'guest_voice',
             'items':[{'id':'greet_0','text':'Hello'}]}]}
    result=client.post(f'/api/game/projects/{s["id"]}/voice-list?expectedRevision={s["revision"]}',
                       files={'file':('voice.json',json.dumps(payload).encode(),'application/json')})
    assert result.status_code==200,result.text
    assert result.json()['voices'][0]['speaker']=='guest_voice'


def test_game_sheet_filter_ui_status_and_noncharacter_count():
    root=Path(__file__).resolve().parents[1]
    html=(root/'static/game.html').read_text(encoding='utf8')
    js=(root/'static/game.js').read_text(encoding='utf8')
    assert 'id="speakerFilterInfo"' in html
    assert 'ignoredSpeakers' in js
    assert 'game.js?v=036' in html


def test_reference_character_ids_and_aliases_from_legacy_pipeline(client):
    state=new_game(client)
    sheet={'characters':{'mahoru':{'display_name':'真白真歩流'}},
           'reference_characters':{
               'arthur_king':{'display_name':'アーサー・キング','aliases':['arther']},
               'maikuro_houmu':{'display_name':'舞黒邦夢','aliases':['maicro']}},
           'non_character_speakers':{'TIPS':{'direction':'対象外'}}}
    res=client.post(f'/api/game/projects/{state["id"]}/character-sheet?expectedRevision={state["revision"]}',
        files={'file':('sheet.json',json.dumps(sheet,ensure_ascii=False).encode('utf8'),'application/json')})
    assert res.status_code==200,res.text
    state=res.json()
    assert 'arthur_king' in state['referenceCharacters']
    assert 'maikuro_houmu' in state['referenceCharacters']
    text=('#arther\nようこそ。[p]\n'
          '#maicro\n館へようこそ。[p]\n'
          '#TIPS\nこれは説明です。[p]\n')
    res=ks_file(client,state,text,name='old_script.ks')
    assert res.status_code==200,res.text
    result=res.json()
    assert [v['speaker'] for v in result['voices']]==['arther','maicro']
    assert result['scenarios'][0]['ignoredSpeakers']=={'TIPS':1}
    assert game._speaker_profile(result,'arther')[0]=='arthur_king'
    assert game._speaker_profile(result,'maicro')[0]=='maikuro_houmu'


def test_reference_sheet_only_cast_binding_is_used_by_alias_generation(client):
    state=new_game(client)
    sheet={'characters':{},'reference_characters':{
        'arthur_king':{'display_name':'アーサー・キング','aliases':['arther']}}}
    res=client.post(f'/api/game/projects/{state["id"]}/character-sheet?expectedRevision={state["revision"]}',
        files={'file':('sheet.json',json.dumps(sheet,ensure_ascii=False).encode(),'application/json')})
    assert res.status_code==200,res.text
    state=res.json()
    cast=client.post('/api/projects',json={'title':'参照音声を含むキャスト'}).json()
    cast['voices'].append({'name':'アーサー・キング','engine':'irodori',
        'model':'Aratako/Irodori-TTS-v4.1-Small','mode':'clone','language':'Japanese','refs':[]})
    cast=client.put(f'/api/projects/{cast["id"]}',json={'state':cast,'expectedRevision':cast['revision']}).json()
    res=client.put(f'/api/game/projects/{state["id"]}',json={'expectedRevision':state['revision'],
        'changes':{'bindings':{'arthur_king':{'projectId':cast['id'],'castName':'アーサー・キング'}}}})
    assert res.status_code==200,res.text
    state=res.json()
    assert state['bindings']['arthur_king']['castName']=='アーサー・キング'
    # The KS speaker uses the old pipeline's alias. The saved canonical
    # character-sheet binding must still be used for synthesis.
    res=ks_file(client,state,'#arther\nご挨拶します。[p]\n',name='old_script.ks')
    assert res.status_code==200,res.text
    game_state=res.json()
    plan=game.generation_groups(game_state['id'],{game_state['voices'][0]['voiceId']})
    assert len(plan)==1 and plan[0][3]['name']=='アーサー・キング'


def test_game_cast_roster_contains_sheet_reference_characters_without_lines():
    root=Path(__file__).resolve().parents[1]
    src=(root/'static'/'game.js').read_text(encoding='utf8')
    assert 'function sheetEntries()' in src
    assert 'Object.entries(game?.referenceCharacters||{})' in src
    assert 'function castRoster()' in src
    assert 'キャラクターシート登録者（台詞未検出' in src
    assert 'シート登録済み · 台詞未検出' in src
    assert 'for(const item of castRoster())' in src
    assert 'resolveSheetProfile(v.speaker)' in src
    html=(root/'static'/'game.html').read_text(encoding='utf8')
    assert 'game.js?v=036' in html and 'game.css?v=036' in html


def test_sheet_update_recovers_previously_filtered_ks_aliases_without_reimport(client):
    """Regression: prologue lines for #arther/#maicro were lost at first sheet import."""
    state=new_game(client)
    original=('#maicro\n[voice id="v00373"]\n一つ目。[p]\n'
              '#yuduki\n[voice id="v00375"]\nこんにちは。[p]\n'
              '#arther\n[voice id="v00376"]\nお久しぶりです。[p]\n')
    # Import the old sheet with reference names, but no legacy KS aliases.
    sheet={'characters':{},'reference_characters':{
        'yuduki':{'display_name':'真白結月'},
        'arthur_king':{'display_name':'アーサー・キング'},
        'maikuro_houmu':{'display_name':'舞黒邦夢'}},
        'non_character_speakers':{'TIPS':{'direction':'不要'}}}
    def upload(s,body):
        return client.post(f'/api/game/projects/{s["id"]}/character-sheet?expectedRevision={s["revision"]}',
            files={'file':('character_sheet.json',json.dumps(body,ensure_ascii=False).encode(),'application/json')})
    res=upload(state,sheet)
    assert res.status_code==200,res.text
    state=res.json()
    res=ks_file(client,state,original,name='prologue.ks')
    assert res.status_code==200,res.text
    state=res.json()
    assert [(x['speaker'],x['voiceId']) for x in state['voices']]==[('yuduki','v00375')]
    assert state['scenarios'][0]['ignoredSpeakers']=={'arther':1,'maicro':1}
    # A generated take/caption for a pre-existing character survives sheet re-import.
    state['voices'][0]['caption']='既存の演技指示'
    state['voices'][0]['audio']='yuduki/prologue/v00375.wav'
    game._write(state['id'],state)
    sheet['reference_characters']['arthur_king']['aliases']=['arther']
    sheet['reference_characters']['maikuro_houmu']['aliases']=['maicro']
    res=upload(state,sheet)
    assert res.status_code==200,res.text
    restored=res.json()
    assert [(x['speaker'],x['voiceId']) for x in restored['voices']]==[
        ('maicro','v00373'),('yuduki','v00375'),('arther','v00376')]
    assert restored['scenarios'][0]['ignoredSpeakers']=={}
    assert restored['scenarios'][0]['count']==3
    assert restored['scenarios'][0]['content']==original
    previous=next(v for v in restored['voices'] if v['speaker']=='yuduki')
    assert previous['caption']=='既存の演技指示'
    assert previous['audio']=='yuduki/prologue/v00375.wav'
    assert not previous['stale']
    # No phantom ID may be allocated during subsequent reimport.
    again=ks_file(client,restored,original,name='prologue.ks')
    assert again.status_code==200,again.text
    assert [(x['speaker'],x['voiceId']) for x in again.json()['voices']]==[
        ('maicro','v00373'),('yuduki','v00375'),('arther','v00376')]


def test_sheet_update_does_not_invent_audio_ids_when_new_ks_speaker_is_untagged(client):
    s=new_game(client)
    old={'characters':{'mahoru':{'display_name':'真白真歩流'}},
         'reference_characters':{'arthur_king':{'display_name':'アーサー・キング'}}}
    def upload(state,body):
        return client.post(f'/api/game/projects/{state["id"]}/character-sheet?expectedRevision={state["revision"]}',
            files={'file':('sheet.json',json.dumps(body,ensure_ascii=False).encode(),'application/json')})
    s=upload(s,old).json()
    text='#mahoru\n既存。[p]\n#arther\n後から認識される。[p]\n'
    s=ks_file(client,s,text,name='prologue.ks').json()
    former=s['voices'][0]['voiceId']
    old['reference_characters']['arthur_king']['aliases']=['arther']
    res=upload(s,old)
    assert res.status_code==200,res.text
    recovered=res.json()
    assert len(recovered['voices'])==2
    assert recovered['voices'][0]['voiceId']==former
    assert recovered['voices'][1]['speaker']=='arther'
    assert recovered['voices'][1]['voiceId']!=former
    assert recovered['lastIssued']>=2


def test_sheet_update_keeps_filtered_voice_list_and_recalculates_tips(client):
    s=new_game(client)
    s=ks_file(client,s,'#mahoru\n本文。[p]\n#TIPS\n説明。[p]\n',name='prologue.ks').json()
    old=s['voices'][0]['voiceId']
    sheet={'characters':{'mahoru':{'display_name':'真白真歩流'}},
           'reference_characters':{'arthur_king':{'display_name':'アーサー・キング','aliases':['arther']}}}
    res=client.post(f'/api/game/projects/{s["id"]}/character-sheet?expectedRevision={s["revision"]}',
        files={'file':('sheet.json',json.dumps(sheet,ensure_ascii=False).encode(),'application/json')})
    assert res.status_code==200,res.text
    result=res.json()
    assert len(result['voices'])==1 and result['voices'][0]['voiceId']==old
    assert result['scenarios'][0]['ignoredSpeakers']=={'TIPS':1}



def _stage_script(client,state,name,content):
    response=client.post(f'/api/game/projects/{state["id"]}/stage-ks?expectedRevision={state["revision"]}',
        files=[('files',(name,content.encode('utf-8'),'text/plain'))])
    assert response.status_code==200,response.text
    return response.json()


def _finish_scripts(client,state):
    return client.post(f'/api/game/projects/{state["id"]}/finalize-ks',
        json={'expectedRevision':state['revision']})


def test_stage_two_scenes_then_assign_all_ids_in_one_global_pass(client):
    state=new_game(client)
    state=_stage_script(client,state,'scene6.ks',
        '#reido\n新しい台詞A。[p]\n#reido\n新しい台詞B。[p]\n')
    assert not state['voices']
    assert [x['key'] for x in state['pendingKS']]==['scene6']
    assert state['lastIssued']==0
    state=_stage_script(client,state,'scene7.ks',
        '#mahoru\n[voice id="v00001"]\n以前から存在。[p]\n'
        '#mahoru\n[voice id="v00002"]\nこちらも既存。[p]\n')
    assert not state['voices']
    assert len(state['pendingKS'])==2
    response=_finish_scripts(client,state)
    assert response.status_code==200,response.text
    state=response.json()
    assert not state['pendingKS']
    voices={(v['scenario'],v['text']):v['voiceId'] for v in state['voices']}
    assert voices[('scene7','以前から存在。')]=='v00001'
    assert voices[('scene7','こちらも既存。')]=='v00002'
    assert voices[('scene6','新しい台詞A。')]=='v00003'
    assert voices[('scene6','新しい台詞B。')]=='v00004'
    assert len(set(voices.values()))==4
    assert state['lastIssued']==4
    # Reload sheet must not allocate new identities.
    source=client.get(f'/api/game/projects/{state["id"]}').json()
    assert source['voices']==state['voices']
    assert _finish_scripts(client,state).status_code==400


def test_late_explicit_scene7_claims_scene6_generated_id_preserves_audio(client):
    s=new_game(client)
    s=ks_file(client,s,'#reido\n後から追加した台詞。[p]\n',name='scene6.ks').json()
    oldid=s['voices'][0]['voiceId']
    assert oldid=='v00001'
    v=s['voices'][0]
    v['caption']='緊迫して話す'
    v['audio']='reido/scene6/v00001.wav'
    path=core.WORKSPACE/'game_voice'/s['id']/'audio'/v['audio']
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_bytes(short_wav())
    game._write(s['id'],s)
    s=_stage_script(client,s,'scene7.ks','#mahoru\n[voice id="v00001"]\n元の台詞。[p]\n')
    assert next(v for v in s['voices'] if v['scenario']=='scene6')['voiceId']=='v00001'
    r=_finish_scripts(client,s)
    assert r.status_code==200,r.text
    s=r.json()
    by_scene={v['scenario']:v for v in s['voices']}
    assert by_scene['scene7']['voiceId']=='v00001'
    assert by_scene['scene6']['voiceId']=='v00002'
    assert by_scene['scene6']['caption']=='緊迫して話す'
    assert by_scene['scene6']['audio']=='reido/scene6/v00001.wav'
    assert path.exists()  # no irreversible moves of original audio files
    assert s['lastIdRemaps']==[{'scenario':'scene6','speaker':'reido',
                               'oldId':'v00001','newId':'v00002'}]
    z=client.get(f'/api/game/projects/{s["id"]}/export.zip')
    assert z.status_code==200,z.text[:250]
    with ZipFile(io.BytesIO(z.content)) as bundle:
        names=bundle.namelist()
        assert 'audio/reido/scene6/v00002.wav' in names
        assert bundle.read('audio/reido/scene6/v00002.wav')==short_wav()
        assert '[voice id="v00002"]' in bundle.read('voiced_scenarios/scene6.ks').decode('utf-8')
        assert '[voice id="v00001"]' in bundle.read('voiced_scenarios/scene7.ks').decode('utf-8')


def test_batch_old_and_new_scripts_prioritizes_existing_tags(client):
    s=new_game(client)
    files=[('files',('scene6.ks',b'#reido\nNew line.[p]\n','text/plain')),
           ('files',('scene7.ks',b'#mahoru\n[voice id="v00001"]\nExisting line.[p]\n','text/plain'))]
    r=client.post(f'/api/game/projects/{s["id"]}/import-ks?expectedRevision={s["revision"]}',files=files)
    assert r.status_code==200,r.text
    state=r.json()
    assert next(x for x in state['voices'] if x['scenario']=='scene7')['voiceId']=='v00001'
    assert next(x for x in state['voices'] if x['scenario']=='scene6')['voiceId']=='v00002'


def test_true_duplicate_explicit_scene6_scene7_refused_atomically(client):
    s=new_game(client)
    s=_stage_script(client,s,'scene6.ks','#reido\n[voice id="v00352"]\n元の台詞A。[p]\n')
    s=_stage_script(client,s,'scene7.ks','#mahoru\n[voice id="v00352"]\n元の台詞B。[p]\n')
    r=_finish_scripts(client,s)
    assert r.status_code==400,r.text
    detail=r.json()['detail']
    assert 'scene6.ks' in detail and 'scene7.ks' in detail
    assert 'v00352' in detail
    after=client.get(f'/api/game/projects/{s["id"]}').json()
    assert after['revision']==s['revision']
    assert not after['voices']
    assert len(after['pendingKS'])==2
    # Replace offending staged script before finalizing.
    s=_stage_script(client,after,'scene7.ks','#mahoru\n[voice id="v00353"]\n元の台詞B。[p]\n')
    r=_finish_scripts(client,s)
    assert r.status_code==200,r.text
    assert len({x['voiceId'] for x in r.json()['voices']})==2


def test_tips_explicit_ids_still_reserved_across_scenarios(client):
    s=new_game(client)
    s=_stage_script(client,s,'scene6.ks','#TIPS\n[voice id="v00045"]\n説明です。[p]\n#reido\n新規。[p]\n')
    s=_stage_script(client,s,'scene7.ks','#mahoru\n[voice id="v00046"]\n旧台詞。[p]\n')
    r=_finish_scripts(client,s)
    assert r.status_code==200,r.text
    result=r.json()
    assert result['scenarios'][0]['ignoredSpeakers']=={'TIPS':1}
    assert len(result['voices'])==2
    assert sorted(x['voiceId'] for x in result['voices'])==['v00046','v00047']


def test_stage_ui_and_api_are_available_without_changing_legacy_endpoint(client):
    html=client.get('/game').text
    assert 'id="finalizeKs"' in html
    assert '全KSのボイスIDを一括確定' in html
    js=(Path(__file__).resolve().parents[1]/'static'/'game.js').read_text('utf-8')
    assert 'stage-ks?expectedRevision=' in js
    assert '/finalize-ks' in js
    assert 'game.js?v=036' in html


def test_stage_blocks_early_generation_and_export(client):
    s=new_game(client)
    s=ks_file(client,s,'#mahoru\nもう保存済み。[p]\n',name='scene5.ks').json()
    s=_stage_script(client,s,'scene6.ks','#reido\n新しい台詞。[p]\n')
    output=client.get(f'/api/game/projects/{s["id"]}/export.zip')
    assert output.status_code==400
    assert '一括確定' in output.json()['detail']
    generate=client.post(f'/api/game/projects/{s["id"]}/generate',json={
         'voiceIds':[s['voices'][0]['voiceId']],'numSteps':40})
    assert generate.status_code==400
    assert '一括確定' in generate.json()['detail']


def test_remapped_ids_manifest_included_in_game_export(client):
    s=new_game(client)
    s=ks_file(client,s,'#mahoru\n自動採番。[p]\n',name='scene6.ks').json()
    s=_stage_script(client,s,'scene7.ks','#reido\n[voice id="v00001"]\n明示ID。[p]\n')
    s=_finish_scripts(client,s).json()
    raw=client.get(f'/api/game/projects/{s["id"]}/export.zip')
    assert raw.status_code==200,raw.text[:200]
    with ZipFile(io.BytesIO(raw.content)) as archive:
        remaps=json.loads(archive.read('id_remaps.json'))
        assert remaps==s['lastIdRemaps']
        assert remaps[0]['oldId']=='v00001'
        assert remaps[0]['newId']=='v00002'
