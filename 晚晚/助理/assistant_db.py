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
SCHEMA_VERSION = 6

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
    reminded_at   TEXT,                       -- 最近一次已提醒的时间（防重复提醒）
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
    pending_knowledge_id INTEGER,             -- 当前正在问他/等回答的知识点
    ask_attempts    INTEGER DEFAULT 0,        -- 同一题已提示几次（引导式纠错用）
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
    extra         TEXT,                       -- JSON：音标/例句等附加信息
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

-- 主动消息每日账（面板展示"今天主动了几条、被压住几次"）
CREATE TABLE IF NOT EXISTS proactive_stats (
    day      TEXT NOT NULL,
    user_id  TEXT NOT NULL,
    sent     INTEGER DEFAULT 0,
    blocked  INTEGER DEFAULT 0,
    PRIMARY KEY (day, user_id)
);

-- 情绪采样：每小时记一次当前强度（画 24 小时曲线用）
CREATE TABLE IF NOT EXISTS emotion_samples (
    user_id   TEXT NOT NULL,
    day       TEXT NOT NULL,
    hour      INTEGER NOT NULL,
    happy     REAL DEFAULT 0,
    angry     REAL DEFAULT 0,
    hurt      REAL DEFAULT 0,
    tired     REAL DEFAULT 0,
    updated_at TEXT,
    PRIMARY KEY (user_id, day, hour)
);

