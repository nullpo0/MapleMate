"""Register local stdio MCP without replacing unrelated client settings."""
import hashlib
from importlib.resources import files
import json
import os
from pathlib import Path
import re
import tomllib
import uuid
from .config import Config, managed_data_directory
from .launch import command_for

SERVER_NAME = "maple-mcp"


class ClientSetupError(RuntimeError):
    pass


def server_settings(config: Config) -> dict:
    command = command_for("serve", config.data_dir)
    return {"command": command[0], "args": command[1:]}


def codex_snippet(config: Config) -> str:
    return "[mcp_servers.maple-mcp]\n" + "\n".join(f"{key} = {json.dumps(value, ensure_ascii=False)}" for key, value in server_settings(config).items()) + "\n"


def _managed(value: dict | None, config: Config) -> bool:
    return managed_data_directory(value) == config.data_dir.resolve()


def _read(path: Path) -> bytes:
    try:
        return path.read_bytes() if path.exists() else b""
    except OSError:
        raise ClientSetupError("AI 클라이언트 설정 파일을 읽지 못했습니다.") from None


def _commit(path: Path, original: bytes, updated: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(".maplemate-" + uuid.uuid4().hex + ".tmp")
    try:
        if _read(path) != original:
            raise ClientSetupError("다른 곳에서 설정이 변경되었습니다. 다시 연결해 주세요.")
        if original:
            backup = path.with_name(path.name + ".maplemate-" + uuid.uuid4().hex + ".bak")
            with backup.open("xb") as stream:
                stream.write(original)
        with temporary.open("xb") as stream:
            stream.write(updated)
            stream.flush()
            os.fsync(stream.fileno())
        if _read(path) != original:
            raise ClientSetupError("다른 곳에서 설정이 변경되었습니다. 다시 연결해 주세요.")
        temporary.replace(path)
    except OSError:
        raise ClientSetupError("설정 파일을 저장하지 못했습니다. 폴더 권한을 확인해 주세요.") from None
    finally:
        temporary.unlink(missing_ok=True)


class CodexSetup:
    def __init__(self, config: Config, directory: Path | None = None):
        self.config = config
        self.path = (directory or Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex")) / "config.toml"

    def _parsed(self) -> tuple[bytes, dict]:
        original = _read(self.path)
        try:
            parsed = tomllib.loads(original.decode("utf-8-sig"))
            if not isinstance(parsed.get("mcp_servers", {}), dict):
                raise ValueError("Invalid MCP settings")
            return original, parsed
        except (ValueError, UnicodeError):
            raise ClientSetupError("Codex 설정 형식이 잘못되어 변경하지 않았습니다.") from None

    def status(self) -> str:
        _, parsed = self._parsed()
        value = parsed.get("mcp_servers", {}).get(SERVER_NAME)
        if value is None:
            return "not_registered"
        expected = server_settings(self.config)
        if not isinstance(value, dict) or any(value.get(key) != setting for key, setting in expected.items()):
            return "different_settings"
        return "registered" if value.get("enabled", True) else "disabled"

    def install(self) -> str:
        original, parsed = self._parsed()
        entries = parsed.get("mcp_servers", {})
        value = entries.get(SERVER_NAME)
        if value is not None and not _managed(value, self.config):
            raise ClientSetupError("같은 이름의 다른 MCP 설정이 있습니다. 기존 설정은 유지했습니다.")
        old = entries.get("maplemcp")
        text = original.decode("utf-8-sig")
        if value is None and _managed(old, self.config):
            # Rename this app's entry and its policy subtables, preserving comments.
            text = re.sub(r'(?m)^(\[\[?mcp_servers\.)(?:maplemcp|"maplemcp"|\x27maplemcp\x27)(?=[.\]])', r'\1maple-mcp', text)
        elif value is None:
            text += ("\n" if text.endswith("\n") else "\n\n") + codex_snippet(self.config)
        elif self.status() in ("registered", "disabled"):
            return self.status()
        # Replace the complete TOML values, including multiline args, while
        # preserving every other field, comment and tool policy subtable.
        if value is not None or _managed(old, self.config):
            section = re.search(r'(?m)^\[mcp_servers\.(?:maple-mcp|"maple-mcp"|\x27maple-mcp\x27)\][^\n]*(?:\n|$)', text)
            if section is None:
                raise ClientSetupError("MCP 등록 항목을 안전하게 갱신할 수 없어 기존 설정을 유지했습니다.")
            end = re.search(r'(?m)^\[', text[section.end():])
            stop = section.end() + end.start() if end else len(text)
            body = text[section.end():stop]
            # Locate top-level assignments. TOML arrays may span multiple lines;
            # test prefixes with the TOML parser to find each value's end.
            for key, setting in server_settings(self.config).items():
                match = re.search(rf'(?m)^\s*{key}\s*=', body)
                if match is None:
                    body += f'\n{key} = {json.dumps(setting, ensure_ascii=False)}\n'
                    continue
                tail = body[match.start():]
                lines = tail.splitlines(keepends=True)
                consumed = 0
                for line in lines:
                    consumed += len(line)
                    try:
                        tomllib.loads(tail[:consumed])
                    except ValueError:
                        continue
                    break
                body = body[:match.start()] + f'{key} = {json.dumps(setting, ensure_ascii=False)}\n' + tail[consumed:]
            text = text[:section.end()] + body + text[stop:]
        try:
            checked = tomllib.loads(text)
            if any(checked["mcp_servers"][SERVER_NAME].get(key) != setting for key, setting in server_settings(self.config).items()):
                raise ValueError("Settings did not migrate")
        except (ValueError, KeyError):
            raise ClientSetupError("Codex 설정을 안전하게 갱신하지 못했습니다. 기존 설정은 변경하지 않았습니다.") from None
        _commit(self.path, original, text.encode("utf-8"))
        return self.status()


class ClaudeDesktopSetup:
    def __init__(self, config: Config, directory: Path | None = None):
        self.config = config
        base = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming")) / "Claude"
        self.path = (directory or base) / "claude_desktop_config.json"

    def _parsed(self) -> tuple[bytes, dict]:
        original = _read(self.path)
        try:
            parsed = json.loads(original.decode("utf-8-sig")) if original else {}
            if not isinstance(parsed, dict) or not isinstance(parsed.get("mcpServers", {}), dict):
                raise ValueError("Invalid MCP settings")
            return original, parsed
        except (ValueError, UnicodeError):
            raise ClientSetupError("Claude Desktop 설정 형식이 잘못되어 변경하지 않았습니다.") from None

    def status(self) -> str:
        _, parsed = self._parsed()
        value = parsed.get("mcpServers", {}).get(SERVER_NAME)
        if value is None:
            return "not_registered"
        return "registered" if isinstance(value, dict) and all(value.get(key) == setting for key, setting in server_settings(self.config).items()) else "different_settings"

    def install(self) -> str:
        original, parsed = self._parsed()
        entries = parsed.setdefault("mcpServers", {})
        value = entries.get(SERVER_NAME)
        if value is not None and not _managed(value, self.config):
            raise ClientSetupError("Claude Desktop에 같은 이름의 다른 MCP 설정이 있습니다. 기존 설정을 유지했습니다.")
        if self.status() == "registered":
            return "registered"
        if _managed(entries.get("maplemcp"), self.config):
            del entries["maplemcp"]
        entries[SERVER_NAME] = {**(value or {}), **server_settings(self.config)}
        _commit(self.path, original, (json.dumps(parsed, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
        return "registered"


def install_maplemate_skill(destination: Path | None = None) -> str:
    destination = destination or Path.home() / ".agents" / "skills" / "maplemate"
    legacy = destination.parent / "maple-beginner-guide"
    previous = _owned_legacy_skill(legacy)
    names = ("SKILL.md", "agents/openai.yaml", "references/overview.md", "references/upgrade.md",
             "references/damage.md", "references/progression.md", "references/beginner.md",
             "references/intermediate.md", "references/advanced.md", "references/playstyle.md",
             "references/community.md", "references/state.md", "references/equipment.md", "references/knowledge.md")
    result = _install_skill("maplemate", destination, names, [])
    if previous is not None:
        if _owned_legacy_skill(legacy) != previous:
            raise ClientSetupError("기존 guide가 변경되어 백업 이동하지 않았습니다. 다시 연결해 주세요.")
        backup_parent = _skill_backup_parent(legacy)
        backup = backup_parent / (legacy.name + ".maplemate-" + uuid.uuid4().hex + ".bak")
        # Verify both absolute paths before moving this specific installed skill tree.
        if legacy.resolve().parent != destination.parent.resolve() or backup.resolve().parent != backup_parent.resolve():
            raise ClientSetupError("기존 guide의 백업 경로를 확인하지 못했습니다.")
        backup_parent.mkdir(parents=True, exist_ok=True)
        legacy.rename(backup)
    return result


def _skill_backup_parent(destination: Path) -> Path:
    return destination.parent.parent / "maplemate-skill-backups" if destination.parent.name == "skills" else destination.parent


def _owned_legacy_skill(directory: Path) -> dict | None:
    if not directory.exists():
        return None
    if directory.is_symlink() or any(path.is_symlink() for path in directory.rglob("*")):
        raise ClientSetupError("기존 guide가 링크 경로여서 유지했습니다.")
    previous = {path.relative_to(directory).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
                for path in directory.rglob("*") if path.is_file() and path != directory / ".maplemate-managed.json"}
    known = json.loads(files("maplemate").joinpath("previous_skill_hashes.json").read_text(encoding="utf-8"))
    manifest = directory / ".maplemate-managed.json"
    if manifest.is_file():
        try:
            known.append(json.loads(manifest.read_text(encoding="utf-8")))
        except (ValueError, UnicodeError):
            raise ClientSetupError("기존 guide의 관리 기록을 확인하지 못해 유지했습니다.") from None
    known = [{name.replace("\\", "/"): value for name, value in version.items() if value is not None}
             for version in known if isinstance(version, dict)]
    if "SKILL.md" not in previous or previous not in known:
        raise ClientSetupError("기존 maple-beginner-guide에 사용자 변경이 있거나 관리 이력이 없어 유지했습니다.")
    return previous


def _install_skill(source_name: str, destination: Path, names: tuple[str, ...], known: list[dict]) -> str:
    source = files("maplemate.skills").joinpath(source_name)
    contents = {name: source.joinpath(name).read_bytes() for name in names}
    hashes = {name: hashlib.sha256(value).hexdigest() for name, value in contents.items()}
    if destination.exists():
        previous = {name: hashlib.sha256((destination / name).read_bytes()).hexdigest() if (destination / name).is_file() else None for name in names}
        if previous == hashes:
            return "already_installed"
        manifest = destination / ".maplemate-managed.json"
        if manifest.exists():
            known.append(json.loads(manifest.read_text(encoding="utf-8")))
        known = [{name.replace("\\", "/"): value for name, value in version.items()} for version in known]
        if not any(all(previous[name] == version.get(name) for name in names) for version in known):
            raise ClientSetupError("skill에 기존 변경이 있어 유지했습니다. MCP의 get_maple_advice_guide에서 새 지침을 사용할 수 있습니다.")
        # A sibling SKILL.md under .agents/skills is discovered as another skill.
        # Keep upgrade backups outside that collection without deleting them.
        backup_parent = _skill_backup_parent(destination)
        backup = backup_parent / (destination.name + ".maplemate-" + uuid.uuid4().hex + ".bak")
        for name in names:
            target = backup / name
            if (destination / name).is_file():
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes((destination / name).read_bytes())
    for name, value in contents.items():
        target = destination / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(value)
    (destination / ".maplemate-managed.json").write_text(json.dumps(hashes), encoding="utf-8")
    return "installed"
