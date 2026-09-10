# -*- coding: utf-8 -*-
"""连续生活状态引擎。

维护角色唯一的“当前位置 / 正在做什么 / 何时开始 / 预计何时结束 / 下一项计划”。
状态写入 bot_memory.db，重启后继续；没有明确状态时才按稳定日程兜底。
只保存结构化生活动作，不保存用户或助手原话。
"""
import logging
import random
import re
import sqlite3
import threading
from datetime import datetime, timedelta

from 路径 import data_path

logger = logging.getLogger(__name__)

DB_PATH = data_path("晚晚", "数据", "bot_memory.db")
STATE_KEY = "bot"
_lock = threading.RLock()
_conn = None


ACTIVITY_META = {
    "睡觉": ("宿舍", 8 * 3600),
    "休息": ("宿舍", 60 * 60),
    "洗澡": ("宿舍", 35 * 60),
    "吹头发": ("宿舍", 30 * 60),
    "吃饭": ("食堂", 45 * 60),
    "上课": ("教室", 100 * 60),
    "画画": ("画室", 2 * 3600),
    "赶作业": ("画室", 2 * 3600),
    "写作业": ("宿舍", 90 * 60),
    "自习": ("图书馆", 2 * 3600),
    "打游戏": ("宿舍", 90 * 60),
    "看番": ("宿舍", 70 * 60),
    "散步": ("校园", 45 * 60),
    "出门": ("外面", 60 * 60),
    "回宿舍": ("路上", 30 * 60),
}

ACTION_ALIASES = {
    "睡": "睡觉", "睡觉": "睡觉", "午睡": "睡觉",
    "休息": "休息", "歇会": "休息", "躺着": "休息",
    "洗澡": "洗澡", "洗漱": "洗澡", "吹头发": "吹头发",
    "吃饭": "吃饭", "干饭": "吃饭", "夜宵": "吃饭",
    "上课": "上课", "有课": "上课", "去上课": "上课",
    "画画": "画画", "画稿": "画画", "赶稿": "赶作业", "赶作业": "赶作业",
    "写作业": "写作业", "自习": "自习",
    "打游戏": "打游戏", "打瓦": "打游戏", "上号": "打游戏",
    "看番": "看番", "散步": "散步", "出门": "出门", "回宿舍": "回宿舍",
}

LOCATION_ALIASES = {
    "宿舍": "宿舍", "寝室": "宿舍", "床上": "宿舍",
    "画室": "画室", "教室": "教室", "食堂": "食堂",
    "图书馆": "图书馆", "校园": "校园", "操场": "校园",
    "外面": "外面", "路上": "路上", "家": "家里",
}


def _get_conn() -> sqlite3.Connection:
    global _conn
    with _lock:
        if _conn is None:
            _conn = sqlite3.connect(DB_PATH, check_same_thread=False)
            _conn.row_factory = sqlite3.Row
            _conn.execute("PRAGMA journal_mode=WAL")
            _conn.execute("PRAGMA busy_timeout=5000")
            _conn.execute(
                "CREATE TABLE IF NOT EXISTS life_state ("
                " state_key TEXT PRIMARY KEY, location TEXT NOT NULL, activity TEXT NOT NULL,"
                " detail TEXT NOT NULL DEFAULT '', started_at REAL NOT NULL,"
                " expected_end_at REAL NOT NULL, source TEXT NOT NULL,"
                " updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)"
            )
            _conn.execute(
                "CREATE TABLE IF NOT EXISTS life_plan ("
                " id INTEGER PRIMARY KEY AUTOINCREMENT, activity TEXT NOT NULL,"
                " location TEXT NOT NULL, start_at REAL NOT NULL, end_at REAL NOT NULL,"
                " status TEXT NOT NULL DEFAULT 'scheduled', source TEXT NOT NULL,"
                " created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)"
            )
            _conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_life_plan_due "
                "ON life_plan(status, start_at)"
            )
            _conn.commit()
    return _conn


def _row_dict(row) -> dict:
    return dict(row) if row else {}


