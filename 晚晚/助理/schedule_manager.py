# -*- coding: utf-8 -*-
"""智能日程：从自然语言里提取日程、按优先级规划当天、生成到点提醒候选。

分工：
- 本模块只产出结构化日程与"该不该提醒、什么优先级"，**不生成任何 QQ 文案**；
  文案仍由现有人设链路生成（提醒走主动消息的人设 prompt）。
- 用户明确告知的事项（source=user）优先级高于 AI 自动安排的任务（source=ai），
  AI 任务可以移动/推迟，用户固定事项不允许被覆盖。
- 解析优先用本地规则（快、零成本、可测），本地解析不出时间时才调用大模型兜底，
  且模型结果必须通过本地校验才会入库。
"""
import calendar
import json
import logging
import re
from datetime import datetime, timedelta

import assistant_db as adb

logger = logging.getLogger(__name__)

# 统一优先级（与主动消息调度表一致）
PRIORITY_CRITICAL_TODAY = 100   # 当天的考试 / 截止日期
PRIORITY_EXAM = 95              # 临近的考试 / 截止日期
PRIORITY_SCHEDULE = 85          # 用户明确的固定日程
PRIORITY_STUDY_TASK = 80        # 学习任务
PRIORITY_TASK_FOLLOWUP = 60     # 任务追问
PRIORITY_PROACTIVE = 30         # 普通主动消息
PRIORITY_MURMUR = 10            # 碎碎念

REMIND_WINDOW_MINUTES = 10      # 提醒扫描窗口

EXAM_WORDS = ("考试", "考级", "四级", "六级", "考研", "雅思", "托福", "答辩", "截止", "ddl", "due", "交作业", "面试")
FIXED_WORDS = ("课", "上课", "开会", "会议", "上班", "值班", "面试", "约", "看医生", "体检", "车票", "机票", "航班")
STUDY_WORDS = ("学习", "背单词", "学英语", "刷题", "复习", "预习", "写作业", "看书", "练听力", "做阅读")
FUTURE_WORDS = ("明天", "后天", "大后天", "下周", "周", "星期", "今晚", "一会儿", "等下", "之后", "以后", "每天", "要去", "要", "安排", "提醒")
PAST_WORDS = ("昨天", "前天", "上周", "刚才", "已经")

WEEKDAY_MAP = {"一": 0, "二": 1, "三": 2, "四": 3, "五": 4, "六": 5, "日": 6, "天": 6,
               "1": 0, "2": 1, "3": 2, "4": 3, "5": 4, "6": 5, "7": 6}

