"""Shared pronunciation dictionary for VoiceDrama and game voice."""
import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import numpy as np
import openpyxl
import pytest
import soundfile as sf
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import core
import game_voice as game
import speech_dict
from app import app

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(core, 'WORKSPACE', tmp_path)
    monkeypatch.setattr(__import__('importlib').import_module('app'), 'EXPORTS_DIR', tmp_path / 'exports')
    return TestClient(app)


def entry(*readings, default=0):
    return {'readings': [{'label': label, 'reading': reading} for label, reading in readings], 'default': default}


MAIKURO = entry(('通常', 'まいくろかん'), ('別読み', 'ぶこくかん'))


def shared(client):
    response = client.get('/api/pronunciation-dictionary')
    assert response.status_code == 200, response.text
    return response.json()


def put_shared(client, dictionary, revision=None):
    if revision is None:
        revision = shared(client)['revision']
    return client.put('/api/pronunciation-dictionary', json={'dictionary': dictionary, 'expectedRevision': revision})


def import_shared(client, body, mode='merge', revision=None):
    if revision is None:
        revision = shared(client)['revision']
    return client.post(f'/api/pronunciation-dictionary/import?expectedRevision={revision}&mode={mode}',
                       files={'file': ('dict.json', json.dumps(body, ensure_ascii=False).encode('utf-8'),
                                       'application/json')})


def wav_bytes(seconds=.2):
    buf = io.BytesIO()
    sf.write(buf, np.zeros(int(16000 * seconds), dtype='float32'), 16000, format='WAV')
    return buf.getvalue()


# --- substitution rules -----------------------------------------------------

def test_longest_word_wins_and_each_place_is_replaced_once():
    d = speech_dict.normalize_dictionary({'舞黒': 'まいくろ', '舞黒館': 'まいくろかん', '館': 'やかた'})
    assert speech_dict.apply('舞黒館と舞黒の館', d) == 'まいくろかんとまいくろのやかた'
    # A reading is never substituted again, even if it contains another word.
    d = speech_dict.normalize_dictionary({'旧館': '舞黒', '舞黒': 'まいくろ'})
    assert speech_dict.apply('旧館', d) == '舞黒'


def test_ascii_word_is_not_replaced_inside_other_letters_or_digits():
    d = speech_dict.normalize_dictionary({'2F': 'にかい'})
    assert speech_dict.apply('12F 2F 2Floor F2F', d) == '12F にかい 2Floor F2F'
    assert speech_dict.apply('2F階、あ2Fい', d) == 'にかい階、あにかいい'
    # The game module keeps its historical helper name for the same function.
    assert game._speech_text('12F 2F 2Floor', {'2F': 'にかい'}) == '12F にかい 2Floor'


def test_legacy_format_is_read_as_one_pattern_with_default_zero():
    assert speech_dict.normalize_dictionary({'舞黒館': ' まいくろかん '}) == {
        '舞黒館': {'readings': [{'label': '通常', 'reading': 'まいくろかん'}], 'default': 0}}
    mixed = speech_dict.normalize_dictionary({'舞黒館': MAIKURO, '朱志香': 'じゅしか'})
    assert mixed['舞黒館'] == MAIKURO and mixed['朱志香']['readings'][0]['reading'] == 'じゅしか'


@pytest.mark.parametrize('bad', [
    'not a dict',
    {f'語{i}': 'よみ' for i in range(1001)},
    {'': 'よみ'},
    {'語' * 101: 'よみ'},
    {'舞黒館': ''},
    {'舞黒館': 'あ' * 121},
    {'舞黒館': {'readings': []}},
    {'舞黒館': {'readings': [{'label': f'{i}', 'reading': 'よみ'} for i in range(11)]}},
    {'舞黒館': {'readings': [{'label': '', 'reading': 'よみ'}]}},
    {'舞黒館': {'readings': [{'label': 'あ' * 41, 'reading': 'よみ'}]}},
    {'舞黒館': {'readings': [{'label': '通常', 'reading': 'よみ'}], 'default': 1}},
    {'舞黒館': {'readings': [{'label': '通常', 'reading': 'よみ'}], 'default': True}},
    {'舞黒館': ['まいくろかん']},
])
def test_dictionary_limits_are_validated(bad):
    with pytest.raises(ValueError):
        speech_dict.normalize_dictionary(bad)


