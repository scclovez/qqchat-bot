# -*- coding: utf-8 -*-
"""bot_memory.db 的进程内 SQLite 协调工具。

多个业务模块各自保留独立连接，但必须共用同一把可重入锁，避免同一进程里的
记忆、成长、生活状态、空间与互动任务同时争抢 SQLite 的单写者锁。
"""
from __future__ import annotations

import sqlite3
import threading


# 所有访问 bot_memory.db 的模块都引用同一个对象。使用 RLock 是因为部分初始化
# 与迁移函数会在已持锁时调用另一段数据库逻辑。
BOT_DB_LOCK = threading.RLock()
BUSY_TIMEOUT_MS = 5000


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
