import io
import sys
from pathlib import Path
import numpy as np
import pytest
import soundfile as sf
from fastapi.testclient import TestClient
import openpyxl
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import core
from app import app

@pytest.fixture
def client(tmp_path,monkeypatch):
    monkeypatch.setattr(core,'WORKSPACE',tmp_path)
    return TestClient(app)


def project(client):
    r=client.post('/api/projects',json={'title':'舞黒館テスト'})
    assert r.status_code==200,r.text
    return r.json()


def wav_blob(freq=330,seconds=.3):
    sr=44100;t=np.arange(int(sr*seconds))/sr
    buf=io.BytesIO();sf.write(buf,(np.sin(2*np.pi*freq*t)*.15).astype('float32'),sr,format='WAV');buf.seek(0)
    return buf


def upload(client,pid,freq=440,kind='voice'):
    r=client.post(f'/api/projects/{pid}/assets',files={'file':('voice.wav',wav_blob(freq),'audio/wav')},data={'kind':kind})
    assert r.status_code==200,r.text
    return r.json()['asset']


def test_project_audio_workflow_and_mixer(client):
    s=project(client);pid=s['id'];sid=s['scenes'][0]['id']
    l=core.new_line(sid,'真歩流','サブレがない！','驚き',.4)
    s['lines']=[l]
    r=client.put(f'/api/projects/{pid}',json={'state':s,'expectedRevision':s['revision']})
    assert r.status_code==200,r.text
    a=upload(client,pid,440,'voice')
    r=client.post(f'/api/projects/{pid}/lines/{l["id"]}/attach/{a["id"]}')
    assert r.status_code==200,r.text
    s=r.json();assert s['lines'][0]['selectedTakeId'] and s['lines'][0]['status']=='ready'
    s['clips'].append({'id':core.uid('clip'),'sceneId':sid,'trackId':'bgm','assetId':a['id'],
          'start':0,'duration':.7,'loop':True,'gain':.25,'fadeIn':.1,'fadeOut':.1})
    r=client.put(f'/api/projects/{pid}',json={'state':s,'expectedRevision':s['revision']})
    assert r.status_code==200,r.text
    r=client.post(f'/api/projects/{pid}/render',json={'sceneId':sid,'format':'wav'})
    assert r.status_code==200,r.text
    path=r.json()['url'];download=client.get(path);assert download.status_code==200
    audio,samplerate=sf.read(io.BytesIO(download.content));assert samplerate==44100
    assert len(audio)>=int(.69*samplerate) and np.abs(audio[:int(.28*samplerate)]).mean()>.01
    assert client.get(f'/api/projects/{pid}').json()['clips'][1]['loop']


def test_excel_roundtrip_preserves_ids_and_stale_audio(client):
    s=project(client);pid=s['id'];sid=s['scenes'][0]['id']
    l=core.new_line(sid,'愛理','どこに行ったの？','少し困惑',.5)
    s['lines']=[l]
    s=client.put(f'/api/projects/{pid}',json={'state':s,'expectedRevision':s['revision']}).json()
    a=upload(client,pid,200)
    s=client.post(f'/api/projects/{pid}/lines/{l["id"]}/attach/{a["id"]}').json()
    x=client.get(f'/api/projects/{pid}/excel/export');assert x.status_code==200
    wb=openpyxl.load_workbook(io.BytesIO(x.content));ws=wb.active
    assert ws['A2'].value==l['id']
    ws['D2']='ねえ、サブレはどこに行ったの？'
    ws.append([None,'シーン1','真歩流','分からないわ','落ち着いて',.7])
    bio=io.BytesIO();wb.save(bio);bio.seek(0)
    r=client.post(f'/api/projects/{pid}/excel/import',files={'file':('script.xlsx',bio,'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')})
    assert r.status_code==200,r.text
    s=r.json()['project'];assert len(s['lines'])==2
    prior=next(x for x in s['lines'] if x['id']==l['id']);assert prior['status']=='stale'
    assert prior['takes'][0]['assetId']==a['id']
    assert r.json()['summary']['added']==1
    # Reimport the same ID-bearing Excel must not duplicate known lines.
    x=client.get(f'/api/projects/{pid}/excel/export')
    r=client.post(f'/api/projects/{pid}/excel/import',files={'file':('again.xlsx',x.content,'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')})
    assert len(r.json()['project']['lines'])==2


def test_audio_asset_security_and_revision_conflict(client):
    s=project(client);pid=s['id']
    r=client.post(f'/api/projects/{pid}/assets',files={'file':('notes.txt',b'audio','text/plain')},data={'kind':'sfx'})
    assert r.status_code==400
    malicious=dict(s);malicious['assets']=[{'id':'asset_bad','filename':'../../x.wav','duration':1}]
    r=client.put(f'/api/projects/{pid}',json={'state':malicious,'expectedRevision':s['revision']})
    assert r.status_code==400
    s['title']='Updated'
    r=client.put(f'/api/projects/{pid}',json={'state':s,'expectedRevision':s['revision']})
    assert r.status_code==200
    r=client.put(f'/api/projects/{pid}',json={'state':s,'expectedRevision':s['revision']})
    assert r.status_code==409


def test_timeline_anchor_reflow():
    s=core.default_state('test','p_1111111111111111');sid=s['scenes'][0]['id']
    l=core.new_line(sid,'a','hello',after=1)
    a={'id':'asset_1','filename':'a.wav','duration':2};s['assets'].append(a);s['lines'].append(l)
    core.attach_take(s,l,a)
    c=s['clips'][0]
    s['clips'].append({'id':'clip_sfx','sceneId':sid,'trackId':'sfx','assetId':'asset_1','start':.3,'duration':1,
          'anchorLineId':l['id'],'anchorOffset':.3})
    # Move the dialogue start manually, reflow does not override pinned clips.
    c['pinned']=True;c['start']=3
    core.reflow_dialogue(s,sid)
    assert c['start']==3
    c['pinned']=False
    core.reflow_dialogue(s,sid)
    assert c['start']==0 and s['clips'][1]['start']==.3


