# -*- coding: utf-8 -*-
"""学习目标与任务：从聊天里建立目标、拆成可执行的每日任务、统计进度。

分工：
- 只产出结构化目标/任务/进度，**不生成 QQ 文案**（文案由现有人设链路生成）；
- 任务时长参考行为规律（更容易学习的时段、一次能坚持多久），避免排"理想化但做不到"的计划；
- 目标下面继续拆：Goal → Plan(Task) → Study Session → Knowledge Item → Review。
"""
import json
import logging
import re
from datetime import datetime, timedelta

import assistant_db as adb
import schedule_manager as sm
import english_profile as ep

logger = logging.getLogger(__name__)

CATEGORY_RULES = (
    ("english", ("英语", "四级", "六级", "考研英语", "雅思", "托福", "单词", "口语", "听力", "阅读")),
    ("japanese", ("日语", "N1", "N2", "N3", "五十音")),
    ("programming", ("python", "java", "编程", "代码", "算法", "前端", "后端", "sql")),
    ("exam", ("考试", "考级", "资格证", "公务员", "教资", "注会", "期末")),
)

GOAL_HINTS = ("我要", "我想", "帮我", "准备", "打算", "目标", "学会", "学好", "练好", "过")
LEARN_WORDS = ("学", "背", "练", "考", "过", "掌握", "入门")

DEFAULT_SESSION_MINUTES = 10
MIN_SESSION_MINUTES = 5
MAX_SESSION_MINUTES = 45


def detect_category(text: str) -> str:
    low = (text or "").lower()
    for category, words in CATEGORY_RULES:
        if any(word.lower() in low for word in words):
            return category
    return ""


def looks_like_goal(text: str) -> bool:
    """粗筛：像不像在立学习目标（避免把日常聊天当目标）。"""
    text = (text or "").strip()
    if len(text) < 3 or len(text) > 80:
        return False
    if "？" in text or "?" in text:
        return False
    if not any(word in text for word in GOAL_HINTS):
        return False
    if not any(word in text for word in LEARN_WORDS):
        return False
    return bool(detect_category(text)) or any(
        word in text for word in ("考试", "证书", "口语", "写作", "阅读", "单词", "编程"))


_PLAN_PROMPT = (
    "你是学习计划助手。请把目标拆成本周可执行的每日任务，输出 JSON 数组，不要解释。"
    "每个元素：{{\"title\": 任务名（≤14字）, \"minutes\": 每次分钟数, \"per_week\": 每周次数}}。"
    "用户一次能坚持约 {minutes} 分钟，请把单次任务控制在 5-45 分钟之间，宁可短而能坚持。"
    "目标：{goal}（{category}），期限：{deadline}。{profile_context}最多 3 条。"
)

_WORD_PROMPT = (
    "你是英语学习内容生成器。请针对「{goal}」生成 {count} 个适合我现在学的英语单词，"
    "输出 JSON 数组，不要解释。每个元素字段："
    "{{\"word\": 英文单词, \"phonetic\": 音标（含斜杠）, \"meaning\": 中文释义（≤10字）, "
    "\"example_en\": 一句地道的英文例句（6-12 词，必须包含该单词）, \"example_cn\": 例句中文翻译}}。"
    "要求：① 只给真实词汇（四级/六级高频实词优先），不要给 practice、sentence、subject、"
    "mistake 这类“学习元词汇”，也不要给 the、and 这类功能词；② 例句只用最常见的基础词，"
    "让基础薄弱的我也能看懂；③ 不要重复单词，难度贴合目标水平。{profile_context}"
)


def _extract_json_list(raw: str):
    text = raw or ""
    start, end = text.find("["), text.rfind("]")
    if start < 0 or end <= start:
        return None
    try:
        data = json.loads(text[start:end + 1])
    except (ValueError, TypeError):
        return None
    return data if isinstance(data, list) else None


