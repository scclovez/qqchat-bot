# -*- coding: utf-8 -*-
"""可衰减的短期情绪状态。

开心、生气、委屈、疲惫分别保存强度与更新时间。读取时按各自半衰期自然衰减；
安抚只让负面情绪逐步缓和。同类事件的重复次数用于连接长期性格演化。
只保存事件类别，不保存用户原话。
"""
from __future__ import annotations

import atexit
import logging
import os
import sqlite3
import time

from 路径 import data_path
from sqlite_runtime import BOT_DB_LOCK, connect_bot_db, is_database_busy, rollback_quietly

logger = logging.getLogger(__name__)

DB_PATH = data_path("晚晚", "数据", "bot_memory.db")
EMOTIONS = ("happy", "angry", "hurt", "tired")
EMOTION_LABELS = {
    "happy": "开心",
    "angry": "生气",
    "hurt": "委屈",
    "tired": "疲惫",
}
HALF_LIFE_SECONDS = {
    "happy": 2.0 * 3600,
    "angry": 3.0 * 3600,
    "hurt": 5.0 * 3600,
    "tired": 4.0 * 3600,
}
ACTIVE_THRESHOLD = 8.0
STRONG_THRESHOLD = 35.0
REPEAT_WINDOW_SECONDS = 72 * 3600
REPEAT_MILESTONES = (3, 6, 10, 15)

SOOTHE_WORDS = (
    "对不起", "抱歉", "我错了", "别生气", "原谅我", "哄哄你", "不气了",
    "亲亲", "抱抱", "摸摸", "爱你", "喜欢你",
)
INSULT_WORDS = ("滚", "闭嘴", "烦死你", "恶心", "废物", "蠢货", "去死")
DISMISSIVE_WORDS = ("不想理你", "别烦我", "懒得理你", "你别说了", "不需要你")
TRUST_HURT_WORDS = ("你骗我", "你敷衍", "你变了", "你不在乎", "你根本不懂")
AFFECTION_WORDS = ("喜欢你", "爱你", "想你", "亲亲", "抱抱", "宝宝", "老婆")
PLAYFUL_WORDS = ("哈哈", "嘿嘿", "笑死", "好可爱", "真棒", "开心")

_lock = BOT_DB_LOCK
_conn: sqlite3.Connection | None = None


def _get_conn() -> sqlite3.Connection:
    global _conn
    with _lock:
        if _conn is None:
            os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
            _conn = connect_bot_db(DB_PATH)
            _conn.execute(
                "CREATE TABLE IF NOT EXISTS emotion_state ("
                " user_id TEXT NOT NULL, emotion TEXT NOT NULL, intensity REAL NOT NULL DEFAULT 0,"
                " updated_at REAL NOT NULL, last_source TEXT NOT NULL DEFAULT '',"
                " repeat_count INTEGER NOT NULL DEFAULT 0, last_event_at REAL NOT NULL DEFAULT 0,"
                " PRIMARY KEY(user_id, emotion))"
            )
            _conn.commit()
    return _conn


def close_db():
    global _conn
    with _lock:
        conn, _conn = _conn, None
        if conn is not None:
            conn.close()


def init_db(db_path: str | None = None):
    """初始化或切换数据库；db_path 主要供隔离测试使用。"""
    global DB_PATH
    if db_path is not None and os.path.abspath(db_path) != os.path.abspath(DB_PATH):
        close_db()
        DB_PATH = db_path
    _get_conn()


def _decay(emotion: str, intensity: float, updated_at: float, now: float) -> float:
    half_life = HALF_LIFE_SECONDS[emotion]
    elapsed = max(0.0, now - float(updated_at or now))
    return max(0.0, float(intensity or 0) * (0.5 ** (elapsed / half_life)))


def snapshot(user_id: str, now: float | None = None) -> dict[str, float]:
    """返回当前有效强度（0~100）；读取不会为了衰减而频繁写库。"""
    ts = float(now if now is not None else time.time())
    values = {emotion: 0.0 for emotion in EMOTIONS}
    conn = None
    try:
        conn = _get_conn()
        with _lock:
            rows = conn.execute(
                "SELECT emotion, intensity, updated_at FROM emotion_state WHERE user_id=?",
                (str(user_id),),
            ).fetchall()
    except sqlite3.OperationalError as exc:
        rollback_quietly(conn)
        if not is_database_busy(exc):
            raise
        logger.warning("持续情绪读取遇到数据库占用，本轮按平静状态降级")
        return values
    for row in rows:
        emotion = row["emotion"]
        if emotion in values:
            values[emotion] = round(_decay(
                emotion, row["intensity"], row["updated_at"], ts,
            ), 2)
    return values


def dominant(user_id: str, now: float | None = None) -> tuple[str, float]:
    values = snapshot(user_id, now)
    emotion, intensity = max(values.items(), key=lambda item: item[1])
    return (emotion, intensity) if intensity >= ACTIVE_THRESHOLD else ("", 0.0)


