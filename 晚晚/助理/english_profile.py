# -*- coding: utf-8 -*-
"""英语学习画像：先了解基础，再决定怎么带着学。

这里只处理结构化证据、自然摸底题和推荐方向，不生成 QQ 文案。它不会把用户一句
「我英语差」当成能力结论；历史词卡表现、摸底回答和之后的学习表现才是依据。
"""
from __future__ import annotations

import re
from datetime import datetime

import assistant_db as adb


DIMENSIONS = ("vocabulary", "grammar", "reading", "listening", "writing", "speaking", "spelling")
CORE_DIMENSIONS = ("vocabulary", "grammar", "reading")
DIMENSION_NAMES = {
    "vocabulary": "词汇", "grammar": "语法", "reading": "阅读", "listening": "听力",
    "writing": "写作", "speaking": "口语", "spelling": "拼写",
}

# 每次只问一题。题目偏基础，仅用于决定从哪里开始，不伪装成完整等级考试。
ONBOARDING_QUESTIONS = {
    "vocabulary": {
        "text": "我先随便问个小的，borrow 大概是什么意思呀？",
        "answer": "借", "hint": "和借东西有关", "detail": "borrow 基础词义",
    },
    "grammar": {
        "text": "再来一个：I ___ to school yesterday. 这里你会填 go、went 还是 going？",
        "answer": "went", "hint": "看 yesterday", "detail": "一般过去时",
    },
    "reading": {
        "text": "最后一句短的：I missed the bus, so I walked to school. 这句大概在说什么？",
        "answer": "没赶上公交所以走路去学校", "hint": "前半句是没赶上车", "detail": "基础句意理解",
    },
}


def _blank_skills() -> dict:
    return {name: {"score": None, "level": "待评估", "confidence": 0.0}
            for name in DIMENSIONS}


def _level(score):
    if score is None:
        return "待评估"
    if score < 0.28:
        return "基础待补"
    if score < 0.48:
        return "基础"
    if score < 0.68:
        return "初级进阶"
    if score < 0.84:
        return "中级"
    return "较稳"


def _overall(skills: dict) -> str:
    scores = [item.get("score") for item in skills.values()
              if isinstance(item, dict) and item.get("score") is not None]
    if not scores:
        return "待评估"
    return _level(sum(float(value) for value in scores) / len(scores))


def _profile_or_blank(user_id: str, target: str = "") -> dict:
    profile = adb.get_english_profile(user_id)
    if profile:
        profile["skills"] = {**_blank_skills(), **(profile.get("skills") or {})}
        if target and not profile.get("target"):
            profile["target"] = target[:80]
        return profile
    return {
        "user_id": str(user_id), "target": (target or "")[:80], "overall_level": "待评估",
        "skills": _blank_skills(), "weak_points": [],
        "pending_dimensions": list(DIMENSIONS), "recommendation": {},
        "confidence": 0.0, "evidence_count": 0, "assessment_state": {},
        "history_context": {}, "last_assessed": "",
    }


def _save(profile: dict):
    skills = profile.get("skills") or _blank_skills()
    profile["overall_level"] = _overall(skills)
    profile["recommendation"] = recommendation(profile)
    adb.save_english_profile(
        profile["user_id"], target=profile.get("target") or "",
        overall_level=profile["overall_level"], skills=skills,
        weak_points=profile.get("weak_points") or [],
        pending_dimensions=profile.get("pending_dimensions") or [],
        recommendation=profile["recommendation"], confidence=profile.get("confidence") or 0,
        evidence_count=profile.get("evidence_count") or 0,
        assessment_state=profile.get("assessment_state") or {},
        history_context=profile.get("history_context") or {},
        last_assessed=profile.get("last_assessed") or "",
    )
    return adb.get_english_profile(profile["user_id"]) or profile


def _history_context(user_id: str) -> dict:
    """把历史聊天压成学习主题摘要，不保留原文。"""
    history = adb.fetch_chat_history(role="user", user_id=user_id)[-300:]
    topics = {
        "vocabulary": ("单词", "词汇", "背词", "背单词"),
        "grammar": ("语法", "时态", "句型"),
        "reading": ("阅读", "短文", "长难句"),
        "listening": ("听力", "听不懂", "听英语"),
        "writing": ("写作", "作文", "写英文"),
        "speaking": ("口语", "开口", "说英语"),
        "spelling": ("拼写", "拼错"),
    }
    mentioned = [dimension for dimension, words in topics.items()
                 if any(any(word in (row.get("content") or "") for word in words) for row in history)]
    return {"history_messages": len(history), "mentioned_dimensions": mentioned}


