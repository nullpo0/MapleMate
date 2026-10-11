import argparse
import getpass
import json
import sys
from .config import Config


def main() -> None:
    parser = argparse.ArgumentParser(description="MapleMate: Windows tray companion, Maple-mcp and Maple skills")
    parser.add_argument("--data-dir", help="Shared local settings/state directory")
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("tray", help="Run in the Windows notification area (default)")
    sub.add_parser("serve", help="Run the bundled Maple-mcp over stdio for an AI client")
    sub.add_parser("status", help="Show status without keys or connection credentials")
    sub.add_parser("stop", help="Stop this data directory's tray process")
    sub.add_parser("capture-worker", help=argparse.SUPPRESS)
    configure = sub.add_parser("configure", help="Private terminal fallback for Nexon API key setup")
    configure.add_argument("--character")
    connect = sub.add_parser("connect", help="Register the bundled MCP in an AI client")
    connect.add_argument("client", choices=["codex", "claude-desktop"])
    args = parser.parse_args()
    if args.command == "capture-worker":
        from .native_capture import main as capture_once
        try:
            capture_once()
        except Exception:
            raise SystemExit(1) from None
        return
    config = Config.load(args.data_dir)
    try:
        if args.command in (None, "tray"):
            from .launch import needs_detach, start_tray
            if needs_detach():
                start_tray(config.data_dir)
                return
            from .tray import run
            run(config)
        elif args.command == "serve":
            from .maple_mcp.server import serve
            serve(config)
        elif args.command == "status":
            from .maple_mcp.service import MapleService
            print(json.dumps(MapleService(config).status(), ensure_ascii=False, indent=2))
        elif args.command == "stop":
            from .runtime import RuntimeClient
            print(json.dumps(RuntimeClient(config).call("shutdown")))
        elif args.command == "configure":
            from .setup import configure_key
            count = configure_key(config, getpass.getpass("Nexon API Key (hidden): "), args.character)
            print(f"API key protected. {count} character identities available.")
        elif args.command == "connect":
            from .clients import ClientSetupError, ClaudeDesktopSetup, CodexSetup, install_maplemate_skill
            status = (CodexSetup(config) if args.client == "codex" else ClaudeDesktopSetup(config)).install()
            print(f"{args.client}: {status}")
            if args.client == "codex":
                try:
                    print("MapleMate skill:", install_maplemate_skill())
                except ClientSetupError as error:
                    print(str(error), file=sys.stderr)
    except KeyboardInterrupt:
        pass
    except Exception:
        # Never let HTTP requests, private keys, or runtime auth data enter logs.
        print("MapleMate could not complete the action. Check local settings and installation.", file=sys.stderr)
        raise SystemExit(1) from None
