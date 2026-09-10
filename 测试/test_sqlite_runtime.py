# -*- coding: utf-8 -*-
"""SQLite 跨模块协调器的纯标准库回归测试。"""
import os
import sqlite3
import sys
import tempfile
import threading
import time
import unittest


PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOOLS_DIR = os.path.join(PROJECT_ROOT, "晚晚", "工具")
if TOOLS_DIR not in sys.path:
    sys.path.insert(0, TOOLS_DIR)

from sqlite_runtime import BOT_DB_LOCK, connect_bot_db  # noqa: E402


class SQLiteRuntimeTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        path = os.path.join(self._tmp.name, "test.db")
        self.a = connect_bot_db(path)
        self.b = connect_bot_db(path)
        self.a.execute("CREATE TABLE sample(id INTEGER PRIMARY KEY, value TEXT)")
        self.a.commit()

    def tearDown(self):
        self.a.close()
        self.b.close()
        self._tmp.cleanup()

    def test_stale_transaction_is_recovered_before_next_operation(self):
        with BOT_DB_LOCK:
            self.a.execute("INSERT INTO sample(value) VALUES ('unfinished')")
            # 故意不提交，模拟业务异常被上层吞掉后留下的事务。

        with BOT_DB_LOCK:
            self.b.execute("INSERT INTO sample(value) VALUES ('healthy')")
            self.b.commit()

        rows = self.b.execute("SELECT value FROM sample ORDER BY id").fetchall()
        self.assertEqual([row[0] for row in rows], ["healthy"])

    def test_exception_rolls_back_immediately(self):
        with self.assertRaises(RuntimeError):
            with BOT_DB_LOCK:
                self.a.execute("INSERT INTO sample(value) VALUES ('broken')")
                raise RuntimeError("simulated failure")

        with BOT_DB_LOCK:
            self.b.execute("INSERT INTO sample(value) VALUES ('after-error')")
            self.b.commit()
        values = [row[0] for row in self.b.execute("SELECT value FROM sample").fetchall()]
        self.assertEqual(values, ["after-error"])

    def test_parallel_connections_are_serialized(self):
        errors = []

        def worker(conn, prefix):
            try:
                for index in range(15):
                    with BOT_DB_LOCK:
                        conn.execute(
                            "INSERT INTO sample(value) VALUES (?)",
                            (f"{prefix}-{index}",),
                        )
                        time.sleep(0.002)
                        conn.commit()
            except Exception as exc:  # pragma: no cover - 仅用于把线程异常带回主线程
                errors.append(exc)

        threads = [
            threading.Thread(target=worker, args=(self.a, "a")),
            threading.Thread(target=worker, args=(self.b, "b")),
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        self.assertEqual(errors, [])
        count = self.a.execute("SELECT COUNT(*) FROM sample").fetchone()[0]
        self.assertEqual(count, 30)


if __name__ == "__main__":
    unittest.main()
