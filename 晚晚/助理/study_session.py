# -*- coding: utf-8 -*-
"""学习会话（Study Session）与语言类朗读。

职责边界：
- 本模块管理"这一次学习"的状态与进度（active / paused / completed / abandoned / partial），
  以及语言类词卡的内容与朗读文本；
- **不生成最终 QQ 文案**：词卡讲解由调用方走现有人设链路生成，朗读直接复用现有 TTS；
- 中断不丢进度：会话可暂停、可第二天续，词卡本身存进知识点表长期保留。
"""
import logging
import re
from datetime import datetime, timedelta

import assistant_db as adb
import goal_manager as gm
import english_profile as ep

logger = logging.getLogger(__name__)

WORDS_PER_SESSION = 5
MASTERY_STEP = 0.25
REVIEW_STEPS = (1, 2, 4, 7, 15, 30)


def _extract_json_list(raw: str):
    text = raw or ""
    start, end = text.find("["), text.rfind("]")
    if start < 0 or end <= start:
        return None
    import json
    try:
        data = json.loads(text[start:end + 1])
    except (ValueError, TypeError):
        return None
    return data if isinstance(data, list) else None


async def generate_word_cards(goal_title: str, count: int, llm_call, profile_context: str = "") -> list:
    """生成词卡（LLM 结果逐字段校验，缺失例句的直接丢弃）。"""
    if llm_call is None:
        return []
    try:
        raw = await llm_call(
            [{"role": "system", "content": gm._WORD_PROMPT.format(
                goal=goal_title or "英语", count=int(count),
                profile_context=(profile_context or ""))},
             {"role": "user", "content": goal_title or "四级词汇"}],
            temperature=0.6, max_tokens=900, disable_thinking=True,
        )
    except Exception as exc:
        logger.warning("词卡生成失败: %s", exc)
        return []
    items = _extract_json_list(raw) or []
    cards = []
    for item in items:
        if not isinstance(item, dict):
            continue
        word = str(item.get("word") or "").strip()
        example = str(item.get("example_en") or "").strip()
        if not word or not re.match(r"^[A-Za-z][A-Za-z' -]{1,30}$", word):
            continue
        if not example or word.lower() not in example.lower():
            continue
        cards.append({
            "word": word,
            "phonetic": str(item.get("phonetic") or "").strip()[:30],
            "meaning": str(item.get("meaning") or "").strip()[:20],
            "example_en": example[:160],
            "example_cn": str(item.get("example_cn") or "").strip()[:160],
        })
    return cards


async def ensure_word_pool(user_id: str, goal, llm_call, want: int = WORDS_PER_SESSION) -> list:
    """确保有足够的词卡：不足时生成并写入知识点表（长期保留、可复习）。"""
    goal_id = (goal or {}).get("id")
    subject = (goal or {}).get("category") or "english"
    pool = adb.list_knowledge(user_id, goal_id=goal_id, limit=200)
    unseen = [row for row in pool if row.get("status") == "unknown"]
    if len(unseen) >= want:
        return unseen[:want]
    cards = await generate_word_cards(
        (goal or {}).get("title") or "英语", want - len(unseen) + 2, llm_call,
        profile_context=ep.generation_context(user_id),
    )
    for card in cards:
        adb.add_knowledge(
            user_id, card["word"], answer=card["meaning"], subject=subject, goal_id=goal_id,
            extra={"phonetic": card["phonetic"], "example_en": card["example_en"],
                   "example_cn": card["example_cn"]},
        )
    pool = adb.list_knowledge(user_id, goal_id=goal_id, limit=200)
    unseen = [row for row in pool if row.get("status") == "unknown"]
    return unseen[:want]


def read_aloud_text(card: dict) -> str:
    """朗读文本：先读例句，再把单词重读两遍（实测该写法合成的英文最清晰）。"""
    word = card.get("content") or card.get("word") or ""
    extra = card.get("extra") or {}
    example = extra.get("example_en") or card.get("example_en") or ""
    parts = []
    if example:
        parts.append(example.rstrip(".?!") + ".")
    if word:
        parts.append("%s." % word)
        parts.append("%s." % word)
    return " ... ".join(parts) if len(parts) > 1 else (parts[0] if parts else "")


