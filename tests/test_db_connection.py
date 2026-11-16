from __future__ import annotations

import sqlite3
import unittest
from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from dca_tracker import db


class DatabaseConnectionTest(unittest.TestCase):
    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.db_path = Path(directory.name) / "dca.db"
        db_path = patch.object(db, "DB_PATH", self.db_path)
        db_path.start()
        self.addCleanup(db_path.stop)

    def assert_connection_closed(self, conn):
        with self.assertRaisesRegex(sqlite3.ProgrammingError, "closed database"):
            conn.execute("SELECT 1")

    def test_normal_exit_commits_and_closes(self):
        with db.connect() as conn:
            self.addCleanup(conn.close)
            conn.execute("CREATE TABLE entries (value INTEGER)")
            conn.execute("INSERT INTO entries VALUES (42)")

        with closing(sqlite3.connect(self.db_path)) as reader:
            self.assertEqual(reader.execute("SELECT value FROM entries").fetchall(), [(42,)])
        self.assert_connection_closed(conn)

    def test_statement_error_rolls_back_and_closes(self):
        with self.assertRaisesRegex(sqlite3.IntegrityError, "UNIQUE constraint failed"):
            with db.connect() as conn:
                self.addCleanup(conn.close)
                conn.execute("CREATE TABLE entries (value INTEGER UNIQUE)")
                conn.execute("INSERT INTO entries VALUES (42)")
                conn.execute("INSERT INTO entries VALUES (42)")

        with closing(sqlite3.connect(self.db_path)) as reader:
            self.assertEqual(reader.execute("SELECT value FROM entries").fetchall(), [])
        self.assert_connection_closed(conn)

    def test_commit_error_rolls_back_and_closes(self):
        with self.assertRaisesRegex(sqlite3.IntegrityError, "FOREIGN KEY constraint failed"):
            with db.connect() as conn:
                self.addCleanup(conn.close)
                conn.execute("CREATE TABLE parent (id INTEGER PRIMARY KEY)")
                conn.execute("CREATE TABLE child (id REFERENCES parent(id) DEFERRABLE INITIALLY DEFERRED)")
                conn.execute("INSERT INTO child VALUES (42)")

        with closing(sqlite3.connect(self.db_path)) as reader:
            self.assertEqual(reader.execute("SELECT id FROM child").fetchall(), [])
        self.assert_connection_closed(conn)

    def test_initialization_error_closes_connection_and_propagates(self):
        self.db_path.write_bytes(b"this is not an SQLite database")
        conn = sqlite3.connect(self.db_path)
        self.addCleanup(conn.close)

        with patch.object(db.sqlite3, "connect", return_value=conn):
            with self.assertRaisesRegex(sqlite3.DatabaseError, "file is not a database"):
                with db.connect():
                    self.fail("初始化失败后不应进入事务")

        self.assert_connection_closed(conn)
