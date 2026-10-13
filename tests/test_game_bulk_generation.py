"""Multi-scenario game voice generation supports >500 lines in bounded worker batches."""
import json
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import app as studio
import core
import game_voice as game
import game_voice_api as api


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(core, 'WORKSPACE', tmp_path)
    return TestClient(studio.app)


def _bulk_game(n):
    state=game.create_game('複数のKS')
    state['scenarios']=[
        {'key':'scene1','filename':'scene1.ks','count':n//2,'format':'ks','content':''},
        {'key':'scene2','filename':'scene2.ks','count':n-n//2,'format':'ks','content':''}]
    state['voices']=[{'voiceId':f'v{i:05}', 'scenario':'scene1' if i<n//2 else 'scene2',
        'speaker':'actor','text':f'{i}番目', 'ttsText':f'{i}番目','caption':'',
        'audio':'','stale':False} for i in range(n)]
    game._write(state['id'],state)
    return state


def _stub_tts(monkeypatch, records, job=None, cancel_after_first=False, failure_id=None):
    monkeypatch.setattr(studio,'python_for',lambda *_:'python')
    monkeypatch.setattr(game,'generation_groups',lambda pid,ids:[
        (v,'irodori','fake-model',{'description':'','mode':'design','language':'Japanese'},[])
        for v in game.get_game(pid)['voices'] if v['voiceId'] in ids])
    class Done:
        returncode=0
        stdout=''
        stderr=''
    def fake_run(args,**kwargs):
        payload=json.loads(Path(args[2]).read_text(encoding='utf8'))
        records.append([v['id'] for v in payload['items']])
        rows=[]
        for item in payload['items']:
            failed=item['id']==failure_id
            if not failed:Path(item['output']).write_bytes(b'generated')
            rows.append({'id':item['id'],'ok':not failed,'output':item['output'],
                         'error':'failed intentionally' if failed else ''})
        Path(args[3]).write_text(json.dumps({'ok':True,'items':rows}),encoding='utf8')
        if cancel_after_first:
            job['cancel']=True
        return Done()
    monkeypatch.setattr(game.subprocess,'run',fake_run)


def test_bulk_generation_processes_more_than_500_with_small_batches(client,monkeypatch):
    state=_bulk_game(501)
    batches=[]
    _stub_tts(monkeypatch,batches)
    job={'status':'running','total':0,'completed':0,'errors':[],'message':''}
    game.generate_game_audio(state['id'],[v['voiceId'] for v in state['voices']],40,job)
    assert job['status']=='done',job
    assert job['completed']==job['total']==501
    assert len(batches)==13
    assert sum(map(len,batches))==501
    assert all(1<=len(chunk)<=40 for chunk in batches)
    result=game.get_game(state['id'])
    assert all(v['audio'] and not v['stale'] for v in result['voices'])
    assert set(v['scenario'] for v in result['voices'])=={'scene1','scene2'}


def test_bulk_generation_stops_between_batches(client,monkeypatch):
    state=_bulk_game(85)
    batches=[]
    job={'status':'running','total':0,'completed':0,'errors':[],'message':''}
    _stub_tts(monkeypatch,batches,job,cancel_after_first=True)
    game.generate_game_audio(state['id'],[v['voiceId'] for v in state['voices']],40,job)
    assert job['status']=='cancelled',job
    assert job['completed']==40
    assert job['total']==85
    assert len(batches)==1
    assert sum(bool(v['audio']) for v in game.get_game(state['id'])['voices'])==40


def test_bulk_generation_reports_partial_errors_without_discarding_others(client,monkeypatch):
    state=_bulk_game(42)
    batches=[]
    _stub_tts(monkeypatch,batches,failure_id='v00001')
    job={'status':'running','total':0,'completed':0,'errors':[],'message':''}
    game.generate_game_audio(state['id'],[v['voiceId'] for v in state['voices']],40,job)
    assert job['status']=='done_with_errors'
    assert job['completed']==42
    assert len(job['errors'])==1
    result=game.get_game(state['id'])
    assert result['voices'][1]['audio']==''
    assert all(v['audio'] for i,v in enumerate(result['voices']) if i!=1)


def test_large_generation_api_exposes_active_job_and_prevents_overlap(client,monkeypatch):
    state=_bulk_game(501)
    records=[]
    monkeypatch.setattr(game,'generation_groups',lambda pid,ids:records.append(len(ids)) or [1])
    submitted=[]
    monkeypatch.setattr(api.executor,'submit',lambda *args:submitted.append(args))
    response=client.post(f'/api/game/projects/{state["id"]}/generate',json={
        'voiceIds':[v['voiceId'] for v in state['voices']], 'numSteps':40})
    assert response.status_code==200,response.text
    job=response.json()
    assert job['total']==501 and job['projectId']==state['id']
    assert records==[501] and len(submitted)==1
    active=client.get(f'/api/game/projects/{state["id"]}/generate/active')
    assert active.status_code==200
    assert active.json()['id']==job['id']
    repeat=client.post(f'/api/game/projects/{state["id"]}/generate',json={
        'voiceIds':[state['voices'][0]['voiceId']]})
    assert repeat.status_code==409
    game.GAME_JOBS[job['id']]['status']='done'
    assert client.get(f'/api/game/projects/{state["id"]}/generate/active').json() is None
    game.GAME_JOBS.pop(job['id'],None)


def test_game_audio_health_separates_missing_files_from_stale_audio(client):
    state=_bulk_game(4)
    pid=state['id']
    root=game._folder(pid)/'audio'
    for i in (0,1):
        item=state['voices'][i]
        item['audio']=f'actor/{item["scenario"]}/{item["voiceId"]}.wav'
        path=root/item['audio']
        path.parent.mkdir(parents=True,exist_ok=True)
        path.write_bytes(b'test audio')
    state['voices'][1]['stale']=True
    # Third line claims an audio take that was removed; fourth is ungenerated.
    state['voices'][2]['audio']='actor/scene2/v00002.wav'
    game._write(pid,state)
    health=client.get(f'/api/game/projects/{pid}/audio-health')
    assert health.status_code==200,health.text
    body=health.json()
    assert body['total']==4
    assert body['ready']==1
    assert body['stale']==1
    assert body['missingFile']==1
    assert body['unassigned']==1
    assert body['missingIds']==['v00002']
    assert body['issues'][0]['scenario']=='scene2'
    # The audit is read-only; neither the project nor the files are modified.
    assert game.get_game(pid)['voices']==state['voices']
    assert (root/state['voices'][0]['audio']).is_file()


def test_bulk_generation_continues_after_subprocess_batch_crashes(client,monkeypatch):
    state=_bulk_game(85)
    successes=[]
    _stub_tts(monkeypatch,successes)
    success_runner=game.subprocess.run
    failures=[]
    class Broken:
        returncode=1
        stdout=''
        stderr='CUDA out of memory (simulated)'
    def fail_first_batch(args,**kwargs):
        if not failures:
            failures.append(True)
            return Broken() # No result JSON is written for this batch
        return success_runner(args,**kwargs)
    monkeypatch.setattr(game.subprocess,'run',fail_first_batch)
    job={'status':'running','total':0,'completed':0,'errors':[],'message':''}
    game.generate_game_audio(state['id'],[v['voiceId'] for v in state['voices']],40,job)
    assert job['status']=='done_with_errors',job
    assert job['total']==job['completed']==85
    assert len(job['errors'])==40
    assert all('CUDA out of memory' in e['error'] for e in job['errors'])
    assert [len(x) for x in successes]==[40,5]
    after=game.get_game(state['id'])
    assert sum(bool(v['audio']) for v in after['voices'])==45
    assert sum(not bool(v['audio']) for v in after['voices'])==40

