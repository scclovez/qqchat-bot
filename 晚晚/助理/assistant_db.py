# -*- coding: utf-8 -*-
"""助理系统数据库层：行为规律 / 日程 / 目标 / 任务 / 学习会话 / 知识点 / 复习。

设计要点（与现有项目保持一致）：
- 复用同一个 bot_memory.db，不新建第二个数据库文件；
- 统一走 sqlite_runtime.connect_bot_db()（WAL / busy_timeout / row_factory）与
  BOT_DB_LOCK 串行化，绝不自己 sqlite3.connect；
- 业务表独立、SQL 集中在本模块，业务判断放在 behavior_profile / schedule_manager 等模块；
- 老库升级只做幂等 CREATE TABLE IF NOT EXISTS 与缺列 ALTER TABLE ADD COLUMN，
  不重建、不删除任何已有表；SCHEMA_VERSION 记录在 schema_meta 里，备后续迁移。
"""
import json
import logging
import sqlite3
import time
from datetime import datetime, timedelta

from 路径 import data_path
from sqlite_runtime import BOT_DB_LOCK, connect_bot_db, is_database_busy, rollback_quietly

logger = logging.getLogger(__name__)

DB_PATH = data_path("晚晚", "数据", "bot_memory.db")

# 表结构版本：以后加列/加表时递增，并在 _MIGRATIONS 里登记迁移步骤。
SCHEMA_VERSION = 1

# 「活跃日」起点：凌晨 0-4 点发的消息算作前一天（否则跨零点会把一次熬夜
# 拆成两天的数据，作息推断随之失真）。
DAY_START_HOUR = 4

_lock = BOT_DB_LOCK
_conn = None


def get_conn() -> sqlite3.Connection:
    """惰性初始化连接（线程安全；与 memory / evolution_db 共用同一把可重入锁）。"""
    global _conn
    with _lock:
        if _conn is None:
            _conn = connect_bot_db(DB_PATH)
        return _conn


# =============================================================================
# 建表与迁移
# =============================================================================

