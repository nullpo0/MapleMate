from pathlib import Path
import os
import shutil
import sys
import tempfile
import tkinter as tk
from PyInstaller.utils.hooks import collect_all, collect_data_files

root = Path(SPECPATH)
# The same venv Tcl lookup repair used by the source tray is needed while
# PyInstaller's isolated hook queries Tcl, before it can collect GUI resources.
for variable, folder in (("TCL_LIBRARY", f"tcl{tk.TclVersion}"), ("TK_LIBRARY", f"tk{tk.TkVersion}")):
    library = Path(sys.base_prefix) / "tcl" / folder
    if not os.environ.get(variable) and library.is_dir():
        os.environ[variable] = str(library)
capture_data, capture_binaries, capture_imports = collect_all("windows_capture")
# Editable setuptools maps `maplemate.skills` to a separate directory, which
# modulegraph cannot resolve. Stage the same package layout as a wheel install.
staging_parent = root / "build" / "package-source"
staging_parent.mkdir(parents=True, exist_ok=True)
staging = Path(tempfile.mkdtemp(dir=staging_parent))
shutil.copytree(root / "src" / "maplemate", staging / "maplemate",
                ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
shutil.copytree(root / "skills", staging / "maplemate" / "skills",
                ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
analysis = Analysis(
    [str(root / "scripts" / "frozen_entry.py")],
    pathex=[str(staging)],
    binaries=capture_binaries,
    datas=(collect_data_files("maplemate") +
           [(str(root / "src" / "maplemate" / "maple_mcp" / "nexon_catalog.json"), "maplemate/maple_mcp")] +
           [(str(root / "skills" / "maplemate"), "maplemate/skills/maplemate")] + capture_data),
    hiddenimports=["maplemate.skills", *capture_imports],
    hookspath=[], runtime_hooks=[], excludes=[],
)
archive = PYZ(analysis.pure)
options = dict(exclude_binaries=True, debug=False, strip=False, upx=False,
               icon=str(root / "src" / "maplemate" / "assets" / "maplemate.ico"))
tray = EXE(archive, analysis.scripts, [], name="MapleMate", console=False, **options)
mcp = EXE(archive, analysis.scripts, [], name="MapleMate-mcp", console=True,
          hide_console="hide-early", **options)
bundle = COLLECT(tray, mcp, analysis.binaries, analysis.datas, strip=False, upx=False, name="MapleMate")
