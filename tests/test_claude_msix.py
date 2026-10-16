"""Regression coverage for Claude Desktop's MSIX-virtualized configuration."""
import json
import os
from pathlib import Path
import shutil
import unittest
from unittest.mock import patch
import uuid

from maplemate.clients import ClientSetupError, ClaudeDesktopSetup, CodexSetup, server_settings
from maplemate.config import Config, linked_data_directory

TEST_ROOT = Path(__file__).resolve().parents[1] / ".cache" / "claude-msix-tests"


class ClaudeMSIXTests(unittest.TestCase):
    def setUp(self):
        self.directory = TEST_ROOT / uuid.uuid4().hex
        self.config = Config.load(self.directory / "state")
        self.roaming = self.directory / "AppData" / "Roaming"
        self.local = self.directory / "AppData" / "Local"
        self.legacy = self.roaming / "Claude" / "claude_desktop_config.json"
        self.environment = patch.dict(os.environ, {
            "APPDATA": str(self.roaming), "LOCALAPPDATA": str(self.local),
            "CODEX_HOME": str(self.directory / "codex"),
            "MAPLEMATE_DATA_DIR": "", "MAPLEMCP_DATA_DIR": "",
        })
        self.environment.start()

    def tearDown(self):
        self.environment.stop()
        assert self.directory.resolve().parent == TEST_ROOT.resolve()
        shutil.rmtree(self.directory)

    def package(self, family="Claude_pzs8sxrjxfjjc", config=None):
        package = self.local / "Packages" / family
        package.mkdir(parents=True)
        path = package / "LocalCache" / "Roaming" / "Claude" / "claude_desktop_config.json"
        if config is not None:
            path.parent.mkdir(parents=True)
            path.write_text(json.dumps(config), encoding="utf-8")
        return path

    def test_reported_false_registration_is_repaired_at_msix_path(self):
        original = {"preferences": {"theme": "dark"}, "mcpServers": {
            "other": {"command": "keep.exe", "args": ["keep"]}}}
        path = self.package(config=original)
        # 1.0.1 wrote this file and reported success, but MSIX never read it.
        self.legacy.parent.mkdir(parents=True)
        self.legacy.write_text(json.dumps({"mcpServers": {"maple-mcp": server_settings(self.config)}}), encoding="utf-8")
        old_bytes = self.legacy.read_bytes()
        connector = ClaudeDesktopSetup(self.config)
        self.assertEqual(connector.path, path)
        self.assertEqual(connector.status(), "not_registered")
        self.assertEqual(connector.install(), "registered")
        saved = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(saved["mcpServers"]["maple-mcp"], server_settings(self.config))
        self.assertEqual(saved["mcpServers"]["other"], original["mcpServers"]["other"])
        self.assertEqual(saved["preferences"], original["preferences"])
        self.assertEqual(self.legacy.read_bytes(), old_bytes)
        backups = list(path.parent.glob("*.bak"))
        self.assertEqual(len(backups), 1)
        self.assertEqual(json.loads(backups[0].read_text(encoding="utf-8")), original)
        before = path.read_bytes()
        connector.install()
        self.assertEqual(before, path.read_bytes())
        self.assertEqual(len(list(path.parent.glob("*.bak"))), 1)

    def test_msix_first_run_creates_config_inside_package(self):
        path = self.package()
        connector = ClaudeDesktopSetup(self.config)
        self.assertEqual(connector.path, path)
        connector.install()
        self.assertTrue(path.is_file())
        self.assertFalse(self.legacy.exists())

    def test_non_packaged_install_keeps_standard_path(self):
        connector = ClaudeDesktopSetup(self.config)
        self.assertEqual(connector.path, self.legacy)
        self.assertEqual(connector.install(), "registered")

    def test_package_family_is_discovered_without_hardcoding_publisher_id(self):
        path = self.package("Claude_otherpublisher", {})
        self.assertEqual(ClaudeDesktopSetup(self.config).path, path)

    def test_explicit_directory_overrides_discovery(self):
        self.package(config={})
        explicit = self.directory / "explicit"
        self.assertEqual(ClaudeDesktopSetup(self.config, explicit).path,
                         explicit / "claude_desktop_config.json")

    def test_ambiguous_packages_do_not_write_to_an_arbitrary_profile(self):
        first = self.package(config={})
        second = self.package("Claude_anotherpublisher", {})
        with self.assertRaises(ClientSetupError):
            ClaudeDesktopSetup(self.config).install()
        self.assertEqual(json.loads(first.read_text(encoding="utf-8")), {})
        self.assertEqual(json.loads(second.read_text(encoding="utf-8")), {})
        self.assertFalse(self.legacy.exists())

    def test_invalid_msix_config_does_not_fall_back_to_legacy(self):
        path = self.package(config={})
        path.write_text("broken {", encoding="utf-8")
        with self.assertRaises(ClientSetupError):
            ClaudeDesktopSetup(self.config).install()
        self.assertEqual(path.read_text(encoding="utf-8"), "broken {")
        self.assertFalse(self.legacy.exists())

    def test_conflicting_msix_server_is_preserved(self):
        original = {"mcpServers": {"maple-mcp": {"command": "unrelated.exe"}}}
        path = self.package(config=original)
        with self.assertRaises(ClientSetupError):
            ClaudeDesktopSetup(self.config).install()
        self.assertEqual(json.loads(path.read_text(encoding="utf-8")), original)

    def test_existing_manual_frozen_registration_is_kept(self):
        path = self.package(config={})
        with patch("sys.frozen", True, create=True), patch("sys.executable", str(self.directory / "MapleMate.exe")):
            expected = server_settings(self.config)
            manual = {"mcpServers": {"maple-mcp": expected}, "preferences": {"keep": True}}
            path.write_text(json.dumps(manual), encoding="utf-8")
            before = path.read_bytes()
            self.assertEqual(ClaudeDesktopSetup(self.config).install(), "registered")
            self.assertEqual(path.read_bytes(), before)

    def test_frozen_gui_reuses_state_linked_from_msix_config(self):
        self.package(config={"mcpServers": {"maple-mcp": {
            "command": str(self.directory / "old" / "MapleMate-mcp.exe"),
            "args": ["--data-dir", str(self.config.data_dir), "serve"]}}})
        (self.config.data_dir / "state.sqlite3").touch()
        self.assertEqual(linked_data_directory(), self.config.data_dir)
        with patch("sys.frozen", True, create=True), patch("sys.executable", str(self.directory / "new" / "MapleMate.exe")):
            self.assertEqual(Config.load().data_dir, self.config.data_dir)

    def test_linked_state_checks_msix_instead_of_stale_legacy_file(self):
        other = Config.load(self.directory / "stale-state")
        (other.data_dir / "state.sqlite3").touch()
        (self.config.data_dir / "state.sqlite3").touch()
        self.legacy.parent.mkdir(parents=True)
        self.legacy.write_text(json.dumps({"mcpServers": {"maple-mcp": server_settings(other)}}), encoding="utf-8")
        self.package(config={"mcpServers": {"maple-mcp": server_settings(self.config)}})
        CodexSetup(self.config).install()
        self.assertEqual(linked_data_directory(), self.config.data_dir)


if __name__ == "__main__":
    unittest.main()
