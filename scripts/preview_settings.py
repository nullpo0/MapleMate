"""Render only our synthetic settings window and exercise reset confirmation.

No desktop/game capture, real API calls, or real user state/config changes.
"""
import ctypes
from ctypes import wintypes
import json
import queue
from pathlib import Path
import shutil
import time
import tkinter as tk
from unittest.mock import patch
import uuid
from PIL import Image
from maplemate.config import Config
from maplemate.runtime import CaptureManager
from maplemate.settings import SettingsWindow
from maplemate.state import StateStore, utc_now
from maplemate.tray import create_root, icon_image

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / ".cache" / "ui-preview"


def save_window(window, path):
    """PrintWindow renders the known Tk window; never reads desktop pixels."""
    window.update()
    save_hwnd(window.winfo_id(), path)


def save_hwnd(window_handle, path):
    """Render a supplied handle belonging to the app under test."""
    user, gdi = ctypes.windll.user32, ctypes.windll.gdi32
    for function, args, result in (
        (user.GetAncestor, [wintypes.HWND, wintypes.UINT], wintypes.HWND),
        (user.GetWindowDC, [wintypes.HWND], wintypes.HDC),
        (user.GetWindowRect, [wintypes.HWND, ctypes.POINTER(wintypes.RECT)], wintypes.BOOL),
        (user.PrintWindow, [wintypes.HWND, wintypes.HDC, wintypes.UINT], wintypes.BOOL),
        (user.ReleaseDC, [wintypes.HWND, wintypes.HDC], ctypes.c_int),
        (gdi.CreateCompatibleDC, [wintypes.HDC], wintypes.HDC),
        (gdi.CreateCompatibleBitmap, [wintypes.HDC, ctypes.c_int, ctypes.c_int], wintypes.HBITMAP),
        (gdi.SelectObject, [wintypes.HDC, wintypes.HANDLE], wintypes.HANDLE),
        (gdi.DeleteObject, [wintypes.HANDLE], wintypes.BOOL),
        (gdi.DeleteDC, [wintypes.HDC], wintypes.BOOL),
        (gdi.GetDIBits, [wintypes.HDC, wintypes.HBITMAP, wintypes.UINT, wintypes.UINT,
                        ctypes.c_void_p, ctypes.c_void_p, wintypes.UINT], ctypes.c_int),
    ):
        function.argtypes, function.restype = args, result
    class Header(ctypes.Structure):
        _fields_ = [("size", wintypes.DWORD), ("width", wintypes.LONG), ("height", wintypes.LONG),
                    ("planes", wintypes.WORD), ("bits", wintypes.WORD), ("compression", wintypes.DWORD),
                    ("image_size", wintypes.DWORD), ("xppm", wintypes.LONG), ("yppm", wintypes.LONG),
                    ("used", wintypes.DWORD), ("important", wintypes.DWORD)]
    hwnd = user.GetAncestor(window_handle, 2)
    rect = wintypes.RECT()
    assert user.GetWindowRect(hwnd, ctypes.byref(rect))
    width, height = rect.right - rect.left, rect.bottom - rect.top
    source = user.GetWindowDC(hwnd)
    target = gdi.CreateCompatibleDC(source)
    bitmap = gdi.CreateCompatibleBitmap(source, width, height)
    previous = gdi.SelectObject(target, bitmap)
    try:
        assert user.PrintWindow(hwnd, target, 0), "Own-window rendering failed"
        gdi.SelectObject(target, previous)
        header = Header(ctypes.sizeof(Header), width, -height, 1, 32, 0, width * height * 4, 0, 0, 0, 0)
        pixels = ctypes.create_string_buffer(width * height * 4)
        assert gdi.GetDIBits(target, bitmap, 0, height, pixels, ctypes.byref(header), 0) == height
        Image.frombytes("RGB", (width, height), pixels.raw, "raw", "BGRX").save(path)
    finally:
        gdi.SelectObject(target, previous)
        gdi.DeleteObject(bitmap)
        gdi.DeleteDC(target)
        user.ReleaseDC(hwnd, source)


def descendants(widget):
    for child in widget.winfo_children():
        yield child
        yield from descendants(child)


