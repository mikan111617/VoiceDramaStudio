"""Characters shared by every VoiceDrama project and game project."""
import io
import json
import sys
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import characters
import core
import game_voice as game
from app import app

ROOT = Path(__file__).resolve().parents[1]
ANIME = 'phasefield-audio/Irodori-TTS-v4.1-Anime'


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(core, 'WORKSPACE', tmp_path)
    monkeypatch.setattr(__import__('importlib').import_module('app'), 'EXPORTS_DIR', tmp_path / 'exports')
    return TestClient(app)


def wav_bytes(freq=220):
    buf = io.BytesIO()
    t = np.arange(8000) / 16000
    sf.write(buf, (0.1 * np.sin(2 * np.pi * freq * t)).astype('float32'), 16000, format='WAV')
    return buf.getvalue()


def registry(client):
    response = client.get('/api/characters')
    assert response.status_code == 200, response.text
    return response.json()


def create(client, **spec):
    response = client.post('/api/characters', json={'character': spec, 'expectedRevision': registry(client)['revision']})
    assert response.status_code == 200, response.text
    return next(c for c in response.json()['characters'] if c['name'] == spec['name'])


def update(client, character, **changes):
    response = client.put(f'/api/characters/{character["id"]}', json={
        'character': {**character, **changes}, 'expectedRevision': registry(client)['revision']})
    assert response.status_code == 200, response.text
    return next(c for c in response.json()['characters'] if c['id'] == character['id'])


def add_reference(client, character, freq=220):
    response = client.post(f'/api/characters/{character["id"]}/references?expectedRevision={registry(client)["revision"]}',
                           files={'file': ('voice.wav', wav_bytes(freq), 'audio/wav')})
    assert response.status_code == 200, response.text
    return next(c for c in response.json()['characters'] if c['id'] == character['id'])


def fake_tts(monkeypatch, module, sent):
    import app as studio
    monkeypatch.setattr(studio, 'python_for', lambda *_: sys.executable)

    def run(cmd, **kwargs):
        job = json.loads(Path(cmd[-2]).read_text(encoding='utf-8'))
        rows = []
        for item in job['items']:
            sent.append({**item, 'model': job['model'], 'engine': job['engine']})
            sf.write(item['output'], np.zeros(4410, dtype='float32'), 44100)
            rows.append({'id': item['id'], 'ok': True, 'output': item['output']})
        Path(cmd[-1]).write_text(json.dumps({'ok': True, 'items': rows}), encoding='utf-8')
        return type('Done', (), {'returncode': 0, 'stdout': '', 'stderr': ''})()
    monkeypatch.setattr(module.subprocess, 'run', run)


# --- the registry ---------------------------------------------------------------

def test_registry_validates_unique_names_and_aliases_and_detects_conflicts(client):
    assert registry(client) == {'revision': 0, 'characters': [], 'assets': []}
    assert not (core.WORKSPACE / 'characters').exists()  # reading creates nothing
    mahoru = create(client, name='真歩流', aliases=['mahoru', 'ＭＡＨＯＲＵ', ' '], model=ANIME, description='明るい')
    assert mahoru['aliases'] == ['mahoru'] and mahoru['engine'] == 'irodori' and mahoru['mode'] == 'clone'
    assert mahoru['model'] == ANIME and mahoru['language'] == 'Japanese' and mahoru['refs'] == []
    revision = registry(client)['revision']
    for bad in ({'name': ''}, {'name': '真歩流'}, {'name': 'まほる', 'aliases': ['MAHORU']},
                {'name': 'x', 'engine': 'other'}, {'name': 'x', 'language': 'Klingon'},
                {'name': 'x', 'refs': ['cref_0000000000000000']}, {'name': 'あ' * 61}):
        response = client.post('/api/characters', json={'character': bad, 'expectedRevision': revision})
        assert response.status_code == 400, (bad, response.text)
    assert registry(client)['revision'] == revision
    stale = client.post('/api/characters', json={'character': {'name': '愛理'}, 'expectedRevision': revision - 1})
    assert stale.status_code == 409 and '再読み込み' in stale.json()['detail']
    # Renaming keeps the old name as an alias so scripts using it still match.
    renamed = update(client, mahoru, name='真白真歩流')
    assert renamed['aliases'] == ['mahoru', '真歩流']
    assert characters.match(registry(client), ['真歩流'])['id'] == mahoru['id']