def test_limits_accept_boundary_values():
    big = {f'語{i}': 'よみ' for i in range(1000)}
    big['語' * 100] = entry(*[('あ' * 40, 'い' * 120)] * 9, ('別', 'よみ'), default=9)
    big.pop('語0')
    assert len(speech_dict.normalize_dictionary(big)) == 1000


def test_default_pattern_and_per_line_choice():
    d = speech_dict.normalize_dictionary({'舞黒館': {**MAIKURO, 'default': 1}})
    assert speech_dict.apply('舞黒館へ', d) == 'ぶこくかんへ'
    assert speech_dict.apply('舞黒館へ', d, {'舞黒館': 0}) == 'まいくろかんへ'
    # A stale or malformed choice falls back to the default instead of failing.
    assert speech_dict.apply('舞黒館へ', d, {'舞黒館': 7}) == 'ぶこくかんへ'
    assert speech_dict.apply('舞黒館へ', d, {'舞黒館': True}) == 'ぶこくかんへ'
    assert speech_dict.apply('舞黒館へ', d, {'別の語': 0}) == 'ぶこくかんへ'
    with pytest.raises(ValueError):
        speech_dict.normalize_choices({'舞黒館': 10})


def test_browser_preview_matches_python_substitution():
    node = shutil.which('node')
    if not node:
        pytest.skip('Node.js is not installed')
    d = speech_dict.normalize_dictionary({'舞黒': 'まいくろ', '舞黒館': {**MAIKURO, 'default': 1},
                                          '2F': 'にかい', 'A.B': 'えーびー', '𠮷野': 'よしの'})
    cases = [('舞黒館の2Fと12F、舞黒。', {}), ('舞黒館', {'舞黒館': 0}), ('xA.B A.B 𠮷野家', {})]
    script = ("const vm=require('vm'),fs=require('fs');const ctx=vm.createContext({});"
              f"vm.runInContext(fs.readFileSync({json.dumps(str(ROOT / 'static' / 'pronunciation.js'))},'utf8'),ctx);"
              f"const d={json.dumps(d, ensure_ascii=False)},cases={json.dumps(cases, ensure_ascii=False)};"
              "process.stdout.write(JSON.stringify(cases.map(([t,c])=>ctx.PronunciationDictionary.apply(t,d,c))));"
              "process.stdout.write('\\n'+JSON.stringify(ctx.PronunciationDictionary.choosable('舞黒館と舞黒',d)))")
    out = subprocess.run([node, '-e', script], capture_output=True, text=True, encoding='utf-8', check=True).stdout
    spoken, choosable = out.split('\n')
    assert json.loads(spoken) == [speech_dict.apply(t, d, c) for t, c in cases]
    # Only words actually read are offered; 舞黒 inside 舞黒館 is not separate.
    assert json.loads(choosable) == ['舞黒館']


# --- storage in global_settings.json ----------------------------------------

def test_existing_global_settings_without_dictionary_key_still_loads(client, tmp_path):
    path = tmp_path / 'global_settings.json'
    legacy = {'irodori_dir': 'C:\\Irodori', 'irodori_python': '', 'qwen_python': '', 'revision': 4}
    path.write_text(json.dumps(legacy), encoding='utf-8')
    settings = client.get('/api/settings/global')
    assert settings.status_code == 200, settings.text
    assert settings.json() == legacy
    data = shared(client)
    assert data['dictionary'] == {} and data['revision'] == 4
    # Saving TTS paths keeps the dictionary; saving the dictionary keeps the paths.
    saved = put_shared(client, {'舞黒館': MAIKURO}, 4)
    assert saved.status_code == 200, saved.text
    assert saved.json()['revision'] == 5
    paths = client.put('/api/settings/global', json={'irodori_dir': 'D:\\New', 'irodori_python': '',
                                                     'qwen_python': '', 'expectedRevision': 5})
    assert paths.status_code == 200, paths.text
    assert paths.json() == {'irodori_dir': 'D:\\New', 'irodori_python': '', 'qwen_python': '', 'revision': 6}
    stored = json.loads(path.read_text(encoding='utf-8'))
    assert stored['pronunciationDictionary'] == {'舞黒館': MAIKURO}
    assert stored['irodori_dir'] == 'D:\\New'
    assert core.load_global_settings()['irodori_dir'] == 'D:\\New'