def test_generate_job_registration_with_fake_tts(client,monkeypatch,tmp_path):
    import json
    import app as api
    s=project(client);pid=s['id'];sid=s['scenes'][0]['id']
    s['lines'].append(core.new_line(sid,'真歩流','ここはどこ？','困惑',.4))
    s=client.put(f'/api/projects/{pid}',json={'state':s,'expectedRevision':s['revision']}).json()
    # Mock only the external model runtime, not project or audio handling.
    monkeypatch.setattr(api,'python_for',lambda *_:sys.executable)
    def fake_subprocess_run(cmd,**kwargs):
        job=json.loads(Path(cmd[-2]).read_text(encoding='utf-8'))
        rows=[]
        for item in job['items']:
            path=Path(item['output']);path.parent.mkdir(exist_ok=True)
            sf.write(path,np.zeros(4410,dtype='float32'),44100)
            rows.append({'id':item['id'],'ok':True,'output':str(path)})
        Path(cmd[-1]).write_text(json.dumps({'ok':True,'items':rows}),encoding='utf-8')
        return type('Finished',(),{'returncode':0})()
    monkeypatch.setattr(api.subprocess,'run',fake_subprocess_run)
    job_id=core.uid('job')
    api.JOBS[job_id]={'status':'running','completed':0,'total':0,'errors':[],'message':''}
    api.run_generation(job_id,pid,[s['lines'][0]['id']],40)
    assert api.JOBS[job_id]['status']=='done'
    final=client.get(f'/api/projects/{pid}').json()
    assert len(final['lines'][0]['takes'])==1 and len(final['clips'])==1
    assert final['lines'][0]['status']=='ready'


def test_voice_design_creates_reusable_reference(client,monkeypatch):
    import json
    import app as api
    s=project(client);pid=s['id']
    s['voices']=[{'name':'真歩流','engine':'qwen','model':'Qwen/Qwen3-TTS-12Hz-1.7B-VoiceDesign',
                 'mode':'design','description':'明るく元気な少女の声','language':'Japanese','refText':'','refs':[]}]
    s=client.put(f'/api/projects/{pid}',json={'state':s,'expectedRevision':s['revision']}).json()
    monkeypatch.setattr(api,'python_for',lambda *_:sys.executable)
    def fake_run(cmd,**kwargs):
        job=json.loads(Path(cmd[-2]).read_text(encoding='utf-8'))
        item=job['items'][0];assert job['model'].endswith('VoiceDesign')
        assert item['mode']=='design'
        f=Path(item['output']);sf.write(f,np.zeros(4410,dtype='float32'),44100)
        Path(cmd[-1]).write_text(json.dumps({'ok':True,'items':[{'id':item['id'],'ok':True,'output':str(f)}]}))
        return type('Completed',(),{'returncode':0})()
    monkeypatch.setattr(api.subprocess,'run',fake_run)
    jid=core.uid('job');api.JOBS[jid]={'status':'running','total':1,'completed':0,'errors':[],'message':''}
    api.run_voice_design(jid,pid,'真歩流','こんにちは！')
    assert api.JOBS[jid]['status']=='done'
    latest=client.get(f'/api/projects/{pid}').json()
    voice=latest['voices'][0]
    assert len(voice['refs'])==1 and voice['refText']=='こんにちは！'
    assert voice['mode']=='clone' and voice['model'].endswith('Base')
    assert latest['assets'][0]['kind']=='reference'


def test_batch_upload_assigns_in_file_order_and_keeps_old_takes(client):
    s=project(client);pid=s['id'];sid=s['scenes'][0]['id']
    lines=[core.new_line(sid,'真歩流',f'台詞{i}',after=.2) for i in range(1,4)]
    s['lines']=lines
    s=client.put(f'/api/projects/{pid}',json={'state':s,'expectedRevision':s['revision']}).json()
    old=upload(client,pid,freq=180)
    s=client.post(f'/api/projects/{pid}/lines/{lines[0]["id"]}/attach/{old["id"]}').json()
    old_take_id=s['lines'][0]['selectedTakeId']
    r=client.post(f'/api/projects/{pid}/assets/batch',
                  files=[('files',('voice_01.wav',wav_blob(300),'audio/wav')),
                         ('files',('voice_02.wav',wav_blob(420),'audio/wav')),
                         ('files',('voice_10.wav',wav_blob(540),'audio/wav'))],
                  data={'kind':'voice','lineIds':__import__('json').dumps([l['id'] for l in lines])})
    assert r.status_code==200,r.text
    body=r.json();s=body['project']
    assert body['summary']=={'imported':3,'assigned':3,'references':0}
    assert [a['name'] for a in body['assets']]==['voice_01.wav','voice_02.wav','voice_10.wav']
    assert [next(t for t in l['takes'] if t['id']==l['selectedTakeId'])['assetId'] for l in s['lines']]==[a['id'] for a in body['assets']]
    assert len(s['lines'][0]['takes'])==2 and s['lines'][0]['takes'][0]['id']==old_take_id
    assert [c['lineId'] for c in s['clips']]==[l['id'] for l in lines]
    assert [round(c['start'],2) for c in s['clips']]==[0,.5,1.0]
    assert len(list((core.workdir(pid)/'assets').glob('*.wav')))==4
    assert s['revision']==5 # create, save lines, upload original, attach original, entire batch
    rr=client.post(f'/api/projects/{pid}/render',json={'sceneId':sid,'format':'wav'})
    assert rr.status_code==200,rr.text