def test_reference_audio_upload_unlink_and_delete_keep_files(client):
    airi = create(client, name='愛理')
    airi = add_reference(client, airi)
    aid = airi['refs'][0]
    asset = next(a for a in registry(client)['assets'] if a['id'] == aid)
    path = characters.asset_path(asset)
    assert path.parent == core.WORKSPACE / 'characters' / 'reference_audio' and path.is_file()
    assert client.get(f'/api/characters/assets/{aid}').content == path.read_bytes()
    bad = client.post(f'/api/characters/{airi["id"]}/references?expectedRevision={registry(client)["revision"]}',
                      files={'file': ('notes.txt', b'hello', 'text/plain')})
    assert bad.status_code == 400
    unlinked = client.delete(f'/api/characters/{airi["id"]}/references/{aid}?expectedRevision={registry(client)["revision"]}')
    assert unlinked.status_code == 200 and unlinked.json()['characters'][0]['refs'] == []
    deleted = client.delete(f'/api/characters/{airi["id"]}?expectedRevision={registry(client)["revision"]}')
    assert deleted.status_code == 200 and deleted.json()['characters'] == []
    assert path.is_file() and [a['id'] for a in registry(client)['assets']] == [aid]


# --- VoiceDrama -----------------------------------------------------------------

def drama(client, speakers, voices=()):
    state = client.post('/api/projects', json={'title': '共通キャラクター'}).json()
    scene = state['scenes'][0]['id']
    state['lines'] = [core.new_line(scene, speaker, f'{speaker}の台詞{i}。') for i, speaker in enumerate(speakers)]
    state['voices'] = list(voices)
    saved = client.put(f'/api/projects/{state["id"]}', json={'state': state, 'expectedRevision': state['revision']})
    assert saved.status_code == 200, saved.text
    return saved.json()


def attach_takes(client, state):
    for line in state['lines']:
        asset = client.post(f'/api/projects/{state["id"]}/assets', data={'kind': 'voice'},
                            files={'file': ('take.wav', wav_bytes(), 'audio/wav')}).json()['asset']
        assert client.post(f'/api/projects/{state["id"]}/lines/{line["id"]}/attach/{asset["id"]}').status_code == 200
    return client.get(f'/api/projects/{state["id"]}').json()


def statuses(client, pid):
    return [line['status'] for line in client.get(f'/api/projects/{pid}').json()['lines']]


def generate_drama(client, monkeypatch, state):
    import app as api
    sent = []
    fake_tts(monkeypatch, api, sent)
    job = core.uid('job')
    api.JOBS[job] = {'status': 'running', 'completed': 0, 'total': 0, 'errors': [], 'message': ''}
    api.run_generation(job, state['id'], [line['id'] for line in state['lines']], 40)
    assert api.JOBS[job]['status'] == 'done', api.JOBS[job]
    return sent


def test_voicedrama_lines_use_shared_character_by_name_alias_or_choice(client, monkeypatch):
    local = {'name': '愛理', 'engine': 'irodori', 'model': 'Aratako/Irodori-TTS-v4.1-Small', 'mode': 'clone',
             'language': 'Japanese', 'description': 'この作品の愛理', 'refText': '', 'refs': []}
    state = drama(client, ['真歩流', 'mahoru', '愛理', '零度'], [local])
    mahoru = add_reference(client, create(client, name='真歩流', aliases=['mahoru'], model=ANIME, description='共通の真歩流'))
    sent = generate_drama(client, monkeypatch, state)
    by_speaker = {item['text'].split('の')[0]: item for item in sent}
    for speaker in ('真歩流', 'mahoru'):
        assert by_speaker[speaker]['model'] == ANIME and by_speaker[speaker]['caption'] == '共通の真歩流'
        assert by_speaker[speaker]['refs'] and Path(by_speaker[speaker]['refs'][0]).parent.name == 'reference_audio'
        assert Path(by_speaker[speaker]['refs'][0]).parents[1].name == 'characters'
    assert by_speaker['愛理']['caption'] == 'この作品の愛理'      # no shared 愛理: the project's cast
    assert by_speaker['零度']['refs'] == []                       # nobody: the standard voice
    assert statuses(client, state['id']) == ['ready'] * 4
    # A shared 愛理 takes over; choosing "project" keeps this work's own cast.
    create(client, name='愛理', description='共通の愛理')
    assert statuses(client, state['id']) == ['ready', 'ready', 'stale', 'ready']
    state = client.get(f'/api/projects/{state["id"]}').json()
    state['characterLinks'] = {'愛理': 'project', '零度': mahoru['id']}
    saved = client.put(f'/api/projects/{state["id"]}', json={'state': state, 'expectedRevision': state['revision']})
    assert saved.status_code == 200, saved.text
    assert statuses(client, state['id']) == ['ready', 'ready', 'ready', 'stale']
    state = saved.json()
    state['characterLinks'] = {'愛理': 'somebody'}
    bad = client.put(f'/api/projects/{state["id"]}', json={'state': state, 'expectedRevision': state['revision']})
    assert bad.status_code == 400