def test_dictionary_saves_use_the_shared_revision_and_reject_stale_screens(client):
    first = shared(client)
    paths = client.put('/api/settings/global', json={'irodori_dir': 'C:\\Irodori', 'irodori_python': '',
                                                     'qwen_python': '', 'expectedRevision': first['revision']})
    assert paths.status_code == 200
    stale = put_shared(client, {'舞黒館': 'まいくろかん'}, first['revision'])
    assert stale.status_code == 409 and '再読み込み' in stale.json()['detail']
    assert import_shared(client, {'舞黒館': 'まいくろかん'}, revision=first['revision']).status_code == 409
    assert shared(client)['dictionary'] == {}
    reloaded = shared(client)
    ok = put_shared(client, {'舞黒館': 'まいくろかん'}, reloaded['revision'])
    assert ok.status_code == 200 and ok.json()['revision'] == reloaded['revision'] + 1
    assert put_shared(client, {'舞黒館': 'ぶこくかん'}, reloaded['revision']).status_code == 409
    assert put_shared(client, {'': 'x'}).status_code == 400


def test_import_merge_keeps_readings_replace_replaces_and_export_is_new_format(client):
    assert put_shared(client, {'舞黒館': 'まいくろかん', '朱志香': 'じゅしか'}).status_code == 200
    merged = import_shared(client, {'舞黒館': 'ぶこくかん', '愛理': 'あいり'})
    assert merged.status_code == 200, merged.text
    body = merged.json()
    assert [p['reading'] for p in body['dictionary']['舞黒館']['readings']] == ['まいくろかん', 'ぶこくかん']
    assert body['dictionary']['舞黒館']['default'] == 0
    assert body['summary']['added'] == 1 and body['summary']['total'] == 3
    assert [c['surface'] for c in body['summary']['conflicts']] == ['舞黒館']
    exported = client.get('/api/pronunciation-dictionary/export')
    assert exported.status_code == 200
    assert 'attachment' in exported.headers['content-disposition']
    assert json.loads(exported.content.decode('utf-8')) == body['dictionary']
    replaced = import_shared(client, {'愛理': {'readings': [{'label': '通常', 'reading': 'あいり'}], 'default': 0}},
                             mode='replace')
    assert replaced.status_code == 200
    assert list(replaced.json()['dictionary']) == ['愛理']
    # Re-importing the exported new-format file restores the merged state.
    again = import_shared(client, body['dictionary'], mode='replace')
    assert again.json()['dictionary'] == body['dictionary']


# --- merging old game-project dictionaries -----------------------------------

def legacy_game(client, title, dictionary, text='舞黒館に来た。', audio=True, modified=None):
    state = client.post('/api/game/projects', json={'title': title}).json()
    state = client.post(f'/api/game/projects/{state["id"]}/import-ks?expectedRevision={state["revision"]}',
                        files=[('files', ('scene1.ks', f'#mahoru\n{text}[p]\n'.encode('utf8'), 'text/plain'))]).json()
    state['pronunciationDictionary'] = dictionary  # written by an older version
    if audio:
        for voice in state['voices']:
            voice['audio'] = f'mahoru/scene1/{voice["voiceId"]}.wav'
            target = game._folder(state['id']) / 'audio' / voice['audio']
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(wav_bytes())
    game._write(state['id'], state)
    if modified:
        os.utime(game._statefile(state['id']), (modified, modified))
    return state