def test_batch_upload_skips_targets_and_can_register_cast_references(client):
    s=project(client);pid=s['id'];sid=s['scenes'][0]['id']
    s['voices']=[{'name':'愛理','refs':[], 'engine':'irodori', 'mode':'clone', 'model':'test'}]
    s['lines']=[core.new_line(sid,'愛理','よし')]
    s=client.put(f'/api/projects/{pid}',json={'state':s,'expectedRevision':s['revision']}).json()
    import json
    r=client.post(f'/api/projects/{pid}/assets/batch',
                 files=[('files',('a.wav',wav_blob(),'audio/wav')),('files',('b.wav',wav_blob(),'audio/wav'))],
                 data={'kind':'reference','castName':'愛理','lineIds':json.dumps([None,None])})
    assert r.status_code==200,r.text
    res=r.json();assert res['summary']=={'imported':2,'assigned':0,'references':2}
    assert res['project']['voices'][0]['refs']==[a['id'] for a in res['assets']]
    assert res['project']['lines'][0]['selectedTakeId'] is None
    assert not res['project']['clips']
    r=client.post(f'/api/projects/{pid}/assets/batch',
                 files=[('files',('free.wav',wav_blob(),'audio/wav'))],
                 data={'kind':'voice','lineIds':json.dumps([None])})
    assert r.status_code==200,r.text
    assert r.json()['summary']['assigned']==0


def test_batch_upload_rejects_duplicate_targets_and_invalid_files_atomically(client):
    s=project(client);pid=s['id'];sid=s['scenes'][0]['id']
    l=core.new_line(sid,'真歩流','こんにちは')
    s['lines'].append(l)
    s=client.put(f'/api/projects/{pid}',json={'state':s,'expectedRevision':s['revision']}).json()
    import json
    for files,targets,kind,cast in [
        ([('files',('a.wav',wav_blob(),'audio/wav')),('files',('b.wav',wav_blob(),'audio/wav'))],[l['id'],l['id']],'voice',''),
        ([('files',('a.wav',wav_blob(),'audio/wav')),('files',('fake.txt',b'junk','text/plain'))],[l['id'],None],'voice',''),
        ([('files',('a.wav',wav_blob(),'audio/wav'))],[l['id']],'reference',''),
        ([('files',('a.wav',wav_blob(),'audio/wav'))],[None],'reference','誰もいない'),
        ([('files',('broken.wav',b'fake-wav','audio/wav'))],[None],'voice','')
    ]:
        r=client.post(f'/api/projects/{pid}/assets/batch',files=files,data={
            'kind':kind,'lineIds':json.dumps(targets),'castName':cast})
        assert r.status_code==400,r.text
        assert client.get(f'/api/projects/{pid}').json()['revision']==s['revision']
        assert not list((core.workdir(pid)/'assets').iterdir())


def test_create_multiple_projects_and_ui_create_handler(client):
    first=project(client)
    second=client.post('/api/projects',json={'title':'新しいドラマ'}).json()
    assert first['id']!=second['id']
    assert {p['id'] for p in client.get('/api/projects').json()}=={first['id'],second['id']}
    assert second['lines']==[] and second['assets']==[]
    js=Path(__file__).resolve().parents[1]/'static'/'app.js'
    content=js.read_text(encoding='utf-8')
    assert "target.id==='createProjectBtn'" in content
    assert 'createProject(title).then(' in content


def test_cast_references_are_separate_from_dialogue_assets(client):
    state=project(client);pid=state['id'];sid=state['scenes'][0]['id']
    dialogue=upload(client,pid,kind='voice')
    reference=upload(client,pid,kind='reference')
    state=client.get(f'/api/projects/{pid}').json()
    state['voices']=[{'name':'真歩流','refs':[dialogue['id']]}]
    rejected=client.put(f'/api/projects/{pid}',json={'state':state,'expectedRevision':state['revision']})
    assert rejected.status_code==400
    assert '参照音声' in rejected.text
    state['voices'][0]['refs']=[reference['id']]
    assert client.put(f'/api/projects/{pid}',json={'state':state,'expectedRevision':state['revision']}).status_code==200
    # Reference audio cannot be attached to spoken lines.
    state=client.get(f'/api/projects/{pid}').json()
    line=core.new_line(sid,'真歩流','こんにちは')
    state['lines'].append(line)
    state=client.put(f'/api/projects/{pid}',json={'state':state,'expectedRevision':state['revision']}).json()
    r=client.post(f'/api/projects/{pid}/lines/{line["id"]}/attach/{reference["id"]}')
    assert r.status_code==400
    assert len(client.get(f'/api/projects/{pid}').json()['lines'][0]['takes'])==0


def test_unused_asset_trash_and_restore(client):
    s=project(client);pid=s['id'];asset=upload(client,pid)
    original=core.workdir(pid)/'assets'/asset['filename']
    assert original.is_file()
    response=client.delete(f'/api/projects/{pid}/assets/{asset["id"]}')
    assert response.status_code==200,response.text
    assert not original.exists()
    assert (core.workdir(pid)/'trash'/asset['filename']).is_file()
    assert asset['id'] not in [x['id'] for x in response.json()['project']['assets']]
    assert client.get(f'/api/projects/{pid}/trash/assets').json()[0]['asset']['id']==asset['id']
    restored=client.post(f'/api/projects/{pid}/trash/assets/{asset["id"]}/restore')
    assert restored.status_code==200,restored.text
    assert original.is_file()
    assert asset['id'] in [x['id'] for x in restored.json()['project']['assets']]
    assert client.get(f'/api/projects/{pid}/trash/assets').json()==[]


