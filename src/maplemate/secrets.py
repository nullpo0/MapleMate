"""API keys stay outside tool arguments/results and are protected with Windows DPAPI."""
import ctypes
from ctypes import wintypes
import os
from pathlib import Path


class SecretError(RuntimeError):
    pass


def validate_key(key: str) -> str:
    key = key.strip()
    if not key or any(not 33 <= ord(character) <= 126 for character in key):
        raise SecretError("Enter a non-empty API key containing printable ASCII characters without spaces.")
    return key


def _crypt(data: bytes, decrypt: bool = False) -> bytes:
    if os.name != "nt":
        raise SecretError("Windows DPAPI is required for saved keys; use NEXON_API_KEY on other systems.")

    class Blob(ctypes.Structure):
        _fields_ = [("size", wintypes.DWORD), ("data", ctypes.POINTER(ctypes.c_ubyte))]

    buffer = (ctypes.c_ubyte * len(data)).from_buffer_copy(data)
    input_blob = Blob(len(data), buffer)
    output_blob = Blob()
    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    function = crypt32.CryptUnprotectData if decrypt else crypt32.CryptProtectData
    function.argtypes = [ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
    function.restype = wintypes.BOOL
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p
    if not function(ctypes.byref(input_blob), None, None, None, None, 1, ctypes.byref(output_blob)):
        raise SecretError("Could not access the API key protected for this Windows user.")
    try:
        return ctypes.string_at(output_blob.data, output_blob.size)
    finally:
        kernel32.LocalFree(output_blob.data)


def save_key(directory: Path, key: str) -> None:
    encrypted = _crypt(validate_key(key).encode("utf-8"))
    temporary = directory / "nexon-key.tmp"
    temporary.write_bytes(encrypted)
    temporary.replace(directory / "nexon-key.dpapi")


def load_key(directory: Path) -> str:
    key = os.environ.get("NEXON_API_KEY", "").strip()
    if key:
        return validate_key(key)
    path = directory / "nexon-key.dpapi"
    if not path.exists():
        raise SecretError("API key is not configured. Open MapleMate tray settings to configure the key.")
    try:
        return validate_key(_crypt(path.read_bytes(), decrypt=True).decode("utf-8"))
    except UnicodeDecodeError:
        raise SecretError("Could not read the protected API key. Configure it again in MapleMate settings.") from None


def key_configured(directory: Path) -> bool:
    return bool(os.environ.get("NEXON_API_KEY")) or (directory / "nexon-key.dpapi").exists()
