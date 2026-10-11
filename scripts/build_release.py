"""Build a self-contained Windows x64 ZIP; never package local user state."""
import hashlib
import argparse
import os
from pathlib import Path
import shutil
import subprocess
import sys
from verify_release import check_archive, check_bundle, check_source

ROOT = Path(__file__).resolve().parents[1]


def main():
    if os.name != "nt":
        raise SystemExit("Build the Windows release on Windows.")
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, default=ROOT / "dist",
                        help="Use a separate output folder when a previous bundle is running.")
    output = parser.parse_args().output_dir.resolve()
    version = check_source()
    subprocess.run([sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean",
                    "--distpath", str(output), "--workpath", str(ROOT / "build"),
                    str(ROOT / "MapleMate.spec")], check=True, cwd=ROOT)
    bundle = output / "MapleMate"
    for executable in ("MapleMate.exe", "MapleMate-mcp.exe"):
        if not (bundle / executable).is_file():
            raise SystemExit("Incomplete release bundle.")
    shutil.copyfile(ROOT / "README.md", bundle / "README.md")
    shutil.copyfile(ROOT / "CHANGELOG.md", bundle / "CHANGELOG.md")
    check_bundle(bundle)
    archive = Path(shutil.make_archive(str(output / f"MapleMate-{version}-windows-x64"),
                                       "zip", root_dir=bundle.parent, base_dir=bundle.name))
    checksum = hashlib.sha256(archive.read_bytes()).hexdigest()
    archive.with_suffix(".zip.sha256").write_text(f"{checksum}  {archive.name}\n", encoding="ascii")
    check_archive(archive, version)
    print(archive)


if __name__ == "__main__":
    main()