-- 主动消息"为什么没发"：每次被闸门压住都记一条原因
CREATE TABLE IF NOT EXISTS proactive_blocks (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id   TEXT NOT NULL,
    day       TEXT NOT NULL,
    hour      INTEGER NOT NULL,
    ts        REAL,
    source    TEXT DEFAULT '',     -- 普通主动 / 追问 / 撩人 / 碎碎念 ...
    priority  INTEGER DEFAULT 0,
    block_kind TEXT DEFAULT '',    -- sleeping / busy / cooldown / other
    reason    TEXT DEFAULT '',
    state     TEXT DEFAULT '',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_proactive_blocks_day ON proactive_blocks(user_id, day);
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
    """按版本顺序补齐结构（只加列/加表，绝不重建或删除既有数据）。"""
    if version < 1:
        _ensure_columns(conn, "behavior_evidence", {
            "meta": "meta TEXT",
            "slot": "slot INTEGER",
        })
    if version < 2:
        _ensure_columns(conn, "schedules", {"reminded_at": "reminded_at TEXT"})
    if version < 3:
        _ensure_columns(conn, "knowledge_items", {"extra": "extra TEXT"})
    if version < 4:
        _ensure_columns(conn, "study_sessions", {
            "pending_knowledge_id": "pending_knowledge_id INTEGER",
            "ask_attempts": "ask_attempts INTEGER DEFAULT 0",
        })
    if version < 5:
        conn.executescript(
            "CREATE TABLE IF NOT EXISTS proactive_stats ("
            " day TEXT NOT NULL, user_id TEXT NOT NULL,"
            " sent INTEGER DEFAULT 0, blocked INTEGER DEFAULT 0,"
            " PRIMARY KEY (day, user_id));")
    if version < 6:
        conn.executescript(
            "CREATE TABLE IF NOT EXISTS emotion_samples ("
            " user_id TEXT NOT NULL, day TEXT NOT NULL, hour INTEGER NOT NULL,"
            " happy REAL DEFAULT 0, angry REAL DEFAULT 0, hurt REAL DEFAULT 0,"
            " tired REAL DEFAULT 0, updated_at TEXT,"
            " PRIMARY KEY (user_id, day, hour));"
            "CREATE TABLE IF NOT EXISTS proactive_blocks ("
            " id INTEGER PRIMARY KEY AUTOINCREMENT, user_id TEXT NOT NULL,"
            " day TEXT NOT NULL, hour INTEGER NOT NULL, ts REAL, source TEXT DEFAULT '',"
            " priority INTEGER DEFAULT 0, block_kind TEXT DEFAULT '', reason TEXT DEFAULT '',"
            " state TEXT DEFAULT '', created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP);"
            "CREATE INDEX IF NOT EXISTS idx_proactive_blocks_day"
            " ON proactive_blocks(user_id, day);")
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


def local_day_hour(ts: float):
    """对外暴露的 (活跃日, 钟点, 槽位) 计算（回填时复用同一套口径）。"""
    return _local_day_hour(float(ts))


def add_evidence_bucket(user_id: str, kind: str, day: str, hour: int, weight: float,
                        first_ts: float, last_ts: float, meta: dict = None) -> bool:
    """按"活跃日 + 小时"整格写入（回填历史记录时用：一次写一格，避免逐条 upsert）。"""
    user_id = str(user_id or "").strip()
    if not user_id or not kind:
        return False
    slot = int(hour) if int(hour) >= DAY_START_HOUR else int(hour) + 24
    return execute(
        "INSERT INTO behavior_evidence "
        "  (user_id, kind, day, hour, slot, weight, first_ts, last_ts, meta) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) "
        "ON CONFLICT(user_id, kind, day, hour) DO UPDATE SET "
        "  weight = weight + excluded.weight,"
        "  first_ts = COALESCE(MIN(first_ts, excluded.first_ts), excluded.first_ts),"
        "  last_ts = MAX(last_ts, excluded.last_ts)",
        (user_id, kind, str(day), int(hour), slot, float(weight),
         float(first_ts), float(last_ts),
         json.dumps(meta, ensure_ascii=False) if meta else None),
    )


def get_meta(key: str, default: str = "") -> str:
    rows = query("SELECT value FROM schema_meta WHERE key = ?", (str(key),))
    return str(rows[0]["value"]) if rows else default


def set_meta(key: str, value: str) -> bool:
    return execute(
        "INSERT INTO schema_meta (key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (str(key), str(value)),
    )


def fetch_chat_history(role: str = "user", since_id: int = 0, since_time: str = "",
                       user_id: str = "") -> list:
    """读取已有聊天记录（回填行为规律用；只读，不改动原表）。"""
    sql = "SELECT id, user_id, role, content, created_at FROM chat_history WHERE id > ?"
    params = [int(since_id)]
    if role:
        sql += " AND role = ?"
        params.append(str(role))
    if user_id:
        sql += " AND user_id = ?"
        params.append(str(user_id))
    if since_time:
        sql += " AND created_at >= ?"
        params.append(str(since_time))
    sql += " ORDER BY id ASC"
    return [dict(row) for row in query(sql, tuple(params))]


def history_users() -> list:
    """聊天记录里出现过的用户（回填时逐个处理）。"""
    rows = query("SELECT DISTINCT user_id FROM chat_history ORDER BY user_id")
    return [str(row["user_id"]) for row in rows if row["user_id"]]


# =============================================================================
# 面板「陪伴状态」用的实时统计
# =============================================================================

def bump_proactive_stat(user_id: str, field: str = "sent", day: str = "") -> bool:
    """主动消息每日计数（sent=已发出 / blocked=被闸门压住）。"""
    if field not in ("sent", "blocked"):
        return False
    day = day or datetime.now().strftime("%Y-%m-%d")
    return execute(
        "INSERT INTO proactive_stats (day, user_id, %s) VALUES (?, ?, 1) "
        "ON CONFLICT(day, user_id) DO UPDATE SET %s = %s + 1" % (field, field, field),
        (day, str(user_id)),
    )


def get_proactive_stats(user_id: str, day: str = "") -> dict:
    day = day or datetime.now().strftime("%Y-%m-%d")
    rows = query(
        "SELECT sent, blocked FROM proactive_stats WHERE user_id = ? AND day = ?",
        (str(user_id), day))
    if not rows:
        return {"sent": 0, "blocked": 0}
    return {"sent": int(rows[0]["sent"] or 0), "blocked": int(rows[0]["blocked"] or 0)}


def message_counts_by_day(user_id: str, day: str = "") -> dict:
    """今天双方各发了多少条、最近一条是几点（时间按本地日切分）。"""
    day = day or datetime.now().strftime("%Y-%m-%d")
    rows = query(
        "SELECT role, COUNT(*) AS cnt, MAX(created_at) AS last_at FROM chat_history "
        "WHERE user_id = ? AND date(created_at, 'localtime') = ? GROUP BY role",
        (str(user_id), day))
    out = {"user": 0, "assistant": 0, "last_at": ""}
    for row in rows:
        role = str(row["role"])
        if role in out:
            out[role] = int(row["cnt"] or 0)
        if row["last_at"] and str(row["last_at"]) > out["last_at"]:
            out["last_at"] = str(row["last_at"])
    return out


def processed_counts(table: str, day: str = "") -> dict:
    """按 kind/tool 统计今天做过多少次（只允许固定白名单表，避免拼接注入）。"""
    allowed = {"qzone_processed": "kind", "interact_usage": "tool"}
    column = allowed.get(table)
    if not column:
        return {}
    day = day or datetime.now().strftime("%Y-%m-%d")
    rows = query(
        "SELECT %s AS name, COUNT(*) AS cnt FROM %s "
        "WHERE date(created_at, 'localtime') = ? GROUP BY %s" % (column, table, column),
        (day,))
    return {str(row["name"]): int(row["cnt"] or 0) for row in rows}


def activity_matrix(user_id: str, days: int = 7) -> list:
    """近 N 天 × 24 小时的活跃权重（面板热力图用；行=活跃日，列=钟点）。"""
    days = max(1, min(31, int(days)))
    first_day = (datetime.now() - timedelta(days=days - 1)).strftime("%Y-%m-%d")
    rows = query(
        "SELECT day, hour, SUM(weight) AS weight FROM behavior_evidence "
        "WHERE user_id = ? AND kind = 'activity' AND day >= ? "
        "GROUP BY day, hour ORDER BY day ASC, hour ASC",
        (str(user_id), first_day))
    return [{"day": row["day"], "hour": int(row["hour"]),
             "weight": float(row["weight"] or 0)} for row in rows]


# ---------------------------------------------------------------- 情绪采样

def record_emotion_sample(user_id: str, values: dict, ts: float = None) -> bool:
    """按小时记一次情绪强度（同一小时内重复记录覆盖为最新值）。"""
    user_id = str(user_id or "").strip()
    if not user_id or not values:
        return False
    ts = float(ts if ts is not None else time.time())
    day, hour, _slot = _local_day_hour(ts)
    return execute(
        "INSERT INTO emotion_samples (user_id, day, hour, happy, angry, hurt, tired, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
        "ON CONFLICT(user_id, day, hour) DO UPDATE SET "
        "  happy = excluded.happy, angry = excluded.angry, hurt = excluded.hurt,"
        "  tired = excluded.tired, updated_at = excluded.updated_at",
        (user_id, day, int(hour), float(values.get("happy") or 0), float(values.get("angry") or 0),
         float(values.get("hurt") or 0), float(values.get("tired") or 0),
         datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
    )


def emotion_series(user_id: str, hours: int = 24, now: datetime = None) -> list:
    """最近 N 小时的情绪序列（缺的时段补 0，便于直接画曲线）。"""
    hours = max(2, min(168, int(hours)))
    now = now or datetime.now()
    since = now - timedelta(hours=hours - 1)
    rows = query(
        "SELECT day, hour, happy, angry, hurt, tired FROM emotion_samples "
        "WHERE user_id = ? AND day >= ? ORDER BY day ASC, hour ASC",
        (str(user_id), since.strftime("%Y-%m-%d")))
    buckets = {}
    for row in rows:
        key = (str(row["day"]), int(row["hour"]))
        buckets[key] = {"happy": float(row["happy"] or 0), "angry": float(row["angry"] or 0),
                        "hurt": float(row["hurt"] or 0), "tired": float(row["tired"] or 0)}
    series = []
    for offset in range(hours):
        moment = since + timedelta(hours=offset)
        day = (moment - timedelta(hours=DAY_START_HOUR)).strftime("%Y-%m-%d")
        values = buckets.get((day, moment.hour)) or {}
        series.append({
            "label": moment.strftime("%H"),
            "hour": moment.hour,
            "day": day,
            "happy": values.get("happy", 0.0),
            "angry": values.get("angry", 0.0),
            "hurt": values.get("hurt", 0.0),
            "tired": values.get("tired", 0.0),
        })
    return series


# ---------------------------------------------------------------- 压制原因账

def add_proactive_block(user_id: str, source: str = "", priority: int = 0,
                        block_kind: str = "", reason: str = "", state: str = "",
                        ts: float = None) -> bool:
    """记一条"这次主动消息为什么没发"。

    这里按**自然日**归档（面板问的是"今天为什么没找我"），与行为证据的"活跃日"口径不同。
    """
    user_id = str(user_id or "").strip()
    if not user_id:
        return False
    ts = float(ts if ts is not None else time.time())
    moment = datetime.fromtimestamp(ts)
    return execute(
        "INSERT INTO proactive_blocks "
        "  (user_id, day, hour, ts, source, priority, block_kind, reason, state) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (user_id, moment.strftime("%Y-%m-%d"), moment.hour, ts, str(source or ""),
         int(priority), str(block_kind or ""), str(reason or "")[:80], str(state or "")),
    )


def proactive_blocks_today(user_id: str, day: str = "", limit: int = 50) -> list:
    day = day or datetime.now().strftime("%Y-%m-%d")
    rows = query(
        "SELECT hour, ts, source, priority, block_kind, reason, state FROM proactive_blocks "
        "WHERE user_id = ? AND day = ? ORDER BY id DESC LIMIT ?",
        (str(user_id), day, max(1, int(limit))))
    return [dict(row) for row in rows]


def prune_proactive_blocks(keep_days: int = 60) -> int:
    cutoff = (datetime.now() - timedelta(days=max(7, int(keep_days)))).strftime("%Y-%m-%d")
    conn = get_conn()
    try:
        with _lock:
            cur = conn.execute("DELETE FROM proactive_blocks WHERE day < ?", (cutoff,))
            conn.commit()
            return cur.rowcount or 0
    except sqlite3.Error as exc:
        rollback_quietly(conn)
        logger.warning("清理压制记录失败: %s", exc)
        return 0


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


def evidence_span(user_id: str) -> dict:
    """证据覆盖范围（起始日 / 最近日 / 天数），面板用来显示"观察了多久"。"""
    rows = query(
        "SELECT MIN(day) AS first_day, MAX(day) AS last_day, COUNT(DISTINCT day) AS days, "
        "       SUM(weight) AS weight FROM behavior_evidence WHERE user_id = ? AND kind = 'activity'",
        (str(user_id),))
    if not rows:
        return {"first_day": "", "last_day": "", "days": 0, "messages": 0}
    row = rows[0]
    return {"first_day": row["first_day"] or "", "last_day": row["last_day"] or "",
            "days": int(row["days"] or 0), "messages": int(row["weight"] or 0)}


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


# =============================================================================
# 日程（schedules）
# =============================================================================

SCHEDULE_FIELDS = (
    "title", "description", "start_time", "end_time", "repeat_rule",
    "priority", "source", "status", "progress", "remind_before", "reminded_at",
)


def add_schedule(user_id: str, title: str, start_time: str = "", end_time: str = "",
                 description: str = "", repeat_rule: str = "", priority: int = 50,
                 source: str = "user", remind_before: int = 15, status: str = "pending"):
    """新建日程，返回新行 id；失败返回 None。"""
    user_id, title = str(user_id or "").strip(), (title or "").strip()
    if not user_id or not title:
        return None
    conn = get_conn()
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    try:
        with _lock:
            cur = conn.execute(
                "INSERT INTO schedules (user_id, title, description, start_time, end_time, "
                "  repeat_rule, priority, source, status, progress, remind_before, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?)",
                (user_id, title, description or "", start_time or None, end_time or None,
                 repeat_rule or "", int(priority), str(source or "user"), str(status or "pending"),
                 int(remind_before), now, now),
            )
            conn.commit()
            return cur.lastrowid
    except sqlite3.Error as exc:
        rollback_quietly(conn)
        logger.warning("新建日程失败: %s", exc)
        return None


def update_schedule(schedule_id: int, **fields) -> bool:
    """更新日程字段（只接受白名单字段）。"""
    updates = {k: v for k, v in fields.items() if k in SCHEDULE_FIELDS}
    if not updates:
        return False
    updates["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    columns = ", ".join("%s = ?" % key for key in updates)
    return execute(
        "UPDATE schedules SET %s WHERE id = ?" % columns,
        tuple(updates.values()) + (int(schedule_id),),
    )


def get_schedule(schedule_id: int):
    rows = query("SELECT * FROM schedules WHERE id = ?", (int(schedule_id),))
    return dict(rows[0]) if rows else None


def list_schedules(user_id: str, since: str = "", until: str = "", statuses=None,
                   include_repeating: bool = True) -> list:
    """按时间范围列日程（since/until 为 'YYYY-MM-DD'，比较到当天 23:59:59）。"""
    sql = "SELECT * FROM schedules WHERE user_id = ?"
    params = [str(user_id)]
    if statuses:
        sql += " AND status IN (%s)" % ",".join("?" for _ in statuses)
        params.extend(list(statuses))
    clauses = []
    if since:
        clauses.append("COALESCE(end_time, start_time) >= ?")
        params.append(since + " 00:00:00")
    if until:
        clauses.append("COALESCE(start_time, end_time) <= ?")
        params.append(until + " 23:59:59")
    if not include_repeating:
        clauses.append("COALESCE(repeat_rule, '') = ''")
    if clauses:
        sql += " AND " + " AND ".join(clauses)
    sql += " ORDER BY COALESCE(start_time, end_time) ASC, priority DESC, id ASC"
    return [dict(row) for row in query(sql, tuple(params))]


def delete_schedule(schedule_id: int) -> bool:
    return execute("DELETE FROM schedules WHERE id = ?", (int(schedule_id),))


def mark_schedule_reminded(schedule_id: int, when: str = "") -> bool:
    return update_schedule(schedule_id, reminded_at=when or datetime.now().strftime("%Y-%m-%d %H:%M:%S"))


# =============================================================================
# 目标 / 任务（Phase 3 使用，先建最小读写，保证表与调用约定一致）
# =============================================================================

def add_goal(user_id: str, title: str, category: str = "", target: str = "",
             deadline: str = ""):
    conn = get_conn()
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    try:
        with _lock:
            cur = conn.execute(
                "INSERT INTO goals (user_id, title, category, target, deadline, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (str(user_id), (title or "").strip(), category or "", target or "",
                 deadline or None, now, now),
            )
            conn.commit()
            return cur.lastrowid
    except sqlite3.Error as exc:
        rollback_quietly(conn)
        logger.warning("新建目标失败: %s", exc)
        return None


def list_goals(user_id: str, statuses=None) -> list:
    sql = "SELECT * FROM goals WHERE user_id = ?"
    params = [str(user_id)]
    if statuses:
        sql += " AND status IN (%s)" % ",".join("?" for _ in statuses)
        params.extend(list(statuses))
    sql += " ORDER BY id DESC"
    return [dict(row) for row in query(sql, tuple(params))]


def add_task(user_id: str, title: str, goal_id=None, schedule_id=None, description: str = "",
             planned_minutes: int = 0, due_time: str = "", priority: int = 50,
             source: str = "ai", movable: int = 1, status: str = "pending"):
    conn = get_conn()
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    try:
        with _lock:
            cur = conn.execute(
                "INSERT INTO tasks (user_id, goal_id, schedule_id, title, description, "
                "  planned_minutes, due_time, priority, source, status, movable, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (str(user_id), goal_id, schedule_id, (title or "").strip(), description or "",
                 int(planned_minutes), due_time or None, int(priority), str(source),
                 str(status), 1 if movable else 0, now, now),
            )
            conn.commit()
            return cur.lastrowid
    except sqlite3.Error as exc:
        rollback_quietly(conn)
        logger.warning("新建任务失败: %s", exc)
        return None


