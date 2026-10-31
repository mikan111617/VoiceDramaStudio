"""Prevent regressions where a malformed model blocks the whole application on startup.

Run: python -m unittest discover -s tests -p test_pixel_python_syntax.py
Does not import app modules or require API keys.
"""
from __future__ import annotations

import ast
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class PixelPythonSyntaxTests(unittest.TestCase):
    def test_python_files_parse_before_application_startup(self):
        for relative in (
            "pixel_workbench/app.py",
            "pixel_workbench/sequence_render.py",
            "pixel_workbench/credential_store.py",
            "pixel_integration.py",
        ):
            path = ROOT / relative
            with self.subTest(file=relative):
                source = path.read_text(encoding="utf-8")
                ast.parse(source, filename=str(path))


if __name__ == "__main__":
    unittest.main()
