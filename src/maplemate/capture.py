"""Capture only an automatically discovered MapleStory process window."""
import ctypes
from ctypes import wintypes
from datetime import datetime, timezone
import os
from pathlib import Path
from dataclasses import dataclass
import io
import json
import subprocess
from PIL import Image, ImageStat


class CaptureError(RuntimeError):
    pass


@dataclass(frozen=True)
class GameWindow:
    title: str
    handle: int


@dataclass(frozen=True)
class CapturedFrame:
    png: bytes
    captured_at: str


def game_window() -> GameWindow | None:
    if os.name != "nt":
        raise CaptureError("Game window capture currently requires Windows 10/11.")
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
    kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    user32.IsWindowVisible.argtypes = [wintypes.HWND]
    user32.IsIconic.argtypes = [wintypes.HWND]
    user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
    user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    titles = []
    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    @callback_type
    def visit(window, _):
        if not user32.IsWindowVisible(window) or user32.IsIconic(window):
            return True
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(window, ctypes.byref(pid))
        handle = kernel32.OpenProcess(0x1000, False, pid.value)
        if not handle:
            return True
        try:
            length = wintypes.DWORD(32768)
            executable = ctypes.create_unicode_buffer(length.value)
            if not kernel32.QueryFullProcessImageNameW(handle, 0, executable, ctypes.byref(length)):
                return True
            if Path(executable.value).name.lower() not in ("maplestory.exe", "maplestoryt.exe"):
                return True
            title = ctypes.create_unicode_buffer(user32.GetWindowTextLengthW(window) + 1)
            user32.GetWindowTextW(window, title, len(title))
            if title.value:
                titles.append(GameWindow(title.value, int(window)))
        finally:
            kernel32.CloseHandle(handle)
        return True

    user32.EnumWindows.argtypes = [callback_type, wintypes.LPARAM]
    user32.EnumWindows(visit, 0)
    if len(titles) > 1:
        raise CaptureError("Multiple MapleStory windows are visible. Keep one game client open for this MVP.")
    return titles[0] if titles else None


def capture_game() -> CapturedFrame:
    """Bound native capture lifetime to one frame; no background video session."""
    if os.name != "nt":
        raise CaptureError("Windows 10/11 is required for game capture.")
    from .launch import command_for
    try:
        process = subprocess.run(command_for("capture-worker"), stdin=subprocess.DEVNULL,
                                 stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=10,
                                 creationflags=subprocess.CREATE_NO_WINDOW)
    except (OSError, subprocess.TimeoutExpired):
        raise CaptureError("게임 캡처를 완료하지 못했습니다. 메이플 창을 최소화하지 말고 다시 시도해 주세요.") from None
    if process.returncode or len(process.stdout) > 32 * 1024 * 1024:
        raise CaptureError("게임 창을 찾거나 캡처하지 못했습니다. 메이플 클라이언트 한 개를 열어 주세요.")
    try:
        header, png = process.stdout.split(b"\n", 1)
        metadata = json.loads(header)
        with Image.open(io.BytesIO(png)) as image:
            if image.format != "PNG" or not 32 <= image.width <= 8192 or not 32 <= image.height <= 8192 or image.width * image.height > 16_000_000:
                raise ValueError("Invalid frame")
            if max(ImageStat.Stat(image.convert("RGB").resize((64, 64))).mean) < 0.5:
                raise CaptureError("캡처 화면이 검게 나왔습니다. 게임 창이 최소화되어 있는지 확인해 주세요.")
        return CapturedFrame(png, metadata["captured_at"])
    except (ValueError, KeyError, OSError):
        raise CaptureError("캡처 이미지를 읽지 못했습니다. 게임 창을 확인하고 다시 시도해 주세요.") from None