def list_tasks(user_id: str, statuses=None, goal_id=None, day: str = "") -> list:
    sql = "SELECT * FROM tasks WHERE user_id = ?"
    params = [str(user_id)]
    if statuses:
        sql += " AND status IN (%s)" % ",".join("?" for _ in statuses)
        params.extend(list(statuses))
    if goal_id is not None:
        sql += " AND goal_id = ?"
        params.append(int(goal_id))
    if day:
        sql += " AND (due_time IS NULL OR substr(due_time, 1, 10) <= ?)"
        params.append(day)
    sql += " ORDER BY priority DESC, id ASC"
    return [dict(row) for row in query(sql, tuple(params))]


def update_task(task_id: int, **fields) -> bool:
    allowed = ("title", "description", "planned_minutes", "done_minutes", "due_time",
               "priority", "source", "status", "progress", "movable", "goal_id", "schedule_id")
    updates = {k: v for k, v in fields.items() if k in allowed}
    if not updates:
        return False
    updates["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    columns = ", ".join("%s = ?" % key for key in updates)
    return execute("UPDATE tasks SET %s WHERE id = ?" % columns,
                   tuple(updates.values()) + (int(task_id),))


# =============================================================================
# 学习会话 / 知识点（Phase 3-4）
# =============================================================================

SESSION_FIELDS = ("goal_id", "task_id", "started_at", "ended_at", "planned_minutes",
                  "actual_minutes", "status", "progress", "summary",
                  "pending_knowledge_id", "ask_attempts")


def add_session(user_id: str, goal_id=None, task_id=None, planned_minutes: int = 10,
                status: str = "active", started_at: str = ""):
    conn = get_conn()
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    try:
        with _lock:
            cur = conn.execute(
                "INSERT INTO study_sessions (user_id, goal_id, task_id, started_at, "
                "  planned_minutes, status, progress) VALUES (?, ?, ?, ?, ?, ?, 0)",
                (str(user_id), goal_id, task_id, started_at or now, int(planned_minutes), status),
            )
            conn.commit()
            return cur.lastrowid
    except sqlite3.Error as exc:
        rollback_quietly(conn)
        logger.warning("新建学习会话失败: %s", exc)
        return None


def update_session(session_id: int, **fields) -> bool:
    updates = {k: v for k, v in fields.items() if k in SESSION_FIELDS}
    if not updates:
        return False
    columns = ", ".join("%s = ?" % key for key in updates)
    return execute("UPDATE study_sessions SET %s WHERE id = ?" % columns,
                   tuple(updates.values()) + (int(session_id),))


def get_session(session_id: int):
    rows = query("SELECT * FROM study_sessions WHERE id = ?", (int(session_id),))
    return dict(rows[0]) if rows else None


def get_open_session(user_id: str):
    """取当前未结束的会话（active / paused）。"""
    rows = query(
        "SELECT * FROM study_sessions WHERE user_id = ? AND status IN ('active','paused') "
        "ORDER BY id DESC LIMIT 1", (str(user_id),))
    return dict(rows[0]) if rows else None


def list_sessions(user_id: str, limit: int = 10) -> list:
    rows = query(
        "SELECT * FROM study_sessions WHERE user_id = ? ORDER BY id DESC LIMIT ?",
        (str(user_id), max(1, int(limit))))
    return [dict(row) for row in rows]


def get_last_finished_session(user_id: str):
    rows = query(
        "SELECT * FROM study_sessions WHERE user_id = ? AND status IN ('completed','partial','abandoned') "
        "ORDER BY id DESC LIMIT 1", (str(user_id),))
    return dict(rows[0]) if rows else None


# ---------------------------------------------------------------- 知识点

def add_knowledge(user_id: str, content: str, answer: str = "", subject: str = "",
                  goal_id=None, extra: dict = None):
    conn = get_conn()
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    try:
        with _lock:
            cur = conn.execute(
                "INSERT INTO knowledge_items (user_id, goal_id, subject, content, answer, extra, "
                "  status, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, 'unknown', ?, ?)",
                (str(user_id), goal_id, subject or "", (content or "").strip(), answer or "",
                 json.dumps(extra, ensure_ascii=False) if extra else None, now, now),
            )
            conn.commit()
            return cur.lastrowid
    except sqlite3.Error as exc:
        rollback_quietly(conn)
        logger.warning("新建知识点失败: %s", exc)
        return None


def get_knowledge(knowledge_id: int):
    rows = query("SELECT * FROM knowledge_items WHERE id = ?", (int(knowledge_id),))
    if not rows:
        return None
    item = dict(rows[0])
    item["extra"] = _loads(item.get("extra"), {})
    return item


def list_knowledge(user_id: str, goal_id=None, statuses=None, limit: int = 100) -> list:
    sql = "SELECT * FROM knowledge_items WHERE user_id = ?"
    params = [str(user_id)]
    if goal_id is not None:
        sql += " AND goal_id = ?"
        params.append(int(goal_id))
    if statuses:
        sql += " AND status IN (%s)" % ",".join("?" for _ in statuses)
        params.extend(list(statuses))
    sql += " ORDER BY mastery ASC, id ASC LIMIT ?"
    params.append(max(1, int(limit)))
    out = []
    for row in query(sql, tuple(params)):
        item = dict(row)
        item["extra"] = _loads(item.get("extra"), {})
        out.append(item)
    return out


def update_knowledge(knowledge_id: int, **fields) -> bool:
    allowed = ("mastery", "correct_count", "wrong_count", "last_seen", "next_review",
               "status", "answer", "extra", "subject", "goal_id")
    updates = {}
    for key, value in fields.items():
        if key not in allowed:
            continue
        if key == "extra" and not isinstance(value, str):
            value = json.dumps(value, ensure_ascii=False)
        updates[key] = value
    if not updates:
        return False
    updates["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    columns = ", ".join("%s = ?" % key for key in updates)
    return execute("UPDATE knowledge_items SET %s WHERE id = ?" % columns,
                   tuple(updates.values()) + (int(knowledge_id),))


def due_reviews(user_id: str, day: str = "", limit: int = 5) -> list:
    day = day or datetime.now().strftime("%Y-%m-%d")
    rows = query(
        "SELECT * FROM knowledge_items WHERE user_id = ? AND next_review IS NOT NULL "
        "AND next_review <= ? ORDER BY next_review ASC LIMIT ?",
        (str(user_id), day + " 23:59:59", max(1, int(limit))))
    out = []
    for row in rows:
        item = dict(row)
        item["extra"] = _loads(item.get("extra"), {})
        out.append(item)
    return out


def add_review_record(user_id: str, knowledge_id: int, result: str, interval_days: float = 0):
    conn = get_conn()
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    try:
        with _lock:
            conn.execute(
                "INSERT INTO review_records (user_id, knowledge_id, result, interval_days, reviewed_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (str(user_id), int(knowledge_id), str(result), float(interval_days), now),
            )
            conn.commit()
        return True
    except sqlite3.Error as exc:
        rollback_quietly(conn)
        logger.warning("写入复习记录失败: %s", exc)
        return False


def study_days(user_id: str, limit: int = 60) -> list:
    """有过学习会话的日期（倒序），用于计算连续学习天数。"""
    rows = query(
        "SELECT DISTINCT substr(started_at, 1, 10) AS day FROM study_sessions "
        "WHERE user_id = ? AND status IN ('completed','partial') ORDER BY day DESC LIMIT ?",
        (str(user_id), max(1, int(limit))))
    return [row["day"] for row in rows if row["day"]]


init_db()
