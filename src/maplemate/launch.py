"""Commands for source installs and the two executable release bundle."""
import os
from pathlib import Path
import subprocess
import sys


def command_for(action: str, data_dir: Path | None = None) -> list[str]:
    frozen = getattr(sys, "frozen", False)
    executable = Path(sys.executable)
    if frozen:
        executable = executable.with_name("MapleMate.exe" if action == "tray" else "MapleMate-mcp.exe")
        command = [str(executable)]
    else:
        if executable.name.lower() in ("python.exe", "pythonw.exe"):
            executable = executable.with_name("pythonw.exe" if action == "tray" else "python.exe")
        command = [str(executable), "-m", "maplemate"]
    if data_dir is not None:
        command += ["--data-dir", str(data_dir)]
    return command + [action]


def start_tray(data_dir: Path) -> None:
    subprocess.Popen(command_for("tray", data_dir), stdin=subprocess.DEVNULL,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)


def needs_detach() -> bool:
    if os.name != "nt":
        return False
    expected = "maplemate.exe" if getattr(sys, "frozen", False) else "pythonw.exe"
    return Path(sys.executable).name.lower() != expected
