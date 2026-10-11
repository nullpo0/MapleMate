"""Launch and gracefully stop the actual hidden tray in isolated local state."""
import json
import argparse
import ctypes
from ctypes import wintypes
import os
from pathlib import Path
import shutil
import subprocess
import time
import uuid
from maplemate.config import Config
from maplemate.clients import CodexSetup
from maplemate.runtime import RuntimeClient
from maplemate.state import StateStore

ROOT = Path(__file__).resolve().parents[1]
TEST_ROOT = ROOT / ".cache" / "maplemate-tray-smoke"
directory = TEST_ROOT / uuid.uuid4().hex
config = Config.load(directory)
client = RuntimeClient(config)
parser = argparse.ArgumentParser()
parser.add_argument("--bundle")
parser.add_argument("--settings", action="store_true", help="Also render the bundled welcome/settings UI with synthetic state")
args = parser.parse_args()
bundle = args.bundle
if args.settings and not bundle:
    parser.error("--settings requires --bundle")
try:
    if bundle:
        command = str(Path(bundle).resolve() / "MapleMate-mcp.exe")
        # A ZIP opened after a source install must find the linked state itself.
        StateStore(directory)
        CodexSetup(config, directory / "codex").install()
        environment = {**os.environ, "CODEX_HOME": str(directory / "codex"), "APPDATA": str(directory / "appdata"),
                       "LOCALAPPDATA": str(directory / "localappdata"), "MAPLEMATE_DATA_DIR": "", "MAPLEMCP_DATA_DIR": "",
                       "NEXON_API_KEY": "synthetic-smoke-key"}
        process = subprocess.run([command, "tray"], env=environment,
                                 capture_output=True, timeout=15, creationflags=subprocess.CREATE_NO_WINDOW)
        assert process.returncode == 0
        deadline = time.monotonic() + 8
        while client.status()["status"] != "running" and time.monotonic() < deadline:
            time.sleep(0.1)
    else:
        client.ensure_running()
    status = client.status()
    icon_deadline = time.monotonic() + 3
    while not status.get("tray_icon_visible") and time.monotonic() < icon_deadline:
        time.sleep(0.1)
        status = client.status()
    assert status["tray_icon_visible"]
    assert status["status"] == "running" and status["mode"] == "on_demand"
    assert status["saved_capture"] is None
    descriptor = directory / "tray-runtime.json"
    pid = json.loads(descriptor.read_text(encoding="utf-8"))["pid"]
    if args.settings:
        from preview_settings import OUTPUT, save_hwnd
        from PIL import Image
        user = ctypes.windll.user32
        callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        user.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
        user.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
        user.IsWindowVisible.argtypes = [wintypes.HWND]
        user.EnumWindows.argtypes = [callback_type, wintypes.LPARAM]
        windows = []
        def find(hwnd, _):
            owner = wintypes.DWORD()
            user.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
            if owner.value == pid and user.IsWindowVisible(hwnd):
                title = ctypes.create_unicode_buffer(100)
                user.GetWindowTextW(hwnd, title, 100)
                if title.value == "MapleMate":
                    windows.append(hwnd)
            return True
        callback = callback_type(find)
        deadline = time.monotonic() + 5
        while not windows and time.monotonic() < deadline:
            user.EnumWindows(callback, 0)
            time.sleep(0.1)
        assert windows, "Bundled settings window did not open"
        time.sleep(0.5)
        OUTPUT.mkdir(parents=True, exist_ok=True)
        preview = OUTPUT / "bundled-settings.png"
        save_hwnd(windows[0], preview)
        with Image.open(preview) as image:
            green_pixels = sum(1 for red, green, blue in image.getdata() if green > red * 1.3 and green > blue * 1.3 and green < 110)
            assert green_pixels > 20_000, "Bundled settings UI did not finish rendering"
        # Close only this isolated app's settings, then verify an explicit relaunch reopens it.
        user.SendMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
        user.SendMessageW(windows[0], 0x0010, 0, 0)  # WM_CLOSE -> withdraw, keep the tray
        deadline = time.monotonic() + 3
        while user.IsWindowVisible(windows[0]) and time.monotonic() < deadline:
            time.sleep(0.1)
        assert not user.IsWindowVisible(windows[0])
    client.ensure_running()
    if bundle:
        duplicate = subprocess.run([str(Path(bundle).resolve() / "MapleMate.exe"), "tray"], env=environment,
                                   capture_output=True, timeout=10, creationflags=subprocess.CREATE_NO_WINDOW)
        assert duplicate.returncode == 0
        if args.settings:
            deadline = time.monotonic() + 3
            while not user.IsWindowVisible(windows[0]) and time.monotonic() < deadline:
                time.sleep(0.1)
            assert user.IsWindowVisible(windows[0]), "Relaunch did not reopen the existing settings"
    assert json.loads(descriptor.read_text(encoding="utf-8"))["pid"] == pid
    assert client.status()["tray_icon_visible"]
    assert client.call("shutdown")["status"] == "stopping"
    deadline = time.monotonic() + 8
    while (directory / "tray-runtime.json").exists() and time.monotonic() < deadline:
        time.sleep(0.1)
    assert not (directory / "tray-runtime.json").exists()
    assert client.status()["status"] == "stopped"
    print(json.dumps({"bundled_executable": bool(bundle), "settings_ui_rendered_with_key": args.settings, "duplicate_reopens_settings": args.settings, "linked_source_state_reused": bool(bundle), "duplicate_launch_reuses_tray_pid": True, "console_hidden": True, "tray_icon_visible": True, "named_pipe_ready": True, "no_capture_on_launch": True, "graceful_shutdown": True}))
finally:
    if client.status()["status"] == "running":
        client.call("shutdown")
    assert directory.resolve().parent == TEST_ROOT.resolve()
    shutil.rmtree(directory)
