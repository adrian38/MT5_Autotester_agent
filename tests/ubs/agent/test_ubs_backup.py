import sqlite3
import tempfile
import unittest
from pathlib import Path

from ubs.backup import backup_memory


class UBSBackupTests(unittest.TestCase):
    def test_backup_memory_creates_readable_sqlite_copy(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "outputs" / "ubs_memory_ROBOFOREX_ECN.sqlite"
            source.parent.mkdir(parents=True)
            conn = sqlite3.connect(source)
            try:
                conn.execute("create table sample (id integer primary key, value text not null)")
                conn.execute("insert into sample (value) values ('ok')")
                conn.commit()
            finally:
                conn.close()

            backup = backup_memory(root, "ECN", source_path=source)

            self.assertTrue(backup.exists())
            copied = sqlite3.connect(backup)
            try:
                row = copied.execute("select value from sample where id=1").fetchone()
            finally:
                copied.close()
            self.assertEqual(row[0], "ok")


if __name__ == "__main__":
    unittest.main()
