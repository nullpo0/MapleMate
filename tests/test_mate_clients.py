from pathlib import Path
import hashlib
import json
import os
import shutil
import tomllib
import unittest
from unittest.mock import patch
import uuid
from maplemate.clients import ClientSetupError, ClaudeDesktopSetup, CodexSetup, codex_snippet, install_maplemate_skill, server_settings
from maplemate.config import Config, linked_data_directory
from maplemate.lifecycle import tray_lease

TEST_ROOT = Path(__file__).resolve().parents[1] / ".cache" / "client-tests"


class ClientTests(unittest.TestCase):
    def setUp(self):
        self.directory = TEST_ROOT / uuid.uuid4().hex
        self.config = Config.load(self.directory / "state")

    def tearDown(self):
        assert self.directory.resolve().parent == TEST_ROOT.resolve()
        shutil.rmtree(self.directory)

    def test_codex_preserves_comments_models_and_other_servers(self):
        connector = CodexSetup(self.config, self.directory / "codex")
        connector.path.parent.mkdir()
        original = '# keep comment\nmodel = "my-model"\n[mcp_servers.other]\ncommand = "other"\n'
        connector.path.write_text(original)
        self.assertEqual(connector.install(), "registered")
        updated = connector.path.read_text(encoding="utf-8")
        self.assertTrue(updated.startswith(original))
        self.assertEqual(tomllib.loads(updated)["mcp_servers"]["maple-mcp"], server_settings(self.config))
        self.assertEqual(next(connector.path.parent.glob("*.bak")).read_text(), original)
        connector.install()
        self.assertEqual(connector.path.read_text(encoding="utf-8"), updated)

    def test_codex_migrates_only_owned_legacy_entry_and_preserves_policies(self):
        connector = CodexSetup(self.config, self.directory / "codex")
        connector.path.parent.mkdir()
        legacy = server_settings(self.config)
        legacy["args"][1] = "maplemcp"
        content = '[mcp_servers.maplemcp]\n# keep approval\n' + '\n'.join(f'{key} = {json.dumps(value)}' for key, value in legacy.items()) + '\nenabled = false\n[mcp_servers.maplemcp.tools.get_player_state]\napproval_mode = "prompt"\n'
        connector.path.write_text(content)
        self.assertEqual(connector.install(), "disabled")
        parsed = tomllib.loads(connector.path.read_text(encoding="utf-8"))
        self.assertNotIn("maplemcp", parsed["mcp_servers"])
        self.assertEqual(parsed["mcp_servers"]["maple-mcp"]["tools"]["get_player_state"]["approval_mode"], "prompt")
        self.assertIn("keep approval", connector.path.read_text(encoding="utf-8"))

    def test_codex_conflicting_or_invalid_settings_are_not_overwritten(self):
        connector = CodexSetup(self.config, self.directory / "codex")
        connector.path.parent.mkdir()
        for original in ('[mcp_servers.maple-mcp]\ncommand = "other-project"\n', 'not valid = [', 'mcp_servers = "invalid"'):
            connector.path.write_text(original)
            with self.assertRaises(ClientSetupError):
                connector.install()
            self.assertEqual(connector.path.read_text(), original)

    def test_claude_merges_settings_and_backup_without_keys(self):
        connector = ClaudeDesktopSetup(self.config, self.directory / "claude")
        connector.path.parent.mkdir()
        original = {"preferences": {"theme": "dark"}, "mcpServers": {"other": {"command": "other"}}}
        connector.path.write_text(json.dumps(original))
        self.assertEqual(connector.install(), "registered")
        result = json.loads(connector.path.read_text())
        self.assertEqual(result["preferences"], original["preferences"])
        self.assertEqual(result["mcpServers"]["other"], original["mcpServers"]["other"])
        self.assertEqual(result["mcpServers"]["maple-mcp"], server_settings(self.config))
        self.assertNotIn("NEXON_API_KEY", connector.path.read_text())
        before = connector.path.read_bytes()
        connector.install()
        self.assertEqual(connector.path.read_bytes(), before)

    def test_claude_invalid_or_conflicting_config_is_preserved(self):
        connector = ClaudeDesktopSetup(self.config, self.directory / "claude")
        connector.path.parent.mkdir()
        for content in ('broken {', '[]', '{"mcpServers":{"maple-mcp":{"command":"different"}}}'):
            connector.path.write_text(content)
            with self.assertRaises(ClientSetupError):
                connector.install()
            self.assertEqual(connector.path.read_text(), content)

    def test_skill_install_preserves_user_changes(self):
        destination = self.directory / "skills" / "maplemate"
        self.assertEqual(install_maplemate_skill(destination), "installed")
        self.assertEqual(install_maplemate_skill(destination), "already_installed")
        (destination / "SKILL.md").write_text("User's own edits")
        with self.assertRaises(ClientSetupError):
            install_maplemate_skill(destination)
        self.assertEqual((destination / "SKILL.md").read_text(), "User's own edits")

    def test_maplemate_entry_installs_metadata_and_preserves_user_edits(self):
        destination = self.directory / "skills" / "maplemate"
        self.assertEqual(install_maplemate_skill(destination), "installed")
        self.assertEqual(install_maplemate_skill(destination), "already_installed")
        self.assertTrue((destination / "agents/openai.yaml").is_file())
        self.assertTrue((destination / "references/damage.md").is_file())
        self.assertTrue((destination / "references/advanced.md").is_file())
        (destination / "SKILL.md").write_text("User's own MapleMate workflow")
        with self.assertRaises(ClientSetupError):
            install_maplemate_skill(destination)
        self.assertEqual((destination / "SKILL.md").read_text(), "User's own MapleMate workflow")

    def managed_skill(self, name, contents):
        directory = self.directory / "skills" / name
        manifest = {}
        for relative, content in contents.items():
            target = directory / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
            manifest[relative] = hashlib.sha256(target.read_bytes()).hexdigest()
        (directory / ".maplemate-managed.json").write_text(json.dumps(manifest), encoding="utf-8")
        return directory

    def test_two_managed_skills_migrate_to_one_and_keep_backups_outside_discovery(self):
        destination = self.managed_skill("maplemate", {"SKILL.md": "old entry", "agents/openai.yaml": "old metadata"})
        legacy = self.managed_skill("maple-beginner-guide", {"SKILL.md": "old common guide", "references/damage.md": "old damage procedure"})
        other = self.directory / "skills" / "other" / "SKILL.md"
        other.parent.mkdir()
        other.write_text("unrelated user's skill")
        self.assertEqual(install_maplemate_skill(destination), "installed")
        self.assertFalse(legacy.exists())
        self.assertEqual(other.read_text(), "unrelated user's skill")
        self.assertEqual({p.parent.name for p in destination.parent.rglob("SKILL.md")}, {"maplemate", "other"})
        backups = list((self.directory / "maplemate-skill-backups").glob("maple-beginner-guide.*.bak"))
        self.assertEqual(len(backups), 1)
        self.assertEqual((backups[0] / "references/damage.md").read_text(), "old damage procedure")
        self.assertTrue((destination / "references/overview.md").is_file())
        count = len(list((self.directory / "maplemate-skill-backups").iterdir()))
        self.assertEqual(install_maplemate_skill(destination), "already_installed")
        self.assertEqual(len(list((self.directory / "maplemate-skill-backups").iterdir())), count)

    def test_modified_or_unmanaged_legacy_skill_is_not_moved(self):
        destination = self.directory / "skills" / "maplemate"
        legacy = self.managed_skill("maple-beginner-guide", {"SKILL.md": "original guide"})
        (legacy / "SKILL.md").write_text("user's custom workflow")
        with self.assertRaises(ClientSetupError):
            install_maplemate_skill(destination)
        self.assertEqual((legacy / "SKILL.md").read_text(), "user's custom workflow")
        self.assertFalse(destination.exists())
        (legacy / ".maplemate-managed.json").unlink()
        with self.assertRaises(ClientSetupError):
            install_maplemate_skill(destination)
        self.assertTrue(legacy.exists())

    def test_legacy_skill_with_user_added_file_is_preserved(self):
        legacy = self.managed_skill("maple-beginner-guide", {"SKILL.md": "original guide"})
        (legacy / "notes.txt").write_text("user's notes")
        with self.assertRaises(ClientSetupError):
            install_maplemate_skill(self.directory / "skills" / "maplemate")
        self.assertEqual((legacy / "notes.txt").read_text(), "user's notes")

    def test_skill_upgrade_can_add_a_reference_without_overwriting_edits(self):
        destination = self.directory / "skills" / "maplemate"
        install_maplemate_skill(destination)
        (destination / "references/damage.md").unlink()
        manifest = destination / ".maplemate-managed.json"
        old = json.loads(manifest.read_text())
        del old["references/damage.md"]
        manifest.write_text(json.dumps(old))
        self.assertEqual(install_maplemate_skill(destination), "installed")
        self.assertTrue((destination / "references/damage.md").is_file())

    def test_stage_guide_upgrade_preserves_existing_references_and_detects_edits(self):
        destination = self.directory / "skills" / "maplemate"
        install_maplemate_skill(destination)
        manifest = destination / ".maplemate-managed.json"
        old = json.loads(manifest.read_text())
        topics = ("progression", "beginner", "intermediate", "advanced", "playstyle", "community", "state")
        for topic in topics:
            name = f"references/{topic}.md"
            (destination / name).unlink()
            del old[name]
        manifest.write_text(json.dumps(old))
        before = (destination / "references/upgrade.md").read_bytes()
        self.assertEqual(install_maplemate_skill(destination), "installed")
        self.assertEqual(before, (destination / "references/upgrade.md").read_bytes())
        self.assertTrue(all((destination / f"references/{topic}.md").is_file() for topic in topics))
        self.assertFalse(list(destination.parent.glob("*.bak")))
        backups = list((self.directory / "maplemate-skill-backups").glob("*.bak"))
        self.assertTrue(backups)
        self.assertEqual(before, (backups[0] / "references/upgrade.md").read_bytes())
        (destination / "references/advanced.md").write_text("User's own boss strategy")
        with self.assertRaises(ClientSetupError):
            install_maplemate_skill(destination)
        self.assertEqual((destination / "references/advanced.md").read_text(), "User's own boss strategy")

    def test_source_registration_migrates_to_frozen_and_preserves_policies(self):
        connector = CodexSetup(self.config, self.directory / "codex")
        connector.path.parent.mkdir()
        original = codex_snippet(self.config).replace('args = [', 'args = [\n')
        original += 'enabled = false\nenv = { KEEP = "yes" }\n[mcp_servers.maple-mcp.tools.get_player_state]\napproval_mode = "prompt"\n'
        connector.path.write_text(original)
        with patch("sys.frozen", True, create=True), patch("sys.executable", str(self.directory / "MapleMate.exe")):
            self.assertEqual(connector.install(), "disabled")
            parsed = tomllib.loads(connector.path.read_text(encoding="utf-8"))["mcp_servers"]["maple-mcp"]
            self.assertEqual(parsed["command"], str(self.directory / "MapleMate-mcp.exe"))
            self.assertEqual(parsed["args"], ["--data-dir", str(self.config.data_dir), "serve"])
        self.assertEqual(parsed["env"], {"KEEP": "yes"})
        self.assertEqual(parsed["tools"]["get_player_state"]["approval_mode"], "prompt")

    def test_frozen_reuses_adjacent_legacy_state(self):
        legacy = self.directory / ".maplemcp"
        legacy.mkdir()
        with patch.dict(os.environ, {"MAPLEMATE_DATA_DIR": "", "MAPLEMCP_DATA_DIR": ""}), patch("sys.frozen", True, create=True), patch("sys.executable", str(self.directory / "MapleMate.exe")):
            self.assertEqual(Config.load().data_dir, legacy.resolve())

    def test_frozen_reuses_linked_source_state_before_creating_another_tray_directory(self):
        codex_dir, appdata = self.directory / "codex", self.directory / "appdata"
        connector = CodexSetup(self.config, codex_dir)
        connector.install()
        (self.config.data_dir / "state.sqlite3").touch()
        bundle = self.directory / "bundle"
        bundle.mkdir()
        with patch.dict(os.environ, {"CODEX_HOME": str(codex_dir), "APPDATA": str(appdata),
                "LOCALAPPDATA": str(appdata), "MAPLEMATE_DATA_DIR": "", "MAPLEMCP_DATA_DIR": ""}), \
                patch("sys.frozen", True, create=True), patch("sys.executable", str(bundle / "MapleMate.exe")):
            self.assertEqual(Config.load().data_dir, self.config.data_dir)
            explicit = self.directory / "explicit"
            self.assertEqual(Config.load(explicit).data_dir, explicit)

    def test_linked_state_ignores_unrelated_or_conflicting_connections(self):
        codex_dir, appdata = self.directory / "codex", self.directory / "appdata"
        CodexSetup(self.config, codex_dir).install()
        (self.config.data_dir / "state.sqlite3").touch()
        other = Config.load(self.directory / "other")
        (other.data_dir / "state.sqlite3").touch()
        ClaudeDesktopSetup(other, appdata / "Claude").install()
        with patch.dict(os.environ, {"CODEX_HOME": str(codex_dir), "APPDATA": str(appdata)}):
            self.assertIsNone(linked_data_directory())
            (appdata / "Claude" / "claude_desktop_config.json").unlink()
            connector = CodexSetup(self.config, codex_dir)
            connector.path.write_text('[mcp_servers.maple-mcp]\ncommand="other.exe"\nargs=[]\n')
            self.assertIsNone(linked_data_directory())

    def test_source_to_frozen_registration_accepts_equivalent_windows_path_separators(self):
        connector = CodexSetup(self.config, self.directory / "codex")
        connector.path.parent.mkdir()
        original = server_settings(self.config)
        original["args"][-2] = str(self.config.data_dir).replace('\\', '\\\\')
        content = '[mcp_servers.maple-mcp]\n' + '\n'.join(f'{key} = {json.dumps(value)}' for key, value in original.items())
        content += '\nenabled = false\n[mcp_servers.other]\ncommand="keep"\n'
        connector.path.write_text(content)
        with patch("sys.frozen", True, create=True), patch("sys.executable", str(self.directory / "MapleMate.exe")):
            self.assertEqual(connector.install(), "disabled")
            parsed = tomllib.loads(connector.path.read_text(encoding="utf-8"))
            self.assertEqual(parsed["mcp_servers"]["maple-mcp"]["args"], ["--data-dir", str(self.config.data_dir), "serve"])
            self.assertEqual(parsed["mcp_servers"]["other"]["command"], "keep")

    def test_default_data_directory_does_not_depend_on_ai_cwd(self):
        before = Path.cwd()
        try:
            os.chdir(self.directory)
            with patch.dict(os.environ, {"MAPLEMATE_DATA_DIR": "", "MAPLEMCP_DATA_DIR": ""}):
                default = Config.load()
            self.assertNotEqual(default.data_dir, self.directory / ".maplemate")
        finally:
            os.chdir(before)

    @unittest.skipUnless(os.name == "nt", "Windows tray mutex")
    def test_duplicate_tray_is_rejected_and_lock_released(self):
        with tray_lease(self.config.data_dir) as first:
            self.assertTrue(first)
            with tray_lease(self.config.data_dir) as second:
                self.assertFalse(second)
        with tray_lease(self.config.data_dir) as next_instance:
            self.assertTrue(next_instance)