def test_existing_project_casts_keep_working_without_shared_characters(client):
    voice = {'name': '零度', 'engine': 'irodori', 'model': ANIME, 'mode': 'clone', 'language': 'Japanese',
             'description': '', 'refText': '', 'refs': []}
    state = attach_takes(client, drama(client, ['零度'], [voice]))
    assert statuses(client, state['id']) == ['ready']
    assert core.resolve_voice(state, '零度')['model'] == ANIME
    assert not (core.WORKSPACE / 'characters').exists()


def test_import_from_voicedrama_keeps_reference_ids_and_takes_current(client):
    source = client.post('/api/projects', json={'title': '第一作'}).json()
    ref = client.post(f'/api/projects/{source["id"]}/assets', data={'kind': 'reference'},
                      files={'file': ('mahoru.wav', wav_bytes(), 'audio/wav')}).json()['asset']
    voice = {'name': '真歩流', 'engine': 'irodori', 'model': ANIME, 'mode': 'clone', 'language': 'Japanese',
             'description': '元気', 'refText': '', 'refs': [ref['id']]}
    source = client.get(f'/api/projects/{source["id"]}').json()
    source['voices'] = [voice]
    source['lines'] = [core.new_line(source['scenes'][0]['id'], '真歩流', 'おはよう。'),
                       core.new_line(source['scenes'][0]['id'], '愛理', 'こんにちは。')]
    source = client.put(f'/api/projects/{source["id"]}', json={'state': source, 'expectedRevision': source['revision']}).json()
    source = attach_takes(client, source)
    other = drama(client, ['真歩流'], [{**voice, 'model': 'Aratako/Irodori-TTS-v4-Large', 'refs': []}])
    other = attach_takes(client, other)
    sources = client.get('/api/characters/sources').json()
    first = next(s for s in sources if s['id'] == source['id'])
    assert first['kind'] == 'drama' and first['characters'][0]['name'] == '真歩流' and first['characters'][0]['refs'] == 1
    before = core.state_path(other['id']).read_bytes()
    result = client.post('/api/characters/import', json={'source': {'kind': 'drama', 'id': source['id']},
        'keys': ['真歩流'], 'conflict': 'skip', 'applyToSource': True, 'expectedRevision': 0})
    assert result.status_code == 200, result.text
    body = result.json()
    assert body['summary']['added'] == ['真歩流'] and body['summary']['applied'] == 1
    shared = body['registry']['characters'][0]
    assert shared['refs'] == [ref['id']] and shared['model'] == ANIME
    copied = characters.asset_path(body['registry']['assets'][0])
    assert copied.read_bytes() == core.asset_path(source['id'], ref).read_bytes()
    assert core.asset_path(source['id'], ref).is_file()           # the source keeps its file
    # Same settings and reference IDs: the source's takes stay current. The
    # other work had a different 真歩流, so its take needs regeneration.
    assert statuses(client, source['id']) == ['ready', 'ready']
    assert statuses(client, other['id']) == ['stale']
    assert core.state_path(other['id']).read_bytes() == before
    linked = client.get(f'/api/projects/{source["id"]}').json()
    assert linked['characterLinks'] == {'真歩流': shared['id']} and linked['voices'] == [voice]
    # Editing the shared character affects every work that uses it.
    update(client, shared, description='落ち着いた')
    assert statuses(client, source['id']) == ['stale', 'ready']


