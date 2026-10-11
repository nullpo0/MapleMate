"""One tray process per local state directory."""
from contextlib import contextmanager
import ctypes
from ctypes import wintypes
import hashlib
import os
from pathlib import Path


@contextmanager
def tray_lease(directory: Path):
    if os.name != "nt":
        raise RuntimeError("MapleMate tray requires Windows 10/11.")
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
    kernel.CreateMutexW.restype = wintypes.HANDLE
    kernel.ReleaseMutex.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    name = "Local\\MapleMate-" + hashlib.sha256(str(directory.resolve()).casefold().encode()).hexdigest()
    ctypes.set_last_error(0)
    handle = kernel.CreateMutexW(None, True, name)
    error = ctypes.get_last_error()
    if not handle:
        raise RuntimeError("Could not reserve MapleMate tray.")
    if error == 183:
        kernel.CloseHandle(handle)
        yield False
        return
    try:
        yield True
    finally:
        kernel.ReleaseMutex(handle)
        kernel.CloseHandle(handle)
