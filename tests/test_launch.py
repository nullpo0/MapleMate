from pathlib import Path
from contextlib import nullcontext
import json
import shutil
import uuid
import unittest
from unittest.mock import Mock, patch
from maplemate.launch import command_for, needs_detach
from maplemate.config import Config
from maplemate.runtime import RuntimeClient, RuntimeServer
from maplemate.tray import TrayApp, run
from maplemate.cli import main


class LaunchTests(unittest.TestCase):
    def test_startup_opens_settings_and_notifies_even_with_existing_key(self):
        app = TrayApp.__new__(TrayApp)
        app.closing = False
        app.icon = Mock(visible=True)
        app.open_settings, app.notify = Mock(), Mock()
        app.welcome()
        app.open_settings.assert_called_once_with()
        app.notify.assert_called_once()
        self.assertIn("Codex", app.notify.call_args.args[0])
        self.assertIn("Claude Desktop", app.notify.call_args.args[0])

    def test_startup_waits_until_tray_is_ready_and_skips_during_shutdown(self):
        app = TrayApp.__new__(TrayApp)
        app.closing = False
        app.icon, app.root = Mock(visible=False), Mock()
        app.open_settings, app.notify = Mock(), Mock()
        app.welcome()
        app.root.after.assert_called_once_with(200, app.welcome)
        app.open_settings.assert_not_called()
        app.notify.assert_not_called()
        app.closing = True
        app.icon.visible = True
        app.welcome()
        app.open_settings.assert_not_called()

    def test_duplicate_launch_opens_existing_settings_without_new_tray(self):
        config = Config(Path(r"C:\existing-state"))
        with patch("maplemate.tray.tray_lease", return_value=nullcontext(False)), \
                patch("maplemate.tray.RuntimeClient") as client, patch("maplemate.tray.TrayApp") as app:
            run(config)
            client.return_value.call.assert_called_once_with("show_settings")
            app.assert_not_called()

    def test_open_settings_rpc_does_not_capture_or_shutdown(self):
        server = RuntimeServer.__new__(RuntimeServer)
        server.manager, server.on_exit, server.on_open = Mock(), Mock(), Mock()
        connection = Mock()
        connection.recv_bytes.return_value = b'{"action":"show_settings"}'
        server._handle(connection)
        response = json.loads(connection.send_bytes.call_args.args[0])
        self.assertEqual(response, {"status": "opening"})
        server.on_open.assert_called_once_with()
        server.manager.get.assert_not_called()
        server.on_exit.assert_not_called()
        connection.close.assert_called_once_with()

    def test_removed_saved_screen_request_does_not_autostart_or_capture(self):
        from maplemate.maple_mcp.server import build_server
        test_root = Path(__file__).resolve().parents[1] / ".cache" / "launch-tests"
        directory = test_root / uuid.uuid4().hex
        try:
            server = build_server(Config.load(directory))
            tool = server._tool_manager._tools["capture_game_screen"]
            with patch.object(RuntimeClient, "ensure_running") as start, patch.object(RuntimeClient, "call") as call:
                result = tool.fn(use_saved=True)
                self.assertTrue(result.isError)
                start.assert_not_called()
                call.assert_not_called()
        finally:
            assert directory.resolve().parent == test_root.resolve()
            shutil.rmtree(directory)

    def test_existing_runtime_is_reused_without_starting_another_tray(self):
        client = RuntimeClient(Config(Path(r"C:\existing-state")))
        with patch.object(client, "status", return_value={"status": "running"}), patch("maplemate.launch.start_tray") as start:
            client.ensure_running()
            start.assert_not_called()

    def test_capture_worker_dispatch_never_loads_state_or_starts_tray(self):
        with patch("sys.argv", ["MapleMate-mcp.exe", "capture-worker"]), \
                patch("maplemate.native_capture.main") as capture, patch("maplemate.cli.Config.load") as load, \
                patch("maplemate.launch.start_tray") as start:
            main()
            capture.assert_called_once_with()
            load.assert_not_called()
            start.assert_not_called()

    def test_frozen_uses_gui_and_stdio_siblings_without_module_arguments(self):
        with patch("sys.frozen", True, create=True), patch("sys.executable", r"C:\MapleMate\MapleMate.exe"):
            self.assertEqual(command_for("serve", Path(r"C:\state")),
                             [r"C:\MapleMate\MapleMate-mcp.exe", "--data-dir", r"C:\state", "serve"])
            self.assertEqual(command_for("capture-worker"), [r"C:\MapleMate\MapleMate-mcp.exe", "capture-worker"])
            self.assertEqual(command_for("tray")[0], r"C:\MapleMate\MapleMate.exe")
            self.assertFalse(needs_detach())

    def test_console_detaches_tray_but_pythonw_does_not_relaunch(self):
        with patch("sys.frozen", False, create=True), patch("sys.executable", r"C:\Python\python.exe"):
            self.assertTrue(needs_detach())
            self.assertEqual(command_for("tray")[:3], [r"C:\Python\pythonw.exe", "-m", "maplemate"])
        with patch("sys.frozen", False, create=True), patch("sys.executable", r"C:\Python\pythonw.exe"):
            self.assertFalse(needs_detach())
            self.assertEqual(command_for("serve")[0], r"C:\Python\python.exe")
