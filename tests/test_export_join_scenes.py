"""Joining chosen scenes, in a chosen order, into one finished audio file."""
import io
import sys
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import core
from app import app

ROOT = Path(__file__).resolve().parents[1]
SR = 44100


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(core, 'WORKSPACE', tmp_path)
    monkeypatch.setattr(__import__('importlib').import_module('app'), 'EXPORTS_DIR', tmp_path / 'exports')
    return TestClient(app)


def tone(seconds):
    t = np.arange(int(SR * seconds)) / SR
    buf = io.BytesIO()
    sf.write(buf, (.3 * np.sin(2 * np.pi * 440 * t)).astype('float32'), SR, format='WAV')
    return buf.getvalue()


def three_scenes(client):
    """第1幕 0.25s, 第2幕 0.5s, 第3幕 without audio."""
    project = client.post('/api/projects', json={'title': 'つなげる'}).json()
    project['scenes'] = [{'id': core.uid('scene'), 'name': name} for name in ('第1幕', '第2幕', '第3幕')]
    project['lines'] = [core.new_line(scene['id'], '語り手', scene['name'], '', 0) for scene in project['scenes']]
    project = client.put(f'/api/projects/{project["id"]}', json={'state': project, 'expectedRevision': project['revision']}).json()
    for line, seconds in zip(project['lines'][:2], (0.25, 0.5)):
        asset = client.post(f'/api/projects/{project["id"]}/assets', data={'kind': 'voice'},
                            files={'file': ('line.wav', tone(seconds), 'audio/wav')}).json()['asset']
        assert client.post(f'/api/projects/{project["id"]}/lines/{line["id"]}/attach/{asset["id"]}').status_code == 200
    return client.get(f'/api/projects/{project["id"]}').json()


def render(client, pid, **body):
    return client.post(f'/api/projects/{pid}/render', json={'format': 'wav', 'title': 'テスト', 'overwrite': True, **body})


def loud(audio, start, end):
    return float(np.abs(audio[int(start * SR):int(end * SR)]).max(initial=0))


def test_chosen_scenes_are_joined_in_the_chosen_order_with_the_gap(client):
    state = three_scenes(client)
    first, second, _ = [scene['id'] for scene in state['scenes']]
    response = render(client, state['id'], sceneIds=[second, first], sceneGap=1.0)
    assert response.status_code == 200, response.text
    info = response.json()['info']
    assert info['duration'] == pytest.approx(1.75, abs=0.01)          # 0.5 + 1.0 gap + 0.25
    assert [(s['name'], s['start']) for s in info['scenes']] == [('第2幕', 0.0), ('第1幕', 1.5)]
    audio, rate = sf.read(io.BytesIO(client.get(response.json()['url']).content), always_2d=True)
    assert rate == SR and len(audio) == pytest.approx(1.75 * SR, abs=2)
    assert loud(audio, 0.05, 0.45) > 0.1          # 第2幕 first
    assert loud(audio, 0.55, 1.45) == 0           # the silence between scenes
    assert loud(audio, 1.55, 1.7) > 0.1           # then 第1幕


def test_one_scene_and_every_scene_still_export_as_before(client):
    state = three_scenes(client)
    first = state['scenes'][0]['id']
    single = render(client, state['id'], sceneId=first).json()['info']
    assert single['duration'] == pytest.approx(0.25, abs=0.01) and single['scenes'] == []
    every = render(client, state['id']).json()['info']                 # no range: all scenes, 0.5s apart
    assert every['duration'] == pytest.approx(0.25 + 0.5 + 0.5, abs=0.01)
    assert [s['name'] for s in every['scenes']] == ['第1幕', '第2幕', '第3幕']


def test_join_rejects_unknown_repeated_or_conflicting_scenes(client):
    state = three_scenes(client)
    first, second, _ = [scene['id'] for scene in state['scenes']]
    for body in ({'sceneIds': []}, {'sceneIds': [first, first]}, {'sceneIds': [first, 'scene_unknown']},
                 {'sceneIds': [first], 'sceneId': second}, {'sceneIds': [first], 'sceneGap': 61},
                 {'sceneIds': [first], 'sceneGap': -1}):
        response = render(client, state['id'], **body)
        assert response.status_code == 400, (body, response.text)


def two_long_scenes(client, first_seconds=2.0, second_seconds=0.5):
    project = client.post('/api/projects', json={'title': '切り替わり'}).json()
    project['scenes'] = [{'id': core.uid('scene'), 'name': name} for name in ('前の場面', '次の場面')]
    project['lines'] = [core.new_line(scene['id'], '語り手', scene['name'], '', 0) for scene in project['scenes']]
    project = client.put(f'/api/projects/{project["id"]}', json={'state': project, 'expectedRevision': project['revision']}).json()
    for line, seconds in zip(project['lines'], (first_seconds, second_seconds)):
        asset = client.post(f'/api/projects/{project["id"]}/assets', data={'kind': 'voice'},
                            files={'file': ('line.wav', tone(seconds), 'audio/wav')}).json()['asset']
        assert client.post(f'/api/projects/{project["id"]}/lines/{line["id"]}/attach/{asset["id"]}').status_code == 200
    return client.get(f'/api/projects/{project["id"]}').json()