def test_linked_asset_requires_explicit_detach_and_preserves_other_takes(client):
    s=project(client);pid=s['id'];sid=s['scenes'][0]['id']
    s['lines']=[core.new_line(sid,'愛理','確かにあるよ')]
    s=client.put(f'/api/projects/{pid}',json={'state':s,'expectedRevision':s['revision']}).json()
    one=upload(client,pid,freq=222);two=upload(client,pid,freq=444)
    s=client.post(f'/api/projects/{pid}/lines/{s["lines"][0]["id"]}/attach/{one["id"]}').json()
    s=client.post(f'/api/projects/{pid}/lines/{s["lines"][0]["id"]}/attach/{two["id"]}').json()
    usage=client.get(f'/api/projects/{pid}/assets/{two["id"]}/usage').json()
    assert len(usage['lines'])==1 and len(usage['clips'])==1
    assert client.delete(f'/api/projects/{pid}/assets/{two["id"]}').status_code==409
    after=client.get(f'/api/projects/{pid}').json()
    assert len(after['lines'][0]['takes'])==2
    deleted=client.delete(f'/api/projects/{pid}/assets/{two["id"]}?detach=true')
    assert deleted.status_code==200,deleted.text
    after=deleted.json()['project'];line=after['lines'][0]
    assert len(line['takes'])==1 and line['takes'][0]['assetId']==one['id']
    assert line['selectedTakeId']==line['takes'][0]['id']
    assert after['clips'][0]['assetId']==one['id']
    assert (core.workdir(pid)/'trash'/two['filename']).exists()
    restored=client.post(f'/api/projects/{pid}/trash/assets/{two["id"]}/restore').json()['project']
    assert len(restored['lines'][0]['takes'])==1 # Re-association is intentionally manual.


def test_reference_removal_is_guarded_and_invalidates_voice_profile(client):
    s=project(client);pid=s['id'];reference=upload(client,pid,kind='reference')
    s=client.get(f'/api/projects/{pid}').json()
    s['voices']=[{'name':'真歩流','refs':[reference['id']],'engine':'irodori'}]
    s=client.put(f'/api/projects/{pid}',json={'state':s,'expectedRevision':s['revision']}).json()
    usage=client.get(f'/api/projects/{pid}/assets/{reference["id"]}/usage').json()
    assert usage['casts']==['真歩流']
    assert client.delete(f'/api/projects/{pid}/assets/{reference["id"]}').status_code==409
    result=client.delete(f'/api/projects/{pid}/assets/{reference["id"]}?detach=true')
    assert result.status_code==200,result.text
    assert result.json()['project']['voices'][0]['refs']==[]


def test_legacy_voice_reference_remains_removable(client):
    # An old project can contain dialogue-kind audio under refs; do not break
    # loading/editing until the user removes the old checkbox.
    s=project(client);pid=s['id'];legacy=upload(client,pid,kind='voice')
    s=client.get(f'/api/projects/{pid}').json()
    s['voices']=[{'name':'真歩流','refs':[legacy['id']]}]
    # Persist a legacy state as though created by pre-v0.1.3 app.
    (core.workdir(pid)/'project.json').write_text(__import__('json').dumps(s,ensure_ascii=False),encoding='utf-8')
    s['title']='保存しても既存の選択肢は維持される'
    res=client.put(f'/api/projects/{pid}',json={'state':s,'expectedRevision':s['revision']})
    assert res.status_code==200,res.text
    s=res.json();s['voices'][0]['refs']=[]
    assert client.put(f'/api/projects/{pid}',json={'state':s,'expectedRevision':s['revision']}).status_code==200


def test_bulk_trash_unused_only_and_rejects_linked(client):
    s=project(client);pid=s['id'];sid=s['scenes'][0]['id']
    a=upload(client,pid,freq=222);b=upload(client,pid,freq=333)
    response=client.post(f'/api/projects/{pid}/assets/bulk/trash',json={'assetIds':[a['id'],b['id']]})
    assert response.status_code==200,response.text
    assert len(response.json()['trashed'])==2
    assert client.get(f'/api/projects/{pid}').json()['assets']==[]
    assert len(client.get(f'/api/projects/{pid}/trash/assets').json())==2
    another=upload(client,pid,freq=444)
    s=client.get(f'/api/projects/{pid}').json()
    line=core.new_line(sid,'真歩流','聞こえる？')
    s['lines'].append(line)
    s=client.put(f'/api/projects/{pid}',json={'state':s,'expectedRevision':s['revision']}).json()
    s=client.post(f'/api/projects/{pid}/lines/{line["id"]}/attach/{another["id"]}').json()
    extra=upload(client,pid,freq=555)
    response=client.post(f'/api/projects/{pid}/assets/bulk/trash',json={'assetIds':[another['id'],extra['id']]})
    assert response.status_code==409
    current=client.get(f'/api/projects/{pid}').json()
    assert {x['id'] for x in current['assets']}=={another['id'],extra['id']}


