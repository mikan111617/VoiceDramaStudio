"""Generating whole VoiceDrama scenes at once, in bounded TTS batches."""
import json
import sys
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import app as api
import core

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(core, 'WORKSPACE', tmp_path)
    monkeypatch.setattr(api, 'EXPORTS_DIR', tmp_path / 'exports')
    return TestClient(api.app)


def scenes_project(client, counts):
    """One project with a scene per count, each with that many lines."""
    state = client.post('/api/projects', json={'title': 'まとめて生成'}).json()
    state['scenes'] = [{'id': core.uid('scene'), 'name': f'第{i + 1}幕'} for i in range(len(counts))]
    state['lines'] = [core.new_line(scene['id'], '真歩流', f'{scene["name"]}の台詞{n}。', '', 0.5)
                      for scene, count in zip(state['scenes'], counts) for n in range(count)]
    saved = client.put(f'/api/projects/{state["id"]}', json={'state': state, 'expectedRevision': state['revision']})
    assert saved.status_code == 200, saved.text
    return saved.json()


def fake_tts(monkeypatch, calls, fail_calls=(), on_call=None):
    monkeypatch.setattr(api, 'python_for', lambda *_: sys.executable)

    def run(cmd, **kwargs):
        job = json.loads(Path(cmd[-2]).read_text(encoding='utf-8'))
        calls.append([item['id'] for item in job['items']])
        if on_call:
            on_call(len(calls))
        if len(calls) in fail_calls:
            return type('Crashed', (), {'returncode': 1, 'stdout': '', 'stderr': 'CUDA out of memory'})()
        rows = []
        for item in job['items']:
            sf.write(item['output'], np.zeros(4410, dtype='float32'), 44100)
            rows.append({'id': item['id'], 'ok': True, 'output': item['output']})
        Path(cmd[-1]).write_text(json.dumps({'ok': True, 'items': rows}), encoding='utf-8')
        return type('Done', (), {'returncode': 0, 'stdout': '', 'stderr': ''})()
    monkeypatch.setattr(api.subprocess, 'run', run)


def run_job(state, ids):
    job_id = core.uid('job')
    api.JOBS[job_id] = {'id': job_id, 'kind': 'generate', 'projectId': state['id'], 'status': 'running',
                        'completed': 0, 'total': 0, 'errors': [], 'message': ''}
    api.run_generation(job_id, state['id'], ids, 40)
    return api.JOBS.pop(job_id)


def test_several_scenes_generate_in_batches_and_fill_every_timeline(client, monkeypatch):
    state = scenes_project(client, [30, 15])
    calls = []
    fake_tts(monkeypatch, calls)
    job = run_job(state, [line['id'] for line in state['lines']])
    assert job['status'] == 'done' and job['message'] == '生成が完了しました'
    assert (job['completed'], job['succeeded'], job['total']) == (45, 45, 45)
    assert [len(batch) for batch in calls] == [40, 5]
    final = client.get(f'/api/projects/{state["id"]}').json()
    assert all(line['status'] == 'ready' and len(line['takes']) == 1 for line in final['lines'])
    for scene in final['scenes']:
        starts = [c['start'] for c in final['clips'] if c['sceneId'] == scene['id']]
        assert len(starts) == (30 if scene['name'] == '第1幕' else 15)
        assert starts == sorted(starts) and starts[0] == 0          # each scene laid out from its start


def test_a_failed_batch_is_reported_and_the_rest_still_generates(client, monkeypatch):
    state = scenes_project(client, [41, 4])
    calls = []
    fake_tts(monkeypatch, calls, fail_calls={1})
    job = run_job(state, [line['id'] for line in state['lines']])
    assert job['status'] == 'done_with_errors' and '40件で失敗' in job['message']
    assert (job['completed'], job['succeeded']) == (45, 5) and len(job['errors']) == 40
    assert 'CUDA out of memory' in job['errors'][0]['error']
    final = client.get(f'/api/projects/{state["id"]}').json()
    assert [line['status'] for line in final['lines']] == ['missing'] * 40 + ['ready'] * 5


def test_a_stop_request_takes_effect_at_the_next_batch(client, monkeypatch):
    state = scenes_project(client, [45])
    calls = []

    def stop_during_first_batch(n):
        if n == 1:
            for job in api.JOBS.values():
                job['cancel'] = True
    fake_tts(monkeypatch, calls, on_call=stop_during_first_batch)
    job = run_job(state, [line['id'] for line in state['lines']])
    assert job['status'] == 'cancelled' and job['completed'] == 40 and '停止' in job['message']
    assert len(calls) == 1
    statuses = [line['status'] for line in client.get(f'/api/projects/{state["id"]}').json()['lines']]
    assert statuses == ['ready'] * 40 + ['missing'] * 5


def test_generate_api_accepts_whole_projects_once_and_exposes_the_running_job(client, monkeypatch):
    state = scenes_project(client, [3, 2])
    ids = [line['id'] for line in state['lines']]
    url = f'/api/projects/{state["id"]}/generate'
    submitted = []
    monkeypatch.setattr(api.THREADS, 'submit', lambda *args: submitted.append(args))
    for bad in ([], ids + ids[:1], ids + ['line_unknown'], [f'line_{i}' for i in range(10001)]):
        assert client.post(url, json={'lineIds': bad}).status_code == 400
    assert client.get(url + '/active').json() is None
    started = client.post(url, json={'lineIds': ids})
    assert started.status_code == 200, started.text
    job = started.json()
    assert job['projectId'] == state['id'] and job['total'] == 5 and len(submitted) == 1
    assert client.get(url + '/active').json()['id'] == job['id']
    again = client.post(url, json={'lineIds': ids[:1]})
    assert again.status_code == 409 and '生成中' in again.json()['detail']
    other = scenes_project(client, [1])
    assert client.post(f'/api/projects/{other["id"]}/generate', json={'lineIds': [other['lines'][0]['id']]}).status_code == 200
    api.JOBS[job['id']]['status'] = 'done'
    assert client.get(url + '/active').json() is None
    for job_id in [job['id'], *[j['id'] for j in list(api.JOBS.values()) if j.get('projectId') == other['id']]]:
        api.JOBS.pop(job_id, None)


def test_script_screen_offers_scene_selection_and_reconnects_to_a_running_job():
    js = (ROOT / 'static' / 'app.js').read_text(encoding='utf-8')
    for term in ('id="generateScenesBtn"', 'function openSceneGeneration', 'function sceneGenerationTargets',
                 'data-scene-gen', 'name="sceneGenScope"', '/generate/active', 'function resumeGeneration',
                 'loadCharacters();resumeGeneration()', 'function refreshDuringJob'):
        assert term in js, term


def test_characters_can_be_generated_on_their_own_from_three_places():
    js = (ROOT / 'static' / 'app.js').read_text(encoding='utf-8')
    # The bulk dialog filters by speaker as well as by scene.
    for term in ('data-speaker-gen=', 'id="speakerGenAll"', 'function speakersUsing',
                 'sceneGenerationTargets(scenes(),scope(),speakers())'):
        assert term in js, term
    # Entry points: script toolbar, a speaker row on the cast screen, a shared character card.
    for term in ('id="generateSpeakersBtn"', "openSceneGeneration({pickSpeakers:true})",
                 'data-generate-speaker=', "openSceneGeneration({speakers:[speakerGen.dataset.generateSpeaker]})",
                 '✦ この作品のこのキャラクターの台詞を生成'):
        assert term in js, term
