"""No real API keys or network calls. Run: python -m unittest tests.test_pixel_workbench_credentials"""
from __future__ import annotations

import importlib.util
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("credential_store", ROOT / "pixel_workbench" / "credential_store.py")
store = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(store)


def fake_dpapi(data: bytes, *, decrypt: bool) -> bytes:
    if decrypt:
        if not data.startswith(b"mock-dpapi-v1:"):
            raise ValueError("Invalid ciphertext")
        return data[len(b"mock-dpapi-v1:"):][::-1]
    return b"mock-dpapi-v1:" + data[::-1]


class CredentialStorageTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.file = Path(self.tmp.name) / "credentials.dpapi"
        patchers = [
            patch.object(store, "supported", return_value=True),
            patch.object(store, "credential_file", return_value=self.file),
            patch.object(store, "_dpapi", side_effect=fake_dpapi),
        ]
        for patcher in patchers:
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_encrypted_round_trip_and_one_provider_removed(self):
        store.write_credentials({"pixellab": "sample-pixellab-secret", "openai": "sample-openai-secret"})
        ciphertext = self.file.read_bytes()
        self.assertNotIn(b"sample-pixellab-secret", ciphertext)
        self.assertNotIn(b"sample-openai-secret", ciphertext)
        self.assertEqual(store.read_credentials()["pixellab"], "sample-pixellab-secret")
        store.write_credentials({"openai": "sample-openai-secret"})
        self.assertEqual(store.read_credentials(), {"openai": "sample-openai-secret"})
        store.write_credentials({})
        self.assertFalse(self.file.exists())

    def test_does_not_store_other_fields(self):
        store.write_credentials({"pixellab": "example", "extraneous": "must-not-persist"})
        self.assertEqual(store.read_credentials(), {"pixellab": "example"})
        self.assertNotIn(b"must-not-persist", self.file.read_bytes())

    def test_corrupt_file_requires_reentry_instead_of_crashing(self):
        self.file.write_bytes(b"not-a-valid-dpapi-blob")
        with self.assertRaises(RuntimeError):
            store.read_credentials()


class WindowsDpapiTests(unittest.TestCase):
    @unittest.skipUnless(os.name == "nt", "Windows DPAPI is required")
    def test_current_windows_user_dpapi_round_trip(self):
        sample = b"non-secret-demo-credential-for-tests"
        encrypted = store._dpapi(sample, decrypt=False)
        self.assertNotEqual(sample, encrypted)
        self.assertEqual(store._dpapi(encrypted, decrypt=True), sample)


if __name__ == "__main__":
    unittest.main()
