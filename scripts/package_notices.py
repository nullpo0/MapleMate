"""Collect build dependencies' original notices and provide LGPL rebuilding materials."""
from collections import deque
import hashlib
from importlib.metadata import distribution
import json
from pathlib import Path
import platform
import shutil
import sys
import tomllib
import zipfile

from verify_release import check_public_path

ROOT_FILES = ("LICENSE", "THIRD_PARTY_NOTICES.md", "README.md", "CHANGELOG.md",
              "pyproject.toml", "MapleMate.spec", ".gitignore", ".gitattributes")
SOURCE_DIRS = ("src", "skills", "scripts", "tests", "docs", "examples", ".github")


def dependencies(root):
    # packaging is a PyInstaller build dependency, not an application dependency.
    from packaging.requirements import Requirement
    from packaging.utils import canonicalize_name
    project = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    pending = deque(Requirement(text) for text in
                    project["dependencies"] + project["optional-dependencies"]["build"] + ["setuptools"])
    seen, result = {}, {}
    while pending:
        requirement = pending.popleft()
        if requirement.marker and not requirement.marker.evaluate({"extra": ""}):
            continue
        name = canonicalize_name(requirement.name)
        extras = set(requirement.extras)
        if name in seen and extras <= seen[name]:
            continue
        seen.setdefault(name, set()).update(extras)
        installed = result.setdefault(name, distribution(requirement.name))
        for text in installed.requires or []:
            child = Requirement(text)
            if child.marker and not any(child.marker.evaluate({"extra": extra})
                                        for extra in ("", *seen[name])):
                continue
            child.marker = None  # The parent's extra/platform condition was evaluated above.
            pending.append(child)
    return result


def legal_files(installed):
    return [path for path in installed.files or [] if
            "__pycache__" not in path.parts and path.suffix not in (".py", ".pyi", ".pyc") and
            any(part.lower().startswith(("license", "copying", "notice", "authors"))
                for part in path.parts)]


def source_files(root):
    for name in ROOT_FILES:
        yield root / name
    for name in SOURCE_DIRS:
        for path in (root / name).rglob("*"):
            if not path.is_file():
                continue
            relative = path.relative_to(root)
            if ("__pycache__" in relative.parts or path.suffix == ".pyc" or
                    any(part.endswith(".egg-info") for part in relative.parts)):
                continue
            check_public_path(relative)
            yield path


def app_source(root, target):
    with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in source_files(root):
            archive.write(path, (Path("MapleMate") / path.relative_to(root)).as_posix())


def library_source(installed, target):
    sources = [path for path in installed.files or [] if
               path.parts[0] == "pystray" and path.suffix == ".py"]
    if not sources:
        raise ValueError("pystray Python source is unavailable")
    texts = legal_files(installed)
    if not {"COPYING", "COPYING.LGPL"} <= {path.name for path in texts}:
        raise ValueError("pystray GPL/LGPL texts are missing")
    with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sources + texts:
            archive.write(installed.locate_file(path), path.as_posix())
        for path in installed.files or []:
            if path.name == "METADATA":
                archive.write(installed.locate_file(path), path.as_posix())


def add_file(source, target, bundle):
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, target)
    return {"path": target.relative_to(bundle).as_posix(),
            "sha256": hashlib.sha256(target.read_bytes()).hexdigest()}


def package_notices(root, bundle, version):
    licenses, sources = bundle / "licenses", bundle / "source"
    licenses.mkdir(exist_ok=True)
    sources.mkdir(exist_ok=True)
    records, files = [], []
    installed = dependencies(root)
    for name, package in sorted(installed.items()):
        texts = legal_files(package)
        if not texts:
            raise ValueError(f"No original license text found for {name}")
        paths = []
        for relative in texts:
            check_public_path(relative)
            source = package.locate_file(relative)
            if not source.is_file():
                raise ValueError(f"Missing original notice for {name}")
            record = add_file(source, licenses / name / relative, bundle)
            files.append(record)
            paths.append(record["path"])
        records.append({"name": name, "version": package.version,
                        "license": package.metadata.get("License-Expression") or
                                   next(iter((package.metadata.get("License") or "").splitlines()), ""),
                        "license_files": paths})

    python_license = Path(sys.base_prefix) / "LICENSE.txt"
    files.append(add_file(python_license, licenses / "python" / "LICENSE.txt", bundle))
    for name in ("tcl", "tk"):
        candidates = list((Path(sys.base_prefix) / "tcl").glob(f"{name}*/license.terms"))
        fallback = bundle / "_internal" / f"_{name}_data" / "license.terms"
        original = next((path for path in candidates if path.is_file()), fallback)
        if not original.is_file():
            # Windows Python may consolidate Tcl/Tk notices into its LICENSE.txt.
            consolidated = python_license.read_text(encoding="utf-8")
            if "Sun Microsystems" not in consolidated or "Scriptics Corporation" not in consolidated:
                raise ValueError(f"Original {name} notice is unavailable")
            original = python_license
        files.append(add_file(original, licenses / name / "license.terms", bundle))

    (sources / "requirements-build.txt").write_text(
        "".join(f"{name}=={package.version}\n" for name, package in sorted(installed.items())),
        encoding="utf-8")
    app_archive = sources / f"MapleMate-{version}-source.zip"
    app_source(root, app_archive)
    package = installed["pystray"]
    library_archive = sources / f"pystray-{package.version}.zip"
    library_source(package, library_archive)
    for path in (sources / "requirements-build.txt", app_archive, library_archive):
        files.append({"path": path.relative_to(bundle).as_posix(),
                      "sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
    manifest = {"schema_version": 1, "maplemate_version": version,
                "python_version": platform.python_version(), "platform": platform.platform(),
                "packages": records, "files": files}
    (licenses / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
                                           encoding="utf-8")


def check_notices(bundle, version):
    manifest = json.loads((bundle / "licenses" / "manifest.json").read_text(encoding="utf-8"))
    if manifest.get("maplemate_version") != version:
        raise ValueError("License manifest version differs")
    records = manifest.get("files", [])
    paths = {record["path"] for record in records}
    required = {"licenses/python/LICENSE.txt", "licenses/tcl/license.terms", "licenses/tk/license.terms",
                "source/requirements-build.txt", f"source/MapleMate-{version}-source.zip"}
    pystray = next((package for package in manifest.get("packages", []) if package["name"] == "pystray"), None)
    if pystray is None or not pystray.get("license_files"):
        raise ValueError("pystray license manifest is missing")
    required.add(f'source/pystray-{pystray["version"]}.zip')
    if not required <= paths:
        raise ValueError("License or rebuilding material is missing")
    for record in records:
        relative = Path(record["path"])
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError("Unsafe license manifest path")
        check_public_path(relative)
        path = bundle / relative
        if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != record["sha256"]:
            raise ValueError(f"License or source material changed: {relative}")
    with zipfile.ZipFile(bundle / f'source/pystray-{pystray["version"]}.zip') as archive:
        if not {"COPYING", "COPYING.LGPL"} <= {Path(name).name for name in archive.namelist()}:
            raise ValueError("pystray GPL/LGPL archive texts are missing")
    with zipfile.ZipFile(bundle / f"source/MapleMate-{version}-source.zip") as archive:
        if "MapleMate/LICENSE" not in archive.namelist():
            raise ValueError("App source archive is missing MIT")
        for name in archive.namelist():
            path = Path(name)
            if path.is_absolute() or ".." in path.parts:
                raise ValueError("Unsafe source archive path")
            check_public_path(path)
