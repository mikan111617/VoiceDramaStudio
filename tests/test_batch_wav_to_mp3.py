"""Run: python -m unittest discover -s tests -p 'test_batch_wav_to_mp3.py'"""
from __future__ import annotations

import importlib.util
import math
from pathlib import Path
import shutil
import struct
import tempfile
import threading
import unittest
import wave

SCRIPT = Path(__file__).resolve().parents[1] / "batch_wav_to_mp3.py"
spec = importlib.util.spec_from_file_location("batch_wav_to_mp3", SCRIPT)
import sys
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)


def wav(path: Path, duration: float = 0.11):
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as stream:
        stream.setnchannels(1)
        stream.setsampwidth(2)
        stream.setframerate(16000)
        samples = [int(6000 * math.sin(2 * math.pi * 400 * i / 16000)) for i in range(int(16000 * duration))]
        stream.writeframes(struct.pack("<" + "h" * len(samples), *samples))


class BatchWavToMp3Tests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.src = self.root / "WAV"
        self.dst = self.root / "MP3"
        self.src.mkdir()
        self.ffmpeg = module.find_ffmpeg() if shutil.which("ffmpeg") else None

    def test_scan_recursively_and_preserve_speaker_scene_and_id(self):
        wav(self.src / "airi" / "scene1" / "v00191.wav")
        wav(self.src / "mary" / "scene8" / "v00429.WAV")
        (self.src / "notes.txt").write_text("not audio")
        result = module.build_plan(self.src, self.dst)
        self.assertEqual({str(p.destination.relative_to(self.dst)) for p in result}, {
            str(Path("airi") / "scene1" / "v00191.mp3"),
            str(Path("mary") / "scene8" / "v00429.mp3"),
        })

    @unittest.skipUnless(shutil.which("ffmpeg"), "FFmpeg unavailable")
    def test_conversion_originals_untouched_and_second_run_skips(self):
        first = self.src / "airi" / "scene1" / "v00191.wav"
        second = self.src / "mary" / "scene2" / "v00201.wav"
        wav(first)
        wav(second)
        originals = (first.read_bytes(), second.read_bytes())
        plan = module.build_plan(self.src, self.dst)
        progress = []
        stats = module.convert_batch(plan, ffmpeg=self.ffmpeg, workers=2,
                                     on_result=lambda n, total, result: progress.append((n,total,result.status)))
        self.assertEqual(stats, {"converted": 2, "skipped": 0, "failed": 0, "cancelled": 0})
        self.assertEqual(sorted(n for n, total, kind in progress), [1, 2])
        self.assertEqual((first.read_bytes(), second.read_bytes()), originals)
        self.assertTrue((self.dst / "airi" / "scene1" / "v00191.mp3").read_bytes().startswith(b"ID3"))
        second_pass = module.convert_batch(plan, ffmpeg=self.ffmpeg)
        self.assertEqual(second_pass["skipped"], 2)
        self.assertEqual(second_pass["converted"], 0)

    @unittest.skipUnless(shutil.which("ffmpeg"), "FFmpeg unavailable")
    def test_failed_encodings_leave_old_mp3_untouched(self):
        src = self.src / "broken.wav"
        src.write_bytes(b"not-a-real-wav")
        self.dst.mkdir()
        target = self.dst / "broken.mp3"
        target.write_bytes(b"old_mp3_content")
        plan = module.build_plan(self.src, self.dst)
        result = module.convert_batch(plan, ffmpeg=self.ffmpeg, overwrite=True)
        self.assertEqual(result["failed"], 1)
        self.assertEqual(target.read_bytes(), b"old_mp3_content")
        self.assertEqual(list(self.dst.glob(".vds_*")), [])

    def test_stopping_marks_unstarted_files_cancelled(self):
        for i in range(12):
            wav(self.src / (f"v{i:04}.wav"))
        plan = module.build_plan(self.src, self.dst)
        event = threading.Event()
        event.set()
        stats = module.convert_batch(plan, ffmpeg=self.ffmpeg or "ffmpeg", cancelled=event)
        self.assertEqual(stats["cancelled"], 12)
        self.assertEqual(sum(stats.values()), 12)

    def test_duplicate_outputs_caught_before_conversion(self):
        wav(self.src / "a.wav")
        wav(self.src / "a.WAV")
        with self.assertRaisesRegex(ValueError, "重複"):
            module.build_plan(self.src, self.dst)

    def test_no_input_and_no_wav(self):
        with self.assertRaises(ValueError):
            module.build_plan(self.root / "missing", self.dst)
        self.assertEqual(module.build_plan(self.src, self.dst), [])


if __name__ == "__main__":
    unittest.main()