def test_game_dictionaries_merge_once_and_keep_both_conflicting_readings(client):
    newer = legacy_game(client, '新しい作品', {'舞黒館': 'まいくろかん', '朱志香': 'じゅしか'}, modified=2_000_000_000)
    older = legacy_game(client, '古い作品', {'舞黒館': 'ぶこくかん', '朱志香': 'じゅしか', '愛理': 'あいり'},
                        modified=1_000_000_000)
    files = {s['id']: game._statefile(s['id']).read_bytes() for s in (newer, older)}
    data = shared(client)
    d = data['dictionary']
    assert d['舞黒館'] == entry(('通常', 'まいくろかん'), ('古い作品', 'ぶこくかん'))
    assert d['朱志香'] == entry(('通常', 'じゅしか')) and d['愛理'] == entry(('通常', 'あいり'))
    migration = data['migration']
    assert [p['title'] for p in migration['projects']] == ['新しい作品', '古い作品']
    assert migration['conflicts'] == [{'surface': '舞黒館', 'readings': [
        {'reading': 'まいくろかん', 'label': '通常', 'sources': ['新しい作品']},
        {'reading': 'ぶこくかん', 'label': '古い作品', 'sources': ['古い作品']}]}]
    # The original project data stays as a read-only record.
    assert {pid: game._statefile(pid).read_bytes() for pid in files} == files
    # Loading again never merges a second time, even if an old file changes.
    stored = json.loads(game._statefile(older['id']).read_text(encoding='utf8'))
    stored['pronunciationDictionary']['真歩流'] = 'まほる'
    game._statefile(older['id']).write_text(json.dumps(stored, ensure_ascii=False), encoding='utf8')
    again = shared(client)
    assert again == data
    assert len(again['dictionary']['舞黒館']['readings']) == 2 and '真歩流' not in again['dictionary']
    # Generation reads only the shared dictionary: the older project's audio
    # used its own reading, so it alone needs regeneration; no file is deleted.
    old_voice = client.get(f'/api/game/projects/{older["id"]}').json()['voices'][0]
    new_voice = client.get(f'/api/game/projects/{newer["id"]}').json()['voices'][0]
    assert old_voice['stale'] is True and new_voice['stale'] is False
    assert game._speech_inputs(game.get_game(older['id']), old_voice)[0] == 'まいくろかんに来た。'
    assert (game._folder(older['id']) / 'audio' / old_voice['audio']).is_file()
    # Project-level reading edits are refused; the record is not overwritten.
    refused = client.put(f'/api/game/projects/{older["id"]}', json={
        'expectedRevision': game.get_game(older['id'])['revision'],
        'changes': {'pronunciationDictionary': {'舞黒館': 'x'}}})
    assert refused.status_code == 400 and '共通' in refused.json()['detail']
    assert game.get_game(older['id'])['pronunciationDictionary']['舞黒館'] == 'ぶこくかん'


@pytest.mark.parametrize('dictionaries', [
    [{'舞黒館': ''}],
    [{f'甲{i}': 'よみ' for i in range(600)}, {f'乙{i}': 'よみ' for i in range(401)}],
])
def test_failed_merge_does_not_change_the_shared_dictionary(client, tmp_path, dictionaries):
    assert client.get('/api/settings/global').status_code == 200
    before = (tmp_path / 'global_settings.json').read_bytes()
    projects = [legacy_game(client, f'作品{i}', d, audio=False) for i, d in enumerate(dictionaries)]
    failed = client.get('/api/pronunciation-dictionary')
    assert failed.status_code == 400 and '変更していません' in failed.json()['detail']
    assert (tmp_path / 'global_settings.json').read_bytes() == before
    # After the source is fixed, the merge runs (once) and succeeds.
    state = json.loads(game._statefile(projects[0]['id']).read_text(encoding='utf8'))
    state['pronunciationDictionary'] = {'舞黒館': 'まいくろかん'}
    game._statefile(projects[0]['id']).write_text(json.dumps(state, ensure_ascii=False), encoding='utf8')
    assert '舞黒館' in shared(client)['dictionary']


def test_unreadable_game_project_is_skipped_and_reported(client):
    good = legacy_game(client, '読める作品', {'舞黒館': 'まいくろかん'}, audio=False)
    broken = core.WORKSPACE / 'game_voice' / 'g_0123456789abcdef'
    broken.mkdir(parents=True)
    (broken / 'game.json').write_text('{broken', encoding='utf8')
    data = shared(client)
    assert '舞黒館' in data['dictionary']
    assert [p['id'] for p in data['migration']['projects']] == [good['id']]
    assert [u['id'] for u in data['migration']['unreadable']] == ['g_0123456789abcdef']


# --- VoiceDrama: generation and staleness ------------------------------------

def drama_project(client, texts):
    state = client.post('/api/projects', json={'title': '読み方テスト'}).json()
    scene = state['scenes'][0]['id']
    state['lines'] = [core.new_line(scene, '真歩流', text, '静かに') for text in texts]
    saved = client.put(f'/api/projects/{state["id"]}', json={'state': state, 'expectedRevision': state['revision']})
    assert saved.status_code == 200, saved.text
    return saved.json()