_CN_DIGITS = {"零": 0, "〇": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4,
              "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}


def _cn_number(text: str):
    """中文数字 → int（支持 三 / 十 / 十二 / 二十 / 二十三）。"""
    text = (text or "").strip()
    if not text:
        return None
    if text.isdigit():
        return int(text)
    if "十" in text:
        left, _, right = text.partition("十")
        tens = _CN_DIGITS.get(left, 1) if left else 1
        ones = _CN_DIGITS.get(right, 0) if right else 0
        return tens * 10 + ones
    total = 0
    for ch in text:
        if ch not in _CN_DIGITS:
            return None
        total = total * 10 + _CN_DIGITS[ch] if total and _CN_DIGITS[ch] < 10 else _CN_DIGITS[ch]
    return total


_TIME_RE = re.compile(
    r"(?:(凌晨|早上|上午|中午|下午|傍晚|晚上|夜里|今晚|明晚)\s*)?"
    r"(\d{1,2}|[零〇一二两三四五六七八九十]{1,3})\s*[:：点时]\s*"
    r"(\d{1,2}|[零〇一二两三四五六七八九十]{1,3})?\s*(半|一刻|三刻)?"
)
_DATE_OFFSET_RE = re.compile(r"(今天|今晚|明天|明晚|后天|大后天|今天早上|今天下午|今天上午)")
_WEEKDAY_RE = re.compile(r"(下{0,1})(?:周|星期|礼拜)\s*([一二三四五六日天1-7])")
_ABS_DATE_RE = re.compile(r"(\d{1,2})\s*月\s*(\d{1,2})\s*[日号]")
_REPEAT_RE = re.compile(r"(每天|每晚|每天晚上|每周|每星期|以后每天|天天)")


# =============================================================================
# 解析
# =============================================================================

def looks_like_schedule(text: str) -> bool:
    """粗筛：像不像在说一件有时间的事（避免把普通聊天误判成日程）。"""
    text = (text or "").strip()
    if len(text) < 3 or len(text) > 120:
        return False
    if any(word in text for word in PAST_WORDS):
        return False
    # 问句基本不是下日程（"明天几点上课"是询问，交给普通对话）
    if re.search(r"(几点|什么时候|吗[?？]?$|[?？]$)", text) and not any(
            word in text for word in ("提醒我", "帮我记", "记一下")):
        return False
    has_time = bool(_TIME_RE.search(text) or _DATE_OFFSET_RE.search(text)
                    or _WEEKDAY_RE.search(text) or _ABS_DATE_RE.search(text)
                    or _REPEAT_RE.search(text))
    if not has_time:
        return False
    return any(word in text for word in EXAM_WORDS + FIXED_WORDS + STUDY_WORDS
               + ("有", "要", "去", "做", "见", "交"))


def _resolve_day(text: str, now: datetime):
    """解析日期部分 → date；解析不出返回 None。"""
    if "大后天" in text:
        return now.date() + timedelta(days=3)
    if "后天" in text:
        return now.date() + timedelta(days=2)
    if "明天" in text or "明晚" in text:
        return now.date() + timedelta(days=1)
    if "今天" in text or "今晚" in text:
        return now.date()
    m = _WEEKDAY_RE.search(text)
    if m:
        next_week = bool(m.group(1))
        target = WEEKDAY_MAP.get(m.group(2))
        if target is not None:
            delta = (target - now.weekday()) % 7
            if next_week:
                delta = delta + 7 if delta else 7
            elif delta == 0 and _time_mentions_past(text, now):
                delta = 7
            return now.date() + timedelta(days=delta)
    m = _ABS_DATE_RE.search(text)
    if m:
        month, day = int(m.group(1)), int(m.group(2))
        year = now.year
        if month < now.month:
            year += 1
        try:
            return datetime(year, month, day).date()
        except ValueError:
            return None
    return None


def _time_mentions_past(text: str, now: datetime) -> bool:
    """只有星期没有日期时：若该时刻今天已过，则理解为下周。"""
    hour, minute, _ = _resolve_time(text)
    if hour is None:
        return False
    return (hour, minute) < (now.hour, now.minute)


def _resolve_time(text: str):
    """解析时刻 → (hour, minute, matched_span)；解析不出返回 (None, None, None)。"""
    m = _TIME_RE.search(text)
    if not m:
        return None, None, None
    period, hour_text, minute_text, half = m.groups()
    hour = _cn_number(hour_text)
    if hour is None or hour > 24:
        return None, None, None
    minute = 0
    if minute_text:
        minute = _cn_number(minute_text) or 0
        if minute > 59:
            return None, None, None
    elif half == "半":
        minute = 30
    elif half == "一刻":
        minute = 15
    elif half == "三刻":
        minute = 45
    # 口语折算：下午/晚上 X 点 → +12（12 点除外）
    if period in ("下午", "傍晚", "晚上", "夜里", "今晚", "明晚") and hour < 12:
        hour += 12
    elif period in ("中午",) and hour < 11:
        hour += 12
    elif period in ("凌晨",) and hour == 12:
        hour = 0
    if hour == 24:
        hour = 0
    return hour % 24, minute % 60, m.span()


def _resolve_duration(text: str):
    """"两个小时"/"1.5 小时"/"30 分钟" → 分钟数。"""
    m = re.search(r"(\d+(?:\.\d+)?|[一二两三四五六七八九十]+)\s*(个)?\s*(小时|钟头|h|H)", text)
    if m:
        value = _cn_number(m.group(1))
        if value:
            return int(float(value) * 60)
    m = re.search(r"(\d+|[一二两三四五六七八九十]+)\s*(分钟|分)", text)
    if m:
        value = _cn_number(m.group(1))
        if value:
            return int(value)
    return 0


def _resolve_repeat(text: str) -> str:
    if any(word in text for word in ("每天", "每晚", "天天", "以后每天")):
        return "daily"
    if "每周" in text or "每星期" in text:
        days = [str(WEEKDAY_MAP[ch]) for ch in re.findall(r"[一二三四五六日天1-7]", text)
                if ch in WEEKDAY_MAP]
        return "weekly:" + ",".join(sorted(set(days))) if days else "weekly"
    return ""


def _clean_title(text: str) -> str:
    """去掉时间表达，剩下的就是事项本身。"""
    title = text
    for pattern in (_DATE_OFFSET_RE, _WEEKDAY_RE, _ABS_DATE_RE, _REPEAT_RE):
        title = pattern.sub("", title)
    span = _TIME_RE.search(title)
    if span:
        title = title[:span.start()] + title[span.end():]
    for word in ("提醒我", "帮我记一下", "帮我记", "记一下", "帮我", "我要", "我", "要", "去",
                 "记得", "安排", "在", "的", "了", "，", ",", "。", "、"):
        title = title.replace(word, " " if word not in ("我", "的") else "")
    return re.sub(r"\s+", " ", title).strip(" ~～·-—")[:40]


def _priority_for(title: str, day, now: datetime, source: str = "user") -> int:
    if any(word in title for word in EXAM_WORDS):
        return PRIORITY_CRITICAL_TODAY if day == now.date() else PRIORITY_EXAM
    if source == "ai":
        return PRIORITY_STUDY_TASK
    return PRIORITY_SCHEDULE


def parse_text(text: str, now: datetime = None) -> dict:
    """本地规则解析；解析不出时间返回 {}。"""
    now = now or datetime.now()
    text = (text or "").strip()
    if not text:
        return {}
    day = _resolve_day(text, now)
    hour, minute, _ = _resolve_time(text)
    repeat = _resolve_repeat(text)
    if day is None and repeat:
        day = now.date()
    if day is None and hour is None:
        return {}
    if day is None:
        # 只说了时刻：已过则理解为明天
        day = now.date() if (hour, minute) >= (now.hour, now.minute) else now.date() + timedelta(days=1)
    title = _clean_title(text)
    if len(title) < 2:
        return {}
    start = None
    end = None
    duration = _resolve_duration(text)
    if hour is not None:
        start_dt = datetime.combine(day, datetime.min.time()).replace(hour=hour, minute=minute)
        start = start_dt.strftime("%Y-%m-%d %H:%M:%S")
        if duration:
            end = (start_dt + timedelta(minutes=duration)).strftime("%Y-%m-%d %H:%M:%S")
    return {
        "title": title,
        "start_time": start or "",
        "end_time": end or "",
        "repeat_rule": repeat,
        "day": day.strftime("%Y-%m-%d"),
        "all_day": start is None,
        "priority": _priority_for(title, day, now),
        "source": "user",
        "parsed_by": "rule",
    }


DAY_JSON_INSTRUCTION = (
    "你是日程解析器。请把用户这句话解析成 JSON，只输出 JSON，不要解释。字段：\n"
    '{"title": 事项名称（简短）, "date": "YYYY-MM-DD", "time": "HH:MM" 或 "", '
    '"duration_minutes": 数字或 0, "repeat": ""/"daily"/"weekly:0,2", "is_exam": true/false}\n'
    "今天是 {today}（星期{weekday}），现在 {now}。"
)


async def parse_with_llm(text: str, llm_call, now: datetime = None) -> dict:
    """本地解析失败时用大模型兜底；结果必须通过本地校验。"""
    now = now or datetime.now()
    instruction = DAY_JSON_INSTRUCTION.format(
        today=now.strftime("%Y-%m-%d"), weekday="一二三四五六日"[now.weekday()],
        now=now.strftime("%H:%M"),
    )
    try:
        raw = await llm_call(
            [{"role": "system", "content": instruction},
             {"role": "user", "content": text}],
            temperature=0.1, max_tokens=200, disable_thinking=True,
        )
    except Exception as exc:
        logger.warning("日程大模型解析失败: %s", exc)
        return {}
    data = _extract_json(raw)
    if not isinstance(data, dict):
        return {}
    return _validate_llm_result(data, text, now)


def _extract_json(text: str):
    text = text or ""
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        return json.loads(text[start:end + 1])
    except (ValueError, TypeError):
        return None


def _validate_llm_result(data: dict, text: str, now: datetime) -> dict:
    """模型结果只做候选，日期/时间必须能被本地解析且落在合理范围。"""
    title = str(data.get("title") or "").strip()[:40]
    date_text = str(data.get("date") or "").strip()
    time_text = str(data.get("time") or "").strip()
    if not title or not date_text:
        return {}
    try:
        day = datetime.strptime(date_text, "%Y-%m-%d").date()
    except ValueError:
        return {}
    if not (now.date() - timedelta(days=1) <= day <= now.date() + timedelta(days=366)):
        return {}
    start = ""
    end = ""
    hour = minute = None
    if time_text:
        try:
            hm = datetime.strptime(time_text, "%H:%M")
            hour, minute = hm.hour, hm.minute
        except ValueError:
            hour = minute = None
    if hour is not None:
        start_dt = datetime.combine(day, datetime.min.time()).replace(hour=hour, minute=minute)
        start = start_dt.strftime("%Y-%m-%d %H:%M:%S")
        try:
            duration = int(data.get("duration_minutes") or 0)
        except (TypeError, ValueError):
            duration = 0
        if 0 < duration <= 12 * 60:
            end = (start_dt + timedelta(minutes=duration)).strftime("%Y-%m-%d %H:%M:%S")
    repeat = str(data.get("repeat") or "").strip()
    if repeat and not re.match(r"^(daily|weekly(:\d(,\d)*)?)$", repeat):
        repeat = "daily" if repeat.startswith("daily") else ""
    is_exam = bool(data.get("is_exam")) or any(word in title for word in EXAM_WORDS)
    return {
        "title": title, "start_time": start, "end_time": end, "repeat_rule": repeat,
        "day": day.strftime("%Y-%m-%d"), "all_day": not start,
        "priority": (PRIORITY_CRITICAL_TODAY if (is_exam and day == now.date())
                     else PRIORITY_EXAM if is_exam else PRIORITY_SCHEDULE),
        "source": "user", "parsed_by": "llm",
    }


async def create_from_text(user_id: str, text: str, llm_call=None, now: datetime = None):
    """把一句话变成一条日程；成功返回 {"id", "schedule"}，失败返回 None。"""
    now = now or datetime.now()
    parsed = parse_text(text, now)
    if not parsed and llm_call is not None:
        parsed = await parse_with_llm(text, llm_call, now)
    if not parsed:
        return None
    remind_before = 30 if parsed["priority"] >= PRIORITY_EXAM else 15
    schedule_id = adb.add_schedule(
        user_id, parsed["title"], start_time=parsed["start_time"], end_time=parsed["end_time"],
        description=text[:200], repeat_rule=parsed["repeat_rule"],
        priority=parsed["priority"], source="user", remind_before=remind_before,
    )
    if schedule_id is None:
        return None
    return {"id": schedule_id, "schedule": adb.get_schedule(schedule_id), "parsed": parsed}


# =============================================================================
# 查询 / 规划 / 提醒
# =============================================================================

def _occurrence_start(row: dict, day: datetime):
    """日程在指定日期是否发生：返回当天该日程的开始时间（datetime）或 None。"""
    start_raw = (row.get("start_time") or "").strip()
    weekday = "一二三四五六日"[day.weekday()]
    del weekday
    repeat = (row.get("repeat_rule") or "").strip()
    if start_raw:
        try:
            start_dt = datetime.strptime(start_raw, "%Y-%m-%d %H:%M:%S")
        except ValueError:
            return None
    else:
        start_dt = None
    if not repeat:
        if start_dt and start_dt.date() == day.date():
            return start_dt
        return None
    if not start_dt:
        return None
    clock = start_dt.time()
    if repeat == "daily":
        return datetime.combine(day.date(), clock)
    if repeat.startswith("weekly"):
        days = [int(x) for x in re.findall(r"\d", repeat.split(":", 1)[1])] if ":" in repeat else []
        if not days or day.weekday() in days:
            return datetime.combine(day.date(), clock)
    return None


def plan_day(user_id: str, day: datetime = None, tasks=None) -> list:
    """当日计划：用户固定事项（不可移动）+ AI 任务（可移动）。

    固定事项永远排在前面且不被 AI 覆盖；AI 任务只填充空档。
    """
    day = day or datetime.now()
    fixed = []
    for row in adb.list_schedules(user_id, since=day.strftime("%Y-%m-%d"),
                                  until=day.strftime("%Y-%m-%d"),
                                  statuses=("pending", "doing", "partial", "postponed")):
        start = _occurrence_start(row, day)
        if start is None:
            continue
        fixed.append({
            "kind": "user", "id": row["id"], "title": row["title"],
            "start": start.strftime("%Y-%m-%d %H:%M:%S"),
            "end": row.get("end_time") or "", "source": "user",
            "priority": int(row.get("priority") or PRIORITY_SCHEDULE),
            "status": row.get("status") or "pending", "movable": False,
            "repeat_rule": row.get("repeat_rule") or "",
        })
    if tasks is None:
        tasks = adb.list_tasks(user_id, statuses=("pending", "doing", "partial"), day=day.strftime("%Y-%m-%d"))
    ai_items = []
    for row in tasks:
        ai_items.append({
            "kind": "ai", "id": row["id"], "title": row["title"],
            "start": row.get("due_time") or "", "end": "",
            "source": row.get("source") or "ai",
            "priority": int(row.get("priority") or PRIORITY_STUDY_TASK),
            "status": row.get("status") or "pending",
            "movable": bool(row.get("movable", 1)),
            "planned_minutes": int(row.get("planned_minutes") or 0),
        })
    fixed.sort(key=lambda item: (item["start"], -item["priority"]))
    # 学习任务优先安排到"他更容易学习"的时段后面；这里至少保证不与固定事项同刻
    occupied = {item["start"] for item in fixed if item["start"]}
    movable, pinned = [], []
    for item in ai_items:
        if item["start"] and item["start"] in occupied:
            item["conflict"] = True
            movable.append(item)
        elif item["start"]:
            pinned.append(item)
        else:
            movable.append(item)
    return fixed + pinned + movable


def reminder_candidates(user_id: str, now: datetime = None, window_minutes: int = REMIND_WINDOW_MINUTES) -> list:
    """到点该提醒的日程（去重：同一日程同一时刻只提醒一次）。"""
    now = now or datetime.now()
    out = []
    for row in adb.list_schedules(user_id, since=(now - timedelta(days=1)).strftime("%Y-%m-%d"),
                                  until=(now + timedelta(days=2)).strftime("%Y-%m-%d"),
                                  statuses=("pending", "doing", "postponed")):
        start = _occurrence_start(row, now)
        if start is None:
            continue
        remind_at = start - timedelta(minutes=int(row.get("remind_before") or 15))
        if not (remind_at <= now <= start + timedelta(minutes=window_minutes)):
            continue
        reminded_at = (row.get("reminded_at") or "").strip()
        if reminded_at:
            try:
                if abs((datetime.strptime(reminded_at, "%Y-%m-%d %H:%M:%S") - start).total_seconds()) < 6 * 3600:
                    continue  # 该次已经提醒过
            except ValueError:
                pass
        out.append({
            "id": row["id"], "title": row["title"], "start": start.strftime("%Y-%m-%d %H:%M:%S"),
            "priority": int(row.get("priority") or PRIORITY_SCHEDULE),
            "repeat_rule": row.get("repeat_rule") or "",
            "minutes_until": int((start - now).total_seconds() // 60),
        })
    out.sort(key=lambda item: -item["priority"])
    return out


def claim_reminder(schedule_id: int, now: datetime = None) -> bool:
    """标记已提醒（防止同一日程反复轰炸）。"""
    now = now or datetime.now()
    return adb.mark_schedule_reminded(schedule_id, now.strftime("%Y-%m-%d %H:%M:%S"))


def overview(user_id: str, now: datetime = None, days: int = 7) -> dict:
    """面板用：今日 / 明日 / 本周 / 未来重要事项（区分用户安排与 AI 安排）。"""
    now = now or datetime.now()
    today = now.strftime("%Y-%m-%d")
    tomorrow = (now + timedelta(days=1)).strftime("%Y-%m-%d")
    week_end = (now + timedelta(days=days)).strftime("%Y-%m-%d")
    rows = adb.list_schedules(user_id, since=(now - timedelta(days=1)).strftime("%Y-%m-%d"),
                              until=week_end,
                              statuses=("pending", "doing", "partial", "postponed"))

    def _items(day_text):
        items = []
        for row in rows:
            try:
                day_dt = datetime.strptime(day_text, "%Y-%m-%d")
            except ValueError:
                continue
            start = _occurrence_start(row, day_dt)
            if start is None:
                continue
            items.append({
                "id": row["id"], "title": row["title"],
                "time": start.strftime("%H:%M") if row.get("start_time") else "全天",
                "start": start.strftime("%Y-%m-%d %H:%M:%S"),
                "source": row.get("source") or "user",
                "status": row.get("status") or "pending",
                "priority": int(row.get("priority") or 50),
                "repeat_rule": row.get("repeat_rule") or "",
                "movable": (row.get("source") or "user") == "ai",
            })
        items.sort(key=lambda item: (item["time"], -item["priority"]))
        return items

    important = [item for item in _items(week_end) if item["priority"] >= PRIORITY_EXAM]
    return {
        "today": _items(today),
        "tomorrow": _items(tomorrow),
        "week": [item for day in range(days + 1)
                 for item in _items((now + timedelta(days=day)).strftime("%Y-%m-%d"))],
        "important": important,
    }


def advance_repeat(schedule_id: int, now: datetime = None) -> bool:
    """把重复日程推进到下一天（做完当天那次后调用）。"""
    now = now or datetime.now()
    row = adb.get_schedule(schedule_id)
    if not row:
        return False
    repeat = (row.get("repeat_rule") or "").strip()
    if repeat != "daily":
        return False
    start_raw = row.get("start_time") or ""
    end_raw = row.get("end_time") or ""
    try:
        start = datetime.strptime(start_raw, "%Y-%m-%d %H:%M:%S") + timedelta(days=1)
    except ValueError:
        return False
    fields = {"start_time": start.strftime("%Y-%m-%d %H:%M:%S"), "reminded_at": None,
              "status": "pending"}
    if end_raw:
        try:
            end = datetime.strptime(end_raw, "%Y-%m-%d %H:%M:%S") + timedelta(days=1)
            fields["end_time"] = end.strftime("%Y-%m-%d %H:%M:%S")
        except ValueError:
            pass
    return adb.update_schedule(schedule_id, **fields)


def next_weekday_name(day) -> str:
    return "周" + "一二三四五六日"[day.weekday()]


def days_in_month(year: int, month: int) -> int:
    return calendar.monthrange(year, month)[1]