def test_copy_selected_casts_with_independent_reference_assets(client):
    """Multi-select copies settings/files, preserves sharing, and source remains intact."""
    source = project(client)
    target = client.post('/api/projects', json={'title': '続編'}).json()
    pid, tid = source['id'], target['id']
    shared = upload(client, pid, 180, 'reference')
    separate = upload(client, pid, 440, 'reference')
    source = client.get(f'/api/projects/{pid}').json()
    source['voices'] = [
        {'name': '真歩流', 'engine': 'irodori', 'model': 'Aratako/Irodori-TTS-v4.1-Anime',
         'mode': 'clone', 'language': 'Japanese', 'description': '若い元気な探偵',
         'refText': '', 'refs': [shared['id'], separate['id']]},
        {'name': '愛理', 'engine': 'qwen', 'model': 'Qwen/Qwen3-TTS-12Hz-1.7B-Base',
         'mode': 'clone', 'language': 'Japanese', 'description': '落ち着いた妹',
         'refText': '私は愛理です。', 'refs': [shared['id']]},
        {'name': '零度', 'engine': 'irodori', 'model': 'test', 'mode': 'design',
         'description': '渋い', 'refs': []},
    ]
    source = client.put(f'/api/projects/{pid}', json={
        'state': source, 'expectedRevision': source['revision']
    }).json()
    resp = client.post(f'/api/projects/{pid}/casts/copy', json={
        'targetProjectId': tid, 'castNames': ['真歩流', '愛理'],
        'expectedSourceRevision': source['revision'],
        'expectedTargetRevision': target['revision'],
    })
    assert resp.status_code == 200, resp.text
    payload = resp.json()
    dest = payload['project']
    assert payload['summary'] == {'copied': 2, 'skipped': [], 'renamed': [], 'referencesCopied': 2}
    assert [v['name'] for v in dest['voices']] == ['真歩流', '愛理']
    assert dest['voices'][0]['description'] == '若い元気な探偵'
    assert dest['voices'][1]['refText'] == '私は愛理です。'
    assert dest['lines'] == [] and dest['clips'] == []
    assert len(dest['assets']) == 2
    copied_shared = dest['voices'][0]['refs'][0]
    assert dest['voices'][1]['refs'] == [copied_shared]
    assert copied_shared != shared['id']
    assert all(a['kind'] == 'reference' for a in dest['assets'])
    by_id = {a['id']: a for a in dest['assets']}
    dest_file = core.workdir(tid) / 'assets' / by_id[copied_shared]['filename']
    src_file = core.workdir(pid) / 'assets' / shared['filename']
    assert dest_file.read_bytes() == src_file.read_bytes()
    assert client.delete(f'/api/projects/{pid}/assets/{shared["id"]}?detach=true').status_code == 200
    assert dest_file.exists()
    assert client.get(f'/api/projects/{tid}').json()['voices'][1]['refs'] == [copied_shared]


def test_copy_single_cast_collision_skip_and_rename(client):
    src = project(client)
    tgt = client.post('/api/projects', json={'title': '別作品'}).json()
    src['voices'] = [{'name': '真歩流', 'refs': [], 'description': '探偵'},
                     {'name': '愛理', 'refs': [], 'description': '妹'}]
    tgt['voices'] = [{'name': '真歩流', 'refs': [], 'description': '変更禁止'},
                     {'name': '愛理', 'refs': [], 'description': '既存の愛理'},
                     {'name': '愛理（コピー）', 'refs': [], 'description': '既存'}]
    src = client.put(f'/api/projects/{src["id"]}', json={'state': src, 'expectedRevision': src['revision']}).json()
    tgt = client.put(f'/api/projects/{tgt["id"]}', json={'state': tgt, 'expectedRevision': tgt['revision']}).json()
    req = {'targetProjectId': tgt['id'], 'castNames': ['真歩流'], 'conflict': 'skip'}
    r = client.post(f'/api/projects/{src["id"]}/casts/copy', json=req)
    assert r.status_code == 200
    assert r.json()['summary']['copied'] == 0 and r.json()['summary']['skipped'] == ['真歩流']
    assert r.json()['project']['revision'] == tgt['revision']
    req['castNames'] = ['真歩流', '愛理']
    req['conflict'] = 'rename'
    r = client.post(f'/api/projects/{src["id"]}/casts/copy', json=req)
    assert r.status_code == 200, r.text
    voices = r.json()['project']['voices']
    assert [v['name'] for v in voices] == ['真歩流', '愛理', '愛理（コピー）', '真歩流（コピー）', '愛理（コピー2）']
    assert voices[0]['description'] == '変更禁止'
    assert r.json()['summary']['copied'] == 2


def test_copy_casts_rejects_invalid_request_and_stale_revision_without_mutation(client):
    src = project(client)
    tgt = client.post('/api/projects', json={'title': '続編'}).json()
    src['voices'] = [{'name': '零度', 'refs': []}]
    src = client.put(f'/api/projects/{src["id"]}', json={'state': src, 'expectedRevision': src['revision']}).json()
    route = f'/api/projects/{src["id"]}/casts/copy'
    base = {'targetProjectId': tgt['id'], 'castNames': ['零度']}
    assert client.post(route, json={**base, 'targetProjectId': src['id']}).status_code == 400
    assert client.post(route, json={**base, 'castNames': ['零度', '零度']}).status_code == 400
    assert client.post(route, json={**base, 'castNames': ['存在しない人物']}).status_code == 400
    assert client.post(route, json={**base, 'expectedSourceRevision': 0}).status_code == 409
    assert client.post(route, json={**base, 'expectedTargetRevision': 0}).status_code == 409
    assert client.get(f'/api/projects/{tgt["id"]}').json()['revision'] == tgt['revision']
    assert not list((core.workdir(tgt['id']) / 'assets').iterdir())


def test_copy_casts_missing_source_reference_is_atomic(client):
    src = project(client)
    tgt = client.post('/api/projects', json={'title': '続編'}).json()
    ref = upload(client, src['id'], kind='reference')
    src = client.get(f'/api/projects/{src["id"]}').json()
    src['voices'] = [{'name': '零度', 'refs': [ref['id']]}]
    src = client.put(f'/api/projects/{src["id"]}', json={'state': src, 'expectedRevision': src['revision']}).json()
    (core.workdir(src['id']) / 'assets' / ref['filename']).unlink()
    r = client.post(f'/api/projects/{src["id"]}/casts/copy', json={
        'targetProjectId': tgt['id'], 'castNames': ['零度']})
    assert r.status_code == 400, r.text
    assert client.get(f'/api/projects/{tgt["id"]}').json()['revision'] == tgt['revision']
    assert not list((core.workdir(tgt['id']) / 'assets').iterdir())