def _put_state(conn, location: str, activity: str, detail: str,
               started_at: float, expected_end_at: float, source: str):
    conn.execute(
        "INSERT INTO life_state "
        "(state_key, location, activity, detail, started_at, expected_end_at, source) "
        "VALUES (?, ?, ?, ?, ?, ?, ?) "
        "ON CONFLICT(state_key) DO UPDATE SET "
        "location=excluded.location, activity=excluded.activity, detail=excluded.detail, "
        "started_at=excluded.started_at, expected_end_at=excluded.expected_end_at, "
        "source=excluded.source, updated_at=CURRENT_TIMESTAMP",
        (STATE_KEY, location, activity, detail, started_at, expected_end_at, source),
    )


def _day_seed(now: datetime, slot: str) -> random.Random:
    return random.Random(f"{now:%Y-%m-%d}:{slot}")


def _schedule_snapshot(now: datetime) -> dict:
    """生成当天稳定的基础作息；相同时段反复读取永远得到同一个状态。"""
    minute = now.hour * 60 + now.minute
    weekend = now.weekday() >= 5
    if minute < 390:
        slot, start, end = "sleep", 0, 390
        variants = [("宿舍", "睡觉", "在宿舍睡觉")]
    elif minute < 480:
        slot, start, end = "morning", 390, 480
        variants = [("宿舍", "洗漱", "刚起床，在宿舍洗漱收拾")]
    elif minute < 720:
        slot, start, end = "am", 480, 720
        variants = (
            [("宿舍", "休息", "周末在宿舍慢慢醒神"),
             ("校园", "散步", "周末出门走走")]
            if weekend else
            [("教室", "上课", "上午在教室上课"),
             ("画室", "画画", "上午在画室上专业课"),
             ("图书馆", "自习", "上午在图书馆查资料")]
        )
    elif minute < 810:
        slot, start, end = "noon", 720, 810
        variants = [("食堂", "吃饭", "中午在吃饭"),
                    ("宿舍", "休息", "吃完午饭回宿舍歇着")]
    elif minute < 1050:
        slot, start, end = "pm", 810, 1050
        variants = (
            [("画室", "画画", "下午在画室画自己的东西"),
             ("宿舍", "写作业", "下午在宿舍做作业"),
             ("外面", "出门", "下午出门逛一会儿")]
            if weekend else
            [("画室", "赶作业", "下午在画室赶作业"),
             ("教室", "上课", "下午在教室上课"),
             ("图书馆", "自习", "下午在图书馆自习")]
        )
    elif minute < 1140:
        slot, start, end = "dinner", 1050, 1140
        variants = [("食堂", "吃饭", "傍晚在食堂吃饭"),
                    ("路上", "回宿舍", "傍晚正往宿舍走")]
    elif minute < 1290:
        slot, start, end = "evening", 1140, 1290
        variants = [("宿舍", "写作业", "晚上在宿舍弄作业"),
                    ("宿舍", "打游戏", "晚上在宿舍打会儿游戏"),
                    ("宿舍", "看番", "晚上窝在宿舍看番")]
    elif minute < 1335:
        slot, start, end = "wash", 1290, 1335
        variants = [("宿舍", "洗澡", "在宿舍洗澡收拾"),
                    ("宿舍", "休息", "洗漱完在床上歇着")]
    elif minute < 1380:
        slot, start, end = "winddown", 1335, 1380
        variants = [("宿舍", "休息", "在床上刷会儿手机，慢慢准备睡觉"),
                    ("宿舍", "看番", "睡前看一会儿番")]
    else:
        slot, start, end = "night", 1380, 1440
        variants = [("宿舍", "休息", "深夜窝在床上准备睡觉")]
    location, activity, detail = _day_seed(now, slot).choice(variants)
    day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    return {
        "state_key": STATE_KEY,
        "location": location,
        "activity": activity,
        "detail": detail,
        "started_at": (day_start + timedelta(minutes=start)).timestamp(),
        "expected_end_at": (day_start + timedelta(minutes=end)).timestamp(),
        "source": f"schedule:{now:%Y-%m-%d}:{slot}",
    }


