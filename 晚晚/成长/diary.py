# -*- coding: utf-8 -*-
"""日记机制：每天凌晨 3:00 把当天聊天记录 + 情绪标签写成第一人称日记。

- 输入：当天 chat_history + 当日情绪标签 + 当日亲密度变化
- 处理：DeepSeek 按人设写 ≤150 字第一人称日记
- 存储：diary 表（每天一篇）
"""
import json
import logging
from datetime import datetime

import evolution_db as db

logger = logging.getLogger(__name__)

DIARY_PROMPT_TEMPLATE = (
    "你是她。请根据今天和我的聊天记录，用第一人称写一篇不超过150字的日记，"
    "记录你今天的感受、心情和对我的想法。语气符合你的人设（当前性格阶段为{stage}）。"
    "不要出现“日记”二字，直接写内容。今天的情绪标签有：{mood_tags}。"
)

# 今日情绪标签 / 今日亲密度变化：持久化到 diary 表（date=本地当天），重启不丢。
# 实时特征值（亲密度/依赖度/醋意）在 personality_state 表。
# 内存缓存绑定具体日期（_cached_date）：日期翻转为新一天时自动丢弃旧缓存并重载当天行，
# 避免跨天/重启后把"昨天"的情绪/亲密度误记到新的一天（修复 0-3 点间重启覆盖日记元数据）。
_today_tags = set()
_today_affection_delta = 0
_cached_date = ""


def _today() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def _persist(day: str):
    """把某日的情绪/亲密度写入 diary 表（date=day，不清空 content 正文）。"""
    try:
        import json as _json
        conn = db.get_conn()
        with db._lock:
            conn.execute(
                "INSERT INTO diary (date, content, mood_tags, affection_delta) "
                "VALUES (?, '', ?, ?) "
                "ON CONFLICT(date) DO UPDATE SET "
                "mood_tags=excluded.mood_tags, affection_delta=excluded.affection_delta",
                (day, _json.dumps(sorted(_today_tags), ensure_ascii=False), _today_affection_delta),
            )
            conn.commit()
    except Exception as e:
        logger.debug("持久化 %s 状态失败: %s", day, e)


def _ensure_today():
    """确保内存缓存属于今天：跨天/首次访问时重载今天的行（旧的昨日缓存自动丢弃）。

    关键：只有"内存缓存日期 != 今天"才重载；重载以数据库当天行为准，
    因此跨天后首次 add_mood_tag 不会把昨天的标签延续到今天。
    """
    global _today_tags, _today_affection_delta, _cached_date
    today = _today()
    if _cached_date == today:
        return
    _today_tags = set()
    _today_affection_delta = 0
    _cached_date = today
    try:
        import json as _json
        conn = db.get_conn()
        with db._lock:
            row = conn.execute(
                "SELECT mood_tags, affection_delta FROM diary WHERE date = ?", (today,),
            ).fetchone()
        if row:
            _today_tags = set(_json.loads(row["mood_tags"] or "[]"))
            _today_affection_delta = int(row["affection_delta"] or 0)
    except Exception as e:
        logger.debug("恢复 %s 状态失败: %s", today, e)


def add_mood_tag(tag):
    _ensure_today()
    global _today_tags
    tag = (tag or "").strip()
    if tag:
        _today_tags.add(tag)
        _persist(_cached_date)


def add_affection_delta(delta):
    _ensure_today()
    global _today_affection_delta
    _today_affection_delta += int(delta or 0)
    _persist(_cached_date)


def get_today_mood_tags() -> list:
    _ensure_today()
    return list(_today_tags)


def get_today_affection_delta() -> int:
    _ensure_today()
    return _today_affection_delta


def _day_state(day: str) -> tuple:
    """读取指定日期行的情绪标签与亲密度增量（无记录返回 ([], 0)）。

    供凌晨日记/演化生成"昨天"内容时取用：昨天白天的情绪/亲密度在当天
    由 add_mood_tag/add_affection_delta 持续写入 date=昨天 行，这里读回。
    """
    try:
        import json as _json
        conn = db.get_conn()
        with db._lock:
            row = conn.execute(
                "SELECT mood_tags, affection_delta FROM diary WHERE date = ?", (day,),
            ).fetchone()
        if row:
            tags = _json.loads(row["mood_tags"] or "[]")
            delta = int(row["affection_delta"] or 0)
            return (list(tags) if isinstance(tags, list) else []), delta
    except Exception as e:
        logger.debug("读取 %s 日状态失败: %s", day, e)
    return [], 0


