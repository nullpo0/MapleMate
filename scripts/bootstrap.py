"""Keep bootstrap temporary files inside this checkout on restricted Windows hosts."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile

root = Path(__file__).resolve().parents[1]
temporary = root / ".cache" / "tmp"
temporary.mkdir(parents=True, exist_ok=True)
tempfile.tempdir = str(temporary)
environment = os.environ.copy()
environment.update(TMP=str(temporary), TEMP=str(temporary), TMPDIR=str(temporary))
python = root / ".venv" / "Scripts" / "python.exe"
if not python.exists():
    subprocess.run([sys.executable, "-m", "venv", "--without-pip", str(root / ".venv")], check=True, env=environment)
subprocess.run([str(python), "-m", "ensurepip", "--upgrade"], check=True, env=environment)
subprocess.run([str(python), "-m", "pip", "--isolated", "install", "--index-url", "https://pypi.org/simple", "--cache-dir", str(root / ".cache" / "pip"), "-e", str(root)], check=True, env=environment)
print(r"Ready. Start with: .\.venv\Scripts\python.exe -m maplemate tray")
