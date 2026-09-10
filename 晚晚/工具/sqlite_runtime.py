# -*- coding: utf-8 -*-
"""bot_memory.db 的进程内 SQLite 协调工具。

多个业务模块各自保留独立连接，但必须共用同一把可重入锁，避免同一进程里的
记忆、成长、生活状态、空间与互动任务同时争抢 SQLite 的单写者锁。
"""
from __future__ import annotations

import logging
import sqlite3
import threading

logger = logging.getLogger(__name__)

BUSY_TIMEOUT_MS = 5000


class BotDatabaseCoordinator:
    """跨模块串行化数据库操作，并回收异常遗留的事务。"""

    def __init__(self):
        self._lock = threading.RLock()
        self._local = threading.local()
        self._connections: list[sqlite3.Connection] = []

    def register(self, conn: sqlite3.Connection):
        """登记连接，供异常恢复时统一回滚。"""
        with self._lock:
            if conn not in self._connections:
                self._connections.append(conn)

    def _rollback_stale_transactions(self) -> int:
        recovered = 0
        alive = []
        for conn in self._connections:
            try:
                if conn.in_transaction:
                    conn.rollback()
                    recovered += 1
                alive.append(conn)
            except sqlite3.ProgrammingError:
                # 已关闭的连接从登记表移除。
                continue
            except sqlite3.Error as exc:
                logger.warning("回收 SQLite 遗留事务失败: %s", exc)
                alive.append(conn)
        self._connections = alive
        return recovered

    def acquire(self):
        self._lock.acquire()
        depth = int(getattr(self._local, "depth", 0))
        if depth == 0:
            recovered = self._rollback_stale_transactions()
            if recovered:
                logger.warning("检测到 %d 个未完成的 SQLite 事务，已自动回滚", recovered)
        self._local.depth = depth + 1
        return True

    def release(self):
        depth = int(getattr(self._local, "depth", 1))
        self._local.depth = max(0, depth - 1)
        self._lock.release()

    def __enter__(self):
        self.acquire()
        return self

    def __exit__(self, exc_type, exc, traceback):
        try:
            # 数据库操作异常离开最外层临界区时立即回滚；即便调用方捕获异常，
            # 也不会把一个悬空写事务留给下一轮消息。
            if exc_type is not None and int(getattr(self._local, "depth", 1)) == 1:
                self._rollback_stale_transactions()
        finally:
            self.release()
        return False


# 所有访问 bot_memory.db 的模块都引用同一个协调器。可重入是因为部分初始化
# 与迁移函数会在已持锁时调用另一段数据库逻辑。
BOT_DB_LOCK = BotDatabaseCoordinator()


def connect_bot_db(path: str) -> sqlite3.Connection:
    """创建项目统一配置的 SQLite 连接。"""
    conn = sqlite3.connect(
        path,
        timeout=BUSY_TIMEOUT_MS / 1000,
        check_same_thread=False,
    )
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_MS}")
    conn.execute("PRAGMA synchronous=NORMAL")
    BOT_DB_LOCK.register(conn)
    return conn


def is_database_busy(exc: BaseException) -> bool:
    """是否为可降级处理的 SQLite 锁冲突。"""
    message = str(exc).lower()
    return isinstance(exc, sqlite3.OperationalError) and (
        "database is locked" in message
        or "database table is locked" in message
        or "database is busy" in message
    )


def rollback_quietly(conn: sqlite3.Connection | None):
    """异常路径释放当前连接可能残留的写事务。"""
    if conn is None:
        return
    try:
        conn.rollback()
    except sqlite3.Error:
        pass
