"""Camera, cut-in, drawn effects, hit-stop, background scroll and the robot-anime template.

No API keys or network calls.
Run from repository root: python -m unittest discover -s tests -p test_battle_stage.py
"""
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "pixel_workbench"))
import stage_fx  # noqa: E402
from sequence_render import render_frame  # noqa: E402


def make_asset(root: Path, category: str, aid: str, image: Image.Image) -> dict:
    folder = root / category / aid
    folder.mkdir(parents=True)
    image.save(folder / "a.png")
    return {"id": aid, "name": aid, "category": category, "kind": "image",
            "files": ["a.png"], "primary_file": "a.png"}


def opaque_pixels(image: Image.Image) -> int:
    return sum(1 for a in image.getchannel("A").tobytes() if a)


class StageCompositorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        dot = Image.new("RGBA", (2, 2), (255, 0, 0, 255))
        stripe = Image.new("RGBA", (8, 4), (0, 0, 0, 255))
        stripe.putpixel((0, 0), (255, 255, 255, 255))
        self.assets = {
            "dot": make_asset(self.root, "character", "dot", dot),
            "bg": make_asset(self.root, "background", "bg", stripe),
        }

    def seq(self, clips, w=32, h=16):
        return {"width": w, "height": h, "fps": 12, "clips": clips}

    def red_x(self, frame_image):
        xs = [x for x in range(frame_image.width) for y in range(frame_image.height)
              if frame_image.getpixel((x, y))[:3] == (255, 0, 0) and frame_image.getpixel((x, y))[3]]
        return min(xs) if xs else None

    def test_camera_zoom_enlarges_sprite(self):
        sprite = {"id": "s", "track": "character", "asset_id": "dot", "start": 0, "duration": 4, "x": 0, "y": 0}
        plain = render_frame(self.seq([sprite]), self.assets, self.root, 0)
        cam = {"id": "c", "track": "camera", "start": 0, "duration": 4, "zoom": 2.0}
        zoomed = render_frame(self.seq([sprite, cam]), self.assets, self.root, 0)
        self.assertEqual(opaque_pixels(plain), 4)
        self.assertEqual(opaque_pixels(zoomed), 16)

    def test_zoomed_camera_does_not_show_empty_edges(self):
        bg = {"id": "b", "track": "background", "asset_id": "bg", "start": 0, "duration": 2, "x": 0, "y": 0}
        cam = {"id": "c", "track": "camera", "start": 0, "duration": 2, "zoom": 2.0, "x": -100, "y": 0}
        frame = render_frame(self.seq([bg, cam]), self.assets, self.root, 0)
        self.assertEqual(opaque_pixels(frame), 32 * 16)

    def test_shake_is_deterministic(self):
        sprite = {"id": "s", "track": "character", "asset_id": "dot", "start": 0, "duration": 8, "x": 0, "y": 0}
        cam = {"id": "c", "track": "camera", "start": 0, "duration": 8, "shake": 4}
        seq = self.seq([sprite, cam])
        a = render_frame(seq, self.assets, self.root, 3).tobytes()
        b = render_frame(seq, self.assets, self.root, 3).tobytes()
        self.assertEqual(a, b)
        positions = {self.red_x(render_frame(seq, self.assets, self.root, f)) for f in range(8)}
        self.assertGreater(len(positions), 1)

    def test_hitstop_freezes_motion_then_resumes_on_timeline(self):
        move = {"id": "m", "track": "character", "asset_id": "dot", "start": 0, "duration": 11,
                "x": -10, "y": 0, "end_x": 10, "end_y": 0, "easing": "linear"}
        stop = {"id": "h", "track": "fx", "fx_type": "hitstop", "start": 4, "duration": 3}
        seq = self.seq([move, stop])
        xs = [self.red_x(render_frame(seq, self.assets, self.root, f)) for f in range(11)]
        self.assertEqual(xs[4], xs[5])
        self.assertEqual(xs[5], xs[6])
        no_stop = self.seq([move])
        self.assertEqual(xs[7], self.red_x(render_frame(no_stop, self.assets, self.root, 7)))

    def test_background_scroll_shifts_and_tiles(self):
        bg = {"id": "b", "track": "background", "asset_id": "bg", "start": 0, "duration": 4,
              "x": 0, "y": 0, "scroll_x": 4}
        seq = self.seq([bg])
        f0 = render_frame(seq, self.assets, self.root, 0)
        f1 = render_frame(seq, self.assets, self.root, 1)
        self.assertNotEqual(f0.tobytes(), f1.tobytes())
        self.assertEqual(opaque_pixels(f1), 32 * 16)

    def test_flash_whitens_the_screen_and_fades(self):
        flash = {"id": "f", "track": "fx", "fx_type": "flash", "start": 0, "duration": 3, "intensity": 100}
        seq = self.seq([flash])
        self.assertEqual(render_frame(seq, self.assets, self.root, 0).getpixel((0, 0)), (255, 255, 255, 255))
        self.assertEqual(render_frame(seq, self.assets, self.root, 2).getpixel((0, 0))[3], 0)

    def test_drawn_effects_paint_pixels(self):
        for kind in ("speedlines", "hlines", "beam"):
            clip = {"id": kind, "track": "fx", "fx_type": kind, "start": 0, "duration": 6,
                    "intensity": 60, "x": -8, "y": 0, "end_x": 8, "end_y": 0}
            frame = render_frame(self.seq([clip], 64, 48), self.assets, self.root, 3)
            self.assertGreater(opaque_pixels(frame), 0, kind)

    def test_effect_layers(self):
        self.assertEqual(stage_fx.fx_layer({"fx_type": "speedlines"}), "back")
        self.assertEqual(stage_fx.fx_layer({"fx_type": "beam"}), "front")
        self.assertEqual(stage_fx.fx_layer({"fx_type": "beam", "layer": "back"}), "back")

    def test_cutin_draws_panel_with_portrait(self):
        cut = {"id": "c", "track": "cutin", "asset_id": "dot", "start": 0, "duration": 6,
               "text": "テスト", "side": "left", "text_speed": 0}
        frame = render_frame(self.seq([cut], 120, 80), self.assets, self.root, 4)
        self.assertEqual(frame.getpixel((60, 79 - 4)), (16, 24, 40, 235))  # no color = dark panel
        raw = frame.tobytes()
        reds = [i for i in range(0, len(raw), 4) if raw[i:i + 3] == bytes((255, 0, 0))]
        self.assertGreater(len(reds), 4)


class StageApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            from fastapi.testclient import TestClient
            import app as workbench
        except ImportError as exc:  # pragma: no cover - optional server dependencies
            raise unittest.SkipTest(f"server dependencies missing: {exc}")
        cls.workbench = workbench
        cls.TestClient = TestClient

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        robot = make_asset(root, "character", "robot", Image.new("RGBA", (8, 8), (0, 0, 255, 255)))
        face = make_asset(root, "character", "face", Image.new("RGBA", (4, 4), (255, 200, 150, 255)))
        state = {"budget_usd": 2.0, "jobs": [], "assets": [robot, face],
                 "sequences": [{"id": "seq", "name": "t", "fps": 12, "width": 120, "height": 80, "clips": []}]}
        for patcher in (patch.object(self.workbench, "_state", state),
                        patch.object(self.workbench, "save_state", lambda: None),
                        patch.object(self.workbench, "ASSETS", root)):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.state = state
        self.client = self.TestClient(self.workbench.app)

    def post(self, path, payload):
        return self.client.post("/api/sequences/seq" + path, json=payload)

    def test_stage_clips_without_assets(self):
        r = self.post("/clips", {"track": "camera", "start": 0, "duration": 4, "zoom": 2})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["label"], "カメラ")
        r = self.post("/clips", {"track": "fx", "start": 0, "duration": 4})
        self.assertEqual(r.status_code, 422)
        r = self.post("/clips", {"track": "fx", "fx_type": "beam", "start": 0, "duration": 4})
        self.assertEqual(r.json()["label"], "ビーム")
        r = self.post("/clips", {"track": "cutin", "start": 0, "duration": 4})
        self.assertEqual(r.status_code, 422)
        r = self.post("/clips", {"track": "cutin", "asset_id": "face", "text": "行くぞ", "start": 0, "duration": 4})
        self.assertEqual(r.status_code, 200, r.text)
        r = self.post("/clips", {"track": "character", "start": 0, "duration": 4})
        self.assertEqual(r.status_code, 404)

    def test_stage_clip_cannot_move_to_image_lane(self):
        clip = self.post("/clips", {"track": "fx", "fx_type": "flash", "start": 0, "duration": 2}).json()
        body = {"track": "character", "start": 0, "duration": 2, "volume": 1, "x": 0, "y": 0}
        r = self.client.put(f"/api/sequences/seq/clips/{clip['id']}", json=body)
        self.assertEqual(r.status_code, 422)
        body.update(track="fx", fx_type="flash", intensity=30)
        r = self.client.put(f"/api/sequences/seq/clips/{clip['id']}", json=body)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["intensity"], 30)

    def test_robot_template_builds_and_renders(self):
        for style in ("slash", "beam"):
            r = self.post("/srw-template", {"style": style, "unit_asset_id": "robot",
                                            "portrait_asset_id": "face", "text": "必殺！"})
            self.assertEqual(r.status_code, 200, r.text)
            tracks = {c["track"] for c in r.json()["clips"]}
            self.assertTrue({"cutin", "camera", "fx", "character"} <= tracks)
        seq = self.state["sequences"][0]
        assets = {a["id"]: a for a in self.state["assets"]}
        for frame in (0, 20, 26, 30):
            render_frame(seq, assets, self.workbench.ASSETS, frame)
        r = self.post("/srw-template", {"unit_asset_id": "missing"})
        self.assertEqual(r.status_code, 422)


if __name__ == "__main__":
    unittest.main()