async def plan_tasks(goal_title: str, category: str, deadline: str, llm_call,
                     comfortable_minutes: int = 0, english_profile: dict = None) -> list:
    """把目标拆成每日任务（LLM 结果必须通过本地校验，失败则用规则兜底）。"""
    minutes = comfortable_minutes or DEFAULT_SESSION_MINUTES
    minutes = max(MIN_SESSION_MINUTES, min(MAX_SESSION_MINUTES, int(minutes)))
    fallback = _default_tasks(category, goal_title, minutes, english_profile)
    if llm_call is None:
        return fallback
    try:
        raw = await llm_call(
            [{"role": "system", "content": _PLAN_PROMPT.format(
                minutes=minutes, goal=goal_title, category=category or "通用",
                deadline=deadline or "未指定",
                profile_context=("英语画像：" + ep.generation_context_from_profile(english_profile)
                                 if category == "english" else ""))},
             {"role": "user", "content": goal_title}],
            temperature=0.3, max_tokens=400, disable_thinking=True,
        )
    except Exception as exc:
        logger.warning("学习计划生成失败，使用默认计划: %s", exc)
        return fallback
    items = _extract_json_list(raw)
    if not items:
        return fallback
    tasks = []
    for item in items[:3]:
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or "").strip()[:20]
        if not title:
            continue
        try:
            per = int(item.get("minutes") or minutes)
        except (TypeError, ValueError):
            per = minutes
        per = max(MIN_SESSION_MINUTES, min(MAX_SESSION_MINUTES, per))
        try:
            per_week = int(item.get("per_week") or 5)
        except (TypeError, ValueError):
            per_week = 5
        tasks.append({"title": title, "minutes": per, "per_week": max(1, min(7, per_week))})
    return tasks or fallback


def _default_tasks(category: str, goal_title: str, minutes: int, english_profile: dict = None) -> list:
    if category == "english":
        return ep.recommended_tasks(english_profile or {}, minutes)
    if category == "japanese":
        return [{"title": "五十音/单词", "minutes": minutes, "per_week": 6},
                {"title": "听力跟读", "minutes": minutes, "per_week": 3}]
    if category == "programming":
        return [{"title": "看教程敲代码", "minutes": minutes + 10, "per_week": 5}]
    return [{"title": goal_title[:14] or "学习", "minutes": minutes, "per_week": 5}]