_TABLES_SQL = """
-- 行为证据：按「天 + 小时」聚合的原始观察，是全部规律推断的唯一数据来源。
CREATE TABLE IF NOT EXISTS behavior_evidence (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     TEXT NOT NULL,
    kind        TEXT NOT NULL,              -- activity / night_said / wake_hint / busy_hint / study_hint / user_stated ...
    day         TEXT NOT NULL,              -- 本地「活跃日」YYYY-MM-DD（以 DAY_START_HOUR 为界）
    hour        INTEGER NOT NULL,           -- 本地钟点 0-23（用于活跃时段分布）
    slot        INTEGER,                     -- 活跃日内排序槽位 4-27（跨零点消息排在当天末尾）
    weight      REAL DEFAULT 1,             -- 该小时内的次数/权重
    first_ts    REAL,                        -- 该小时内最早一条的时间戳
    last_ts     REAL,                        -- 该小时内最晚一条的时间戳
    meta        TEXT,                        -- JSON 附加（字符数、原始短句等）
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(user_id, kind, day, hour)
);
CREATE INDEX IF NOT EXISTS idx_behavior_evidence_user_day
    ON behavior_evidence(user_id, day);

-- 规律推断结果：一个维度可同时保存「近期」与「长期」两套，近期优先影响行为。
CREATE TABLE IF NOT EXISTS user_behavior_profile (
    user_id         TEXT NOT NULL,
    dimension       TEXT NOT NULL,
    window          TEXT NOT NULL,           -- recent（近 14 天）/ long（近 90 天）
    value           TEXT,                    -- JSON：结构化推断值
    confidence      REAL DEFAULT 0,          -- 0-1，仅内部使用，界面转成文字档位
    evidence_count  INTEGER DEFAULT 0,       -- 支撑该推断的证据天数/样本数
    last_updated    TEXT,
    PRIMARY KEY (user_id, dimension, window)
);

-- 日程：source=user 表示用户明确告知的固定事项，source=ai 表示系统安排的可调整任务。
CREATE TABLE IF NOT EXISTS schedules (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id       TEXT NOT NULL,
    title         TEXT NOT NULL,
    description   TEXT DEFAULT '',
    start_time    TEXT,                       -- 本地时间 YYYY-MM-DD HH:MM:SS
    end_time      TEXT,
    repeat_rule   TEXT DEFAULT '',            -- 空=一次性；daily / weekly:1,3 / ...
    priority      INTEGER DEFAULT 50,
    source        TEXT DEFAULT 'user',
    status        TEXT DEFAULT 'pending',     -- pending / doing / done / partial / postponed / cancelled
    progress      REAL DEFAULT 0,
    remind_before INTEGER DEFAULT 15,         -- 提前多少分钟提醒
    created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_schedules_user_time ON schedules(user_id, start_time);

-- 学习目标
CREATE TABLE IF NOT EXISTS goals (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     TEXT NOT NULL,
    title       TEXT NOT NULL,
    category    TEXT DEFAULT '',              -- english / programming / exam / ...
    target      TEXT DEFAULT '',              -- 目标描述（如"四级 425 分"）
    deadline    TEXT,
    status      TEXT DEFAULT 'active',        -- active / paused / done / dropped
    progress    REAL DEFAULT 0,
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 学习任务（可由目标拆解，也可由日程生成）
CREATE TABLE IF NOT EXISTS tasks (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id         TEXT NOT NULL,
    goal_id         INTEGER,
    schedule_id     INTEGER,
    title           TEXT NOT NULL,
    description     TEXT DEFAULT '',
    planned_minutes INTEGER DEFAULT 0,
    done_minutes    INTEGER DEFAULT 0,
    due_time        TEXT,
    priority        INTEGER DEFAULT 50,
    source          TEXT DEFAULT 'ai',
    status          TEXT DEFAULT 'pending',   -- pending / doing / done / partial / postponed / cancelled
    progress        REAL DEFAULT 0,
    movable         INTEGER DEFAULT 1,
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_tasks_user_status ON tasks(user_id, status);

-- 学习会话
CREATE TABLE IF NOT EXISTS study_sessions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id         TEXT NOT NULL,
    goal_id         INTEGER,
    task_id         INTEGER,
    started_at      TEXT,
    ended_at        TEXT,
    planned_minutes INTEGER DEFAULT 0,
    actual_minutes  INTEGER DEFAULT 0,
    status          TEXT DEFAULT 'active',    -- active / paused / completed / abandoned / partial
    progress        REAL DEFAULT 0,
    summary         TEXT DEFAULT '',
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_study_sessions_user ON study_sessions(user_id, started_at);

-- 知识点与错题
CREATE TABLE IF NOT EXISTS knowledge_items (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id       TEXT NOT NULL,
    goal_id       INTEGER,
    subject       TEXT DEFAULT '',
    content       TEXT NOT NULL,
    answer        TEXT DEFAULT '',
    mastery       REAL DEFAULT 0,
    correct_count INTEGER DEFAULT 0,
    wrong_count   INTEGER DEFAULT 0,
    last_seen     TEXT,
    next_review   TEXT,
    status        TEXT DEFAULT 'unknown',     -- unknown / learning / unstable / mastered
    created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_knowledge_user_review ON knowledge_items(user_id, next_review);

-- 复习记录
CREATE TABLE IF NOT EXISTS review_records (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id       TEXT NOT NULL,
    knowledge_id  INTEGER,
    result        TEXT DEFAULT '',            -- correct / wrong
    interval_days REAL DEFAULT 0,
    reviewed_at   TEXT,
    created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 结构版本（当前仓库原先没有统一迁移机制，这里补齐最小可用版本）
CREATE TABLE IF NOT EXISTS schema_meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);
"""


def _table_columns(conn: sqlite3.Connection, table: str) -> set:
    try:
        rows = conn.execute("PRAGMA table_info(%s)" % table).fetchall()
    except sqlite3.Error:
        return set()
    return {row["name"] for row in rows}


def _ensure_columns(conn: sqlite3.Connection, table: str, columns: dict):
    """缺列才 ADD COLUMN（老库升级用，绝不重建表）。"""
    existing = _table_columns(conn, table)
    for name, ddl in columns.items():
        if name in existing:
            continue
        try:
            conn.execute("ALTER TABLE %s ADD COLUMN %s" % (table, ddl))
            logger.info("助理系统：%s 补充列 %s", table, name)
        except sqlite3.Error as exc:
            logger.warning("助理系统：%s 补列 %s 失败: %s", table, name, exc)


def _migrate(conn: sqlite3.Connection, version: int):
    """按版本顺序补齐结构；当前为第 1 版，仅补历史缺失列。"""
    if version < 1:
        _ensure_columns(conn, "behavior_evidence", {
            "meta": "meta TEXT",
            "slot": "slot INTEGER",
        })
    return SCHEMA_VERSION


