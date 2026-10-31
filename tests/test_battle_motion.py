"""Tests for shared preview/GIF/MP4 compositor motion paths; no API keys needed.

Run from repository root: python -m unittest discover -s tests -p test_battle_motion.py
"""
import sys
import tempfile
import unittest
from pathlib import Path

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "pixel_workbench"))
from sequence_render import clip_position, render_frame, render_png


class MotionTimelineTests(unittest.TestCase):
    def test_old_stationary_clips_do_not_move(self):
        clip = {"start": 4, "duration": 8, "x": -20, "y": 9}
        self.assertEqual(clip_position(clip, 4), (-20, 9))
        self.assertEqual(clip_position(clip, 11), (-20, 9))

    def test_linear_motion_reaches_target_at_last_visible_frame(self):
        clip = {
            "start": 10, "duration": 5, "x": -30, "y": 5,
            "end_x": 30, "end_y": -5, "easing": "linear"
        }
        self.assertEqual(clip_position(clip, 10), (-30, 5))
        self.assertEqual(clip_position(clip, 12), (0, 0))
        self.assertEqual(clip_position(clip, 14), (30, -5))

    def test_easing_and_single_frame_are_deterministic(self):
        clip = {"start": 0, "duration": 5, "x": 0, "y": 0,
                "end_x": 100, "end_y": 0, "easing": "ease_in_out"}
        self.assertEqual(clip_position(clip, 0), (0, 0))
        self.assertEqual(clip_position(clip, 1), (16, 0))
        self.assertEqual(clip_position(clip, 2), (50, 0))
        self.assertEqual(clip_position(clip, 4), (100, 0))
        clip["duration"] = 1
        self.assertEqual(clip_position(clip, 0), (100, 0))

    def test_shared_png_renderer_moves_sprite_and_flips_for_return(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            folder = root / "character" / "runner"
            folder.mkdir(parents=True)
            img = Image.new("RGBA", (2, 2), (255, 0, 0, 255))
            img.putpixel((1, 0), (0, 0, 255, 255))
            img.save(folder / "run.png")
            assets = {"runner": {
                "id": "runner", "category": "character", "kind": "image",
                "files": ["run.png"], "primary_file": "run.png"
            }}
            seq = {"width": 64, "height": 32, "fps": 12, "clips": [{
                "id": "c", "asset_id": "runner", "track": "character",
                "start": 0, "duration": 5, "x": -20, "y": 0,
                "end_x": 20, "end_y": 0, "easing": "linear",
                "flip_x": False
            }]}
            frame0 = render_frame(seq, assets, root, 0)
            frame4 = render_frame(seq, assets, root, 4)
            self.assertEqual(frame0.getpixel((11, 15)), (255, 0, 0, 255))
            self.assertEqual(frame4.getpixel((51, 15)), (255, 0, 0, 255))
            self.assertEqual(frame0.getpixel((51, 15))[3], 0)
            seq["clips"][0].update({"flip_x": True, "end_x": None, "end_y": None})
            flipped = render_frame(seq, assets, root, 0)
            self.assertEqual(flipped.getpixel((11, 15)), (0, 0, 255, 255))
            self.assertTrue(render_png(seq, assets, root, 0).startswith(b"\x89PNG\r\n\x1a\n"))


if __name__ == "__main__":
    unittest.main()