def apply_event(user_id: str, emotion: str, delta: float, source: str,
                now: float | None = None, cooldown_seconds: float = 0) -> dict:
    """叠加一次情绪事件，并返回强度与重复里程碑信息。"""
    if emotion not in EMOTIONS:
        raise ValueError(f"未知情绪: {emotion}")
    ts = float(now if now is not None else time.time())
    conn = _get_conn()
    with _lock:
        row = conn.execute(
            "SELECT intensity, updated_at, last_source, repeat_count, last_event_at "
            "FROM emotion_state WHERE user_id=? AND emotion=?",
            (str(user_id), emotion),
        ).fetchone()
        current = _decay(emotion, row["intensity"], row["updated_at"], ts) if row else 0.0
        last_source = str(row["last_source"] or "") if row else ""
        last_event_at = float(row["last_event_at"] or 0) if row else 0.0
        if cooldown_seconds and source == last_source and ts - last_event_at < cooldown_seconds:
            return {
                "emotion": emotion, "intensity": round(current, 2),
                "repeat_count": int(row["repeat_count"] or 0) if row else 0,
                "milestone": False, "ignored": True, "source": source,
            }
        repeated = source == last_source and ts - last_event_at <= REPEAT_WINDOW_SECONDS
        repeat_count = (int(row["repeat_count"] or 0) + 1) if row and repeated else 1
        repeat_boost = 1.0 + min(max(0, repeat_count - 1), 4) * 0.12
        value = max(0.0, min(100.0, current + float(delta) * repeat_boost))
        conn.execute(
            "INSERT INTO emotion_state "
            "(user_id, emotion, intensity, updated_at, last_source, repeat_count, last_event_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(user_id, emotion) DO UPDATE SET "
            "intensity=excluded.intensity, updated_at=excluded.updated_at, "
            "last_source=excluded.last_source, repeat_count=excluded.repeat_count, "
            "last_event_at=excluded.last_event_at",
            (str(user_id), emotion, value, ts, source, repeat_count, ts),
        )
        # 情绪相互牵制：负面上升会压低开心，开心则只轻轻冲淡负面，不能瞬间翻篇。
        opposite = "happy" if emotion in ("angry", "hurt") else ""
        if opposite:
            happy = conn.execute(
                "SELECT intensity, updated_at FROM emotion_state WHERE user_id=? AND emotion='happy'",
                (str(user_id),),
            ).fetchone()
            if happy:
                happy_value = max(0.0, _decay("happy", happy["intensity"], happy["updated_at"], ts)
                                  - abs(float(delta)) * 0.30)
                conn.execute(
                    "UPDATE emotion_state SET intensity=?, updated_at=? "
                    "WHERE user_id=? AND emotion='happy'",
                    (happy_value, ts, str(user_id)),
                )
        conn.commit()
    return {
        "emotion": emotion,
        "intensity": round(value, 2),
        "repeat_count": repeat_count,
        "milestone": repeat_count in REPEAT_MILESTONES,
        "ignored": False,
        "source": source,
    }


def soothe(user_id: str, now: float | None = None) -> dict:
    """逐步安抚生气与委屈；一次安抚不会把状态直接清零。"""
    ts = float(now if now is not None else time.time())
    conn = _get_conn()
    before = snapshot(user_id, ts)
    changed = before["angry"] >= ACTIVE_THRESHOLD or before["hurt"] >= ACTIVE_THRESHOLD
    with _lock:
        for emotion in ("angry", "hurt"):
            old = before[emotion]
            if old > 0:
                conn.execute(
                    "UPDATE emotion_state SET intensity=?, updated_at=? "
                    "WHERE user_id=? AND emotion=?",
                    (max(0.0, old * 0.72), ts, str(user_id), emotion),
                )
        conn.commit()
    happy = (apply_event(user_id, "happy", 8, "soothe", ts, cooldown_seconds=90)
             if changed else {"ignored": True})
    after = snapshot(user_id, ts)
    return {
        "changed": changed,
        "before": before,
        "after": after,
        "happy": happy,
    }


def observe_user_message(user_id: str, text: str, now: float | None = None) -> dict:
    """从对方这轮话中更新情绪；返回事件摘要，不保存原话。"""
    value = text or ""
    ts = float(now if now is not None else time.time())
    events = []
    soothed = False
    if any(word in value for word in SOOTHE_WORDS):
        soothed = bool(soothe(user_id, ts)["changed"])
    if any(word in value for word in INSULT_WORDS):
        events.append(apply_event(user_id, "angry", 34, "insult", ts))
        events.append(apply_event(user_id, "hurt", 24, "insult", ts))
    elif any(word in value for word in DISMISSIVE_WORDS):
        events.append(apply_event(user_id, "angry", 24, "dismissive", ts))
        events.append(apply_event(user_id, "hurt", 18, "dismissive", ts))
    elif any(word in value for word in TRUST_HURT_WORDS):
        events.append(apply_event(user_id, "hurt", 28, "trust_hurt", ts))
        events.append(apply_event(user_id, "angry", 12, "trust_hurt", ts))
    if any(word in value for word in AFFECTION_WORDS):
        events.append(apply_event(user_id, "happy", 16, "affection", ts))
    elif any(word in value for word in PLAYFUL_WORDS):
        events.append(apply_event(user_id, "happy", 9, "playful", ts))
    return {
        "soothed": soothed,
        "events": events,
        "milestones": [event for event in events if event.get("milestone")],
        "snapshot": snapshot(user_id, ts),
    }


def ensure_fatigue(user_id: str, energy_text: str = "", now: float | None = None) -> dict:
    """按真实时段/身体能量补充疲惫，30 分钟内不重复叠加。"""
    ts = float(now if now is not None else time.time())
    hour = time.localtime(ts).tm_hour
    delta = 0
    source = ""
    if "很累" in (energy_text or "") or "困了" in (energy_text or ""):
        delta, source = 18, "low_energy"
    elif hour >= 23 or hour < 6:
        delta, source = 10, "late_night"
    if not source:
        return {"ignored": True, "emotion": "tired", "intensity": snapshot(user_id, ts)["tired"]}
    return apply_event(user_id, "tired", delta, source, ts, cooldown_seconds=30 * 60)


atexit.register(close_db)
