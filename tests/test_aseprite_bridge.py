"""Mock tests: no Steam install, GPU, Ollama or credentials required."""
import io
import json
import stat
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import aseprite_bridge as bridge
import aseprite_mcp_server as mcp


@pytest.fixture
def fake(tmp_path, monkeypatch):
    # Emulate only the Aseprite CLI file output contract.
    exe = tmp_path / "aseprite"
    exe.write_text(
        "#!/usr/bin/env python3\n"
        "import re,sys\n"
        "from pathlib import Path\n"
        "from PIL import Image\n"
        "args=sys.argv[1:]\n"
        "if '--script' in args:\n"
        " text=Path(args[args.index('--script')+1]).read_text(encoding='utf-8')\n"
        " match=re.search(r'sprite:saveCopyAs\\(\"([^\"\\n]+)\"\\)',text)\n"
        " if not match: sys.exit(2)\n"
        " Path(match.group(1)).write_bytes(b'fake')\n"
        "elif '--save-as' in args:\n"
        " path=Path(args[args.index('--save-as')+1])\n"
        " if path.suffix=='.aseprite': path.write_bytes(b'fake')\n"
        " else: Image.new('RGBA',(16,16)).save(path)\n"
        "elif '--sheet' in args: Image.new('RGBA',(32,16)).save(args[args.index('--sheet')+1])\n"
        "else: sys.exit(2)\n", encoding="utf-8")
    exe.chmod(exe.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setattr(bridge, "DATA", tmp_path / "workspace" / "aseprite_studio")
    monkeypatch.setattr(bridge, "discover_executable", lambda: str(exe))
    return bridge


def test_create_edit_and_export(fake):
    item = fake.create_sprite({
        "name": "勇者", "width": 32, "height": 32, "frames": 2,
        "layers": [{"name": "Body", "frames": [
            [{"type": "rect", "x": 1, "y": 1, "w": 12, "h": 9, "color": "#114499"}],
            [{"type": "line", "x": 1, "y": 1, "x2": 8, "y2": 8, "color": "#00aaff"}]
        ]}]
    })
    assert item["name"] == "勇者"
    assert (fake.DATA / (item["id"] + ".aseprite")).is_file()
    assert fake.export_sprite(item["id"], "gif").is_file()
    assert fake.export_sprite(item["id"], "sheet").is_file()
    result = fake.add_layers(item["id"], {"commands": [
        {"type": "pixel", "x": 3, "y": 4, "color": "#ff0000"}]})
    assert result["layers_added"] == 2
    assert len(fake.list_sprites()) == 1


def test_security_limits(fake):
    with pytest.raises(ValueError):
        fake.create_sprite({"width": 2000, "height": 40, "commands": []})
    with pytest.raises(ValueError):
        fake.create_sprite({"width": 32, "height": 32,
                            "commands": [{"type": "eval", "color": "#ffffff"}]})
    with pytest.raises(ValueError):
        fake.create_sprite({"width": 32, "height": 32,
                            "commands": [{"type": "pixel", "x": 0, "y": 0, "color": "red"}]})
    with pytest.raises(ValueError):
        fake.add_layers("../outside", {"commands": []})


def test_api_and_mcp(fake):
    app = FastAPI()
    app.include_router(fake.router)
    client = TestClient(app)
    assert client.get("/api/aseprite/status").status_code == 200
    created = client.post("/api/aseprite/sprites", json={
        "width": 16, "height": 16, "commands": [
            {"type": "pixel", "x": 0, "y": 0, "color": "#ffffff"}]})
    assert created.status_code == 200, created.text
    key = created.json()["id"]
    assert client.get(f"/api/aseprite/sprites/{key}/download/png").status_code == 200
    assert client.get(f"/api/aseprite/sprites/{key}/download/aseprite").status_code == 200
    assert client.get("/api/aseprite/sprites/not-valid/download/png").status_code == 400
    assert mcp.dispatch("tools/list", {})["tools"]
    assert mcp.dispatch("tools/call", {"name": "aseprite_list", "arguments": {}})["content"]
    incoming = "\n".join([
        json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}),
        json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
    ]) + "\n"
    output = io.StringIO()
    mcp.serve(io.StringIO(incoming), output)
    assert len(output.getvalue().strip().splitlines()) == 2
