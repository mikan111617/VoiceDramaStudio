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


def test_export_dialog_offers_joining_reordered_scenes():
    js = (ROOT / 'static' / 'app.js').read_text(encoding='utf-8')
    for term in ('name="exportScope" value="join"', 'id="exportSceneList"', 'data-export-move=',
                 'id="exportSceneGap"', 'sceneIds:joinIds()'):
        assert term in js, term