def comfortable_minutes(user_id: str) -> int:
    """从学习会话历史估计"一次能坚持多久"；没有数据时用默认值。"""
    sessions = [row for row in adb.list_sessions(user_id, 20)
                if row.get("status") in ("completed", "partial") and row.get("actual_minutes")]
    if not sessions:
        return DEFAULT_SESSION_MINUTES
    values = sorted(int(row["actual_minutes"]) for row in sessions)
    median = values[len(values) // 2]
    return max(MIN_SESSION_MINUTES, min(MAX_SESSION_MINUTES, median))


async def create_goal(user_id: str, text: str, llm_call=None) -> dict:
    """从一句话建立学习目标，并拆出每日任务。"""
    text = (text or "").strip()
    category = detect_category(text)
    title = _clean_goal_title(text)
    if not title:
        return {}
    deadline = _parse_deadline(text)
    goal_id = adb.add_goal(user_id, title, category=category, target=text[:60], deadline=deadline)
    if goal_id is None:
        return {}
    minutes = comfortable_minutes(user_id)
    profile = ep.prepare_profile(user_id, target=text) if category == "english" else {}
    tasks = await plan_tasks(title, category, deadline, llm_call, minutes, profile)
    task_ids = []
    for item in tasks:
        task_id = adb.add_task(
            user_id, item["title"], goal_id=goal_id, planned_minutes=item["minutes"],
            priority=sm.PRIORITY_STUDY_TASK, source="ai", movable=1,
            description="每周 %d 次" % item["per_week"],
        )
        if task_id:
            task_ids.append(task_id)
    return {"goal_id": goal_id, "title": title, "category": category,
            "deadline": deadline, "tasks": tasks, "task_ids": task_ids,
            "minutes": minutes, "english_profile": profile}


def refresh_english_goal_plan(user_id: str, profile: dict, goal_id: int = None) -> list:
    """画像变化后，只调整尚未开始的 AI 英语任务；不改用户手工日程和已完成记录。"""
    goal = active_goal(user_id)
    if goal_id:
        goal = next((item for item in adb.list_goals(user_id, statuses=("active",))
                     if item.get("id") == goal_id), goal)
    if not goal or goal.get("category") != "english":
        return []
    minutes = comfortable_minutes(user_id)
    wanted = ep.recommended_tasks(profile, minutes)
    pending = [item for item in adb.list_tasks(user_id, goal_id=goal["id"], statuses=("pending",))
               if item.get("source") == "ai"]
    changed = []
    for index, item in enumerate(wanted):
        title = item["title"]
        description = "本周试运行，每周 %d 次；会按你的实际表现继续调整" % item["per_week"]
        if index < len(pending):
            adb.update_task(pending[index]["id"], title=title, planned_minutes=item["minutes"],
                            description=description)
            changed.append(pending[index]["id"])
        else:
            task_id = adb.add_task(
                user_id, title, goal_id=goal["id"], planned_minutes=item["minutes"],
                priority=sm.PRIORITY_STUDY_TASK, source="ai", movable=1, description=description)
            if task_id:
                changed.append(task_id)
    return changed


def _clean_goal_title(text: str) -> str:
    title = text.strip()
    for word in ("我要", "我想", "帮我", "打算", "准备", "目标", "计划"):
        title = title.replace(word, "")
    title = title.strip("，。,.!！~ ")
    return title[:20]


def _parse_deadline(text: str) -> str:
    """从目标里抠出期限（"下个月""年底""6月"等），解析不出返回空串。"""
    now = datetime.now()
    if "月底" in text:
        return (now.replace(day=1) + timedelta(days=32)).replace(day=1).strftime("%Y-%m-%d")
    if "年底" in text:
        return "%d-12-31" % now.year
    m = re.search(r"(\d{1,2})\s*月", text)
    if m:
        month = int(m.group(1))
        year = now.year if month >= now.month else now.year + 1
        return "%d-%02d-01" % (year, month)
    m = re.search(r"(\d{1,2})\s*月\s*(\d{1,2})\s*[日号]", text)
    if m:
        return "%d-%02d-%02d" % (now.year, int(m.group(1)), int(m.group(2)))
    if "下个月" in text or "一个月" in text:
        return (now + timedelta(days=30)).strftime("%Y-%m-%d")
    if "这周" in text or "本周" in text:
        return (now + timedelta(days=(6 - now.weekday()))).strftime("%Y-%m-%d")
    return ""


def active_goal(user_id: str):
    goals = adb.list_goals(user_id, statuses=("active",))
    return goals[0] if goals else None


def goal_progress(user_id: str, goal_id: int) -> dict:
    """目标进度：任务完成度 + 学习时长。"""
    tasks = adb.list_tasks(user_id, goal_id=goal_id)
    total = len(tasks)
    done = sum(1 for row in tasks if row.get("status") == "done")
    sessions = [row for row in adb.list_sessions(user_id, 50)
                if row.get("goal_id") == goal_id]
    minutes = sum(int(row.get("actual_minutes") or 0) for row in sessions)
    progress = (done / total) if total else 0.0
    return {"total_tasks": total, "done_tasks": done, "progress": round(progress, 3),
            "minutes": minutes, "sessions": len(sessions)}


def study_streak(user_id: str) -> int:
    """连续学习天数（含今天或昨天为止）。"""
    days = set(adb.study_days(user_id, 60))
    if not days:
        return 0
    streak = 0
    cursor = datetime.now().date()
    if cursor.strftime("%Y-%m-%d") not in days:
        cursor = cursor - timedelta(days=1)
    while cursor.strftime("%Y-%m-%d") in days:
        streak += 1
        cursor = cursor - timedelta(days=1)
    return streak


def weak_points(user_id: str, limit: int = 5) -> list:
    """薄弱知识点（掌握度低 / 答错多）。"""
    items = adb.list_knowledge(user_id, limit=50)
    scored = sorted(items, key=lambda row: (float(row.get("mastery") or 0),
                                            -int(row.get("wrong_count") or 0)))
    return [row for row in scored if int(row.get("wrong_count") or 0) > 0
            or float(row.get("mastery") or 0) < 0.6][:limit]


def overview(user_id: str) -> dict:
    """面板「学习」子页数据。"""
    goal = active_goal(user_id)
    if not goal:
        return {"has_goal": False, "streak": study_streak(user_id),
                "goal": {}, "progress": {}, "weak": [], "due": [],
                "last_session": adb.get_last_finished_session(user_id) or {}}
    progress = goal_progress(user_id, goal["id"])
    return {
        "has_goal": True,
        "goal": goal,
        "progress": progress,
        "streak": study_streak(user_id),
        "minutes": progress["minutes"],
        "weak": weak_points(user_id),
        "due": adb.due_reviews(user_id),
        "last_session": adb.get_last_finished_session(user_id) or {},
    }