def word_only_instruction(base_voice: str = "") -> str:
    """让 TTS 只念一个英文单词时的音色指令（实测：不加这句会用中文音读英文词）。"""
    voice = (base_voice or "").strip()
    return (voice + "。" if voice else "") + "只读这个单词，读两遍，发音要清楚。"


def start_session(user_id: str, goal=None, planned_minutes: int = 0, task_id=None) -> dict:
    """开始一次学习会话（若已有未结束会话则直接返回它，避免重复计数）。"""
    existing = adb.get_open_session(user_id)
    if existing:
        return existing
    minutes = int(planned_minutes or gm.comfortable_minutes(user_id))
    session_id = adb.add_session(user_id, goal_id=(goal or {}).get("id"), task_id=task_id,
                                 planned_minutes=minutes, status="active")
    if session_id is None:
        return {}
    return adb.get_session(session_id)


def pause_session(session_id: int, done: int = 0, total: int = 0) -> bool:
    """暂停：保留已完成进度，第二天可以接着来。"""
    progress = (done / total) if total else 0.0
    return adb.update_session(session_id, status="paused", progress=round(progress, 3))


def resume_session(session_id: int) -> bool:
    return adb.update_session(session_id, status="active")


def finish_session(session_id: int, done: int = 0, total: int = 0, summary: str = "",
                   force_status: str = "") -> dict:
    """结束会话：按完成比例给出 completed / partial，并写入实际时长。"""
    session = adb.get_session(session_id) or {}
    progress = (done / total) if total else 0.0
    if force_status:
        status = force_status
    elif progress >= 0.999:
        status = "completed"
    elif progress > 0:
        status = "partial"
    else:
        status = "abandoned"
    actual = _elapsed_minutes(session.get("started_at"))
    adb.update_session(session_id, status=status, progress=round(progress, 3),
                       ended_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                       actual_minutes=actual,
                       summary=(summary or "")[:300])
    return {"status": status, "progress": round(progress, 3), "actual_minutes": actual,
            "done": done, "total": total}