def init_db():
    """建表 + 迁移（幂等）。老库直接补齐缺失表，不动任何既有数据。"""
    conn = get_conn()
    with _lock:
        conn.executescript(_TABLES_SQL)
        row = conn.execute(
            "SELECT value FROM schema_meta WHERE key = 'schema_version'"
        ).fetchone()
        current = int(row["value"]) if row and str(row["value"]).isdigit() else 0
        target = _migrate(conn, current)
        conn.execute(
            "INSERT INTO schema_meta (key, value) VALUES ('schema_version', ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (str(target),),
        )
        conn.commit()
    logger.info("助理系统数据库已就绪: %s (schema v%s)", DB_PATH, SCHEMA_VERSION)


# =============================================================================
# 通用读写原语（busy 时安全降级，不中断聊天）
# =============================================================================

def execute(sql: str, params=()) -> bool:
    """执行一条写语句；数据库被占用时回滚并返回 False（调用方自行决定是否重试）。"""
    conn = get_conn()
    try:
        with _lock:
            conn.execute(sql, params)
            conn.commit()
        return True
    except sqlite3.OperationalError as exc:
        rollback_quietly(conn)
        if is_database_busy(exc):
            logger.warning("助理系统写入遇到数据库占用，已跳过: %s", sql.split()[0:3])
            return False
        raise
    except sqlite3.Error as exc:
        rollback_quietly(conn)
        logger.warning("助理系统写入失败: %s", exc)
        return False


def query(sql: str, params=()) -> list:
    conn = get_conn()
    try:
        with _lock:
            return conn.execute(sql, params).fetchall()
    except sqlite3.OperationalError as exc:
        rollback_quietly(conn)
        if is_database_busy(exc):
            logger.warning("助理系统查询遇到数据库占用，返回空结果")
            return []
        raise


def _loads(raw, default):
    if not raw:
        return default
    try:
        value = json.loads(raw)
    except (TypeError, ValueError):
        return default
    return value


# =============================================================================
# 行为证据
# =============================================================================

def _local_day_hour(ts: float):
    """时间戳 → (活跃日, 钟点, 排序槽位)。活跃日以 DAY_START_HOUR 为界，钟点保持 0-23。"""
    dt = datetime.fromtimestamp(ts)
    day = (dt - timedelta(hours=DAY_START_HOUR)).strftime("%Y-%m-%d")
    hour = dt.hour
    slot = hour if hour >= DAY_START_HOUR else hour + 24
    return day, hour, slot


def add_evidence(user_id: str, kind: str, ts: float = None, weight: float = 1.0,
                 meta: dict = None) -> bool:
    """记录一条行为证据（按「活跃日 + 小时」聚合，重复落到同一格时累加权重）。"""
    user_id = str(user_id or "").strip()
    if not user_id or not kind:
        return False
    ts = float(ts if ts is not None else time.time())
    day, hour, slot = _local_day_hour(ts)
    meta_text = json.dumps(meta, ensure_ascii=False) if meta else None
    return execute(
        "INSERT INTO behavior_evidence "
        "  (user_id, kind, day, hour, slot, weight, first_ts, last_ts, meta) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) "
        "ON CONFLICT(user_id, kind, day, hour) DO UPDATE SET "
        "  weight = weight + excluded.weight,"
        "  slot = excluded.slot,"
        "  first_ts = COALESCE(MIN(first_ts, excluded.first_ts), excluded.first_ts),"
        "  last_ts = MAX(last_ts, excluded.last_ts),"
        "  meta = COALESCE(excluded.meta, meta)",
        (user_id, kind, day, hour, slot, float(weight), ts, ts, meta_text),
    )


def get_evidence(user_id: str, since_day: str = "", kinds=None) -> list:
    """取证据行（按天、小时排序）。kinds 为 None 时取全部类型。"""
    params = [str(user_id)]
    sql = "SELECT kind, day, hour, weight, first_ts, last_ts, meta FROM behavior_evidence WHERE user_id = ?"
    if since_day:
        sql += " AND day >= ?"
        params.append(since_day)
    if kinds:
        placeholders = ",".join("?" for _ in kinds)
        sql += " AND kind IN (%s)" % placeholders
        params.extend(list(kinds))
    sql += " ORDER BY day ASC, hour ASC"
    return [dict(row) for row in query(sql, tuple(params))]


