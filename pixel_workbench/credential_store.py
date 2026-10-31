"""Encrypted per-Windows-user persistence for Pixel Workbench API credentials.

Only DPAPI-protected ciphertext is written to LOCALAPPDATA. Keys never enter
repository files, app state.json, logs, or web status responses.
"""
from __future__ import annotations

import ctypes
import json
import os
from ctypes import wintypes
from pathlib import Path

PROVIDERS = ("pixellab", "openai")
_CRYPTPROTECT_UI_FORBIDDEN = 0x01


class _DataBlob(ctypes.Structure):
    _fields_ = [
        ("cbData", wintypes.DWORD),
        ("pbData", ctypes.POINTER(ctypes.c_ubyte)),
    ]


def supported() -> bool:
    """DPAPI user-scoped credential persistence is available on Windows only."""
    return os.name == "nt"


def credential_file() -> Path:
    if not supported():
        raise RuntimeError("APIキーの暗号化保存はWindows専用です。")
    local = os.environ.get("LOCALAPPDATA")
    root = Path(local) if local else Path.home() / "AppData" / "Local"
    return root / "VoiceDramaStudio" / "PixelWorkbench" / "credentials.dpapi"


def _dpapi(data: bytes, *, decrypt: bool) -> bytes:
    if not supported():
        raise RuntimeError("この環境ではWindows DPAPIを利用できません。")
    if not data:
        raise ValueError("APIキーの保存データが空です。")
    in_buffer = (ctypes.c_ubyte * len(data)).from_buffer_copy(data)
    input_blob = _DataBlob(len(data), ctypes.cast(in_buffer, ctypes.POINTER(ctypes.c_ubyte)))
    output_blob = _DataBlob()
    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    if decrypt:
        fn = crypt32.CryptUnprotectData
        fn.argtypes = [
            ctypes.POINTER(_DataBlob), ctypes.POINTER(ctypes.c_wchar_p),
            ctypes.POINTER(_DataBlob), ctypes.c_void_p, ctypes.c_void_p,
            wintypes.DWORD, ctypes.POINTER(_DataBlob),
        ]
        fn.restype = wintypes.BOOL
        ok = fn(ctypes.byref(input_blob), None, None, None, None,
                _CRYPTPROTECT_UI_FORBIDDEN, ctypes.byref(output_blob))
    else:
        fn = crypt32.CryptProtectData
        fn.argtypes = [
            ctypes.POINTER(_DataBlob), ctypes.c_wchar_p,
            ctypes.POINTER(_DataBlob), ctypes.c_void_p, ctypes.c_void_p,
            wintypes.DWORD, ctypes.POINTER(_DataBlob),
        ]
        fn.restype = wintypes.BOOL
        ok = fn(ctypes.byref(input_blob), "VoiceDramaStudio Pixel Workbench API credentials",
                None, None, None, _CRYPTPROTECT_UI_FORBIDDEN, ctypes.byref(output_blob))
    if not ok:
        raise OSError("Windows DPAPIによるAPIキーの暗号化・復号に失敗しました。")
    try:
        return ctypes.string_at(output_blob.pbData, output_blob.cbData)
    finally:
        # The Windows API allocates the output; Python must release its buffer.
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        free = kernel32.LocalFree
        free.argtypes = [ctypes.c_void_p]
        free.restype = ctypes.c_void_p
        free(ctypes.cast(output_blob.pbData, ctypes.c_void_p))


def read_credentials() -> dict[str, str]:
    if not supported():
        return {}
    try:
        ciphertext = credential_file().read_bytes()
    except FileNotFoundError:
        return {}
    try:
        data = json.loads(_dpapi(ciphertext, decrypt=True).decode("utf-8"))
        if not isinstance(data, dict):
            raise ValueError("APIキー保存データの形式が不正です。")
        return {p: data[p].strip() for p in PROVIDERS
                if isinstance(data.get(p), str) and data[p].strip()}
    except (OSError, ValueError, UnicodeError) as exc:
        raise RuntimeError("保存済みAPIキーを読み込めませんでした。Windowsアカウントの変更などをご確認ください。") from exc


def write_credentials(values: dict[str, str]) -> None:
    if not supported():
        raise RuntimeError("安全な自動保存はWindowsのみ対応しています。")
    clean = {p: values[p].strip() for p in PROVIDERS
             if isinstance(values.get(p), str) and values[p].strip()}
    path = credential_file()
    if not clean:
        path.unlink(missing_ok=True)
        return
    ciphertext = _dpapi(json.dumps(clean, ensure_ascii=False).encode("utf-8"), decrypt=False)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".tmp")
    try:
        temp.write_bytes(ciphertext)
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)
