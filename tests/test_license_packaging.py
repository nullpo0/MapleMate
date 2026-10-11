import hashlib
import json
from pathlib import Path
import shutil
import sys
import unittest
import uuid
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from package_notices import app_source, check_notices, ROOT_FILES


class LicensePackagingTests(unittest.TestCase):
    def setUp(self):
        self.parent = Path(__file__).resolve().parents[1] / ".cache" / "license-tests"
        self.root = self.parent / uuid.uuid4().hex
        self.root.mkdir(parents=True)

    def tearDown(self):
        self.assertEqual(self.root.resolve().parent, self.parent.resolve())
        shutil.rmtree(self.root)

    def test_source_snapshot_omits_private_roots_and_rejects_keys_inside_source(self):
        source = self.root / "checkout"
        source.mkdir()
        for name in ROOT_FILES:
            (source / name).write_text("synthetic public source", encoding="utf-8")
        (source / ".maplemcp").mkdir()
        (source / ".maplemcp" / "nexon-key.dpapi").write_bytes(b"synthetic private key")
        (source / "src").mkdir()
        (source / "src" / "app.py").write_text("pass\n", encoding="utf-8")
        target = self.root / "source.zip"
        app_source(source, target)
        with zipfile.ZipFile(target) as archive:
            self.assertIn("MapleMate/src/app.py", archive.namelist())
            self.assertFalse(any(".maplemcp" in name for name in archive.namelist()))
        (source / "src" / "accidental-key.dpapi").write_bytes(b"synthetic private key")
        with self.assertRaisesRegex(ValueError, "Private"):
            app_source(source, target)

    def test_missing_or_modified_license_and_rebuild_materials_fail_validation(self):
        files = {}
        for name in ("licenses/python/LICENSE.txt", "licenses/tcl/license.terms",
                     "licenses/tk/license.terms", "licenses/pystray/COPYING",
                     "licenses/pystray/COPYING.LGPL", "source/requirements-build.txt"):
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("synthetic license/build fixture", encoding="utf-8")
            files[name] = path
        for name, entries in (("source/MapleMate-1.0.1-source.zip", ["MapleMate/LICENSE"]),
                              ("source/pystray-0.19.5.zip", ["COPYING", "COPYING.LGPL", "pystray/__init__.py"])):
            path = self.root / name
            with zipfile.ZipFile(path, "w") as archive:
                for entry in entries:
                    archive.writestr(entry, "synthetic")
            files[name] = path
        manifest = {"maplemate_version": "1.0.1",
                    "packages": [{"name": "pystray", "version": "0.19.5",
                                  "license_files": ["licenses/pystray/COPYING", "licenses/pystray/COPYING.LGPL"]}],
                    "files": [{"path": name, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
                              for name, path in files.items()]}
        (self.root / "licenses/manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        check_notices(self.root, "1.0.1")
        with self.assertRaisesRegex(ValueError, "version"):
            check_notices(self.root, "different-version")
        path = self.root / "licenses/pystray/COPYING.LGPL"
        original = path.read_bytes()
        path.write_bytes(b"synthetic corruption")
        with self.assertRaisesRegex(ValueError, "changed"):
            check_notices(self.root, "1.0.1")
        path.write_bytes(original)
        path.unlink()
        with self.assertRaisesRegex(ValueError, "changed"):
            check_notices(self.root, "1.0.1")


if __name__ == "__main__":
    unittest.main()
