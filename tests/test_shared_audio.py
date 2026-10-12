"""Regression tests for cross-project common BGM/SFX catalog."""
import io
import sys
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import core
import shared_audio
from app import app


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(core, "WORKSPACE", tmp_path)
    return TestClient(app)


def new_project(client, name):
    res = client.post("/api/projects", json={"title": name})
    assert res.status_code == 200, res.text
    return res.json()


def upload(client, pid, kind, filename="music.wav"):
    sr = 44100
    t = np.arange(sr // 5) / sr
    buffer = io.BytesIO()
    sf.write(buffer, np.sin(2 * np.pi * 440 * t).astype("float32") * 0.1, sr, format="WAV")
    res = client.post(f"/api/projects/{pid}/assets",
                      files={"file": (filename, buffer.getvalue(), "audio/wav")},
                      data={"kind": kind})
    assert res.status_code == 200, res.text
    return res.json()["asset"]


def test_shared_bgm_survives_removal_from_source_and_other_project(client):
    original = new_project(client, "最初の作品")
    other = new_project(client, "次の作品")
    bgm = upload(client, original["id"], "bgm", "港のBGM.wav")

    sent = client.post(f"/api/projects/{original['id']}/assets/share",
                       json={"assetIds": [bgm["id"]]})
    assert sent.status_code == 200, sent.text
    assert sent.json()["added"] == 1
    listing = client.get("/api/shared-assets").json()
    assert len(listing) == 1
    shared = listing[0]
    assert shared["name"] == "港のBGM.wav"
    assert shared["kind"] == "bgm"
    assert client.get(f"/api/shared-assets/{shared['id']}/audio").status_code == 200
    assert shared_audio.get_shared_file(shared["id"])[0].is_file()

    # Registration is idempotent: reuse the byte-identical sound/kind.
    repeated = client.post(f"/api/projects/{original['id']}/assets/share",
                           json={"assetIds": [bgm["id"]]})
    assert repeated.status_code == 200
    assert repeated.json()["added"] == 0
    assert repeated.json()["existing"] == 1

    imported = client.post(f"/api/projects/{other['id']}/assets/import-shared/{shared['id']}",
                           json={"expectedRevision": other["revision"]})
    assert imported.status_code == 200, imported.text
    body = imported.json()
    assert body["alreadyImported"] is False
    dest = body["asset"]
    assert dest["id"] != bgm["id"]
    assert dest["sharedId"] == shared["id"]
    assert core.asset_path(other["id"], dest).read_bytes() == core.asset_path(original["id"], bgm).read_bytes()

    again = client.post(f"/api/projects/{other['id']}/assets/import-shared/{shared['id']}",
                        json={"expectedRevision": body["project"]["revision"]})
    assert again.status_code == 200
    assert again.json()["alreadyImported"] is True
    assert len(again.json()["project"]["assets"]) == 1

    deleted = client.delete(f"/api/projects/{original['id']}/assets/{bgm['id']}")
    assert deleted.status_code == 200, deleted.text
    assert not core.asset_path(original["id"], bgm).exists()
    assert core.asset_path(other["id"], dest).is_file()
    assert client.get(f"/api/shared-assets/{shared['id']}/audio").status_code == 200


def test_share_restricts_asset_types_and_is_atomic(client):
    project = new_project(client, "共有テスト")
    bgm = upload(client, project["id"], "bgm")
    voice = upload(client, project["id"], "voice", "line.wav")
    response = client.post(f"/api/projects/{project['id']}/assets/share",
                           json={"assetIds": [bgm["id"], voice["id"]]})
    assert response.status_code == 400
    assert client.get("/api/shared-assets").json() == []
    assert not list(shared_audio.root().glob("shared_*.wav"))


@pytest.mark.parametrize("kind", ["sfx", "ambience"])
def test_other_sound_categories_are_reusable(client, kind):
    source = new_project(client, "元の作品")
    target = new_project(client, "別の作品")
    item = upload(client, source["id"], kind)
    response = client.post(f"/api/projects/{source['id']}/assets/share",
                           json={"assetIds": [item["id"]]})
    assert response.status_code == 200, response.text
    sid = response.json()["assets"][0]["id"]
    downloaded = client.post(f"/api/projects/{target['id']}/assets/import-shared/{sid}",
                             json={"expectedRevision": target["revision"]})
    assert downloaded.status_code == 200
    assert downloaded.json()["asset"]["kind"] == kind


def test_wrong_revision_and_missing_shared_asset_do_not_change_target(client):
    source = new_project(client, "source")
    target = new_project(client, "target")
    bgm = upload(client, source["id"], "bgm")
    shared = client.post(f"/api/projects/{source['id']}/assets/share",
                         json={"assetIds": [bgm["id"]]}).json()["assets"][0]
    before = core.load_project(target["id"])
    conflict = client.post(f"/api/projects/{target['id']}/assets/import-shared/{shared['id']}",
                           json={"expectedRevision": -1})
    assert conflict.status_code == 409
    missing = client.post(f"/api/projects/{target['id']}/assets/import-shared/shared_aaaaaaaaaaaaaaaa",
                          json={"expectedRevision": target["revision"]})
    assert missing.status_code == 404
    assert core.load_project(target["id"]) == before
    assert not list((core.workdir(target["id"]) / "assets").glob("asset_*"))


def test_shared_index_stays_beneath_git_ignored_workspace(client):
    item = new_project(client, "test")
    bgm = upload(client, item["id"], "bgm")
    res = client.post(f"/api/projects/{item['id']}/assets/share",
                      json={"assetIds": [bgm["id"]]})
    assert res.status_code == 200
    assert shared_audio.root() == core.WORKSPACE / "shared_audio"
    assert shared_audio.index_path().is_file()
    # Reject untrusted names and requests that pretend dialogue is shared.
    assert client.get("/api/shared-assets/not-a-shared-id/audio").status_code == 404