def prepare_profile(user_id: str, target: str = "") -> dict:
    """建立或读取初步画像。

    只把已有英语词卡的真实表现作为历史能力证据。聊天里出现过“英语很差”之类的
    自我描述最多影响之后的沟通方式，不能直接变成能力评级。
    """
    profile = _profile_or_blank(user_id, target)
    profile["history_context"] = _history_context(user_id)
    if adb.get_english_profile(user_id):
        return _save(profile)

    items = [row for row in adb.list_knowledge(user_id, limit=300)
             if (row.get("subject") or "").lower() == "english"]
    attempted = [row for row in items if int(row.get("correct_count") or 0)
                 or int(row.get("wrong_count") or 0)]
    # 至少三张有作答记录，才把旧词卡表现纳入画像，避免一两次偶然表现定级。
    if len(attempted) >= 3:
        score = sum(float(row.get("mastery") or 0) for row in attempted) / len(attempted)
        adb.add_english_assessment_event(user_id, "vocabulary", score, "history",
                                         "既有词卡表现（%d 条）" % len(attempted))
    return refresh_profile(user_id, target=target)


def refresh_profile(user_id: str, target: str = "") -> dict:
    """由可追溯事件重新计算画像；近期表现略高于很久以前的结果。"""
    profile = _profile_or_blank(user_id, target)
    events = adb.list_english_assessment_events(user_id, limit=120)
    skills = _blank_skills()
    weak = []
    for dimension in DIMENSIONS:
        samples = [event for event in events if event.get("dimension") == dimension][:8]
        if not samples:
            continue
        # 查询是倒序，越近的表现权重越高；单题不会制造虚假的高置信度。
        weights = [max(1, len(samples) - index) for index in range(len(samples))]
        score = sum(float(row.get("score") or 0) * weight
                    for row, weight in zip(samples, weights)) / sum(weights)
        confidence = min(0.9, 0.18 + len(samples) * 0.16)
        skills[dimension] = {"score": round(score, 3), "level": _level(score),
                             "confidence": round(confidence, 3)}
        if len(samples) >= 2 and score < 0.52:
            weak.append(dimension)
    # 尚未直接看过的维度保留“待评估”，不会被整体平均值伪装成已掌握。
    pending = [dimension for dimension in DIMENSIONS
               if skills[dimension].get("score") is None]
    profile["skills"] = skills
    profile["weak_points"] = weak
    profile["pending_dimensions"] = pending
    profile["confidence"] = round(min(0.9, len(events) / 10.0), 3)
    profile["evidence_count"] = len(events)
    profile["last_assessed"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S") if events else ""
    return _save(profile)


def pending_question(user_id: str) -> dict:
    """取或新建当前的一道自然摸底题；三道核心题后就先停止，不拉长成考试。"""
    profile = _profile_or_blank(user_id)
    state = profile.get("assessment_state") or {}
    if state.get("dimension") in ONBOARDING_QUESTIONS:
        return {"dimension": state["dimension"], **ONBOARDING_QUESTIONS[state["dimension"]]}
    events = adb.list_english_assessment_events(user_id, limit=80)
    seen = {item.get("dimension") for item in events if item.get("source") == "onboarding"}
    dimension = next((name for name in CORE_DIMENSIONS if name not in seen), "")
    if not dimension:
        return {}
    profile["assessment_state"] = {"dimension": dimension}
    _save(profile)
    return {"dimension": dimension, **ONBOARDING_QUESTIONS[dimension]}


def has_pending_assessment(user_id: str) -> bool:
    profile = adb.get_english_profile(user_id) or {}
    state = profile.get("assessment_state") or {}
    return state.get("dimension") in ONBOARDING_QUESTIONS


def looks_like_pending_answer(user_id: str, text: str) -> bool:
    """在学习中聊天保护之前，识别明显是在回答当前自然摸底题的内容。"""
    question = pending_question(user_id)
    value = _compact(text)
    if not question or not value:
        return False
    if any(word in value for word in ("不会", "不知道", "忘了", "不懂")):
        return True
    dimension = question.get("dimension")
    if dimension == "grammar":
        return any(word in value for word in ("go", "went", "going"))
    if dimension == "vocabulary":
        return "借" in value or "borrow" in value
    if dimension == "reading":
        signals = ("没赶", "公交", "走路", "学校", "车")
        return sum(1 for token in signals if token in value) >= 2
    return False


def _compact(text: str) -> str:
    return re.sub(r"[\s，。,.!！?？、;；:：'\"“”‘’()（）]", "", (text or "").lower())


def _answer_score(question: dict, text: str) -> float:
    value = _compact(text)
    if not value or any(word in value for word in ("不会", "不知道", "忘了", "不懂")):
        return 0.0
    dimension = question.get("dimension")
    if dimension == "grammar":
        return 1.0 if "went" in value else 0.0
    if dimension == "vocabulary":
        return 1.0 if "借" in value else 0.0
    if dimension == "reading":
        signals = ("没赶", "公交", "走路", "学校", "车")
        return 1.0 if sum(1 for token in signals if token in value) >= 2 else 0.35
    return 0.0


def grade_pending_answer(user_id: str, text: str) -> dict:
    """记录当前摸底题的表现并返回下一题；不使用模型判卷，保证结论可解释。"""
    question = pending_question(user_id)
    if not question:
        return {}
    score = _answer_score(question, text)
    adb.add_english_assessment_event(user_id, question["dimension"], score, "onboarding",
                                     question.get("detail") or "自然摸底")
    profile = _profile_or_blank(user_id)
    profile["assessment_state"] = {}
    _save(profile)
    profile = refresh_profile(user_id)
    next_question = pending_question(user_id)
    return {"dimension": question["dimension"], "score": score,
            "hint": question.get("hint") or "", "next_question": next_question,
            "profile": adb.get_english_profile(user_id) or profile}


def record_practice_result(user_id: str, dimension: str, correct: bool,
                           source: str = "word_card") -> dict:
    """把后续真实学习表现回灌进画像，供下一阶段计划使用。"""
    adb.add_english_assessment_event(user_id, dimension, 1.0 if correct else 0.0, source,
                                     "学习中的一次%s" % ("掌握" if correct else "卡住"))
    return refresh_profile(user_id)


def primary_focus(profile: dict) -> str:
    skills = (profile or {}).get("skills") or {}
    core_unknown = [name for name in CORE_DIMENSIONS if (skills.get(name) or {}).get("score") is None]
    if core_unknown:
        return "assessment"
    candidates = [(float((skills.get(name) or {}).get("score") or 0), name)
                  for name in ("vocabulary", "grammar", "reading", "listening")
                  if (skills.get(name) or {}).get("score") is not None]
    return min(candidates)[1] if candidates else "assessment"


def focus_for_request(profile: dict, text: str = "") -> str:
    """用户明确说想练哪项时尊重他的选择，否则按画像给当前优先项。"""
    value = (text or "").replace(" ", "")
    requested = (("listening", ("听力", "听")), ("reading", ("阅读", "读文章")),
                 ("grammar", ("语法", "时态", "造句")), ("writing", ("写作", "作文")),
                 ("speaking", ("口语", "开口")), ("vocabulary", ("单词", "背词", "背单词")))
    for dimension, words in requested:
        if any(word in value for word in words):
            return dimension
    return primary_focus(profile)


def recommendation(profile: dict) -> dict:
    focus = primary_focus(profile)
    labels = {
        "assessment": "先用几道小题确认基础", "vocabulary": "先补核心词汇",
        "grammar": "先把常用语法理顺", "reading": "先练短句和阅读理解",
        "listening": "先练短句听力", "writing": "先练简单表达", "speaking": "先练开口表达",
    }
    return {"focus": focus, "focus_name": labels.get(focus, "先稳住基础"),
            "reason": "依据已有学习表现，后续会继续调整"}


def recommended_tasks(profile: dict, minutes: int) -> list:
    """第一周试运行任务：不一次定死长计划；任务由当前最弱/最待确认项决定。"""
    minutes = max(5, min(45, int(minutes or 10)))
    focus = primary_focus(profile)
    tasks = {
        "assessment": [("英语基础摸底", min(12, minutes), 2), ("短句理解", minutes, 3)],
        "vocabulary": [("核心词汇巩固", minutes, 5), ("短句阅读", minutes + 5, 3)],
        "grammar": [("常用语法练习", minutes, 4), ("短句造句", minutes, 3)],
        "reading": [("短句阅读理解", minutes + 5, 4), ("核心词汇复习", minutes, 3)],
        "listening": [("短句听力", minutes, 4), ("听后复述", minutes, 2)],
    }
    return [{"title": title, "minutes": duration, "per_week": weekly}
            for title, duration, weekly in tasks.get(focus, tasks["assessment"])]


def generation_context(user_id: str) -> str:
    """给内容生成器的极短事实，不让它编造完整能力等级。"""
    return generation_context_from_profile(adb.get_english_profile(user_id) or {})


def generation_context_from_profile(profile: dict) -> str:
    """同 generation_context，但调用方已经拿到画像时不再重复查库。"""
    profile = profile or {}
    skills = profile.get("skills") or {}
    focus = (profile.get("recommendation") or {}).get("focus") or primary_focus(profile)
    known = ["%s%s" % (DIMENSION_NAMES[name], (skills.get(name) or {}).get("level"))
             for name in ("vocabulary", "grammar", "reading")
             if (skills.get(name) or {}).get("score") is not None]
    history = (profile.get("history_context") or {}).get("mentioned_dimensions") or []
    history_note = ("；他过去提过：" + "、".join(DIMENSION_NAMES.get(name, name) for name in history)
                    if history else "")
    return "当前优先：%s；已确认：%s%s。只生成适合这一阶段的基础内容，不要跳难度。" % (
        DIMENSION_NAMES.get(focus, "基础"), "、".join(known) or "仍在了解", history_note)
