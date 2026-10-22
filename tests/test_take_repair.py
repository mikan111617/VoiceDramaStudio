"""Takes whose audio asset is gone: rejected on save, repaired on open."""
import json
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import app as api
import core
from test_studio import upload


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(core, 'WORKSPACE', tmp_path)
    monkeypatch.setattr(api, 'EXPORTS_DIR', tmp_path / 'exports')
    core.REPAIR_NOTICES.clear()
    return TestClient(api.app)


def project_with_takes(client):
    """Three lines: two with a take each, one with a take and an older one."""
    pid = client.post('/api/projects', json={'title': '修復テスト'}).json()['id']
    assets = [upload(client, pid, freq) for freq in (330, 440, 550)]
    with core.LOCK:
        state = core.load_project(pid)
        sid = state['scenes'][0]['id']
        state['lines'] = [core.new_line(sid, '愛理', text) for text in ('一つ目。', '二つ目。', '三つ目。')]
        by_id = {a['id']: a for a in state['assets']}
        core.attach_take(state, state['lines'][0], by_id[assets[0]['id']])
        core.attach_take(state, state['lines'][1], by_id[assets[1]['id']])
        core.attach_take(state, state['lines'][2], by_id[assets[2]['id']])
        core.save_project(pid, state)
    return pid


def break_on_disk(pid):
    """Write takes pointing at missing audio, as saves allowed before the fix."""
    path = core.state_path(pid)
    state = json.loads(path.read_text(encoding='utf-8'))
    first, second, _ = state['lines']
    first['takes'].append({'id': 'take_gone1', 'assetId': 'asset_gone1', 'engine': 'irodori'})
    first['selectedTakeId'] = 'take_gone1'
    second['takes'] = [{'id': 'take_gone2', 'assetId': 'asset_gone2', 'engine': 'irodori'}]
    second['selectedTakeId'] = 'take_gone2'
    state['clips'] = [c for c in state['clips'] if c.get('lineId') != second['id']]
    path.write_text(json.dumps(state, ensure_ascii=False), encoding='utf-8')
    return state


def test_saving_a_take_with_missing_audio_is_rejected_in_japanese(client):
    pid = project_with_takes(client)
    state = client.get(f'/api/projects/{pid}').json()
    state['lines'][1]['takes'].append({'id': 'take_x', 'assetId': 'asset_missing'})
    r = client.put(f'/api/projects/{pid}', json={'state': state, 'expectedRevision': state['revision']})
    assert r.status_code == 400
    assert '台詞「二つ目。」のテイクが指す音声素材がありません' in r.json()['detail']


def test_opening_a_broken_project_drops_the_missing_takes_once_with_a_backup(client):
    pid = project_with_takes(client)
    broken = break_on_disk(pid)
    keep_asset = broken['lines'][0]['takes'][0]['assetId']

    opened = client.get(f'/api/projects/{pid}').json()
    notice = opened.pop('repairNotice')
    assert (notice['removedTakes'], notice['lines']) == (2, 2)
    first, second, third = opened['lines']
    # The remaining take that the timeline already plays is selected again.
    assert [t['assetId'] for t in first['takes']] == [keep_asset]
    assert first['selectedTakeId'] == first['takes'][0]['id'] and first['status'] == 'ready'
    # A line left with no takes goes back to "not generated".
    assert second['takes'] == [] and second['selectedTakeId'] is None and second['status'] == 'missing'
    assert third['takes'] == broken['lines'][2]['takes']
    assert len(opened['clips']) == len(broken['clips'])

    # The repair is saved, and the broken JSON is kept as a backup.
    backup = core.workdir(pid) / 'backups' / notice['backup']
    assert json.loads(backup.read_text(encoding='utf-8')) == broken
    on_disk = json.loads(core.state_path(pid).read_text(encoding='utf-8'))
    assert on_disk['revision'] == broken['revision'] + 1 and 'repairNotice' not in on_disk

    # Shown once; the project now saves normally.
    again = client.get(f'/api/projects/{pid}').json()
    assert 'repairNotice' not in again
    again['lines'][0]['text'] = '一つ目を直した。'
    saved = client.put(f'/api/projects/{pid}', json={'state': again, 'expectedRevision': again['revision']})
    assert saved.status_code == 200, saved.text
    assert len(list((core.workdir(pid) / 'backups').glob('before_take_repair_*.json'))) == 1


def test_a_notice_echoed_back_by_the_client_is_not_stored(client):
    pid = project_with_takes(client)
    break_on_disk(pid)
    opened = client.get(f'/api/projects/{pid}').json()
    assert 'repairNotice' in opened
    saved = client.put(f'/api/projects/{pid}', json={'state': opened, 'expectedRevision': opened['revision']})
    assert saved.status_code == 200, saved.text
    assert 'repairNotice' not in json.loads(core.state_path(pid).read_text(encoding='utf-8'))


def test_the_editor_shows_the_repair_notice_when_a_project_loads():
    js = (Path(__file__).resolve().parents[1] / 'static' / 'app.js').read_text(encoding='utf-8')
    assert 'async function fetchProject(pid)' in js and 'delete s.repairNotice' in js
    assert "state=await fetchProject(pid)" in js
    assert "req('/api/projects/'+state.id)" not in js and 'req(`/api/projects/${state.id}`)' not in js


def test_when_the_repair_cannot_be_saved_nothing_on_disk_changes(client, monkeypatch):
    pid = project_with_takes(client)
    broken = break_on_disk(pid)

    def refuse(*args, **kwargs):
        raise ValueError('other legacy data')
    monkeypatch.setattr(core, 'save_project', refuse)
    opened = core.load_project(pid)
    assert opened['lines'][1]['takes'] == [] and pid not in core.REPAIR_NOTICES
    assert json.loads(core.state_path(pid).read_text(encoding='utf-8')) == broken
    assert not list((core.workdir(pid) / 'backups').glob('before_take_repair_*.json'))