def test_excel_import_creates_project_casts_only_for_unknown_speakers(client):
    import openpyxl
    create(client, name='真歩流')
    state = client.post('/api/projects', json={'title': 'Excel'}).json()
    book = openpyxl.Workbook()
    sheet = book.active
    sheet.append(['話者', '台詞'])
    sheet.append(['真歩流', 'おはよう。'])
    sheet.append(['新人', 'はじめまして。'])
    data = io.BytesIO()
    book.save(data)
    response = client.post(f'/api/projects/{state["id"]}/excel/import',
                           files={'file': ('script.xlsx', data.getvalue(), 'application/octet-stream')})
    assert response.status_code == 200, response.text
    assert [v['name'] for v in response.json()['project']['voices']] == ['新人']


# --- game voice -----------------------------------------------------------------

def game_with_sheet(client, ks='#mahoru\nこんにちは。[p]\n#airi\nおはよう。[p]\n'):
    state = client.post('/api/game/projects', json={'title': '舞黒館'}).json()
    sheet = {'characters': {'mahoru': {'display_name': '真歩流', 'aliases': ['まほる']},
                            'airi': {'display_name': '愛理'}}}
    state = client.post(f'/api/game/projects/{state["id"]}/character-sheet?expectedRevision={state["revision"]}',
                        files={'file': ('sheet.json', json.dumps(sheet, ensure_ascii=False).encode(), 'application/json')}).json()
    response = client.post(f'/api/game/projects/{state["id"]}/import-ks?expectedRevision={state["revision"]}',
                           files=[('files', ('scene1.ks', ks.encode('utf8'), 'text/plain'))])
    assert response.status_code == 200, response.text
    return response.json()


def put_game(client, state, **changes):
    response = client.put(f'/api/game/projects/{state["id"]}', json={'expectedRevision': state['revision'], 'changes': changes})
    assert response.status_code == 200, response.text
    return response.json()


def generate_game(client, monkeypatch, state):
    sent = []
    fake_tts(monkeypatch, game, sent)
    job = {'status': 'running', 'total': 0, 'completed': 0, 'message': '', 'errors': []}
    game.generate_game_audio(state['id'], [v['voiceId'] for v in state['voices']], 40, job)
    assert job['status'] == 'done', job
    return sent


def test_game_speakers_use_shared_characters_by_sheet_name_and_explicit_choice(client, monkeypatch):
    state = game_with_sheet(client)
    with pytest.raises(ValueError, match='キャストが未設定'):
        game.generation_groups(state['id'], {state['voices'][0]['voiceId']})
    mahoru = add_reference(client, create(client, name='真歩流', model=ANIME, description='共通の声'))
    airi = create(client, name='愛理')
    plan = {v['speaker']: rest for v, *rest in game.generation_groups(state['id'], {v['voiceId'] for v in state['voices']})}
    assert plan['mahoru'][1] == ANIME and plan['mahoru'][2]['id'] == mahoru['id']
    assert Path(plan['mahoru'][3][0]).parents[1].name == 'characters'
    assert plan['airi'][2]['id'] == airi['id']
    # An explicit choice wins over the name match; a game-only cast wins over both.
    state = put_game(client, state, bindings={'airi': {'characterId': mahoru['id']}})
    assert game.generation_groups(state['id'], {state['voices'][1]['voiceId']})[0][3]['id'] == mahoru['id']
    bad = client.put(f'/api/game/projects/{state["id"]}', json={'expectedRevision': state['revision'],
        'changes': {'bindings': {'airi': {'characterId': 'chr_0000000000000000'}}}})
    assert bad.status_code == 400
    local = game.default_local_cast('mahoru')
    state = put_game(client, state, localCasts={'mahoru': local}, localCastMode={'mahoru': 'local'})
    assert game.generation_groups(state['id'], {state['voices'][0]['voiceId']})[0][3]['name'] == 'mahoru'
    # An older binding to a VoiceDrama cast now follows the shared character of that name.
    project = drama(client, ['愛理'], [{'name': '愛理', 'model': 'Aratako/Irodori-TTS-v4-Large', 'refs': []}])
    state = put_game(client, state, bindings={'airi': {'projectId': project['id'], 'castName': '愛理'}})
    assert game.generation_groups(state['id'], {state['voices'][1]['voiceId']})[0][3]['id'] == airi['id']