def attach_audio(client, state):
    for line in state['lines']:
        asset = client.post(f'/api/projects/{state["id"]}/assets', data={'kind': 'voice'},
                            files={'file': ('take.wav', wav_bytes(), 'audio/wav')}).json()['asset']
        response = client.post(f'/api/projects/{state["id"]}/lines/{line["id"]}/attach/{asset["id"]}')
        assert response.status_code == 200, response.text
    return client.get(f'/api/projects/{state["id"]}').json()


def statuses(client, pid):
    return [line['status'] for line in client.get(f'/api/projects/{pid}').json()['lines']]


def save_drama(client, state):
    response = client.put(f'/api/projects/{state["id"]}', json={'state': state, 'expectedRevision': state['revision']})
    assert response.status_code == 200, response.text
    return client.get(f'/api/projects/{state["id"]}').json()


def test_voicedrama_line_choice_and_dictionary_edits_mark_only_affected_lines(client):
    assert put_shared(client, {'舞黒館': MAIKURO}).status_code == 200
    state = drama_project(client, ['舞黒館に着いた。', '舞黒館は古い。', 'こんにちは。'])
    state = attach_audio(client, state)
    assert statuses(client, state['id']) == ['ready', 'ready', 'ready']
    assets = len(state['assets'])
    state['lines'][0]['pronunciationChoices'] = {'舞黒館': 1}
    state = save_drama(client, state)
    assert [l['status'] for l in state['lines']] == ['stale', 'ready', 'ready']
    # Picking the pattern that equals the default is the same spoken text.
    state['lines'][0]['pronunciationChoices'] = {'舞黒館': 0}
    state = save_drama(client, state)
    assert [l['status'] for l in state['lines']] == ['ready', 'ready', 'ready']
    # Changing the default affects only lines that follow the default.
    assert put_shared(client, {'舞黒館': {**MAIKURO, 'default': 1}}).status_code == 200
    final = client.get(f'/api/projects/{state["id"]}').json()
    assert [l['status'] for l in final['lines']] == ['ready', 'stale', 'ready']
    assert len(final['assets']) == assets
    assert all(core.asset_path(state['id'], a).is_file() for a in final['assets'])
    assert [l['text'] for l in final['lines']] == ['舞黒館に着いた。', '舞黒館は古い。', 'こんにちは。']


def old_signature(line, state):
    """generation_signature before the shared dictionary existed."""
    voice = next((v for v in state['voices'] if v['name'] == line.get('speaker')), {})
    data = {'text': line.get('text'), 'caption': line.get('caption'),
            'effectiveTTS': core.resolve_line_tts(line, voice),
            'profile': {k: voice.get(k) for k in ('refs', 'description', 'refText', 'language', 'model', 'engine', 'mode')}}
    return hashlib.sha256(json.dumps(data, ensure_ascii=False, sort_keys=True).encode()).hexdigest()[:24]


def test_existing_voicedrama_project_without_new_keys_keeps_ready_takes(client):
    state = attach_audio(client, drama_project(client, ['こんにちは。', '舞黒館に着いた。']))
    raw = json.loads(core.state_path(state['id']).read_text(encoding='utf-8'))
    for line in raw['lines']:
        line.pop('pronunciationChoices', None)
        for take in line['takes']:
            take['signature'] = old_signature(line, raw)  # written by an older version
    core.state_path(state['id']).write_text(json.dumps(raw, ensure_ascii=False), encoding='utf-8')
    assert statuses(client, state['id']) == ['ready', 'ready']
    # A dictionary word that the line does not contain changes nothing.
    assert put_shared(client, {'朱志香': 'じゅしか'}).status_code == 200
    assert statuses(client, state['id']) == ['ready', 'ready']
    assert put_shared(client, {'朱志香': 'じゅしか', '舞黒館': 'まいくろかん'}).status_code == 200
    assert statuses(client, state['id']) == ['ready', 'stale']


