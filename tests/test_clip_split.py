"""Split dialogue clips: they render like the original, follow their line, and reset on a new take."""
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


def wav(samples):
    buf = io.BytesIO()
    sf.write(buf, samples.astype('float32'), SR, format='WAV', subtype='FLOAT')
    return buf.getvalue()


VOICE = (np.random.default_rng(7).uniform(-.4, .4, SR)).astype('float32')     # 1.0 s, every sample distinct


def two_lines(client):
    project = client.post('/api/projects', json={'title': '分割'}).json()
    sid = project['scenes'][0]['id']
    project['lines'] = [core.new_line(sid, '愛理', '海が見える。', '', 0), core.new_line(sid, '真歩流', '行こう！', '', 0)]
    pid = project['id']
    project = client.put(f'/api/projects/{pid}', json={'state': project, 'expectedRevision': project['revision']}).json()
    for line, audio in zip(project['lines'], (VOICE, VOICE[:SR // 2] * .5)):
        asset = client.post(f'/api/projects/{pid}/assets', data={'kind': 'voice'},
                            files={'file': ('line.wav', wav(audio), 'audio/wav')}).json()['asset']
        assert client.post(f'/api/projects/{pid}/lines/{line["id"]}/attach/{asset["id"]}').status_code == 200
    return client.get(f'/api/projects/{pid}').json()


def split(state, clip, at):
    """What the editor's ✂ button saves: the clip keeps the left part, a piece plays on."""
    off = round(at - clip['start'], 4)
    piece = {**clip, 'id': core.uid('clip'), 'lineId': None, 'pieceOf': clip.get('lineId') or clip.get('pieceOf'),
             'anchorLineId': clip.get('lineId') or clip.get('pieceOf'), 'start': round(clip['start'] + off, 4),
             'duration': round(clip['duration'] - off, 4), 'trimStart': round(clip.get('trimStart', 0) + off, 4), 'fadeIn': 0}
    head = next(c for c in state['clips'] if c.get('lineId') == piece['pieceOf'])
    piece['anchorOffset'] = round(piece['start'] - head['start'], 4)
    clip['duration'], clip['fadeOut'] = off, 0
    state['clips'].insert(state['clips'].index(clip) + 1, piece)
    return piece


def save(client, state):
    r = client.put(f'/api/projects/{state["id"]}', json={'state': state, 'expectedRevision': state['revision']})
    assert r.status_code == 200, r.text
    return r.json()


def mix(client, state):
    r = client.post(f'/api/projects/{state["id"]}/render',
                    json={'format': 'wav', 'title': '分割', 'overwrite': True, 'sceneId': state['scenes'][0]['id']})
    assert r.status_code == 200, r.text
    audio, rate = sf.read(io.BytesIO(client.get(r.json()['url']).content), always_2d=True)
    return audio[:, 0]


def head_of(state, line):
    return next(c for c in state['clips'] if c.get('lineId') == line['id'])


def test_a_split_clip_renders_exactly_like_the_whole_clip(client):
    state = two_lines(client)
    before = mix(client, state)
    piece = split(state, head_of(state, state['lines'][0]), 0.4)
    state = save(client, state)
    assert any(c.get('pieceOf') == state['lines'][0]['id'] for c in state['clips']) and piece['trimStart'] == 0.4
    after = mix(client, state)
    assert len(after) == len(before) and np.array_equal(after, before)


def test_cutting_out_a_piece_closes_the_gap_and_pulls_the_next_line_forward(client):
    state = two_lines(client)
    before = mix(client, state)
    first, second = state['lines']
    middle = split(state, head_of(state, first), 0.4)
    rest = split(state, middle, 0.6)
    state['clips'].remove(middle)                       # 🗑 削除して後ろを詰める
    rest['start'], rest['anchorOffset'] = 0.4, 0.4
    core.reflow_dialogue(state, state['scenes'][0]['id'])
    assert head_of(state, second)['start'] == pytest.approx(0.8)      # after the pieces, not the 0.4 s head
    state = save(client, state)
    audio = mix(client, state)
    assert len(audio) == pytest.approx(1.3 * SR, abs=2)
    kept = np.concatenate([before[:int(.4 * SR)], before[int(.6 * SR):SR]])
    assert np.array_equal(audio[:int(.8 * SR)], kept)                # 0.4–0.6 s cut out, the rest joined


def test_reflow_moves_pieces_with_their_line(client):
    state = two_lines(client)
    first, second = state['lines']
    split(state, head_of(state, second), 0.25 + 1.0)
    first['after'] = 0.5
    core.reflow_dialogue(state, state['scenes'][0]['id'])
    piece = next(c for c in state['clips'] if c.get('pieceOf') == second['id'])
    assert head_of(state, second)['start'] == pytest.approx(1.5) and piece['start'] == pytest.approx(1.75)


def test_a_new_take_replaces_the_split_voice_with_the_whole_audio(client):
    state = two_lines(client)
    first = state['lines'][0]
    head = head_of(state, first)
    head['fadeOut'] = 0.2
    split(state, head, 0.5)
    state = save(client, state)
    asset = client.post(f'/api/projects/{state["id"]}/assets', data={'kind': 'voice'},
                        files={'file': ('retake.wav', wav(VOICE[:int(.7 * SR)]), 'audio/wav')}).json()['asset']
    state = client.post(f'/api/projects/{state["id"]}/lines/{first["id"]}/attach/{asset["id"]}').json()
    assert not any(c.get('pieceOf') == first['id'] for c in state['clips'])
    head = head_of(state, first)
    assert (head['assetId'], head['duration'], head['trimStart'], head['fadeOut']) == (asset['id'], asset['duration'], 0.0, 0.2)
    assert head_of(state, state['lines'][1])['start'] == pytest.approx(asset['duration'])


def test_editor_offers_split_close_gap_and_restore():
    js = (ROOT / 'static' / 'app.js').read_text(encoding='utf-8')
    html = (ROOT / 'static' / 'index.html').read_text(encoding='utf-8')
    for term in ('function splitSelectedAtPlayhead', 'function deleteClipClosingGap', 'function restoreLineVoice',
                 '✂ 再生位置でこのクリップを分割', '🗑 削除して後ろを詰める', '↺ この台詞の音声を元の長さに戻す',
                 "e.key.toLowerCase()==='s'", 'function paintWaveforms'):
        assert term in js, term
    assert 'id="splitClipsBtn"' in html and '✂ 選択クリップを再生位置で分割' in html