def _transition_after(state: dict, now_ts: float) -> dict:
    """明确活动刚结束后的短过渡，避免结束瞬间直接跳到无关日程。"""
    activity = state.get("activity", "")
    location = state.get("location") or "宿舍"
    if activity == "睡觉":
        activity_now, detail = "刚醒", "刚睡醒，还在缓神"
    elif activity == "回宿舍":
        location, activity_now, detail = "宿舍", "休息", "刚回宿舍，正歇着"
    elif activity in ("洗澡", "洗漱"):
        activity_now, detail = "吹头发", "刚洗漱完，在收拾头发"
    elif activity in ("上课", "自习"):
        activity_now, detail = "休息", "刚忙完，正歇一会儿"
    else:
        activity_now, detail = "休息", f"刚结束{activity}，在缓一会儿"
    end_at = float(state.get("expected_end_at") or now_ts)
    return {
        "state_key": STATE_KEY,
        "location": location,
        "activity": activity_now,
        "detail": detail,
        "started_at": end_at,
        "expected_end_at": end_at + 20 * 60,
        "source": "transition",
    }


def get_snapshot(now: datetime = None) -> dict:
    """取得并推进当前生活状态；计划到点自动开始，活动到时自动结束。"""
    now = now or datetime.now()
    now_ts = now.timestamp()
    conn = _get_conn()
    with _lock:
        # 清掉已经错过的计划，再激活当前时间覆盖到的最新一项。
        conn.execute(
            "UPDATE life_plan SET status='completed' "
            "WHERE status IN ('scheduled','active') AND end_at <= ?", (now_ts,)
        )
        plan = conn.execute(
            "SELECT * FROM life_plan WHERE status IN ('scheduled','active') "
            "AND start_at <= ? AND end_at > ? ORDER BY start_at DESC LIMIT 1",
            (now_ts, now_ts),
        ).fetchone()
        if plan:
            conn.execute("UPDATE life_plan SET status='active' WHERE id=?", (plan["id"],))
            source = f"plan:{plan['id']}"
            row = conn.execute(
                "SELECT * FROM life_state WHERE state_key=?", (STATE_KEY,)
            ).fetchone()
            if not row or row["source"] != source:
                _put_state(conn, plan["location"], plan["activity"],
                           f"正在{plan['activity']}", plan["start_at"], plan["end_at"], source)
            conn.commit()
            return _row_dict(conn.execute(
                "SELECT * FROM life_state WHERE state_key=?", (STATE_KEY,)
            ).fetchone())

        row = conn.execute(
            "SELECT * FROM life_state WHERE state_key=?", (STATE_KEY,)
        ).fetchone()
        state = _row_dict(row)
        if state and float(state.get("expected_end_at") or 0) > now_ts:
            return state
        # 明确说过或计划产生的活动结束后，保留 20 分钟自然过渡。
        if state and (str(state.get("source", "")).startswith(("assistant", "plan:"))):
            ended = float(state.get("expected_end_at") or 0)
            if ended <= now_ts < ended + 20 * 60:
                state = _transition_after(state, now_ts)
                _put_state(conn, state["location"], state["activity"], state["detail"],
                           state["started_at"], state["expected_end_at"], state["source"])
                conn.commit()
                return state

        state = _schedule_snapshot(now)
        _put_state(conn, state["location"], state["activity"], state["detail"],
                   state["started_at"], state["expected_end_at"], state["source"])
        conn.commit()
        return state


def set_activity(activity: str, location: str = "", duration_seconds: int = 0,
                 detail: str = "", source: str = "assistant", now: datetime = None) -> dict:
    """明确切换当前状态。"""
    now = now or datetime.now()
    canonical = ACTION_ALIASES.get(activity, activity or "休息")
    default_location, default_duration = ACTIVITY_META.get(canonical, ("宿舍", 60 * 60))
    location = LOCATION_ALIASES.get(location, location) or default_location
    duration = max(5 * 60, int(duration_seconds or default_duration))
    state = {
        "state_key": STATE_KEY,
        "location": location,
        "activity": canonical,
        "detail": detail or f"正在{canonical}",
        "started_at": now.timestamp(),
        "expected_end_at": now.timestamp() + duration,
        "source": source,
    }
    conn = _get_conn()
    with _lock:
        if source.startswith("assistant"):
            # 她明确说出的新状态优先于旧计划：视为计划已完成/被实际行动取代，
            # 否则下一次读取会被仍处于 active 的旧计划重新覆盖。
            conn.execute("UPDATE life_plan SET status='completed' WHERE status='active'")
        _put_state(conn, location, canonical, state["detail"], state["started_at"],
                   state["expected_end_at"], source)
        conn.commit()
    return state


