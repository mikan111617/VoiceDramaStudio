"""APNG, transparent WebM, Unity and GBA exports. No API keys or network calls.

Run from repository root: python -m unittest discover -s tests -p test_battle_export.py
"""
import json
import math
import subprocess
import sys
import tempfile
import unittest
import wave
import zipfile
from pathlib import Path

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "pixel_workbench"))
import sequence_export  # noqa: E402
from sequence_render import find_ffmpeg, save_media  # noqa: E402


def make_image_asset(root: Path, aid: str, image: Image.Image, category: str = "character") -> dict:
    folder = root / category / aid
    folder.mkdir(parents=True)
    image.save(folder / "a.png")
    return {"id": aid, "name": aid, "category": category, "kind": "image", "files": ["a.png"], "primary_file": "a.png"}


def make_sound_asset(root: Path, aid: str) -> dict:
    folder = root / "sound" / aid
    folder.mkdir(parents=True)
    with wave.open(str(folder / "s.wav"), "w") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(8000)
        out.writeframes(b"\x00\x10" * 4000)
    return {"id": aid, "name": aid, "category": "sound", "kind": "audio", "files": ["s.wav"], "primary_file": "s.wav"}


class ExportTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / "assets"
        sprite = Image.new("RGBA", (20, 20), (40, 80, 200, 255))
        sprite.putpixel((10, 5), (248, 32, 32, 255))  # a one-pixel accent ("eye")
        gradient = Image.new("RGBA", (240, 160))
        gradient.putdata([(x, y, (x * y) % 256, 255) for y in range(160) for x in range(240)])
        self.assets = {
            "unit": make_image_asset(self.root, "unit", sprite),
            "sky": make_image_asset(self.root, "sky", gradient, "background"),
            "hit": make_sound_asset(self.root, "hit"),
        }
        self.out = Path(self.tmp.name) / "out"
        self.out.mkdir()

    def seq(self, with_background=False, width=240, height=160):
        clips = [{"id": "u", "track": "character", "asset_id": "unit", "start": 0, "duration": 6, "x": -40, "y": 0,
                  "end_x": 40, "end_y": 0},
                 {"id": "s1", "track": "sound", "asset_id": "hit", "start": 2, "duration": 3, "volume": 1.0},
                 {"id": "s2", "track": "sound", "asset_id": "hit", "start": 4, "duration": 2, "volume": 0.5}]
        if with_background:
            clips.append({"id": "b", "track": "background", "asset_id": "sky", "start": 0, "duration": 6, "x": 0, "y": 0})
        return {"name": "テスト", "width": width, "height": height, "fps": 12, "clips": clips}

    def test_apng_keeps_frames_and_transparency(self):
        dest = self.out / "a.png"
        save_media(self.seq(), self.assets, self.root, dest, "apng")
        image = Image.open(dest)
        self.assertEqual(image.n_frames, 6)
        image.seek(3)
        self.assertEqual(image.convert("RGBA").getpixel((0, 0))[3], 0)

    def test_unity_package_layout_and_manifest(self):
        dest = self.out / "u.zip"
        save_media(self.seq(), self.assets, self.root, dest, "unity")
        with zipfile.ZipFile(dest) as archive:
            names = set(archive.namelist())
            base = "PixelBattle/Sequences/テスト"
            self.assertIn("PixelBattle/Scripts/PixelBattleCuePlayer.cs", names)
            self.assertIn("PixelBattle/Scripts/Editor/PixelBattleImporter.cs", names)
            self.assertIn(f"{base}/spritesheet.png", names)
            manifest = json.loads(archive.read(f"{base}/pixelbattle.json"))
            self.assertEqual(manifest["frame_count"], 6)
            self.assertEqual(len([n for n in names if n.startswith(f"{base}/frames/")]), 6)
            self.assertEqual([s["start_frame"] for s in manifest["sounds"]], [2, 4])
            self.assertEqual({s["file"] for s in manifest["sounds"]}, {"s.wav"})
            self.assertEqual(len([n for n in names if n.startswith(f"{base}/sounds/")]), 1)

    def test_gba_palette_rules(self):
        for colors in (16, 256):
            dest = self.out / f"g{colors}.zip"
            save_media(self.seq(with_background=True), self.assets, self.root, dest, "gba", colors=colors)
            with zipfile.ZipFile(dest) as archive:
                base = f"テスト_gba{colors}"
                manifest = json.loads(archive.read(f"{base}/pixelbattle_gba.json"))
                self.assertEqual(manifest["gba_vblanks_per_frame"], 5.0)
                self.assertEqual(len(archive.read(f"{base}/palette_bgr555.bin")), colors * 2)
                frame = Image.open(archive.open(f"{base}/frames/frame_003.png"))
                frame.load()
            self.assertEqual(frame.mode, "P")
            self.assertEqual(frame.info.get("transparency"), 0)
            self.assertLess(frame.getextrema()[1], colors)
            palette = frame.getpalette()[:colors * 3]
            self.assertTrue(all(v % 8 == 0 for v in palette))
            self.assertEqual(palette[:3], [248, 0, 248])

    def test_gba_small_accent_survives_16_colours(self):
        dest = self.out / "g.zip"
        save_media(self.seq(with_background=True), self.assets, self.root, dest, "gba", colors=16)
        with zipfile.ZipFile(dest) as archive:
            frame = Image.open(archive.open("テスト_gba16/frames/frame_000.png")).convert("RGB")
        raw = frame.tobytes()
        reds = [i for i in range(0, len(raw), 3) if raw[i] >= 200 and raw[i + 1] < 80 and raw[i + 2] < 80]
        self.assertTrue(reds, "the red accent pixel was merged into another colour")

    def test_gba_transparent_pixels_use_index_zero(self):
        dest = self.out / "t.zip"
        save_media(self.seq(), self.assets, self.root, dest, "gba", colors=16)
        with zipfile.ZipFile(dest) as archive:
            frame = Image.open(archive.open("テスト_gba16/frames/frame_000.png"))
            frame.load()
        self.assertEqual(frame.getpixel((0, 0)), 0)
        self.assertNotEqual(frame.getpixel((120 - 40, 80)), 0)

    def test_gba_requires_screen_size_and_valid_colours(self):
        with self.assertRaises(ValueError):
            save_media(self.seq(width=200), self.assets, self.root, self.out / "x.zip", "gba")
        with self.assertRaises(ValueError):
            save_media(self.seq(), self.assets, self.root, self.out / "y.zip", "gba", colors=32)

    def test_palette_picker_keeps_rare_distinct_colours(self):
        counts = [(10000, (8, 8, 24)), (9000, (16, 16, 32)), (8000, (24, 24, 40)), (5, (248, 32, 32))]
        self.assertIn((248, 32, 32), sequence_export.pick_palette(counts, 2))

    def test_scale_limits(self):
        self.assertEqual(sequence_export.check_scale({"width": 240, "height": 160}, 3), 3)
        with self.assertRaises(ValueError):
            sequence_export.check_scale({"width": 960, "height": 540}, 5)
        with self.assertRaises(ValueError):
            sequence_export.check_scale({"width": 240, "height": 160}, 0)

    def test_webm_has_alpha(self):
        binary = find_ffmpeg()
        if not binary or "libvpx-vp9" not in sequence_export._encoders(binary):
            self.skipTest("FFmpeg with libvpx-vp9 is not available")
        dest = self.out / "w.webm"
        save_media(self.seq(), self.assets, self.root, dest, "webm", scale=2)
        png = self.out / "w.png"
        subprocess.run([binary, "-v", "error", "-c:v", "libvpx-vp9", "-i", str(dest), "-frames:v", "1", "-y", str(png)],
                       check=True, timeout=60)
        image = Image.open(png)
        self.assertEqual(image.size, (480, 320))
        self.assertEqual(image.getchannel("A").getextrema()[0], 0)


if __name__ == "__main__":
    unittest.main()