def main():
    OUTPUT.mkdir(parents=True, exist_ok=True)
    directory = OUTPUT / uuid.uuid4().hex
    config = Config.load(directory)
    store = StateStore(directory)
    root = create_root()
    root.withdraw()
    try:
        store.set("registered_characters", [{"character_name": "미리보기용사"}])
        store.update_context("미리보기용사", {"goal": "하드 림보 솔플", "budget_meso": 50_000_000_000, "daily_play_minutes": 90})
        store.record_assessment("미리보기용사", {"stage": "advanced"}, 1)
        store.set("screen:미리보기용사", {"observed_at": utc_now()})
        manager = CaptureManager(store)
        actions = queue.Queue()
        def finish_task():
            deadline = time.monotonic() + 5
            while settings.busy and time.monotonic() < deadline:
                while not actions.empty():
                    actions.get_nowait()()
                root.update()
                time.sleep(0.01)
            assert not settings.busy
        message = tk.StringVar(value="준비됨")
        with patch("maplemate.settings.key_configured", return_value=True), \
             patch.object(SettingsWindow, "connection_status", side_effect=["연결됨", "미연결"] * 10):
            settings = SettingsWindow(root, config, store, manager, actions.put,
                                      message, icon_image())
            with patch("maplemate.settings.configure_key", return_value=1) as configure:
                settings.key_entry.insert(0, "synthetic-preview-key")
                next(child for child in descendants(settings.body) if isinstance(child, tk.Button) and child.cget("text") == "저장").invoke()
                finish_task()
                configure.assert_called_once_with(config, "synthetic-preview-key")
                assert "저장 완료" in message.get()
            message.set("준비됨")
            for page in ("connection", "records", "help"):
                settings.render(page)
                root.update()
                assert not settings.scrollbar.winfo_ismapped(), "Fitting content should not show a scrollbar"
                before_scroll = settings.canvas.yview()
                for delta in (-120, 120):
                    settings.canvas.event_generate("<MouseWheel>", delta=delta)
                    root.update()
                    assert settings.canvas.yview() == before_scroll, "Fitting content moved on wheel input"
                save_window(settings.window, OUTPUT / (page + ".png"))
            store.update_context("미리보기용사", {"goal": "여러 조건이 포함된 긴 목표의 줄바꿈과 스크롤을 확인합니다. " * 30},
                                 store.snapshot("미리보기용사")["revision"])
            settings.render("records")
            root.update()
            assert settings.scrollbar.winfo_ismapped(), "Overflowing content needs a scrollbar"
            settings.canvas.event_generate("<MouseWheel>", delta=-120)
            root.update()
            assert settings.canvas.yview()[0] > 0, "Overflowing content did not scroll"
            store.update_context("미리보기용사", {"goal": "하드 림보 솔플"}, store.snapshot("미리보기용사")["revision"])
            settings.render("records")
            root.update()
            assert not settings.scrollbar.winfo_ismapped()
            assert settings.canvas.yview()[0] == 0, "Content did not reset to top after overflow disappeared"
            settings.reset_scope.set("all")
            before = store.snapshot("미리보기용사")["revision"]
            settings._confirm_reset()
            dialog = next(child for child in settings.window.winfo_children() if isinstance(child, tk.Toplevel))
            save_window(dialog, OUTPUT / "reset-confirmation.png")
            cancel = next(child for child in descendants(dialog) if isinstance(child, tk.Button) and child.cget("text") == "취소")
            cancel.invoke()
            assert store.snapshot("미리보기용사")["revision"] == before
            settings.reset_scope.set("context")
            settings._confirm_reset()
            dialog = next(child for child in settings.window.winfo_children() if isinstance(child, tk.Toplevel))
            confirm = next(child for child in descendants(dialog) if isinstance(child, tk.Button) and child.cget("text") == "초기화")
            confirm.invoke()
            finish_task()
            assert store.snapshot("미리보기용사")["user_context"] == {}
            assert store.snapshot("미리보기용사")["screen_observation"] is not None
            settings.window.geometry(f"{settings.px(440)}x{settings.px(360)}")
            settings.render("connection")
            save_window(settings.window, OUTPUT / "small-window.png")
            print(json.dumps({"synthetic_ui_rendered": True, "api_save_without_character_input": True, "cancel_preserved_state": True,
                              "no_scroll_when_content_fits": True, "overflow_scroll_and_restore": True,
                              "confirmed_reset_completed": True, "previews": str(OUTPUT)}))
    finally:
        root.destroy()
        assert directory.resolve().parent == OUTPUT.resolve()
        shutil.rmtree(directory)


if __name__ == "__main__":
    main()
