"""Windows tray shell with a small, on-demand settings dialog."""
import queue
import os
from pathlib import Path
import sys
import threading
import time
import tkinter as tk
from PIL import Image, ImageDraw
import pystray
from .config import Config
from .lifecycle import tray_lease
from .runtime import CaptureManager, MateRuntimeError, RuntimeClient, RuntimeServer
from .settings import SettingsWindow
from .state import StateStore

def create_root() -> tk.Tk:
    # Some Windows venv launchers omit the base install's Tcl search path.
    for variable, folder in (("TCL_LIBRARY", f"tcl{tk.TclVersion}"), ("TK_LIBRARY", f"tk{tk.TkVersion}")):
        library = Path(sys.base_prefix) / "tcl" / folder
        if not os.environ.get(variable) and library.is_dir():
            os.environ[variable] = str(library)
    return tk.Tk()


def icon_image() -> Image.Image:
    image = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((1, 1, 63, 63), radius=16, fill="#243c35")
    draw.polygon([(32, 8), (38, 23), (46, 18), (43, 30), (56, 28), (48, 39), (51, 43), (35, 44), (35, 55), (29, 55), (29, 44), (13, 43), (16, 39), (8, 28), (21, 30), (18, 18), (26, 23)], fill="#f3a949")
    return image


class TrayApp:
    def __init__(self, config: Config):
        self.config = config
        self.store = StateStore(config.data_dir)
        self.manager = CaptureManager(self.store)
        self.actions: queue.Queue = queue.Queue()
        self.closing = False
        self.root = create_root()
        self.root.withdraw()
        self.root.title("MapleMate")
        self.settings = None
        self.message = tk.StringVar(value="준비됨")
        self.icon = pystray.Icon("MapleMate", icon_image(), "MapleMate", menu=pystray.Menu(
            pystray.MenuItem("MapleMate 설정", lambda *_: self.post(self.open_settings), default=True),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("종료", lambda *_: self.post(self.quit))))
        self.server = RuntimeServer(config, self.manager, lambda: self.post(self.quit),
                                    lambda: {"tray_icon_visible": self.icon.visible},
                                    on_open=lambda: self.post(self.welcome))
        self.root.after(100, self.drain)

    def post(self, action):
        self.actions.put(action)

    def drain(self):
        while not self.actions.empty():
            self.actions.get_nowait()()
        if not self.closing:
            self.root.after(100, self.drain)

    def notify(self, text):
        try:
            self.icon.notify(text, "MapleMate")
        except Exception:
            pass
        self.post(lambda: self.message.set(text))

    def welcome(self):
        if self.closing:
            return
        if not self.icon.visible:
            self.root.after(200, self.welcome)
            return
        self.open_settings()
        self.notify("설정 후 Codex나 Claude Desktop에 메이플 질문을 해보세요.")

    def open_settings(self):
        if self.settings and self.settings.window.winfo_exists():
            self.settings.show()
            return
        self.settings = SettingsWindow(
            self.root, self.config, self.store, self.manager, self.post, self.message,
            icon_image())

    def quit(self):
        if self.closing:
            return
        self.closing = True
        self.server.close()
        self.icon.stop()
        self.root.destroy()

    def run(self):
        thread = threading.Thread(target=self.icon.run, daemon=True, name="MapleMate tray")
        thread.start()
        self.root.after(300, self.welcome)
        try:
            self.root.mainloop()
        finally:
            self.quit()


def run(config: Config) -> None:
    with tray_lease(config.data_dir) as acquired:
        if not acquired:
            # A second explicit launch opens the existing window, without another tray.
            client = RuntimeClient(config)
            deadline = time.monotonic() + 8
            while time.monotonic() < deadline:
                try:
                    client.call("show_settings")
                    return
                except MateRuntimeError:
                    time.sleep(0.1)
            return
        app = TrayApp(config)
        app.run()