def get_daily_activity(user_id: str, since_day: str) -> list:
    """按活跃日汇总聊天活跃：最早槽位 / 最晚槽位 / 消息量（作息推断的核心输入）。

    槽位把跨零点的消息排到当天末尾，因此「最早 = 起床、最晚 = 睡前」始终成立。
    """
    rows = query(
        "SELECT day, "
        "       MIN(COALESCE(slot, CASE WHEN hour < ? THEN hour + 24 ELSE hour END)) AS first_hour, "
        "       MAX(COALESCE(slot, CASE WHEN hour < ? THEN hour + 24 ELSE hour END)) AS last_hour, "
        "       SUM(weight) AS messages, MIN(first_ts) AS first_ts, MAX(last_ts) AS last_ts "
        "FROM behavior_evidence WHERE user_id = ? AND kind = 'activity' AND day >= ? "
        "GROUP BY day ORDER BY day ASC",
        (DAY_START_HOUR, DAY_START_HOUR, str(user_id), since_day),
    )
    return [dict(row) for row in rows]


def get_hour_histogram(user_id: str, since_day: str, kinds=("activity",)) -> dict:
    """小时分布（权重合计），用于活跃时段与学习偏好时段。"""
    placeholders = ",".join("?" for _ in kinds)
    rows = query(
        "SELECT hour, SUM(weight) AS weight FROM behavior_evidence "
        "WHERE user_id = ? AND day >= ? AND kind IN (%s) GROUP BY hour" % placeholders,
        tuple([str(user_id), since_day] + list(kinds)),
    )
    return {int(row["hour"]): float(row["weight"] or 0) for row in rows}


def get_hint_hours(user_id: str, kind: str, since_day: str) -> dict:
    """某类内容线索的小时分布（如 busy_hint / study_hint / night_said）。"""
    return get_hour_histogram(user_id, since_day, (kind,))


def prune_evidence(keep_days: int = 180) -> int:
    """清理过老的证据行（保留近 keep_days 天），控制表体积。"""
    cutoff = (datetime.now() - timedelta(days=max(30, int(keep_days)))).strftime("%Y-%m-%d")
    conn = get_conn()
    try:
        with _lock:
            cur = conn.execute("DELETE FROM behavior_evidence WHERE day < ?", (cutoff,))
            conn.commit()
            return cur.rowcount or 0
    except sqlite3.Error as exc:
        rollback_quietly(conn)
        logger.warning("清理行为证据失败: %s", exc)
        return 0


# =============================================================================
# 规律推断结果（user_behavior_profile）
# =============================================================================

def save_profile(user_id: str, dimension: str, window: str, value,
                 confidence: float, evidence_count: int) -> bool:
    return execute(
        "INSERT INTO user_behavior_profile "
        "  (user_id, dimension, window, value, confidence, evidence_count, last_updated) "
        "VALUES (?, ?, ?, ?, ?, ?, ?) "
        "ON CONFLICT(user_id, dimension, window) DO UPDATE SET "
        "  value = excluded.value, confidence = excluded.confidence,"
        "  evidence_count = excluded.evidence_count, last_updated = excluded.last_updated",
        (str(user_id), dimension, str(window),
         json.dumps(value, ensure_ascii=False) if not isinstance(value, str) else value,
         float(confidence), int(evidence_count),
         datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
    )


def get_profiles(user_id: str, window: str = "") -> dict:
    """取某用户全部推断：{'recent': {dimension: {...}}, 'long': {...}}。"""
    params = [str(user_id)]
    sql = ("SELECT dimension, window, value, confidence, evidence_count, last_updated "
           "FROM user_behavior_profile WHERE user_id = ?")
    if window:
        sql += " AND window = ?"
        params.append(window)
    result = {}
    for row in query(sql, tuple(params)):
        bucket = result.setdefault(row["window"], {})
        bucket[row["dimension"]] = {
            "value": _loads(row["value"], {}),
            "confidence": float(row["confidence"] or 0),
            "evidence_count": int(row["evidence_count"] or 0),
            "last_updated": row["last_updated"] or "",
        }
    return result


def add_user_correction(user_id: str, text: str) -> bool:
    """用户手动纠正（"我一般 12 点就睡了"）：作为一条高权重证据参与推断，不做手工画像配置。"""
    text = (text or "").strip()
    if not text:
        return False
    return add_evidence(user_id, "user_stated", weight=3.0, meta={"text": text[:200]})


def get_user_corrections(user_id: str, limit: int = 10) -> list:
    rows = query(
        "SELECT day, hour, meta FROM behavior_evidence WHERE user_id = ? AND kind = 'user_stated' "
        "ORDER BY day DESC, hour DESC LIMIT ?",
        (str(user_id), max(1, int(limit))),
    )
    out = []
    for row in rows:
        meta = _loads(row["meta"], {})
        out.append({"day": row["day"], "hour": int(row["hour"]),
                    "text": str(meta.get("text") or "")})
    return out


init_db()
