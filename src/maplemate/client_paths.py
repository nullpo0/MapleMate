"""Locate the Claude configuration shared by registration and state discovery."""
import os
from pathlib import Path


class ClaudeConfigPathError(RuntimeError):
    pass


def claude_config_path() -> Path:
    roaming = Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming")
    local = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
    packages = local / "Packages"
    try:
        # MSIX redirects Claude's userData into its per-user package container.
        # Discover the family name rather than hardcoding the publisher ID.
        containers = sorted(path for path in packages.iterdir()
                            if path.is_dir() and path.name.casefold().startswith("claude_"))
    except FileNotFoundError:
        containers = []
    except OSError:
        raise ClaudeConfigPathError("Claude 설치 경로를 읽지 못했습니다. 폴더 권한을 확인해 주세요.") from None
    if len(containers) > 1:
        raise ClaudeConfigPathError("Claude 패키지 경로가 여러 개라 자동 등록하지 않았습니다. 사용할 설정 파일을 확인해 주세요.")
    if containers:
        # The container can exist before Claude creates its first config file.
        directory = containers[0] / "LocalCache" / "Roaming" / "Claude"
    else:
        directory = roaming / "Claude"
    return directory / "claude_desktop_config.json"