def test_voicedrama_generation_sends_reading_but_keeps_script_caption_and_excel(client, monkeypatch):
    import app as api
    assert put_shared(client, {'舞黒館': MAIKURO, '朱志香': 'じゅしか'}).status_code == 200
    state = drama_project(client, ['朱志香、舞黒館へ。'])
    state['lines'][0]['pronunciationChoices'] = {'舞黒館': 1}
    state = save_drama(client, state)
    sent = []
    monkeypatch.setattr(api, 'python_for', lambda *_: sys.executable)

    def fake_run(cmd, **kwargs):
        job = json.loads(Path(cmd[-2]).read_text(encoding='utf-8'))
        rows = []
        for item in job['items']:
            sent.append(item)
            sf.write(item['output'], np.zeros(4410, dtype='float32'), 44100)
            rows.append({'id': item['id'], 'ok': True, 'output': item['output']})
        Path(cmd[-1]).write_text(json.dumps({'ok': True, 'items': rows}), encoding='utf-8')
        return type('Finished', (), {'returncode': 0, 'stdout': '', 'stderr': ''})()
    monkeypatch.setattr(api.subprocess, 'run', fake_run)
    job = core.uid('job')
    api.JOBS[job] = {'status': 'running', 'completed': 0, 'total': 0, 'errors': [], 'message': ''}
    api.run_generation(job, state['id'], [state['lines'][0]['id']], 40)
    assert api.JOBS[job]['status'] == 'done', api.JOBS[job]
    assert [item['text'] for item in sent] == ['じゅしか、ぶこくかんへ。']
    assert sent[0]['caption'] == '静かに'
    final = client.get(f'/api/projects/{state["id"]}').json()['lines'][0]
    assert final['text'] == '朱志香、舞黒館へ。' and final['caption'] == '静かに'
    assert final['status'] == 'ready'
    sheet = openpyxl.load_workbook(io.BytesIO(client.get(f'/api/projects/{state["id"]}/excel/export').content)).active
    assert sheet.cell(2, 4).value == '朱志香、舞黒館へ。'


@pytest.mark.parametrize('choices', [{'舞黒館': 'x'}, {'舞黒館': True}, {'': 0}, {'舞黒館': 10}, ['舞黒館']])
def test_voicedrama_validate_state_rejects_invalid_pronunciation_choices(client, choices):
    state = drama_project(client, ['舞黒館。'])
    state['lines'][0]['pronunciationChoices'] = choices
    response = client.put(f'/api/projects/{state["id"]}', json={'state': state, 'expectedRevision': state['revision']})
    assert response.status_code == 400
    state['lines'][0]['pronunciationChoices'] = {'舞黒館': 1}
    assert client.put(f'/api/projects/{state["id"]}',
                      json={'state': state, 'expectedRevision': state['revision']}).status_code == 200


# --- game voice: generation and staleness ------------------------------------

def generate_game(client, monkeypatch, state, voice_ids):
    import app as studio
    monkeypatch.setattr(studio, 'python_for', lambda engine, settings: sys.executable)
    sent = []

    def fake(args, **kwargs):
        data = json.loads(Path(args[2]).read_text(encoding='utf8'))
        rows = []
        for item in data['items']:
            sent.append(item)
            Path(item['output']).write_bytes(wav_bytes())
            rows.append({'ok': True, 'id': item['id'], 'output': item['output']})
        Path(args[3]).write_text(json.dumps({'ok': True, 'items': rows}))
        return type('CP', (), {'returncode': 0, 'stdout': '', 'stderr': ''})()
    monkeypatch.setattr(game.subprocess, 'run', fake)
    job = {'status': 'running', 'total': 0, 'completed': 0, 'message': '', 'errors': []}
    game.generate_game_audio(state['id'], voice_ids, 40, job)
    assert job['status'] == 'done', job
    return sent


