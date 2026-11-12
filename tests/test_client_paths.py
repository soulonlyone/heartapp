import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from heartapp import paths


class TestClientPaths(unittest.TestCase):
    def test_frozen_client_and_portable_paths(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            exe_dir = root / "下载"
            exe_dir.mkdir()
            with (
                patch.object(paths, "is_frozen", return_value=True),
                patch.object(paths, "app_dir", return_value=exe_dir),
                patch.dict("os.environ", {"LOCALAPPDATA": str(root / "profile")}),
            ):
                self.assertEqual(paths.default_db_path(), root / "profile/HeartApp/heartrate.db")
                self.assertEqual(list(exe_dir.iterdir()), [])
                (exe_dir / paths.PORTABLE_MARKER).touch()
                self.assertEqual(paths.default_db_path(), exe_dir / "data/heartrate.db")

    def test_migration_preserves_wal_and_refuses_to_overwrite(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "旧记录.db"
            target = Path(tmp) / "新记录.db"
            with closing(sqlite3.connect(source)) as old:
                old.execute("PRAGMA journal_mode=WAL")
                old.execute("CREATE TABLE records (bpm INTEGER)")
                old.execute("INSERT INTO records VALUES (72)")
                old.commit()
                self.assertTrue(source.with_suffix(".db-wal").is_file())
                paths.migrate_database(source, target)
                old.execute("INSERT INTO records VALUES (80)")
                old.commit()
                with self.assertRaises(FileExistsError):
                    paths.migrate_database(source, target)
                with closing(sqlite3.connect(target)) as new:
                    self.assertEqual(new.execute("SELECT bpm FROM records").fetchall(), [(72,)])
                self.assertEqual(old.execute("SELECT bpm FROM records").fetchall(), [(72,), (80,)])
            self.assertEqual(set(Path(tmp).iterdir()), {source, target})

    def test_corrupt_source_leaves_no_destination(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "broken.db"
            target = Path(tmp) / "target.db"
            source.write_bytes(b"not a SQLite database")
            with self.assertRaises(sqlite3.DatabaseError):
                paths.migrate_database(source, target)
            self.assertEqual(list(Path(tmp).iterdir()), [source])