def test_cast_copy_ui_has_bulk_single_and_all_actions():
    js = (Path(__file__).resolve().parents[1] / 'static' / 'app.js').read_text(encoding='utf-8')
    assert 'openCastCopyBtn' in js
    assert 'data-cast-copy' in js
    assert 'castCopyAllBtn' in js and 'castCopyNoneBtn' in js
    assert 'castCopyConfirmBtn' in js
    assert '/casts/copy' in js


def test_project_rename_preserves_assets_casts_and_id(client):
    initial = project(client)
    pid = initial['id']
    ref = upload(client, pid, kind='reference')
    state = client.get(f'/api/projects/{pid}').json()
    state['voices'] = [{'name': '真歩流', 'refs': [ref['id']]}]
    state['lines'] = [core.new_line(state['scenes'][0]['id'], '真歩流', 'おはよう')]
    state = client.put(f'/api/projects/{pid}', json={'state':state, 'expectedRevision':state['revision']}).json()
    copy = {k:state[k] for k in ('id','scenes','lines','tracks','clips','assets','voices','settings')}
    path = core.workdir(pid)/'assets'/ref['filename']
    r = client.patch(f'/api/projects/{pid}/title', json={'title':'  新しい作品名  ', 'expectedRevision':state['revision']})
    assert r.status_code == 200, r.text
    after = r.json()
    assert after['title'] == '新しい作品名'
    assert after['revision'] == state['revision'] + 1
    assert all(after[k] == value for k,value in copy.items())
    assert path.exists()
    assert any(p['id'] == pid and p['title'] == '新しい作品名' for p in client.get('/api/projects').json())
    same = client.patch(f'/api/projects/{pid}/title', json={'title':after['title'], 'expectedRevision':after['revision']})
    assert same.status_code == 200 and same.json()['revision'] == after['revision']


def test_project_rename_rejects_invalid_title_and_conflicts(client):
    state = project(client)
    route = f'/api/projects/{state["id"]}/title'
    for title in ('', '   ', 'x'*121, 'bad\nname'):
        assert client.patch(route,json={'title':title}).status_code == 400
    assert client.patch(route,json={'title':'新タイトル','expectedRevision':0}).status_code == 409
    assert client.get(f'/api/projects/{state["id"]}').json()['title'] == state['title']
    assert client.patch('/api/projects/p_0000000000000000/title',json={'title':'new'}).status_code == 404


def test_project_rename_button_is_connected():
    js = (Path(__file__).resolve().parents[1]/'static'/'app.js').read_text(encoding='utf-8')
    assert 'renameProjectTitle' in js and 'renameProjectBtn' in js
    assert "json('PATCH'" in js and 'renameCurrentProject()' in js


def test_project_title_rename_works_via_legacy_save_endpoint(client):
    """The existing PUT route works even on installations without PATCH /title."""
    s=project(client)
    pid=s['id']
    sid=s['scenes'][0]['id']
    original_id=s['id']
    s['lines'].append(core.new_line(sid,'真歩流','これは音声ドラマです'))
    s=client.put(f'/api/projects/{pid}',json={'state':s,'expectedRevision':s['revision']}).json()
    old_revision=s['revision']
    s['title']='変更後の作品名'
    response=client.put(f'/api/projects/{pid}',json={'state':s,'expectedRevision':old_revision})
    assert response.status_code==200,response.text
    changed=response.json()
    assert changed['id']==original_id
    assert changed['title']=='変更後の作品名'
    assert changed['lines'][0]['text']=='これは音声ドラマです'
    assert changed['revision']==old_revision+1
    assert client.get(f'/api/projects/{pid}').json()['title']=='変更後の作品名'
    # Stale requests may not silently overwrite another browser's edits.
    assert client.put(f'/api/projects/{pid}',json={'state':s,'expectedRevision':old_revision}).status_code==409


def test_rename_ui_uses_backwards_compatible_save_endpoint():
    js=(Path(__file__).resolve().parents[1]/'static'/'app.js').read_text(encoding='utf-8')
    fragment=js[js.index('async function renameCurrentProject()'):js.index('function openSettings(){')]
    assert "json('PUT',{state:renamedState,expectedRevision:state.revision})" in fragment
    assert "json('PATCH'" not in fragment

# v0.1.7: Excel full-replace safety and backward compatibility

def _workbook_bytes(rows, *, sheet='第一幕', title_column=True):
    wb = openpyxl.Workbook(); ws=wb.active; ws.title=sheet
    ws.append(['台詞ID','シーン','話者','台詞','キャプション','間(秒)'] if title_column else ['備考'])
    for row in rows: ws.append(row)
    out=io.BytesIO();wb.save(out);return out.getvalue()


