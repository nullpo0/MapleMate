import hashlib
from pathlib import Path
import sys
import shutil
import unittest
import uuid
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from verify_release import check_archive, check_public_path, check_source


class ReleaseTests(unittest.TestCase):
    def test_release_metadata_and_skill_references(self):
        self.assertTrue(check_source())

    def test_private_data_is_rejected_even_inside_runtime_subdirectories(self):
        for value in ("_internal/.maplemcp/state.sqlite3", "nexon-key.dpapi",
                      ".env.production", "_internal/tray-runtime.json", "MapleMate.lnk"):
            with self.subTest(path=value), self.assertRaises(ValueError):
                check_public_path(Path(value))
        check_public_path(Path("_internal/maplemate/maple_mcp/nexon_catalog.json"))

    def test_archive_traversal_and_wrong_checksum_are_rejected(self):
        temporary = Path(__file__).resolve().parents[1] / ".cache" / "release-tests"
        temporary.mkdir(parents=True, exist_ok=True)
        directory = temporary / uuid.uuid4().hex
        directory.mkdir()
        try:
            archive = Path(directory) / "MapleMate-1.0.0-windows-x64.zip"
            checksum = archive.with_suffix(".zip.sha256")
            with zipfile.ZipFile(archive, "w") as package:
                package.writestr("MapleMate/../private.txt", "synthetic")
            with self.assertRaisesRegex(ValueError, "ZIP path"):
                check_archive(archive, "1.0.0")
            with zipfile.ZipFile(archive, "w") as package:
                package.writestr("MapleMate/README.md", "synthetic")
            checksum.write_text("wrong", encoding="ascii")
            with self.assertRaisesRegex(ValueError, "checksum"):
                check_archive(archive, "1.0.0")
            checksum.write_text(f"{hashlib.sha256(archive.read_bytes()).hexdigest()}  {archive.name}\n", encoding="ascii")
            check_archive(archive, "1.0.0")
        finally:
            self.assertEqual(directory.resolve().parent, temporary.resolve())
            shutil.rmtree(directory)


if __name__ == "__main__":
    unittest.main()
