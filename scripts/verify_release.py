"""Check release versions, bundled guidance, ZIP integrity, and private-data exclusions."""
import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import tomllib
import zipfile

ROOT = Path(__file__).resolve().parents[1]
PRIVATE_PARTS = {".git", ".maplemcp", ".maplemate", ".cache", ".venv", "__pycache__"}


def check_public_path(path):
    parts = {part.casefold() for part in path.parts}
    name = path.name.casefold()
    if (parts & PRIVATE_PARTS or name.startswith(".env") or
            name.endswith((".dpapi", ".sqlite3", ".sqlite3-shm", ".sqlite3-wal", ".lnk")) or
            name == "tray-runtime.json"):
        raise ValueError(f"Private or generated file in release: {path}")


def check_source():
    version = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
    module = (ROOT / "src" / "maplemate" / "__init__.py").read_text(encoding="utf-8")
    if f'__version__ = "{version}"' not in module:
        raise ValueError("Package and project versions disagree")
    if f"MapleMate-{version}-windows-x64.zip" not in (ROOT / "README.md").read_text(encoding="utf-8"):
        raise ValueError("README release version disagrees")
    if f"## {version} — " not in (ROOT / "CHANGELOG.md").read_text(encoding="utf-8"):
        raise ValueError("Release is missing from CHANGELOG")
    skill = ROOT / "skills" / "maplemate"
    if list((ROOT / "skills").rglob("SKILL.md")) != [skill / "SKILL.md"]:
        raise ValueError("Expected exactly one maplemate skill")
    for document in skill.rglob("*.md"):
        for target in re.findall(r"\]\(([^)]+)\)", document.read_text(encoding="utf-8")):
            if "://" in target or target.startswith("#"):
                continue
            linked = (document.parent / target.split("#", 1)[0]).resolve()
            if not linked.is_relative_to(skill.resolve()) or not linked.is_file():
                raise ValueError(f"Broken skill reference in {document.name}: {target}")
    return version


def check_bundle(bundle):
    for name in ("MapleMate.exe", "MapleMate-mcp.exe", "README.md", "CHANGELOG.md"):
        if not (bundle / name).is_file():
            raise ValueError(f"Missing bundle file: {name}")
    if (bundle / "README.md").read_bytes() != (ROOT / "README.md").read_bytes():
        raise ValueError("Bundled README differs from source")
    if (bundle / "CHANGELOG.md").read_bytes() != (ROOT / "CHANGELOG.md").read_bytes():
        raise ValueError("Bundled CHANGELOG differs from source")
    packaged = bundle / "_internal" / "maplemate" / "skills" / "maplemate"
    if list(bundle.rglob("SKILL.md")) != [packaged / "SKILL.md"]:
        raise ValueError("Bundle must contain exactly one maplemate skill")
    source = ROOT / "skills" / "maplemate"
    expected = {p.relative_to(source): p.read_bytes() for p in source.rglob("*") if p.is_file()}
    actual = {p.relative_to(packaged): p.read_bytes() for p in packaged.rglob("*") if p.is_file()}
    if expected != actual:
        raise ValueError("Bundled skill files differ from source")
    count = 0
    for path in bundle.rglob("*"):
        if path.is_file():
            check_public_path(path.relative_to(bundle))
            count += 1
    return count


def check_archive(archive, version):
    if archive.name != f"MapleMate-{version}-windows-x64.zip":
        raise ValueError("Archive version disagrees")
    with zipfile.ZipFile(archive) as package:
        if package.testzip() is not None:
            raise ValueError("ZIP integrity check failed")
        for member in package.infolist():
            path = PurePosixPath(member.filename)
            if path.is_absolute() or ".." in path.parts or not path.parts or path.parts[0] != "MapleMate":
                raise ValueError("Unsafe or unexpected ZIP path")
            check_public_path(path)
    expected = f"{hashlib.sha256(archive.read_bytes()).hexdigest()}  {archive.name}\n"
    if archive.with_suffix(".zip.sha256").read_text(encoding="ascii") != expected:
        raise ValueError("Archive checksum does not match")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--bundle", type=Path)
    parser.add_argument("--archive", type=Path)
    args = parser.parse_args()
    version = check_source()
    count = check_bundle(args.bundle.resolve()) if args.bundle else None
    if args.archive:
        check_archive(args.archive.resolve(), version)
    print(json.dumps({"version": version, "source_verified": True, "bundle_files": count,
                      "archive_verified": bool(args.archive)}))


if __name__ == "__main__":
    main()