def get_day_mood_tags(day: str) -> list:
    """指定日期（如昨天）的情绪标签；供凌晨按日批量修正/演化使用。"""
    return _day_state(day)[0]


# 目标日：日记/演化在凌晨执行，总结"刚过去的完整自然日"（昨天）。
# 若用 datetime.now() 当天，凌晨跑时只能查到 0 点以来的消息，整天聊天会丢。
def _target_day() -> str:
    from datetime import timedelta
    return (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")


def _today_chat():
    """从长期记忆库取目标日（昨天）的对话（全部用户合并，截取当日约 90% 的内容）。

    原固定 50 条对聊得多的日子覆盖不够；改为取当天消息总量的 90%
    （最少 1 条），让日记能总结到当天绝大部分聊天。
    当天消息量大时输入 token 也随之增大（DeepSeek 长上下文可容纳，
    凌晨 3:00 执行不受聊天延迟影响）。

    注意：chat_history.created_at 存的是 UTC（SQLite CURRENT_TIMESTAMP），
    而目标日是本地日期 → 查询必须用 date(created_at, 'localtime') 转本地，
    否则凌晨 0-8 点的对话会被错分到昨天/今天（8 小时时区偏移）。
    """
    import memory as longterm_memory
    conn = longterm_memory._get_conn()
    day = _target_day()
    with longterm_memory._lock:
        total = conn.execute(
            "SELECT COUNT(*) AS cnt FROM chat_history WHERE date(created_at, 'localtime') = ?",
            (day,),
        ).fetchone()["cnt"]
        limit = max(1, int(total * 0.9))
        rows = conn.execute(
            "SELECT role, content FROM chat_history WHERE date(created_at, 'localtime') = ? "
            "ORDER BY id DESC LIMIT ?",
            (day, limit),
        ).fetchall()
    return [(r["role"], r["content"]) for r in reversed(rows)]


async def generate_diary(llm_call):
    """生成目标日（昨天）日记并存储；llm_call 为 async (messages, **kw) -> str（传 bot._deepseek.chat）。

    目标日情绪/亲密度从 diary 表 date=目标日 行读取（昨天白天 add_* 时已逐次持久化），
    不再使用"今天"的内存缓存，避免 0-3 点间重启后把今天的空标签覆盖掉昨天的元数据。
    """
    chats = _today_chat()
    if not chats:
        logger.info("今日无聊天记录，跳过日记")
        return ""
    day = _target_day()
    try:
        import personality_state
        stage = personality_state.stage_name()
    except Exception:
        stage = "礼貌试探期"
    tags, aff_delta = _day_state(day)
    chat_text = "\n".join(f"{'我' if r == 'user' else '她'}: {c}" for r, c in chats)
    prompt = DIARY_PROMPT_TEMPLATE.format(stage=stage, mood_tags=json.dumps(tags, ensure_ascii=False))
    messages = [
        {"role": "system", "content": prompt},
        {"role": "user", "content": chat_text},
    ]
    try:
        content = await llm_call(messages, max_tokens=300)
        content = (content or "").strip()
        if not content:
            logger.warning("日记生成返回空，跳过")
            return ""
    except Exception as e:
        logger.error("日记生成失败: %s", e)
        return ""

    conn = db.get_conn()
    with db._lock:
        conn.execute(
            """INSERT INTO diary (date, content, mood_tags, affection_delta) VALUES (?, ?, ?, ?)
               ON CONFLICT(date) DO UPDATE SET content=excluded.content,
                   mood_tags=excluded.mood_tags, affection_delta=excluded.affection_delta""",
            (day, content, json.dumps(tags, ensure_ascii=False), aff_delta),
        )
        conn.commit()
    logger.info("日记已生成 %s（%d 字，情绪 %s，亲密度 +%d）",
                day, len(content), tags, aff_delta)

    # 注意：这里刻意不把日记写入 user_memory —— user_memory 存的是"关于用户的事实"，
    # 把 bot 的第一人称日记塞进去会以「关于用户的重要信息」注入 prompt，导致角色错乱，
    # 且会污染当天所有活跃用户（曾实测 4 个用户同一时间出现同一条日记 fact）。
    # 不再调用 reset_day()：跨日翻新由 _ensure_today() 在下次 add/get 时自动完成，
    # 避免在这里把"今天"（新一天凌晨）的行元数据清空/覆盖。
    return content
