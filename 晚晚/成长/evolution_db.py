# -*- coding: utf-8 -*-
"""成长系统数据库：日记 / 性格状态 / 主动撩人控制 / 性格演化 四张表（复用 bot_memory.db）。"""
import logging
import os
import sqlite3
from datetime import datetime

from 路径 import PROJECT_ROOT, data_path
from sqlite_runtime import BOT_DB_LOCK, connect_bot_db

logger = logging.getLogger(__name__)

DB_PATH = data_path("晚晚", "数据", "bot_memory.db")

_lock = BOT_DB_LOCK
_conn = None


def get_conn() -> sqlite3.Connection:
    """惰性初始化连接（线程安全，与 memory.py 共用同一个 db 文件，WAL 并发安全）。"""
    global _conn
    with _lock:
        if _conn is None:
            _conn = connect_bot_db(DB_PATH)
        return _conn


def init_db():
    """建四张表；personality_state 种子数据（三项初始为 0）。"""
    conn = get_conn()
    with _lock:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS diary (
                date TEXT PRIMARY KEY,
                content TEXT,
                mood_tags TEXT,
                affection_delta INTEGER DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS personality_state (
                key TEXT PRIMARY KEY,
                value INTEGER DEFAULT 0,
                last_updated TEXT
            );
            CREATE TABLE IF NOT EXISTS active_pull_state (
                date TEXT PRIMARY KEY,
                count INTEGER DEFAULT 0,
                last_cold_trigger_date TEXT,
                consecutive_no_cold_days INTEGER DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS evolution_notes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                note_date TEXT NOT NULL,
                note TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        for key in ("affection", "dependency", "jealousy_tendency"):
            conn.execute(
                "INSERT OR IGNORE INTO personality_state (key, value, last_updated) VALUES (?, 0, ?)",
                (key, now),
            )
        conn.commit()
    logger.info("成长系统数据库已初始化: %s", DB_PATH)


# =============================================================================
# 性格演化记录（evolution_notes）
# =============================================================================

def add_evolution_note(note_date: str, note: str):
    """写入一条性格演化记录（当天日期 + 变化描述）。"""
    note = (note or "").strip()
    if not note:
        return
    conn = get_conn()
    with _lock:
        conn.execute(
            "INSERT INTO evolution_notes (note_date, note) VALUES (?, ?)",
            (note_date, note),
        )
        conn.commit()


def get_recent_evolution_notes(limit: int = 3) -> list[dict]:
    """取最近 limit 条演化记录（按时间升序返回），供回复时注入。"""
    conn = get_conn()
    with _lock:
        rows = conn.execute(
            "SELECT note_date, note FROM evolution_notes ORDER BY id DESC LIMIT ?",
            (max(1, int(limit)),),
        ).fetchall()
    return [{"note_date": r["note_date"], "note": r["note"]} for r in reversed(rows)]


def has_evolution_today(today: str) -> bool:
    """当天是否已做过性格演化（存在非"说话方式"类记录即视为已演化，防重复执行）。"""
    conn = get_conn()
    with _lock:
        row = conn.execute(
            "SELECT COUNT(*) AS cnt FROM evolution_notes "
            "WHERE note_date = ? AND note NOT LIKE '说话方式：%'",
            (today,),
        ).fetchone()
    return row["cnt"] > 0


def diary_exists(date: str) -> bool:
    """该日期是否已写过日记（补跑检查用）。"""
    conn = get_conn()
    with _lock:
        row = conn.execute(
            "SELECT 1 FROM diary WHERE date = ?", (date,),
        ).fetchone()
    return row is not None


init_db()
