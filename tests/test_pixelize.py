"""Local pixel-art conversion of imported images. No API keys or network calls.

Run from repository root: python -m unittest discover -s tests -p test_pixelize.py
"""
import io
import json
import random
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image, ImageDraw, ImageFilter

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "pixel_workbench"))
import pixelize  # noqa: E402


def illustration(arm_out: bool = False, size: int = 600) -> Image.Image:
    """An AI-style picture: soft edges, noise, a flat light background and a tiny red accent."""
    rnd = random.Random(7)
    image = Image.new("RGB", (size, size), (245, 245, 240))
    draw = ImageDraw.Draw(image)
    s = size / 600
    draw.rectangle([220 * s, 160 * s, 380 * s, 420 * s], fill=(40, 100, 210))
    draw.rectangle([250 * s, 80 * s, 350 * s, 160 * s], fill=(220, 222, 230))
    draw.rectangle([290 * s, 110 * s, 310 * s, 120 * s], fill=(235, 30, 30))
    draw.rectangle([230 * s, 420 * s, 290 * s, 560 * s], fill=(200, 200, 210))
    draw.rectangle([310 * s, 420 * s, 370 * s, 560 * s], fill=(200, 200, 210))
    if arm_out:
        draw.rectangle([380 * s, 180 * s, 560 * s, 220 * s], fill=(60, 60, 72))
    image = image.filter(ImageFilter.GaussianBlur(1.2))
    pixels = image.load()
    for _ in range(size * 40):
        x, y = rnd.randrange(size), rnd.randrange(size)
        r, g, b = pixels[x, y]
        n = rnd.randint(-5, 5)
        pixels[x, y] = (max(0, min(255, r + n)), max(0, min(255, g + n)), max(0, min(255, b + n)))
    return image


def colours(image: Image.Image) -> set:
    return {c[:3] for _, c in image.getcolors(1 << 16) if c[3]}


class PixelizeTests(unittest.TestCase):
    def test_background_removed_and_height_kept(self):
        [result], _ = pixelize.pixelize_many([illustration()], pixelize.PixelizeSettings(height=48, colors=12))
        self.assertEqual(result.height, 48)
        self.assertEqual(result.getpixel((0, 0))[3], 0)
        self.assertLessEqual(len(colours(result)), 12)

    def test_outline_is_inside_requested_height(self):
        settings = pixelize.PixelizeSettings(height=40, colors=8, outline="#102030")
        [result], _ = pixelize.pixelize_many([illustration()], settings)
        self.assertEqual(result.height, 40)
        self.assertIn((16, 32, 48), colours(result))

    def test_shared_palette_is_identical_across_poses(self):
        settings = pixelize.PixelizeSettings(height=48, colors=10)
        results, palettes = pixelize.pixelize_many([illustration(), illustration(arm_out=True)], settings, True)
        self.assertEqual(palettes[0], palettes[1])
        allowed = set(palettes[0])
        for result in results:
            self.assertTrue(colours(result) <= allowed)

    def test_small_red_accent_survives(self):
        [result], _ = pixelize.pixelize_many([illustration()], pixelize.PixelizeSettings(height=64, colors=8))
        self.assertTrue(any(r > 200 and g < 90 and b < 90 for r, g, b in colours(result)))

    def test_gba_colours(self):
        settings = pixelize.PixelizeSettings(height=32, colors=16, rgb555=True, outline="auto")
        [result], palettes = pixelize.pixelize_many([illustration()], settings)
        self.assertTrue(all(v % 8 == 0 for c in colours(result) for v in c))

    def test_transparent_input_is_not_flood_filled(self):
        sprite = Image.new("RGBA", (200, 200), (0, 0, 0, 0))
        ImageDraw.Draw(sprite).rectangle([20, 20, 180, 180], fill=(250, 250, 250, 255))  # white body, like a background
        [result], _ = pixelize.pixelize_many([sprite], pixelize.PixelizeSettings(height=20, colors=4))
        self.assertEqual(result.getpixel((10, 10))[3], 255)

    def test_keep_background(self):
        settings = pixelize.PixelizeSettings(height=30, colors=8, background="keep", trim=False)
        [result], _ = pixelize.pixelize_many([illustration()], settings)
        self.assertEqual(result.size, (30, 30))
        self.assertEqual(result.getpixel((0, 0))[3], 255)

    def test_stray_pixel_cleanup(self):
        indices = [1] * 9
        opaque = [True] * 9
        indices[4] = 2
        pixelize.remove_stray_pixels(indices, opaque, 3, 3)
        self.assertEqual(indices[4], 1)


class PixelizeApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            from fastapi.testclient import TestClient
            import app as workbench
        except ImportError as exc:  # pragma: no cover - optional server dependencies
            raise unittest.SkipTest(f"server dependencies missing: {exc}")
        cls.workbench, cls.TestClient = workbench, TestClient

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.state = {"budget_usd": 2.0, "jobs": [], "assets": [], "sequences": []}
        for patcher in (patch.object(self.workbench, "_state", self.state),
                        patch.object(self.workbench, "save_state", lambda: None),
                        patch.object(self.workbench, "ASSETS", Path(self.tmp.name))):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.client = self.TestClient(self.workbench.app)

    def files(self):
        out = []
        for i, arm in enumerate((False, True)):
            buf = io.BytesIO()
            illustration(arm, 300).save(buf, format="PNG")
            out.append(("files", (f"pose{i}.png", buf.getvalue(), "image/png")))
        return out

    def test_preview_then_save(self):
        settings = json.dumps({"height": 32, "colors": 8, "outline": "auto", "shared_palette": True})
        r = self.client.post("/api/pixelize", files=self.files(), data={"settings": settings})
        self.assertEqual(r.status_code, 200, r.text)
        body = r.json()
        self.assertEqual(len(body["results"]), 2)
        self.assertEqual(len(body["palettes"]), 1)
        self.assertEqual(body["saved"], [])
        self.assertTrue(body["results"][0]["image"].startswith("data:image/png;base64,"))
        r = self.client.post("/api/pixelize", files=self.files(),
                             data={"settings": settings, "save": "true", "category": "character",
                                   "names": json.dumps(["ロボ待機", "ロボ射撃"])})
        self.assertEqual(r.status_code, 200, r.text)
        saved = r.json()["saved"]
        self.assertEqual([a["name"] for a in saved], ["ロボ待機", "ロボ射撃"])
        sprite = Path(self.tmp.name) / "character" / saved[0]["id"] / "sprite.png"
        with Image.open(sprite) as image:
            self.assertEqual(image.height, 32)
        self.assertEqual(self.state["assets"][0]["primary_file"], "sprite.png")

    def test_invalid_settings(self):
        r = self.client.post("/api/pixelize", files=self.files(), data={"settings": json.dumps({"height": 2})})
        self.assertEqual(r.status_code, 422)
        r = self.client.post("/api/pixelize", files=self.files(), data={"settings": json.dumps({"outline": "red"})})
        self.assertEqual(r.status_code, 422)
        bad = [("files", ("x.png", b"not an image", "image/png"))]
        self.assertEqual(self.client.post("/api/pixelize", files=bad).status_code, 422)


if __name__ == "__main__":
    unittest.main()