def schedule_plan(activity: str, start_at: datetime, duration_seconds: int = 0,
                  location: str = "", source: str = "assistant") -> int:
    """加入结构化计划；相近时间的同类计划自动去重。"""
    canonical = ACTION_ALIASES.get(activity, activity)
    default_location, default_duration = ACTIVITY_META.get(canonical, ("宿舍", 60 * 60))
    location = LOCATION_ALIASES.get(location, location) or default_location
    duration = max(5 * 60, int(duration_seconds or default_duration))
    start_ts = start_at.timestamp()
    end_ts = start_ts + duration
    conn = _get_conn()
    with _lock:
        existing = conn.execute(
            "SELECT id FROM life_plan WHERE status IN ('scheduled','active') "
            "AND activity=? AND ABS(start_at - ?) < 1800 ORDER BY id DESC LIMIT 1",
            (canonical, start_ts),
        ).fetchone()
        if existing:
            return int(existing["id"])
        cur = conn.execute(
            "INSERT INTO life_plan(activity, location, start_at, end_at, source) "
            "VALUES (?, ?, ?, ?, ?)",
            (canonical, location, start_ts, end_ts, source),
        )
        conn.commit()
        return int(cur.lastrowid)


def get_next_plan(now: datetime = None) -> dict:
    now_ts = (now or datetime.now()).timestamp()
    conn = _get_conn()
    with _lock:
        return _row_dict(conn.execute(
            "SELECT * FROM life_plan WHERE status='scheduled' AND start_at > ? "
            "ORDER BY start_at LIMIT 1", (now_ts,)
        ).fetchone())


def _canonical_action(raw: str) -> str:
    return ACTION_ALIASES.get((raw or "").strip(), (raw or "").strip())


def _location_for_action(action: str) -> str:
    return ACTIVITY_META.get(action, ("宿舍", 3600))[0]


def _near_plan_time(prefix: str, now: datetime) -> datetime:
    offsets = {"马上": 5, "等下": 15, "一会儿": 15, "一会": 15,
               "待会儿": 20, "待会": 20, "过会儿": 25, "过会": 25}
    return now + timedelta(minutes=offsets.get(prefix, 15))