def test_game_import_overwrites_same_name_and_switches_scenario_without_stale(client, monkeypatch):
    state = game_with_sheet(client)
    cast = {**game.default_local_cast('mahoru'), 'model': ANIME, 'description': 'ゲームの真歩流'}
    state = put_game(client, state, localCasts={'mahoru': cast}, localCastMode={'mahoru': 'local'})
    state = client.post(f'/api/game/projects/{state["id"]}/casts/mahoru/reference?expectedRevision={state["revision"]}',
                        files={'file': ('mahoru.wav', wav_bytes(), 'audio/wav')}).json()
    ref_id = state['localCasts']['mahoru']['refs'][0]
    old = create(client, name='真歩流', description='古い設定')
    airi = add_reference(client, create(client, name='愛理', description='共通の愛理'), 330)
    generate_game(client, monkeypatch, state)
    state = client.get(f'/api/game/projects/{state["id"]}').json()
    assert [v['stale'] for v in state['voices']] == [False, False]
    drama_state = attach_takes(client, drama(client, ['真歩流']))
    assert statuses(client, drama_state['id']) == ['ready']
    source = next(s for s in client.get('/api/characters/sources').json() if s['id'] == state['id'])
    candidates = {c['key']: c for c in source['characters']}
    assert candidates['mahoru']['name'] == '真歩流' and candidates['mahoru']['existing'] == '真歩流'
    assert candidates['mahoru']['hasVoice'] and candidates['mahoru']['aliases'] == ['mahoru', 'まほる']
    assert candidates['airi']['hasVoice'] is False and candidates['airi']['existing'] == '愛理'
    response = client.post('/api/characters/import', json={'source': {'kind': 'game', 'id': state['id']},
        'keys': ['mahoru', 'airi'], 'conflict': 'overwrite', 'applyToSource': True,
        'expectedRevision': registry(client)['revision']})
    assert response.status_code == 200, response.text
    summary = response.json()['summary']
    assert summary['updated'] == ['真歩流', '愛理'] and summary['applied'] == 2
    shared = {c['name']: c for c in response.json()['registry']['characters']}
    assert shared['真歩流']['id'] == old['id'] and shared['真歩流']['model'] == ANIME
    assert shared['真歩流']['description'] == 'ゲームの真歩流' and shared['真歩流']['refs'] == [ref_id]
    assert shared['真歩流']['aliases'] == ['mahoru', 'まほる']
    # A name-only character keeps its voice settings when overwritten.
    assert shared['愛理']['description'] == '共通の愛理' and shared['愛理']['aliases'] == ['airi']
    assert shared['愛理']['refs'] == airi['refs']
    # The game now uses the shared characters; identical voice, so no regeneration.
    state = client.get(f'/api/game/projects/{state["id"]}').json()
    assert state['localCastMode']['mahoru'] == 'shared' and state['localCasts']['mahoru']['description'] == 'ゲームの真歩流'
    assert state['bindings']['mahoru'] == {'characterId': old['id']}
    assert [v['stale'] for v in state['voices']] == [False, False]
    # VoiceDrama scripts with 真歩流 follow the overwritten settings.
    assert statuses(client, drama_state['id']) == ['stale']
    # A shared edit made elsewhere marks only that speaker's audio on opening.
    update(client, shared['愛理'], description='変更')
    assert [v['stale'] for v in client.get(f'/api/game/projects/{state["id"]}').json()['voices']] == [False, True]


def test_import_skip_rename_and_failure_leave_registry_consistent(client):
    state = game_with_sheet(client)
    cast = {**game.default_local_cast('mahoru'), 'description': 'ゲームの真歩流'}
    state = put_game(client, state, localCasts={'mahoru': cast}, localCastMode={'mahoru': 'local'})
    existing = create(client, name='真歩流', description='そのまま')
    source = {'kind': 'game', 'id': state['id']}

    def run(conflict, keys=('mahoru',), revision=None, apply=False):
        return client.post('/api/characters/import', json={'source': source, 'keys': list(keys), 'conflict': conflict,
            'applyToSource': apply, 'expectedRevision': registry(client)['revision'] if revision is None else revision})
    skipped = run('skip')
    assert skipped.status_code == 200 and skipped.json()['summary']['skipped'] == ['真歩流']
    assert registry(client)['characters'][0]['description'] == 'そのまま'
    renamed = run('rename', apply=True).json()
    assert renamed['summary']['renamed'] == [{'from': '真歩流', 'to': '真歩流（コピー）'}]
    copy = next(c for c in renamed['registry']['characters'] if c['name'] == '真歩流（コピー）')
    assert copy['description'] == 'ゲームの真歩流' and copy['aliases'] == []
    assert game.get_game(state['id'])['bindings']['mahoru'] == {'characterId': copy['id']}
    assert existing['id'] != copy['id']
    revision = registry(client)['revision']
    for body, code in ((run('replace'), 400), (run('skip', keys=('nobody',)), 400), (run('skip', revision=revision - 1), 409)):
        assert body.status_code == code, body.text
    # A missing reference file aborts the import without copying anything.
    state = client.post(f'/api/game/projects/{state["id"]}/casts/mahoru/reference?expectedRevision={game.get_game(state["id"])["revision"]}',
                        files={'file': ('mahoru.wav', wav_bytes(), 'audio/wav')}).json()
    asset = state['voiceAssets'][0]
    game._game_ref_path(state['id'], asset).unlink()
    before = registry(client)
    failed = run('overwrite')
    assert failed.status_code == 404 and registry(client) == before
    assert not (core.WORKSPACE / 'characters' / 'reference_audio' / asset['filename']).exists()


