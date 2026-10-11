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
        print(json.dumps({"bundled_single_skill_installed": True, "two_old_skills_migrated": True,
                          "references_match_bundle": True, "backup_outside_discovery": True,
                          "repeated_install_idempotent": True, "unrelated_config_preserved": True}))
    finally:
        assert directory.resolve().parent == TEST_ROOT.resolve()
        if directory.exists():
            shutil.rmtree(directory)


if __name__ == "__main__":
    main()
