# -*- coding: utf-8 -*-
"""日记机制：每天凌晨 3:00 把当天聊天记录 + 情绪标签写成第一人称日记。

- 输入：当天 chat_history + 当日情绪标签 + 当日亲密度变化
- 处理：DeepSeek 按人设写 ≤150 字第一人称日记
- 存储：diary 表 + 写入长期记忆（用户 facts，供后续对话引用）
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

# 今日情绪标签 / 今日亲密度变化：持久化到 diary 表（date=今天），重启不丢。
# 实时特征值（亲密度/依赖度/醋意）在 personality_state 表。
_today_tags = set()
_today_affection_delta = 0
_loaded = False


def _today() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def _persist_today():
    """把今日情绪/亲密度写入 diary 表（date=今天），供重启后恢复（不清空 content）。"""
    try:
        import json as _json
        conn = db.get_conn()
        with db._lock:
            conn.execute(
                "INSERT INTO diary (date, content, mood_tags, affection_delta) "
                "VALUES (?, '', ?, ?) "
                "ON CONFLICT(date) DO UPDATE SET "
                "mood_tags=excluded.mood_tags, affection_delta=excluded.affection_delta",
                (_today(), _json.dumps(sorted(_today_tags), ensure_ascii=False), _today_affection_delta),
            )
            conn.commit()
    except Exception as e:
        logger.debug("持久化今日状态失败: %s", e)


def _load_today_from_db():
    """首次读取时从 diary 表恢复今日情绪与亲密度（跨重启）。"""
    global _today_tags, _today_affection_delta, _loaded
    if _loaded:
        return
    _loaded = True
    try:
        import json as _json
        conn = db.get_conn()
        with db._lock:
            row = conn.execute(
                "SELECT mood_tags, affection_delta FROM diary WHERE date = ?", (_today(),),
            ).fetchone()
        if row:
            _today_tags = set(_json.loads(row["mood_tags"] or "[]"))
            _today_affection_delta = int(row["affection_delta"] or 0)
    except Exception as e:
        logger.debug("恢复今日状态失败: %s", e)


def add_mood_tag(tag):
    _load_today_from_db()
    global _today_tags
    tag = (tag or "").strip()
    if tag:
        _today_tags.add(tag)
        _persist_today()


def add_affection_delta(delta):
    _load_today_from_db()
    global _today_affection_delta
    _today_affection_delta += int(delta or 0)
    _persist_today()


def get_today_mood_tags() -> list:
    _load_today_from_db()
    return list(_today_tags)


def get_today_affection_delta() -> int:
    _load_today_from_db()
    return _today_affection_delta


def reset_day():
    global _today_tags, _today_affection_delta
    _today_tags = set()
    _today_affection_delta = 0
    _persist_today()


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
    """
    import memory as longterm_memory
    conn = longterm_memory._get_conn()
    day = _target_day()
    with longterm_memory._lock:
        total = conn.execute(
            "SELECT COUNT(*) AS cnt FROM chat_history WHERE date(created_at) = ?",
            (day,),
        ).fetchone()["cnt"]
        limit = max(1, int(total * 0.9))
        rows = conn.execute(
            "SELECT role, content FROM chat_history WHERE date(created_at) = ? "
            "ORDER BY id DESC LIMIT ?",
            (day, limit),
        ).fetchall()
    return [(r["role"], r["content"]) for r in reversed(rows)]


def _today_users():
    import memory as longterm_memory
    conn = longterm_memory._get_conn()
    day = _target_day()
    with longterm_memory._lock:
        rows = conn.execute(
            "SELECT DISTINCT user_id FROM chat_history WHERE date(created_at) = ?", (day,),
        ).fetchall()
    return [r["user_id"] for r in rows]


async def generate_diary(llm_call):
    """生成当天日记并存储；llm_call 为 async (messages, **kw) -> str（传 bot._deepseek.chat）。"""
    chats = _today_chat()
    if not chats:
        logger.info("今日无聊天记录，跳过日记")
        return ""
    try:
        import personality_state
        stage = personality_state.stage_name()
    except Exception:
        stage = "礼貌试探期"
    tags = get_today_mood_tags()
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

    day = _target_day()
    conn = db.get_conn()
    with db._lock:
        conn.execute(
            """INSERT INTO diary (date, content, mood_tags, affection_delta) VALUES (?, ?, ?, ?)
               ON CONFLICT(date) DO UPDATE SET content=excluded.content,
                   mood_tags=excluded.mood_tags, affection_delta=excluded.affection_delta""",
            (day, content, json.dumps(tags, ensure_ascii=False), _today_affection_delta),
        )
        conn.commit()
    logger.info("日记已生成 %s（%d 字，情绪 %s，亲密度 +%d）",
                day, len(content), tags, _today_affection_delta)

    # 联动：写入长期记忆，供后续对话引用
    try:
        import memory as longterm_memory
        for uid in _today_users():
            longterm_memory.add_user_fact(uid, f"日记：{content}")
    except Exception as e:
        logger.warning("日记写入长期记忆失败: %s", e)

    reset_day()
    return content