def test_shared_character_voice_design_regenerates_in_place(client, monkeypatch):
    import app as api
    character = add_reference(client, create(client, name='零度', engine='qwen', mode='design',
                                              model='Qwen/Qwen3-TTS-12Hz-1.7B-VoiceDesign', description='低い声'))
    recorded = character['refs'][0]
    sent = []
    fake_tts(monkeypatch, api, sent)

    def design(text, replace=None):
        job = core.uid('job')
        api.JOBS[job] = {'status': 'running', 'total': 1, 'completed': 0, 'errors': [], 'message': ''}
        api.run_character_design(job, character['id'], text, replace)
        assert api.JOBS[job]['status'] == 'done', api.JOBS[job]
        return api.JOBS[job]['referenceAssetId']
    first = design('こんにちは')
    assert sent[0]['mode'] == 'design' and sent[0]['model'].endswith('VoiceDesign') and sent[0]['caption'] == '低い声'
    current = characters.by_id(registry(client), character['id'])
    assert current['refs'] == [recorded, first] and current['mode'] == 'clone' and current['model'].endswith('Base')
    second = design('おやすみ', first)
    current = characters.by_id(registry(client), character['id'])
    assert current['refs'] == [recorded, second] and current['refText'] == 'おやすみ'
    assert client.get(f'/api/characters/assets/{first}').status_code == 200   # unlinked, not deleted
    submitted = []
    monkeypatch.setattr(api.THREADS, 'submit', lambda *args: submitted.append(args))
    response = client.post(f'/api/characters/{character["id"]}/design', json={'sampleText': 'もう一度', 'replaceAssetId': second})
    assert response.status_code == 200 and submitted[0][1:] == (response.json()['id'], character['id'], 'もう一度', second)
    assert client.post('/api/characters/chr_0000000000000000/design', json={'sampleText': 'x'}).status_code == 404


# --- screens ----------------------------------------------------------------------

def test_both_screens_load_the_shared_character_editor():
    index = (ROOT / 'static' / 'index.html').read_text(encoding='utf-8')
    game_html = (ROOT / 'static' / 'game.html').read_text(encoding='utf-8')
    app_js = (ROOT / 'static' / 'app.js').read_text(encoding='utf-8')
    game_js = (ROOT / 'static' / 'game.js').read_text(encoding='utf-8')
    shared = (ROOT / 'static' / 'characters.js').read_text(encoding='utf-8')
    for html, script in ((index, 'app.js'), (game_html, 'game.js')):
        assert 'characters.css' in html and html.index('characters.js') < html.index(script)
    for term in ('function renderCastsView', 'data-speaker-link', 'importCharactersBtn', 'addCharacterBtn',
                 'data-cast-promote', 'promoteAllCastsBtn', 'data-register-speaker', 'function voiceFor'):
        assert term in app_js
    for term in ('setupCharacterControls', 'data-bind-character', 'importGameCharacters', 'function resolvedCast',
                 'data-cast-promote', 'characterEditor?.load()'):
        assert term in game_js
    assert 'id="sharedCharacters"' in game_html and 'id="importGameCharacters"' in game_html
    for term in ("value=\"overwrite\"", "value=\"skip\"", "value=\"rename\"", 'data-ch-apply', '/sources', '/import',
                 'expectedRevision', 'data-ch-generate', 'replaceAssetId', 'data-ch-use'):
        assert term in shared