def _elapsed_minutes(started_at: str) -> int:
    if not started_at:
        return 0
    try:
        start = datetime.strptime(started_at, "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return 0
    return max(1, int((datetime.now() - start).total_seconds() // 60))


def record_answer(user_id: str, knowledge_id: int, correct: bool) -> dict:
    """记录一次作答：更新掌握度、状态与下次复习时间（错题优先安排）。"""
    item = adb.get_knowledge(knowledge_id)
    if not item:
        return {}
    mastery = float(item.get("mastery") or 0)
    correct_count = int(item.get("correct_count") or 0)
    wrong_count = int(item.get("wrong_count") or 0)
    if correct:
        correct_count += 1
        mastery = min(1.0, mastery + MASTERY_STEP)
    else:
        wrong_count += 1
        mastery = max(0.0, mastery - MASTERY_STEP * 1.5)
        correct_count = 0
    if mastery >= 0.8 and correct_count >= 3:
        status = "mastered"
    elif wrong_count and mastery < 0.4:
        status = "unstable"
    else:
        status = "learning"
    step = REVIEW_STEPS[min(len(REVIEW_STEPS) - 1, correct_count)]
    if not correct:
        step = 1
    next_review = (datetime.now() + timedelta(days=step)).strftime("%Y-%m-%d 08:00:00")
    adb.update_knowledge(knowledge_id, mastery=round(mastery, 3),
                         correct_count=correct_count, wrong_count=wrong_count,
                         status=status, next_review=next_review,
                         last_seen=datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    adb.add_review_record(user_id, knowledge_id, "correct" if correct else "wrong", step)
    if (item.get("subject") or "").lower() == "english":
        # 词卡不是全部英语能力，但它是词汇维度的一条真实长期证据。
        ep.record_practice_result(user_id, "vocabulary", correct, source="word_card")
    return {"mastery": round(mastery, 3), "status": status, "next_review": next_review,
            "correct_count": correct_count, "wrong_count": wrong_count}


def session_progress(session_id: int) -> dict:
    session = adb.get_session(session_id) or {}
    return {"id": session.get("id"), "status": session.get("status"),
            "progress": float(session.get("progress") or 0),
            "planned_minutes": int(session.get("planned_minutes") or 0),
            "started_at": session.get("started_at") or ""}


def describe_card(card: dict) -> str:
    """词卡的事实文本（供人设链路引用，不作为最终回复直接发出）。"""
    extra = card.get("extra") or {}
    parts = [card.get("content") or ""]
    if extra.get("phonetic"):
        parts.append(extra["phonetic"])
    if card.get("answer"):
        parts.append("释义：" + card["answer"])
    if extra.get("example_en"):
        parts.append("例句：" + extra["example_en"])
    return " ".join(parts)


def next_card(user_id: str, goal_id=None) -> dict:
    """取下一个该学的词（优先没学过的，其次最不熟的）。"""
    pool = adb.list_knowledge(user_id, goal_id=goal_id, limit=200)
    unseen = [row for row in pool if row.get("status") == "unknown"]
    if unseen:
        return unseen[0]
    return pool[0] if pool else {}


# =============================================================================
# 引导式教学（Phase 4）：一次只推进一小步
# =============================================================================

RETRY_LIMIT = 2          # 每题最多提示几次，再不给就解释答案


def build_question(card: dict, mastery: float = 0.0) -> str:
    """按掌握度决定问法：不熟 → 认词（英译中）；熟一些 → 回忆（中译英）。

    返回的是"要问的问题"，不是最终文案；文案由人设链路生成。
    """
    word = card.get("content") or ""
    extra = card.get("extra") or {}
    meaning = card.get("answer") or ""
    if mastery >= 0.5:
        return "「%s」用英语怎么说？" % meaning if meaning else "这个词用英语怎么说？"
    if extra.get("example_en") and mastery < 0.2:
        return "例句里那个「%s」是什么意思？" % word
    return "「%s」是什么意思？" % word


def ask_question(session_id: int, card: dict, mastery: float = 0.0) -> str:
    """把某张卡设成"当前待答题目"，返回问题文本。"""
    question = build_question(card, mastery)
    adb.update_session(session_id, pending_knowledge_id=card.get("id"), ask_attempts=0)
    return question


def clear_question(session_id: int):
    adb.update_session(session_id, pending_knowledge_id=None, ask_attempts=0)


def pending_card(session: dict) -> dict:
    kid = (session or {}).get("pending_knowledge_id")
    return adb.get_knowledge(kid) if kid else {}


def _normalize(text: str) -> str:
    return re.sub(r"[\s，。,.!！?？、;；:：'\"“”‘’()（）]", "", (text or "").lower())


def judge_local(card: dict, user_text: str) -> bool:
    """本地判分：词形或释义命中即算对（快、零成本、可测）。"""
    answer = _normalize(card.get("answer") or "")
    word = _normalize(card.get("content") or "")
    said = _normalize(user_text)
    if not said:
        return False
    if answer and (answer == said or (len(answer) >= 2 and answer in said)):
        return True
    if word and (word == said or (len(word) >= 3 and word in said)):
        return True
    return False


JUDGE_PROMPT = (
    "你是判卷器。用户在学习「{word}」，正确答案是「{answer}」。"
    "请判断他的回答是否正确（同义、近义、拼写小错都算对；意思完全不对才算错）。"
    '只输出 JSON：{{"correct": true/false, "hint": "若错误，给一个不直接透露答案的小提示（≤20字）"}}'
)


async def judge_with_llm(card: dict, user_text: str, llm_call) -> dict:
    if llm_call is None:
        return {"correct": False, "hint": ""}
    try:
        raw = await llm_call(
            [{"role": "system", "content": JUDGE_PROMPT.format(
                word=card.get("content") or "", answer=card.get("answer") or "")},
             {"role": "user", "content": user_text}],
            temperature=0.1, max_tokens=120, disable_thinking=True,
        )
    except Exception as exc:
        logger.warning("判分调用失败: %s", exc)
        return {"correct": False, "hint": ""}
    text = raw or ""
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        return {"correct": False, "hint": ""}
    import json
    try:
        data = json.loads(text[start:end + 1])
    except (ValueError, TypeError):
        return {"correct": False, "hint": ""}
    return {"correct": bool(data.get("correct")),
            "hint": str(data.get("hint") or "").strip()[:40]}


async def grade_answer(user_id: str, session: dict, user_text: str, llm_call=None) -> dict:
    """判分 + 决定下一步（引导式：先提示、再让他试、最后才解释）。"""
    card = pending_card(session)
    if not card:
        return {"verdict": "no_question"}
    correct = judge_local(card, user_text)
    hint = ""
    if not correct:
        judged = await judge_with_llm(card, user_text, llm_call)
        correct = bool(judged.get("correct"))
        hint = judged.get("hint") or ""
    attempts = int(session.get("ask_attempts") or 0)
    if correct:
        result = record_answer(user_id, card["id"], correct=True)
        clear_question(session["id"])
        return {"verdict": "correct", "card": card, "result": result}
    # 答错：先记一次错误（掌握度下降、安排复习），但仍在本次会话里给他机会
    result = record_answer(user_id, card["id"], correct=False)
    attempts += 1
    adb.update_session(session["id"], ask_attempts=attempts)
    if attempts >= RETRY_LIMIT:
        clear_question(session["id"])
        return {"verdict": "explain", "card": card, "result": result, "hint": hint}
    return {"verdict": "retry", "card": card, "result": result, "hint": hint,
            "attempts": attempts}


def step_instruction(verdict: dict) -> str:
    """把判分结果翻译成"给她的内部指示"（不生成最终文案，也不含系统话说）。"""
    card = verdict.get("card") or {}
    word = card.get("content") or ""
    meaning = card.get("answer") or ""
    extra = card.get("extra") or {}
    example = extra.get("example_en") or ""
    kind = verdict.get("verdict")
    if kind == "correct":
        return (f"他刚才答对了（「{word}」）。用你自己的语气夸他一句，很短，"
                "然后说接着下一个；不要重复讲这个词的知识点。")
    if kind == "retry":
        hint = verdict.get("hint") or ""
        clue = f"可以参考这个提示：{hint}。" if hint else ""
        first = (word[:1] or "").upper()
        letter = f"（首字母是 {first}）" if first else ""
        return (f"他刚才答错了（这个词是「{word}」{letter}，但**这次绝对不要说出它的意思**）。"
                f"{clue}请给一个小小的提示：只提醒首字母、词性或它在例句里的位置，"
                "鼓励他再试一次，一句话，不要直接报答案。")
    if kind == "explain":
        return (f"他连着两次没答对（「{word}」，意思是「{meaning}」"
                + (f"，例句 {example}" if example else "")
                + "）。现在把答案讲清楚：一次只讲这一个词，用你自己的语气解释一下，"
                "别像老师念课本，也别顺带塞一堆别的词。")
    return ""


def review_queue(user_id: str, limit: int = 3) -> list:
    """今天该复习的词（到期的优先）。"""
    return adb.due_reviews(user_id, limit=limit)


def session_seen(user_id: str, session: dict) -> list:
    """本次会话里已经考过的知识点（按 last_seen 判断，重启也不丢）。"""
    started = (session or {}).get("started_at") or ""
    return [row for row in adb.list_knowledge(user_id, limit=200)
            if row.get("last_seen") and str(row["last_seen"]) >= started]


def pick_next_for_session(user_id: str, session: dict, goal_id=None) -> dict:
    """下一次要考的内容：先复习到期的，再学没学过的，最后挑最不熟的。"""
    pool = adb.list_knowledge(user_id, goal_id=goal_id, limit=200)
    seen_ids = {row["id"] for row in session_seen(user_id, session)}
    due = [row for row in adb.due_reviews(user_id, limit=10) if row["id"] not in seen_ids]
    if due:
        return due[0]
    unseen = [row for row in pool if row.get("status") == "unknown" and row["id"] not in seen_ids]
    if unseen:
        return unseen[0]
    rest = [row for row in pool if row["id"] not in seen_ids]
    if rest:
        return sorted(rest, key=lambda row: float(row.get("mastery") or 0))[0]
    return {}


def session_done(session_id: int, session: dict, summary: str = "") -> dict:
    """按本次会话实际考过的数量收尾（完成/部分完成由比例决定）。"""
    return finish_session(session_id, done=1, total=1, summary=summary)
