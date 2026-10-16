"""Shared local storage, independent of the AI client's working directory."""
from dataclasses import dataclass
import os
import sys
import json
import tomllib
from pathlib import Path
from .client_paths import ClaudeConfigPathError, claude_config_path


def linked_data_directory() -> Path | None:
    """Reuse this app's existing AI connection when a source user opens a ZIP."""
    codex = Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex") / "config.toml"
    try:
        claude = claude_config_path()
    except ClaudeConfigPathError:
        # Ambiguous/unreadable Claude profiles must not pick an unrelated state.
        return None
    candidates = set()
    for path, section, parse in ((codex, "mcp_servers", tomllib.loads), (claude, "mcpServers", json.loads)):
        try:
            entries = parse(path.read_text(encoding="utf-8-sig")).get(section, {})
            if not isinstance(entries, dict):
                continue
            for name in ("maple-mcp", "maplemcp"):
                directory = managed_data_directory(entries.get(name))
                if directory and directory.is_dir() and any((directory / marker).is_file() for marker in ("state.sqlite3", "nexon-key.dpapi")):
                    candidates.add(directory)
        except (OSError, ValueError, TypeError, AttributeError):
            continue
    # Conflicting AI configurations are not a license to choose one user's state.
    return next(iter(candidates)) if len(candidates) == 1 else None


def managed_data_directory(value: dict | None) -> Path | None:
    """Recognize only MapleMate's exact stdio command shape, not arbitrary args."""
    if not isinstance(value, dict) or not isinstance(value.get("command"), str):
        return None
    name, args = Path(value["command"]).name.lower(), value.get("args")
    if not isinstance(args, list) or not all(isinstance(arg, str) for arg in args):
        return None
    if name in ("python.exe", "pythonw.exe") and len(args) == 5 and args[:2] in (["-m", "maplemate"], ["-m", "maplemcp"]):
        args = args[2:]
    elif name != "maplemate-mcp.exe" or len(args) != 3:
        return None
    if args[0] != "--data-dir" or args[2] != "serve":
        return None
    try:
        directory = Path(args[1]).expanduser()
        return directory.resolve() if directory.is_absolute() else None
    except (OSError, ValueError):
        return None


@dataclass(frozen=True)
class Config:
    data_dir: Path

    @classmethod
    def load(cls, data_dir: str | Path | None = None) -> "Config":
        configured = data_dir or os.environ.get("MAPLEMATE_DATA_DIR") or os.environ.get("MAPLEMCP_DATA_DIR")
        if not configured:
            frozen = getattr(sys, "frozen", False)
            checkout = Path(sys.executable).resolve().parent if frozen else Path(__file__).resolve().parents[2]
            if (checkout / ".maplemcp").is_dir():
                legacy = checkout / ".maplemcp"
                configured = legacy
            elif (checkout / ".maplemate").is_dir() or (not frozen and (checkout / "pyproject.toml").is_file()):
                configured = checkout / ".maplemate"
            elif frozen and (linked := linked_data_directory()) is not None:
                configured = linked
            else:
                configured = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "MapleMate"
        directory = Path(configured).expanduser().resolve()
        directory.mkdir(parents=True, exist_ok=True)
        return cls(directory)