def test_game_voice_choice_and_shared_edits_mark_only_affected_voices(client, monkeypatch):
    assert put_shared(client, {'舞黒館': MAIKURO}).status_code == 200
    state = client.post('/api/game/projects', json={'title': '共通辞書'}).json()
    state = client.post(f'/api/game/projects/{state["id"]}/import-ks?expectedRevision={state["revision"]}',
                        files=[('files', ('scene1.ks', '#mahoru\n舞黒館だ。[p]\n舞黒館へ。[p]\nやあ。[p]\n'.encode('utf8'),
                                          'text/plain'))]).json()
    ids = [v['voiceId'] for v in state['voices']]
    state['localCasts'] = {'mahoru': game.default_local_cast('mahoru')}
    state['localCastMode'] = {'mahoru': 'local'}
    game._write(state['id'], state)
    sent = generate_game(client, monkeypatch, state, ids)
    assert [item['text'] for item in sent] == ['まいくろかんだ。', 'まいくろかんへ。', 'やあ。']
    state = client.get(f'/api/game/projects/{state["id"]}').json()
    assert [v['stale'] for v in state['voices']] == [False, False, False]
    assert [v['ttsText'] for v in state['voices']] == ['舞黒館だ。', '舞黒館へ。', 'やあ。']
    chosen = client.put(f'/api/game/projects/{state["id"]}', json={'expectedRevision': state['revision'], 'changes': {
        'voices': [{'voiceId': ids[0], 'pronunciationChoices': {'舞黒館': 1}}]}})
    assert chosen.status_code == 200, chosen.text
    assert [v['stale'] for v in chosen.json()['voices']] == [True, False, False]
    assert chosen.json()['voices'][0]['pronunciationChoices'] == {'舞黒館': 1}
    bad = client.put(f'/api/game/projects/{state["id"]}', json={'expectedRevision': chosen.json()['revision'],
        'changes': {'voices': [{'voiceId': ids[1], 'pronunciationChoices': {'舞黒館': 'x'}}]}})
    assert bad.status_code == 400
    # The shared default changes while the project is closed; opening it marks
    # just the voice that follows the default.
    assert put_shared(client, {'舞黒館': {**MAIKURO, 'default': 1}}).status_code == 200
    opened = client.get(f'/api/game/projects/{state["id"]}').json()
    assert [v['stale'] for v in opened['voices']] == [True, True, False]
    assert all((game._folder(state['id']) / 'audio' / v['audio']).is_file() for v in opened['voices'])
    # Regenerating with the chosen pattern clears only the regenerated voice.
    sent = generate_game(client, monkeypatch, opened, ids[:1])
    assert sent[0]['text'] == 'ぶこくかんだ。'
    assert [v['stale'] for v in client.get(f'/api/game/projects/{state["id"]}').json()['voices']] == [False, True, False]
    # The ZIP export leaves out audio made before a later dictionary change.
    assert put_shared(client, {'舞黒館': {**MAIKURO, 'default': 0}}).status_code == 200
    target = core.WORKSPACE / 'out.zip'
    result = game.make_export_zip(state['id'], target)
    assert result['audio'] == 2 and result['missing'] == 1


def test_existing_game_project_without_new_keys_opens_unchanged(client):
    state = legacy_game(client, '旧作品', {'舞黒館': 'まいくろかん'})
    for voice in state['voices']:
        voice.pop('pronunciationChoices', None)
        voice.pop('speechSignature', None)
    game._write(state['id'], state)
    before = game._statefile(state['id']).read_bytes()
    opened = client.get(f'/api/game/projects/{state["id"]}')
    assert opened.status_code == 200, opened.text
    assert [v['stale'] for v in opened.json()['voices']] == [False]
    assert game._statefile(state['id']).read_bytes() == before
    assert shared(client)['dictionary'] == {'舞黒館': entry(('通常', 'まいくろかん'))}


# --- screens ------------------------------------------------------------------

def test_voicedrama_has_04_pronunciation_view_and_line_choices():
    html = (ROOT / 'static' / 'index.html').read_text(encoding='utf-8')
    js = (ROOT / 'static' / 'app.js').read_text(encoding='utf-8')
    menu = html.split('class="sidebar-top"', 1)[1].split('</div>', 2)[1]
    assert 'id="navPronunciation"' in menu and '<b>読み方</b>' in menu and '>04<' in menu
    assert html.index('pronunciation.js') < html.index('app.js') and 'pronunciation.css' in html
    for term in ("setView('pronunciation')", 'function renderPronunciationView', 'createEditor',
                 'data-pron-choice', 'function linePronunciationHtml', '辞書適用後の読み上げ'):
        assert term in js
    shared_js = (ROOT / 'static' / 'pronunciation.js').read_text(encoding='utf-8')
    for term in ('/api/pronunciation-dictionary', 'expectedRevision', 'data-pd-import-mode',
                 'data-pd-add-pattern', 'name="pd-default"', 'data-pd-delete', '/export'):
        assert term in shared_js
    # The copy-from-game-project action is intentionally not offered.
    assert 'ゲーム音声プロジェクトの辞書をコピー' not in js + shared_js + html
