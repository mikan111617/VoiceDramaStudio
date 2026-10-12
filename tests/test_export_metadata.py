"""Regression coverage for audio export labels, image validation and ID3 artwork."""
import base64
import io
import json
import shutil
import struct
import subprocess
import sys
import zlib
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import core
from app import app


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(core, 'WORKSPACE', tmp_path)
    monkeypatch.setattr(__import__('importlib').import_module('app'), 'EXPORTS_DIR', tmp_path / 'exports')
    return TestClient(app)


def _project_with_audio(client):
    project = client.post('/api/projects', json={'title': '台本のタイトル'}).json()
    pid = project['id']
    line = core.new_line(project['scenes'][0]['id'], '語り手', 'こんにちは', '', .25)
    project['lines'] = [line]
    saved = client.put(f'/api/projects/{pid}', json={
        'state': project, 'expectedRevision': project['revision'],
    })
    assert saved.status_code == 200, saved.text
    sr = 44100
    t = np.arange(sr // 4) / sr
    audio = io.BytesIO()
    sf.write(audio, (.2 * np.sin(2 * np.pi * 440 * t)).astype('float32'), sr, format='WAV')
    upload = client.post(f'/api/projects/{pid}/assets',
                         files={'file': ('line.wav', audio.getvalue(), 'audio/wav')},
                         data={'kind': 'voice'})
    assert upload.status_code == 200, upload.text
    attached = client.post(f'/api/projects/{pid}/lines/{line["id"]}/attach/{upload.json()["asset"]["id"]}')
    assert attached.status_code == 200, attached.text
    return pid, project['scenes'][0]['id']


def _png_cover():
    # A 32x32, valid RGB PNG, built without nonstandard image dependencies.
    def chunk(kind, data):
        payload = kind + data
        return struct.pack('>I', len(data)) + payload + struct.pack('>I', zlib.crc32(payload) & 0xffffffff)
    pixels = (b'\x00' + b'\x39\x88\xc8' * 32) * 32
    return (b'\x89PNG\r\n\x1a\n'
            + chunk(b'IHDR', struct.pack('>IIBBBBB', 32, 32, 8, 2, 0, 0, 0))
            + chunk(b'IDAT', zlib.compress(pixels))
            + chunk(b'IEND', b''))


def test_wav_export_uses_selected_title_for_download(client):
    pid, sid = _project_with_audio(client)
    response = client.post(f'/api/projects/{pid}/render', json={
        'sceneId': sid, 'format': 'wav', 'title': '第1話 予告編',
    })
    assert response.status_code == 200, response.text
    assert response.json()['file'] == '第1話 予告編.wav'
    assert response.json()['savedAs'] == '第1話 予告編.wav'
    downloaded = client.get(response.json()['url'])
    assert downloaded.status_code == 200
    assert 'filename*=' in downloaded.headers['content-disposition']
    assert sf.info(io.BytesIO(downloaded.content)).duration > 0


def test_export_rejects_invalid_or_unsupported_cover(client):
    pid, sid = _project_with_audio(client)
    bad = client.post(f'/api/projects/{pid}/render', json={
        'sceneId': sid, 'format': 'mp3', 'coverData': 'data:image/png;base64,ZmFrZQ==',
    })
    assert bad.status_code == 400
    cover = 'data:image/png;base64,' + base64.b64encode(_png_cover()).decode('ascii')
    wrong_format = client.post(f'/api/projects/{pid}/render', json={
        'sceneId': sid, 'format': 'wav', 'coverData': cover,
    })
    assert wrong_format.status_code == 400


@pytest.mark.skipif(not shutil.which('ffmpeg') or not shutil.which('ffprobe'),
                    reason='FFmpeg/ffprobe required for ID3 tag verification')
def test_mp3_export_embeds_title_artist_and_cover(client, tmp_path):
    pid, sid = _project_with_audio(client)
    cover = 'data:image/png;base64,' + base64.b64encode(_png_cover()).decode('ascii')
    response = client.post(f'/api/projects/{pid}/render', json={
        'sceneId': sid, 'format': 'mp3', 'title': '第1話 始まり', 'artist': '煉瓦書架',
        'coverData': cover,
    })
    assert response.status_code == 200, response.text
    assert response.json()['file'] == '第1話 始まり.mp3'
    assert response.json()['savedAs'] == '第1話 始まり.mp3'
    output = client.get(response.json()['url'])
    assert output.status_code == 200
    file = tmp_path / 'export.mp3'
    file.write_bytes(output.content)
    probe = subprocess.run(['ffprobe', '-v', 'error',
        '-show_entries', 'format_tags:stream=codec_type:stream_disposition=attached_pic',
        '-of', 'json', str(file)], capture_output=True, text=True, check=True)
    info = json.loads(probe.stdout)
    assert info['format']['tags']['title'] == '第1話 始まり'
    assert info['format']['tags']['artist'] == '煉瓦書架'
    assert any(stream.get('disposition', {}).get('attached_pic') == 1
               for stream in info['streams'])


def test_shared_export_folder_uses_only_the_requested_title_and_confirms_overwrite(client):
    import importlib
    api = importlib.import_module('app')
    pid, sid = _project_with_audio(client)
    response = client.post(f'/api/projects/{pid}/render', json={
        'sceneId': sid, 'format': 'wav', 'title': '第1話　事件編',
    })
    assert response.status_code == 200, response.text
    body = response.json()
    assert body['savedAs'] == '第1話　事件編.wav'
    assert body['file'] == '第1話　事件編.wav'
    output = api.EXPORTS_DIR / body['savedAs']
    assert output.is_file()
    assert body['saveDirectory'] == str(api.EXPORTS_DIR)
    assert output.parent == api.EXPORTS_DIR
    assert not (core.workdir(pid) / 'exports').exists()
    assert client.get(body['url']).content == output.read_bytes()
    old = output.read_bytes()
    duplicate = client.post(f'/api/projects/{pid}/render', json={
        'sceneId': sid, 'format': 'wav', 'title': '第1話　事件編',
    })
    assert duplicate.status_code == 409
    assert '同名ファイル' in duplicate.json()['detail']
    assert output.read_bytes() == old
    # A confirmed overwrite retains exactly the same requested filename.
    replaced = client.post(f'/api/projects/{pid}/render', json={
        'sceneId': sid, 'format': 'wav', 'title': '第1話　事件編',
        'overwrite': True,
    })
    assert replaced.status_code == 200, replaced.text
    assert replaced.json()['savedAs'] == '第1話　事件編.wav'
    assert output.is_file()
    assert not list(api.EXPORTS_DIR.glob('.render_*'))


def test_export_preserves_existing_file_when_render_fails(client, monkeypatch):
    import importlib
    api = importlib.import_module('app')
    pid, sid = _project_with_audio(client)
    audio = api.EXPORTS_DIR / '既存の音声.wav'
    audio.parent.mkdir(parents=True, exist_ok=True)
    audio.write_bytes(b'old contents')

    def raise_after_writing(project, root, out, *args, **kwargs):
        Path(out).write_bytes(b'partial mix')
        raise RuntimeError('simulated audio failure')

    monkeypatch.setattr(api, 'render_project', raise_after_writing)
    response = client.post(f'/api/projects/{pid}/render', json={
        'sceneId': sid, 'format': 'wav', 'title': '既存の音声', 'overwrite': True,
    })
    assert response.status_code == 400
    assert audio.read_bytes() == b'old contents'
    assert not list(api.EXPORTS_DIR.glob('.render_*'))
    assert not api._EXPORT_IN_PROGRESS


def test_export_title_with_extension_is_not_doubled_and_windows_reserved_names_fail(client):
    pid, sid = _project_with_audio(client)
    response = client.post(f'/api/projects/{pid}/render', json={
        'sceneId': sid, 'format': 'wav', 'title': '第2話.wav',
    })
    assert response.status_code == 200, response.text
    assert response.json()['savedAs'] == '第2話.wav'
    reserved = client.post(f'/api/projects/{pid}/render', json={
        'sceneId': sid, 'format': 'wav', 'title': 'CON',
    })
    assert reserved.status_code == 400


def test_shared_export_collision_between_projects_requires_confirmation(client):
    project1, sid1 = _project_with_audio(client)
    other = client.post('/api/projects', json={'title': '別の作品'}).json()
    response = client.post(f'/api/projects/{project1}/render', json={
        'sceneId': sid1, 'format': 'wav', 'title': '同じ作品名',
    })
    assert response.status_code == 200, response.text
    collision = client.post(f'/api/projects/{other["id"]}/render', json={
        'format': 'wav', 'title': '同じ作品名',
    })
    assert collision.status_code == 409


def test_legacy_project_export_links_remain_downloadable(client):
    pid, _ = _project_with_audio(client)
    old_dir = core.workdir(pid) / 'exports'
    old_dir.mkdir(exist_ok=True)
    old_file = old_dir / 'mix_0123456789.wav'
    old_file.write_bytes(b'old-export')
    response = client.get(f'/api/projects/{pid}/exports/{old_file.name}')
    assert response.status_code == 200
    assert response.content == b'old-export'
    invalid = client.get(f'/api/projects/{pid}/exports/other.wav')
    assert invalid.status_code == 400


def test_open_shared_export_directory(client, monkeypatch):
    import importlib
    api = importlib.import_module('app')
    opened = []
    if api.sys.platform == 'win32':
        monkeypatch.setattr(api.os, 'startfile', lambda location: opened.append(location))
    else:
        monkeypatch.setattr(api.subprocess, 'Popen', lambda args: opened.append(args[-1]))
    response = client.post('/api/exports/open-folder')
    assert response.status_code == 200, response.text
    assert opened == [str(api.EXPORTS_DIR)]
    assert api.EXPORTS_DIR.is_dir()


@pytest.mark.parametrize('fail_decode', [False, True])
def test_mapped_mix_file_is_closed_before_temp_directory_cleanup(client, monkeypatch, fail_decode):
    """Windows raises WinError 32 when mix.raw is still memory-mapped."""
    import render
    pid, sid = _project_with_audio(client)
    mappings = []
    original_memmap = render.np.memmap
    original_tempdir = render.tempfile.TemporaryDirectory

    def tracked_memmap(*args, **kwargs):
        result = original_memmap(*args, **kwargs)
        mappings.append(result)
        return result

    class GuardedTempDirectory:
        def __init__(self, *args, **kwargs):
            self.delegate = original_tempdir(*args, **kwargs)

        def __enter__(self):
            return self.delegate.__enter__()

        def __exit__(self, exc_type, exc, tb):
            assert mappings, 'expected a disk-backed mix'
            assert all(mm._mmap.closed for mm in mappings), (
                'Mix temp files must be unmapped before TemporaryDirectory cleanup')
            return self.delegate.__exit__(exc_type, exc, tb)

    monkeypatch.setattr(render.np, 'memmap', tracked_memmap)
    monkeypatch.setattr(render.tempfile, 'TemporaryDirectory', GuardedTempDirectory)
    if fail_decode:
        def fail_to_decode(*args, **kwargs):
            raise RuntimeError('decode failed for test')
        monkeypatch.setattr(render, 'to_pcm', fail_to_decode)

    result = client.post(f'/api/projects/{pid}/render', json={
        'sceneId': sid, 'format': 'wav', 'title': 'temp cleanup test',
    })
    if fail_decode:
        assert result.status_code == 400, result.text
        assert 'decode failed' in result.json()['detail']
    else:
        assert result.status_code == 200, result.text
        assert client.get(result.json()['url']).status_code == 200
    assert all(mm._mmap.closed for mm in mappings)
