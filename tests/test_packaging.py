import tempfile
import unittest
from pathlib import Path
from zipfile import ZipFile

from package_vs import build_package


class PackagingTests(unittest.TestCase):
    def test_archive_is_named_vs_and_has_only_vs_as_top_level_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            output = build_package(Path(directory) / "VS.zip")

            self.assertEqual(output.name, "VS.zip")
            with ZipFile(output) as archive:
                names = archive.namelist()
            self.assertTrue(names)
            self.assertEqual({name.split("/", 1)[0] for name in names}, {"VS"})
            self.assertIn("VS/app.py", names)
            self.assertIn("VS/run.bat", names)
            self.assertNotIn("VS/tests/test_packaging.py", names)
            self.assertFalse(any("workspace_data" in name for name in names))


if __name__ == "__main__":
    unittest.main()