def test_each_scene_change_can_have_its_own_silence(client):
    state = three_scenes(client)
    first, second, _ = [scene['id'] for scene in state['scenes']]
    response = render(client, state['id'], sceneIds=[second, first], sceneGaps=[2.0])
    assert response.status_code == 200, response.text
    info = response.json()['info']
    assert info['duration'] == pytest.approx(0.5 + 2.0 + 0.25, abs=0.01)
    assert [s['start'] for s in info['scenes']] == [0.0, 2.5]
    for body in ({'sceneIds': [first, second], 'sceneGaps': []}, {'sceneIds': [first, second], 'sceneGaps': [1, 1]},
                 {'sceneIds': [first, second], 'sceneGaps': [61]}, {'sceneGaps': [1]}):
        assert render(client, state['id'], **body).status_code == 400, body


def test_transition_preview_is_the_same_audio_as_the_joined_export(client):
    state = two_long_scenes(client)
    before, after = [scene['id'] for scene in state['scenes']]
    full = render(client, state['id'], sceneIds=[before, after], sceneGaps=[1.5])
    exported, _ = sf.read(io.BytesIO(client.get(full.json()['url']).content), always_2d=True)
    preview = client.post(f'/api/projects/{state["id"]}/render/transition', json={
        'fromSceneId': before, 'toSceneId': after, 'gap': 1.5, 'before': 1, 'after': 1})
    assert preview.status_code == 200, preview.text
    assert preview.headers['content-type'] == 'audio/wav'
    assert (preview.headers['x-scene-change'], preview.headers['x-next-scene']) == ('1.000', '2.500')
    audio, rate = sf.read(io.BytesIO(preview.content), always_2d=True)
    # The last second of the first scene, 1.5 s of silence, then the next scene.
    assert rate == SR and len(audio) == pytest.approx((1 + 1.5 + 1) * SR, abs=2)
    assert loud(audio, 0.05, 0.95) > 0.1 and loud(audio, 1.05, 2.45) == 0 and loud(audio, 2.55, 2.95) > 0.1
    window = exported[int(1.0 * SR):int(1.0 * SR) + len(audio)]
    assert np.abs(window[:len(audio)] - audio[:len(window)]).max() < 1e-3


def test_transition_preview_rejects_bad_requests(client):
    state = two_long_scenes(client)
    before, after = [scene['id'] for scene in state['scenes']]
    url = f'/api/projects/{state["id"]}/render/transition'
    for body in ({'fromSceneId': before, 'toSceneId': before}, {'fromSceneId': before, 'toSceneId': 'scene_x'},
                 {'fromSceneId': before, 'toSceneId': after, 'gap': 61},
                 {'fromSceneId': before, 'toSceneId': after, 'before': 0.5},
                 {'fromSceneId': before, 'toSceneId': after, 'after': 31}):
        assert client.post(url, json=body).status_code == 400, body


def test_join_settings_are_saved_with_the_project_and_checked(client):
    state = three_scenes(client)
    ids = [scene['id'] for scene in state['scenes']]

    def save(join):
        current = client.get(f'/api/projects/{state["id"]}').json()
        current['settings'] = {**current['settings'], 'sceneJoin': join}
        return client.put(f'/api/projects/{state["id"]}', json={'state': current, 'expectedRevision': current['revision']})
    good = {'order': ids[::-1], 'picked': ids[:2], 'gaps': {ids[0]: 2.5}, 'defaultGap': 1, 'previewSeconds': 5}
    assert save(good).status_code == 200
    assert client.get(f'/api/projects/{state["id"]}').json()['settings']['sceneJoin'] == good
    for bad in ({**good, 'gaps': {ids[0]: 99}}, {**good, 'order': 'all'}, {**good, 'previewSeconds': 0},
                {**good, 'defaultGap': True}, 'join'):
        assert save(bad).status_code == 400, bad


def test_export_dialog_offers_joining_reordered_scenes():
    js = (ROOT / 'static' / 'app.js').read_text(encoding='utf-8')
    for term in ('name="exportScope" value="join"', 'id="exportSceneList"', 'data-export-move=',
                 'data-export-gap=', 'id="exportGapApply"', 'id="exportPreviewSeconds"', 'data-export-preview=',
                 '/render/transition', 'sceneGaps:joinIds().slice(0,-1).map(gapAfter)', 'sceneJoin:'):
        assert term in js, term
