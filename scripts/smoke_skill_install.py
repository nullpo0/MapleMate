"""Exercise the bundled single-skill installer in an isolated Windows profile."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tomllib
import uuid

ROOT = Path(__file__).resolve().parents[1]
TEST_ROOT = ROOT / ".cache" / "skill-install-smoke"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--bundle", required=True)
    args = parser.parse_args()
    directory = TEST_ROOT / uuid.uuid4().hex
    profile = directory / "profile"
    skills = profile / ".agents" / "skills"
    try:
        for name in ("maplemate", "maple-beginner-guide"):
            skill = skills / name
            skill.mkdir(parents=True)
            contents = {"SKILL.md": b"previous managed guidance"}
            if name == "maplemate":
                contents["agents/openai.yaml"] = b"previous metadata"
            manifest = {}
            for relative, content in contents.items():
                path = skill / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(content)
                manifest[relative] = hashlib.sha256(content).hexdigest()
            (skill / ".maplemate-managed.json").write_text(json.dumps(manifest), encoding="utf-8")
        codex = directory / "codex"
        codex.mkdir()
        configuration = codex / "config.toml"
        configuration.write_text('model="keep-model"\n[mcp_servers.other]\ncommand="other"\n', encoding="utf-8")
        environment = {**os.environ, "USERPROFILE": str(profile), "CODEX_HOME": str(codex),
                       "APPDATA": str(directory / "appdata"), "LOCALAPPDATA": str(directory / "localappdata")}
        assert Path(os.path.expandvars(str(profile))).resolve().is_relative_to(TEST_ROOT.resolve())
        command = [str(Path(args.bundle).resolve() / "MapleMate-mcp.exe"), "--data-dir", str(directory / "state"), "connect", "codex"]
        for _ in range(2):
            result = subprocess.run(command, env=environment, capture_output=True, timeout=20,
                                    creationflags=subprocess.CREATE_NO_WINDOW)
            assert result.returncode == 0 and not result.stderr, result.stderr.decode(errors="replace")
        assert sorted(path.name for path in skills.iterdir()) == ["maplemate"]
        bundled = Path(args.bundle).resolve() / "_internal" / "maplemate" / "skills" / "maplemate"
        for path in bundled.rglob("*"):
            if path.is_file():
                assert (skills / "maplemate" / path.relative_to(bundled)).read_bytes() == path.read_bytes()
        assert (skills / "maplemate" / "references" / "damage.md").is_file()
        backups = list((profile / ".agents" / "maplemate-skill-backups").glob("*.bak"))
        assert len(backups) == 2 and all((path / "SKILL.md").is_file() for path in backups)
        parsed = tomllib.loads(configuration.read_text(encoding="utf-8"))
        assert parsed["model"] == "keep-model" and parsed["mcp_servers"]["other"] == {"command": "other"}
        executable = str(Path(args.bundle).resolve() / "MapleMate-mcp.exe")
        expected = {"command": executable, "args": ["--data-dir", str(directory / "state"), "serve"]}
        original = {"preferences": {"theme": "dark"}, "mcpServers": {
            "other": {"command": "keep.exe", "args": ["keep"]}}}
        standard = Path(environment["APPDATA"]) / "Claude" / "claude_desktop_config.json"
        standard.parent.mkdir(parents=True)
        standard.write_text(json.dumps(original), encoding="utf-8")
        claude_command = [executable, "--data-dir", str(directory / "state"), "connect", "claude-desktop"]
        result = subprocess.run(claude_command, env=environment, capture_output=True, timeout=20,
                                creationflags=subprocess.CREATE_NO_WINDOW)
        assert result.returncode == 0 and not result.stderr, "Standard Claude registration failed"
        saved = json.loads(standard.read_text(encoding="utf-8"))
        assert saved["mcpServers"]["maple-mcp"] == expected
        assert saved["mcpServers"]["other"] == original["mcpServers"]["other"]
        assert saved["preferences"] == original["preferences"]

        # Reproduce the report: the legacy file says registered, while Claude
        # actually reads its MSIX container and has no MapleMate entry there.
        legacy_bytes = standard.read_bytes()
        packaged = (Path(environment["LOCALAPPDATA"]) / "Packages" / "Claude_pzs8sxrjxfjjc" /
                    "LocalCache" / "Roaming" / "Claude" / "claude_desktop_config.json")
        packaged.parent.mkdir(parents=True)
        packaged.write_text(json.dumps(original), encoding="utf-8")
        for _ in range(2):
            result = subprocess.run(claude_command, env=environment, capture_output=True, timeout=20,
                                    creationflags=subprocess.CREATE_NO_WINDOW)
            assert result.returncode == 0 and not result.stderr, "MSIX Claude registration failed"
        saved = json.loads(packaged.read_text(encoding="utf-8"))
        assert saved["mcpServers"].get("maple-mcp") == expected, "MCP not registered in Claude MSIX config"
        assert saved["preferences"] == original["preferences"]
        assert saved["mcpServers"]["other"] == original["mcpServers"]["other"]
        assert standard.read_bytes() == legacy_bytes
        backups = list(packaged.parent.glob("*.bak"))
        assert len(backups) == 1 and json.loads(backups[0].read_text(encoding="utf-8")) == original

        print(json.dumps({"bundled_single_skill_installed": True, "two_old_skills_migrated": True,
                          "references_match_bundle": True, "backup_outside_discovery": True,
                          "repeated_install_idempotent": True, "unrelated_config_preserved": True,
                          "claude_standard_registration": True, "claude_msix_registration": True,
                          "claude_msix_existing_settings_preserved": True}))
    finally:
        assert directory.resolve().parent == TEST_ROOT.resolve()
        if directory.exists():
            shutil.rmtree(directory)


if __name__ == "__main__":
    main()