def test_excel_replace_discards_old_script_but_keeps_audio_and_casts(client):
    s=project(client); pid=s['id']; sid=s['scenes'][0]['id']
    s['scenes'][0]['name']='第一幕'; discarded_scene=core.uid('scene'); s['scenes'].append({'id':discarded_scene,'name':'第二幕'})
    retained=core.new_line(sid,'真歩流','残す台詞'); removed=core.new_line(sid,'愛理','消す台詞')
    removed2=core.new_line(discarded_scene,'零度','削除シーンの台詞')
    s['lines']=[retained,removed,removed2];s['voices']=[{'name':'真歩流','refs':[],'description':'探偵'}]
    s=client.put(f'/api/projects/{pid}',json={'state':s,'expectedRevision':s['revision']}).json()
    a=upload(client,pid,kind='voice');ref=upload(client,pid,kind='reference')
    s=client.get(f'/api/projects/{pid}').json();s['voices'][0]['refs']=[ref['id']]
    s=client.put(f'/api/projects/{pid}',json={'state':s,'expectedRevision':s['revision']}).json()
    s=client.post(f'/api/projects/{pid}/lines/{retained["id"]}/attach/{a["id"]}').json()
    s['clips'] += [
        {'id':'clip_fx_old','sceneId':sid,'trackId':'sfx','assetId':a['id'],'start':.2,'duration':.2,'anchorLineId':removed['id']},
        {'id':'clip_fx_independent','sceneId':sid,'trackId':'sfx','assetId':a['id'],'start':.1,'duration':.2},
        {'id':'clip_fx_old_scene','sceneId':discarded_scene,'trackId':'sfx','assetId':a['id'],'start':.1,'duration':.2},
    ]
    s=client.put(f'/api/projects/{pid}',json={'state':s,'expectedRevision':s['revision']}).json()
    orig=s.copy()
    data=_workbook_bytes([[retained['id'],'第一幕','真歩流','残す台詞','驚いて',.6],
                          [None,'第一幕','愛理','新しい台詞','普通',.3]])
    r=client.post(f'/api/projects/{pid}/excel/import',data={'mode':'replace','expectedRevision':s['revision']},
       files={'file':('correct.xlsx',data,'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')})
    assert r.status_code==200,r.text
    result=r.json();s=result['project'];summary=result['summary']
    assert summary['mode']=='replace' and summary['removed']==2 and summary['removedScenes']==1
    assert len(s['lines'])==2 and s['lines'][0]['id']==retained['id']
    assert removed['id'] not in [x['id'] for x in s['lines']]
    assert all(x['name']!='第二幕' for x in s['scenes'])
    assert len(s['lines'][0]['takes'])==1 and s['lines'][0]['selectedTakeId']==orig['lines'][0]['selectedTakeId']
    assert {x['id'] for x in s['clips']}=={'clip_fx_independent',*{c['id'] for c in orig['clips'] if c.get('lineId')==retained['id']}}
    assert s['voices'][0]['description']=='探偵' and s['voices'][0]['refs']==[ref['id']]
    assert len(s['assets'])==2
    assert (core.workdir(pid)/'assets'/ref['filename']).exists()
    backup=core.workdir(pid)/'backups'/summary['backup']
    assert backup.exists()
    import json
    prior=json.loads(backup.read_text(encoding='utf-8'))
    assert prior['revision']==orig['revision'] and len(prior['lines'])==3
    assert prior['title']==orig['title']


def test_excel_merge_retains_unmentioned_lines(client):
    s=project(client);sid=s['scenes'][0]['id'];s['scenes'][0]['name']='第一幕'
    s['lines']=[core.new_line(sid,'真歩流','前の台詞')]
    s=client.put(f'/api/projects/{s["id"]}',json={'state':s,'expectedRevision':s['revision']}).json()
    x=_workbook_bytes([[None,'第一幕','愛理','新しい台詞',None,.5]])
    r=client.post(f'/api/projects/{s["id"]}/excel/import',files={'file':('new.xlsx',x)},data={'mode':'merge'})
    assert r.status_code==200,r.text
    assert len(r.json()['project']['lines'])==2 and r.json()['summary']['removed']==0
    assert not (core.workdir(s['id'])/'backups').exists()


def test_excel_replace_rejects_missing_data_duplicate_and_conflict(client):
    s=project(client);pid=s['id'];sid=s['scenes'][0]['id']
    s['lines']=[core.new_line(sid,'真歩流','残る')]
    s=client.put(f'/api/projects/{pid}',json={'state':s,'expectedRevision':s['revision']}).json()
    def attempt(data, **fields):
        return client.post(f'/api/projects/{pid}/excel/import',data={'mode':'replace','expectedRevision':s['revision'],**fields},files={'file':('bad.xlsx',data)})
    assert attempt(_workbook_bytes([],title_column=False)).status_code==400
    assert attempt(_workbook_bytes([])).status_code==400
    id=s['lines'][0]['id'];dupes=[[id,'シーン1','真歩流','1',None,.5],[id,'シーン1','真歩流','2',None,.5]]
    assert attempt(_workbook_bytes(dupes)).status_code==400
    good=_workbook_bytes([[None,'シーン1','真歩流','2',None,.5]])
    assert attempt(good,expectedRevision=0).status_code==409
    assert attempt(good,mode='invalid').status_code==400
    unchanged=client.get(f'/api/projects/{pid}').json()
    assert unchanged['revision']==s['revision'] and unchanged['lines'][0]['text']=='残る'
    assert not (core.workdir(pid)/'backups').exists()


def test_excel_ui_exposes_replace_and_confirmation():
    js=(Path(__file__).resolve().parents[1]/'static'/'app.js').read_text(encoding='utf8')
    assert 'chooseExcelImport' in js and "value=\"replace\"" in js
    assert 'if(mode===\'replace\' && !confirm(' in js
    assert "form.append('mode',mode)" in js


