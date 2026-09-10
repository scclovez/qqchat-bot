# -*- coding: utf-8 -*-
"""主动撩人机制：按沉默时长/性格阶段触发，生成短消息主动发。

触发条件（优先级）：
  1. 23:00–07:00 深夜勿扰 → 不触发
  2. 今日已触发次数 ≥ 当日上限 → 不触发
  3. 沉默 ≥ 2h 且阶段二/三，或沉默 ≥ 4h 且阶段一 → 触发
"""
import logging
import time
from datetime import datetime, timedelta

import evolution_db as db
import personality_state
import live_info
import liveness

logger = logging.getLogger(__name__)

# ---------- 可调阈值（常量） ----------
NIGHT_START_HOUR = 23     # 深夜勿扰开始
NIGHT_END_HOUR = 7        # 深夜勿扰结束
DAILY_LIMIT_DEFAULT = 2   # 每日默认触发上限
SILENCE_STAGE23_HOURS = 2  # 阶段二三：沉默这么久触发
SILENCE_STAGE1_HOURS = 4   # 阶段一：沉默这么久触发
CHECK_INTERVAL_SECONDS = 15 * 60  # 独立定时任务间隔

PULL_PROMPT_TEMPLATE = (
    "根据当前时段（{period}）、当前场景（{scene}）、沉默时长（{silence_hours}小时）、当前性格阶段（{stage}），"
    "生成一条主动消息。内容贴合你此刻在做的场景（比如在画室就说画画、刚吃完饭就说吃的什么）。"
    "语气符合当前阶段，不要超过 30 字。短消息风格，不要括号动作描写，直接输出。"
)


def _today() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def _is_night() -> bool:
    h = datetime.now().hour
    return h >= NIGHT_START_HOUR or h < NIGHT_END_HOUR


def _rollover():
    """跨日结算：开新一天的行。"""
    conn = db.get_conn()
    today = _today()
    with db._lock:
        conn.execute(
            "INSERT OR IGNORE INTO active_pull_state (date, count) VALUES (?, 0)", (today,),
        )
        conn.commit()


def _get_state() -> dict:
    conn = db.get_conn()
    today = _today()
    with db._lock:
        row = conn.execute("SELECT * FROM active_pull_state WHERE date = ?", (today,)).fetchone()
    if row is None:
        _rollover()
        with db._lock:
            row = conn.execute("SELECT * FROM active_pull_state WHERE date = ?", (today,)).fetchone()
    if row is None:
        return {"date": today, "count": 0}
    return dict(row)


def daily_limit() -> int:
    """每日撩人次数上限（固定值）。"""
    return DAILY_LIMIT_DEFAULT


def _increment_count():
    conn = db.get_conn()
    today = _today()
    with db._lock:
        conn.execute(
            "INSERT INTO active_pull_state (date, count) VALUES (?, 1) "
            "ON CONFLICT(date) DO UPDATE SET count = count + 1", (today,),
        )
        conn.commit()


def should_pull(last_user_msg_ts, user_id: str = "") -> bool:
    """检查是否满足撩人条件。"""
    if _is_night():
        return False
    st = _get_state()
    if st.get("count", 0) >= daily_limit():
        return False
    # 被惹多了 → 今天更不想主动撩他（有情绪账，先晾他一下）
    try:
        if user_id and liveness.grudge_count_today(user_id) >= 2:
            return False
        if user_id and liveness.emotion_initiative_factor(user_id) < 0.55:
            return False
    except Exception:
        pass
    silence = (time.time() - last_user_msg_ts) / 3600 if last_user_msg_ts else 999
    stage = personality_state.get_stage()
    need = SILENCE_STAGE23_HOURS if stage >= 2 else SILENCE_STAGE1_HOURS
    return silence >= need


def _period_text() -> str:
    h = datetime.now().hour
    if h < 12:
        return "上午"
    if h < 14:
        return "中午"
    if h < 18:
        return "下午"
    if h < 23:
        return "晚上"
    return "深夜"


def _build_persona_prompt(user_id: str = "") -> str:
    from personality import build_system_prompt
    prompt = build_system_prompt() + "\n\n" + personality_state.build_injection()
    if user_id:
        prompt += liveness.build_mood_injection(user_id, None)
    return prompt


async def check_and_run(bot, user_id) -> bool:
    """15 分钟一次的撩人检查；满足条件则生成并发送。bot 提供 _deepseek/_reply_split 等。"""
    try:
        if not should_pull(bot._last_user_msg.get(user_id, 0), user_id):
            return False
        silence = max(1, int((time.time() - bot._last_user_msg.get(user_id, 0)) / 3600))
        prompt = PULL_PROMPT_TEMPLATE.format(
            period=_period_text(),
            scene=live_info.daily_scene(),
            silence_hours=silence,
            stage=personality_state.stage_name(),
        )
        reply = await bot._deepseek.chat(
            [{"role": "system", "content": _build_persona_prompt(user_id)}, {"role": "user", "content": prompt}],
            max_tokens=80,
        )
        reply = (reply or "").strip()
        if not reply:
            return False
        await bot._reply_split("private", user_id, user_id, 0, reply, force_voice=False)
        # 记录：供追问机制使用 + 计入当日次数 + 依赖度
        bot._last_proactive_msg[user_id] = time.time()
        _increment_count()
        personality_state.add_dependency(2)
        logger.info("主动撩人 [%s]: %s", user_id, reply[:30])
        return True
    except Exception as e:
        logger.warning("主动撩人失败 [%s]: %s", user_id, e)
        return False