def observe_assistant_reply(text: str, now: datetime = None) -> dict:
    """从她明确说出口的当前动作/承诺中更新生活线。

    只识别带“我正在/我刚/我等下”等强时态标记的结构，不把普通场景词当事实。
    返回本次识别结果，便于日志和测试。
    """
    now = now or datetime.now()
    value = re.sub(r"\s+", "", text or "")
    result = {"state": "", "plan": ""}
    if not value:
        return result

    action_words = "|".join(sorted((re.escape(k) for k in ACTION_ALIASES), key=len, reverse=True))
    # 近期待办：“我等下去洗澡”“我待会写作业”。
    plan_match = re.search(
        rf"(?<!你)(?:我)?(?:可)?(马上|等下|待会儿?|一会儿?|过会儿?).{{0,6}}?({action_words})", value
    )
    if plan_match:
        action = _canonical_action(plan_match.group(2))
        schedule_plan(action, _near_plan_time(plan_match.group(1), now), source="assistant")
        result["plan"] = action
    else:
        # 明天的明确承诺按动作安排到合理时刻。
        tomorrow_match = re.search(rf"我?明天.{{0,10}}?({action_words})", value)
        if tomorrow_match:
            action = _canonical_action(tomorrow_match.group(1))
            hour = 8 if action == "上课" else (12 if action == "吃饭" else 10)
            start = (now + timedelta(days=1)).replace(hour=hour, minute=0, second=0, microsecond=0)
            schedule_plan(action, start, source="assistant")
            result["plan"] = action

    # 完成态优先，避免“刚洗完”被误记成仍在洗。
    completed_patterns = (
        (r"我?(?:刚|才)洗完(?:澡)?", "吹头发", "宿舍", "刚洗完澡，在收拾头发"),
        (r"我?(?:刚|才)下课", "休息", "校园", "刚下课，正在缓一会儿"),
        (r"我?(?:刚|才)吃完", "休息", "宿舍", "刚吃完，在歇着"),
        (r"我?(?:刚|才)(?:画完|写完|忙完|收工)", "休息", "宿舍", "刚忙完，在歇着"),
        (r"我?(?:刚|才)(?:醒|睡醒)", "刚醒", "宿舍", "刚睡醒，还在缓神"),
        (r"我?(?:刚|才)(?:到|回到?)宿舍", "休息", "宿舍", "刚回宿舍，正歇着"),
    )
    for pattern, action, location, detail in completed_patterns:
        if re.search(pattern, value):
            set_activity(action, location, 40 * 60, detail, "assistant:completed", now)
            result["state"] = action
            return result

    # 强当前时态的动作。
    current_match = re.search(
        rf"我(?:现在|这会儿|这会|刚好)?(?:正|正在|还在)({action_words})", value
    )
    if current_match:
        action = _canonical_action(current_match.group(1))
        set_activity(action, _location_for_action(action), source="assistant:current", now=now)
        result["state"] = action
        return result

    # 强当前时态的地点；活动沿用与地点相容的朴素描述。
    location_words = "|".join(sorted((re.escape(k) for k in LOCATION_ALIASES), key=len, reverse=True))
    location_match = re.search(
        rf"我(?:现在|这会儿|这会|刚好)?(?:正|正在|还)?在({location_words})", value
    )
    if location_match:
        location = LOCATION_ALIASES[location_match.group(1)]
        action = {"画室": "画画", "教室": "上课", "食堂": "吃饭",
                  "图书馆": "自习", "路上": "回宿舍"}.get(location, "休息")
        set_activity(action, location, detail=f"在{location}{action}",
                     source="assistant:location", now=now)
        result["state"] = action
    return result


def _relative_duration(seconds: float) -> str:
    minutes = max(1, int(seconds / 60))
    if minutes < 60:
        return f"约{minutes}分钟"
    hours = minutes / 60
    return f"约{hours:.1f}小时".replace(".0", "")


def current_activity_text(now: datetime = None) -> str:
    state = get_snapshot(now)
    return state.get("detail") or f"在{state.get('location', '宿舍')}{state.get('activity', '休息')}"


def prompt_injection(now: datetime = None) -> str:
    """供对话模型使用的生活事实锚点。"""
    now = now or datetime.now()
    state = get_snapshot(now)
    remaining = max(0, float(state.get("expected_end_at") or 0) - now.timestamp())
    parts = [
        f"你现在在{state['location']}，正在{state['activity']}（{state['detail']}），"
        f"预计还会持续{_relative_duration(remaining)}。"
    ]
    plan = get_next_plan(now)
    if plan:
        start = datetime.fromtimestamp(float(plan["start_at"]))
        when = start.strftime("明天%H:%M") if start.date() > now.date() else start.strftime("%H:%M")
        parts.append(f"下一项已确定的安排是{when}{plan['activity']}。")
    parts.append(
        "这条生活线是事实锚点：不要说自己同时在另一个地点或正在做互斥的事；"
        "除非对方问起或话题自然相关，否则不用主动汇报。"
        "如果你明确说“我正在做某事”或“我等下要做某事”，系统会把它接进后续生活线，"
        "所以只承诺你确实准备延续的安排。"
    )
    return "\n（【连续生活状态】" + "".join(parts) + "）"


def status_line(now: datetime = None) -> str:
    """GUI 用的一行生活线摘要。"""
    now = now or datetime.now()
    state = get_snapshot(now)
    end = datetime.fromtimestamp(float(state["expected_end_at"]))
    current = f"{state['location']} · {state['activity']}（预计到 {end:%H:%M}）"
    plan = get_next_plan(now)
    if not plan:
        return f"生活线：{current}　|　下一项：暂无明确安排"
    start = datetime.fromtimestamp(float(plan["start_at"]))
    when = start.strftime("明天 %H:%M") if start.date() > now.date() else start.strftime("%H:%M")
    return f"生活线：{current}　|　下一项：{when} {plan['activity']}"