def test_cast_anime_model_is_default_for_legacy_and_new_lines(client):
    initial = project(client)
    pid = initial['id']
    current = client.get(f'/api/projects/{pid}').json()
    sid = current['scenes'][0]['id']
    anime = 'phasefield-audio/Irodori-TTS-v4.1-Anime'
    current['voices'] = [{'name': '真歩流', 'engine': 'irodori', 'mode': 'clone', 'model': anime, 'refs': []}]
    for i in range(164):
        item = core.new_line(sid, '真歩流', f'セリフ{i}')
        if i < 163:
            item.pop('ttsSource', None)
        current['lines'].append(item)
    saved = client.put(f'/api/projects/{pid}', json={
        'state': current, 'expectedRevision': current['revision']}).json()
    response = client.post(f'/api/projects/{pid}/generate/preview', json={
        'lineIds': [line['id'] for line in saved['lines']]})
    assert response.status_code == 200, response.text
    assert response.json()['total'] == 164
    assert response.json()['groups'] == [{'engine':'irodori','model':anime,'count':164}]
    assert core.resolve_line_tts(saved['lines'][0], saved['voices'][0])['model'] == anime
    assert core.resolve_line_tts(saved['lines'][-1], saved['voices'][0])['source'] == 'cast'


def test_individual_override_keeps_custom_small_even_with_anime_cast(client):
    s=project(client)
    anime = 'phasefield-audio/Irodori-TTS-v4.1-Anime'
    s['voices'] = [{'name':'愛理','engine':'irodori','model':anime,'mode':'clone','refs':[]}]
    sid = s['scenes'][0]['id']
    normal = core.new_line(sid,'愛理','通常はAnime')
    explicit = core.new_line(sid,'愛理','このセリフだけSmall')
    explicit['ttsSource'] = 'line'
    legacy_custom = core.new_line(sid,'愛理','旧版のカスタムモデル')
    legacy_custom.pop('ttsSource',None)
    legacy_custom['model'] = 'Aratako/Irodori-TTS-v4-Large'
    s['lines'] = [normal,explicit,legacy_custom]
    r=client.put(f'/api/projects/{s["id"]}',json={'state':s,'expectedRevision':s['revision']})
    assert r.status_code==200,r.text
    preview=client.post(f'/api/projects/{s["id"]}/generate/preview',json={
        'lineIds':[x['id'] for x in s['lines']]})
    assert preview.status_code==200,preview.text
    assert preview.json()['groups']==[
        {'engine':'irodori','model':anime,'count':1},
        {'engine':'irodori','model':'Aratako/Irodori-TTS-v4.1-Small','count':1},
        {'engine':'irodori','model':'Aratako/Irodori-TTS-v4-Large','count':1},
    ]


def test_actual_generation_sends_cast_anime_model(client,monkeypatch):
    import app as api
    import json
    s=project(client)
    anime='phasefield-audio/Irodori-TTS-v4.1-Anime'
    s['voices']=[{'name':'真歩流','engine':'irodori','model':anime,'mode':'clone','refs':[]}]
    sid=s['scenes'][0]['id']
    line=core.new_line(sid,'真歩流','テスト用台詞')
    line.pop('ttsSource',None)
    s['lines']=[line]
    s=client.put(f'/api/projects/{s["id"]}',json={'state':s,'expectedRevision':s['revision']}).json()
    monkeypatch.setattr(api,'python_for',lambda *_:sys.executable)
    captured=[]
    def fake_run(cmd,**kw):
        job=json.loads(Path(cmd[-2]).read_text(encoding='utf-8'))
        captured.append(job)
        items=[]
        for it in job['items']:
            out=Path(it['output'])
            sf.write(out,np.zeros(4410,dtype='float32'),44100)
            items.append({'id':it['id'],'ok':True,'output':str(out)})
        Path(cmd[-1]).write_text(json.dumps({'ok':True,'items':items}),encoding='utf-8')
        return type('Result',(),{'returncode':0})()
    monkeypatch.setattr(api.subprocess,'run',fake_run)
    jid=core.uid('job')
    api.JOBS[jid]={'status':'running','completed':0,'total':0,'errors':[],'message':''}
    api.run_generation(jid,s['id'],[line['id']],40)
    assert api.JOBS[jid]['status']=='done',api.JOBS[jid]
    assert captured[0]['model']==anime
    done=client.get(f'/api/projects/{s["id"]}').json()
    assert len(done['lines'][0]['takes'])==1
    assert done['lines'][0]['status']=='ready'


def test_legacy_small_model_with_anime_cast_marks_previous_take_stale(client):
    s=project(client)
    sid=s['scenes'][0]['id']
    line=core.new_line(sid,'真歩流','こんにちは')
    line.pop('ttsSource',None)
    s['lines']=[line]
    s=client.put(f'/api/projects/{s["id"]}',json={'state':s,'expectedRevision':s['revision']}).json()
    audio=upload(client,s['id'],kind='voice')
    s=client.post(f'/api/projects/{s["id"]}/lines/{line["id"]}/attach/{audio["id"]}').json()
    assert s['lines'][0]['status']=='ready'
    s['voices']=[{'name':'真歩流','engine':'irodori','model':'phasefield-audio/Irodori-TTS-v4.1-Anime','refs':[]}]
    s=client.put(f'/api/projects/{s["id"]}',json={'state':s,'expectedRevision':s['revision']}).json()
    assert s['lines'][0]['status']=='stale'
    assert client.get(f'/api/projects/{s["id"]}').json()['lines'][0]['status']=='stale'


def test_tts_cast_defaults_ui_and_preview_are_available():
    js=(Path(__file__).resolve().parents[1]/'static'/'app.js').read_text(encoding='utf-8')
    assert "l.ttsSource='cast'" in js
    assert "v.model||'Aratako/Irodori-TTS-v4.1-Small'" in js
    assert '/generate/preview' in js
    assert 'lineTtsSource' in js
    assert 'この台詞だけ個別設定' in js
    assert 'このキャストの全台詞へ設定を適用' in js
