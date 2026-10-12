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
