"""Authenticated Windows named-pipe RPC; no HTTP listener or web UI."""
import base64
from dataclasses import dataclass
import io
import json
from multiprocessing import AuthenticationError
from multiprocessing.connection import Client, Listener
import os
from pathlib import Path
import secrets
import threading
import time
from typing import Callable
import uuid
from PIL import Image
from .capture import CapturedFrame, CaptureError, capture_game
from .config import Config
from .secrets import SecretError, _crypt
from .state import StateStore


class MateRuntimeError(RuntimeError):
    pass


@dataclass(frozen=True)
class Snapshot:
    metadata: dict
    png: bytes


class CaptureManager:
    def __init__(self, store: StateStore, capture: Callable[[], CapturedFrame] = capture_game):
        self.store, self.capture = store, capture
        self.lock = threading.Lock()
        self.closed = threading.Event()

    def status(self) -> dict:
        # Keep the old status field for older MCP clients; no pixels are retained.
        return {"mode": "on_demand", "saved_capture": None}

    def get(self, use_saved: bool = False, delay_seconds: int = 0) -> Snapshot:
        if self.closed.is_set():
            raise CaptureError("MapleMate가 종료되었습니다.")
        if type(use_saved) is not bool or type(delay_seconds) is not int or not 0 <= delay_seconds <= 15:
            raise ValueError("Invalid capture arguments")
        if use_saved:
            raise CaptureError("화면 고정 기능은 제거되었습니다. 첨부 이미지를 읽거나 새 캡처를 요청하세요.")
        if not self.lock.acquire(blocking=False):
            raise CaptureError("이미 캡처 중입니다. 잠시 후 다시 시도해 주세요.")
        try:
            if self.closed.wait(delay_seconds):
                raise CaptureError("캡처 요청이 취소되었습니다.")
            frame = self.capture()
            if self.closed.is_set():
                raise CaptureError("캡처 요청이 취소되었습니다.")
            with Image.open(io.BytesIO(frame.png)) as image:
                width, height = image.size
            metadata = {"screenshot_id": uuid.uuid4().hex, "captured_at": frame.captured_at,
                        "source": "windows_graphics_capture", "scope": "maplestory_window", "width": width, "height": height,
                        "delivery": "requested", "is_live": False}
            value = Snapshot(metadata, frame.png)
            self.store.register_capture(metadata)
            return value
        finally:
            self.lock.release()

    def reset_player_state(self, name: str, scope: str, expected_revision: int) -> dict:
        if not self.lock.acquire(blocking=False):
            raise CaptureError("화면 캡처 중입니다. 캡처가 끝난 뒤 다시 초기화해 주세요.")
        try:
            return self.store.reset_player_state(name, scope, expected_revision)
        finally:
            self.lock.release()

    def close(self) -> None:
        self.closed.set()


class RuntimeServer:
    def __init__(self, config: Config, manager: CaptureManager, on_exit: Callable[[], None], status_details: Callable[[], dict] | None = None, on_open: Callable[[], None] | None = None):
        self.config, self.manager, self.on_exit = config, manager, on_exit
        self.status_details = status_details or (lambda: {})
        self.on_open = on_open
        self.closed = threading.Event()
        self.authkey = secrets.token_bytes(32)
        self.address = r"\\.\pipe\MapleMate-" + uuid.uuid4().hex
        self.listener = Listener(self.address, family="AF_PIPE", authkey=self.authkey)
        self.path = config.data_dir / "tray-runtime.json"
        self.instance = uuid.uuid4().hex
        descriptor = {"address": self.address, "authkey_dpapi": base64.b64encode(_crypt(self.authkey)).decode("ascii"), "instance": self.instance, "pid": os.getpid()}
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps(descriptor), encoding="utf-8")
        temporary.replace(self.path)
        self.thread = threading.Thread(target=self._serve, daemon=True, name="MapleMate IPC")
        self.thread.start()

    def _serve(self) -> None:
        while not self.closed.is_set():
            try:
                connection = self.listener.accept()
            except AuthenticationError:
                continue
            except (OSError, EOFError):
                break
            threading.Thread(target=self._handle, args=(connection,), daemon=True).start()

    def _handle(self, connection) -> None:
        try:
            if not connection.poll(5):
                return
            request = json.loads(connection.recv_bytes(maxlength=8192))
            action = request.get("action")
            if action == "status":
                value = {"status": "running", **self.manager.status(), **self.status_details()}
            elif action == "capture":
                capture = self.manager.get(request.get("use_saved", False), request.get("delay_seconds", 0))
                value = {"status": "ok", "metadata": capture.metadata, "png": base64.b64encode(capture.png).decode("ascii")}
            elif action == "shutdown":
                value = {"status": "stopping"}
            elif action == "show_settings" and self.on_open:
                value = {"status": "opening"}
            else:
                value = {"status": "error", "message": "Unknown local action"}
            connection.send_bytes(json.dumps(value).encode("utf-8"))
            if action == "shutdown":
                self.on_exit()
            elif action == "show_settings" and self.on_open:
                self.on_open()
        except (CaptureError, ValueError) as error:
            try:
                connection.send_bytes(json.dumps({"status": "error", "message": str(error)}, ensure_ascii=False).encode("utf-8"))
            except OSError:
                pass
        except (OSError, EOFError):
            pass
        finally:
            connection.close()

    def close(self) -> None:
        self.closed.set()
        self.manager.close()
        self.listener.close()
        try:
            if json.loads(self.path.read_text(encoding="utf-8")).get("instance") == self.instance:
                self.path.unlink()
        except (OSError, ValueError):
            pass


class RuntimeClient:
    def __init__(self, config: Config):
        self.config = config

    def call(self, action: str, **arguments) -> dict:
        try:
            descriptor = json.loads((self.config.data_dir / "tray-runtime.json").read_text(encoding="utf-8"))
            address = descriptor["address"]
            if not isinstance(address, str) or not address.startswith(r"\\.\pipe\MapleMate-"):
                raise ValueError("Invalid runtime")
            authkey = _crypt(base64.b64decode(descriptor["authkey_dpapi"], validate=True), decrypt=True)
            with Client(address, family="AF_PIPE", authkey=authkey) as connection:
                connection.send_bytes(json.dumps({"action": action, **arguments}).encode())
                if not connection.poll(30):
                    raise MateRuntimeError("MapleMate 응답을 기다리다 시간이 초과되었습니다.")
                return json.loads(connection.recv_bytes(maxlength=48 * 1024 * 1024))
        except (OSError, ValueError, KeyError, EOFError, SecretError, AuthenticationError):
            raise MateRuntimeError("MapleMate가 실행 중인지 트레이 아이콘을 확인해 주세요.") from None

    def status(self) -> dict:
        try:
            return self.call("status")
        except MateRuntimeError:
            return {"status": "stopped", "mode": "on_demand", "saved_capture": None}

    def ensure_running(self) -> None:
        if self.status()["status"] == "running":
            return
        if os.name != "nt":
            raise MateRuntimeError("Windows 10/11 is required for MapleMate tray.")
        from .launch import start_tray
        start_tray(self.config.data_dir)
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            if self.status()["status"] == "running":
                return
            time.sleep(0.2)
        raise MateRuntimeError("MapleMate를 시작하지 못했습니다. 바로가기로 실행해 주세요.")
