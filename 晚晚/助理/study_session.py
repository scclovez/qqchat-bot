# -*- coding: utf-8 -*-
"""学习会话（Study Session）与语言类朗读。

职责边界：
- 本模块管理"这一次学习"的状态与进度（active / paused / completed / abandoned / partial），
  以及语言类词卡的内容与朗读文本；
- **教学事实由本模块确定性产出**（事实句、提问原文、提示），最终 QQ 文案仍由人设链路润色；
  人设链路只负责语气，词名/释义/提问一旦被改写或提前泄露，一律回退到这里的模板；
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

# 「不知道」类回答：不再让他硬猜，也绝不趁机报答案（他明确说不会就该讲清楚）
_DONT_KNOW_MARKS = (
    "不知道", "不知", "不会", "不懂", "没学过", "没有学过", "没记住", "记不住", "忘了",
    "忘记了", "不记得", "想不起来", "想不出", "猜不到", "说不出来", "啥意思", "什么意思",
    "不会啊", "不知道啊", "i don't know", "dont know", "no idea",
)

# 生成器容易吐出来的"学习元词汇"：看着像词卡，实际不是四级词汇，一律丢弃
_META_WORDS = {
    "practice", "practise", "sentence", "subject", "verb", "noun", "adjective", "adverb",
    "mistake", "correct", "grammar", "vocabulary", "word", "words", "english", "exam",
    "student", "teacher", "lesson", "homework", "exercise", "pronunciation", "spelling",
    "meaning", "translate", "translation", "dictionary", "study", "learn", "learning",
}
# 太虚、教不出东西的功能词
_FUNCTION_WORDS = {
    "the", "a", "an", "and", "or", "but", "is", "are", "am", "be", "was", "were", "do",
    "does", "did", "of", "to", "in", "on", "at", "for", "with", "this", "that", "these",
    "those", "it", "its", "he", "she", "they", "we", "you", "i", "my", "your",
}

_POS_MARKS = (
    ("动词", ("动", "v.")), ("名词", ("名", "n.")), ("形容词", ("形", "adj")),
    ("副词", ("副", "adv")), ("介词", ("介", "prep")), ("短语", ("短语", "词组")),
)

# 非词汇方向也必须有可实际练习的内容，不能只在计划表里换个名字。
_FOCUS_ITEMS = {
    "grammar": [
        ("一般过去时 1", "I ___ to school yesterday.", "went", "yesterday 要用过去式。"),
        ("一般过去时 2", "She ___ a movie last night.", "watched", "last night 提示过去发生。"),
        ("第三人称单数", "He ___ coffee every morning.", "drinks", "he 后面的动词要变化。"),
        ("be 动词", "They ___ happy today.", "are", "they 对应 are。"),
        ("现在进行时", "I am ___ a book now.", "reading", "now 常搭配进行时。"),
    ],
    "reading": [
        ("短句阅读 1", "Tom missed the bus, so he walked to school. Tom 怎么去学校？", "走路", "so 后面说的是结果。"),
        ("短句阅读 2", "Lily is tired because she studied late. Lily 为什么累？", "学习到很晚", "because 后面是原因。"),
        ("短句阅读 3", "The shop closes at eight, but we arrived at nine. 我们到时商店怎么样？", "关门了", "arrived 比 closes 晚。"),
        ("短句阅读 4", "Jack took an umbrella because it was raining. Jack 为什么带伞？", "下雨", "because 后面是原因。"),
        ("短句阅读 5", "Amy wants tea, not coffee. Amy 想喝什么？", "茶", "not 后面排除 coffee。"),
    ],
    "listening": [
        ("短句听力 1", "刚才那句里，她下课后要去哪？", "图书馆", "注意 after class 后面的地点。", "She is going to the library after class."),
        ("短句听力 2", "刚才那句里，他周末做什么？", "看望奶奶", "注意 weekend 的动作。", "He will visit his grandmother this weekend."),
        ("短句听力 3", "刚才那句里，会议几点开始？", "三点", "听时间。", "The meeting starts at three o'clock."),
        ("短句听力 4", "刚才那句里，她为什么晚到？", "错过了火车", "注意 because 后面的原因。", "She was late because she missed the train."),
        ("短句听力 5", "刚才那句里，他们晚饭吃什么？", "面条", "听 dinner 后面的食物。", "They are having noodles for dinner."),
    ],
}


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
    dropped = []
    for item in items:
        if not isinstance(item, dict):
            continue
        word = str(item.get("word") or "").strip()
        example = str(item.get("example_en") or "").strip()
        if not word or not re.match(r"^[A-Za-z][A-Za-z' -]{1,30}$", word):
            dropped.append(word or "?")
            continue
        if not example or not re.search(r"\b%s\b" % re.escape(word.lower()), example.lower()):
            dropped.append(word)
            continue
        if not usable_word(word):
            dropped.append(word)
            continue
        cards.append({
            "word": word,
            "phonetic": str(item.get("phonetic") or "").strip()[:30],
            "meaning": str(item.get("meaning") or "").strip()[:20],
            "example_en": example[:160],
            "example_cn": str(item.get("example_cn") or "").strip()[:160],
        })
    if dropped:
        logger.info("词卡过滤：丢弃 %s", "、".join(dropped[:8]))
    logger.info("词卡生成：目标=%s 请求=%d 通过=%d", goal_title, int(count), len(cards))
    return cards


def usable_word(word: str) -> bool:
    """这个词卡值不值得教：排除功能词与"学习元词汇"（practice/subject 这类）。"""
    value = (word or "").strip().lower()
    if not value or len(value) < 3 or len(value) > 20:
        return False
    if value in _META_WORDS or value in _FUNCTION_WORDS:
        return False
    return bool(re.match(r"^[a-z][a-z']*$", value))


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
    if extra.get("learning_focus"):
        return extra.get("listen_text") or ""
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


# =============================================================================
# 教学事实的确定性渲染与校验
#
# 背景（真机事故）：出场文案由人设链路自由生成时，出现过"提示词是 improve、
# 实际问的是 accept"，以及"他刚说不知道，她直接把释义说了出来"。所以：
#   事实句 / 提问原文 / 提示 全部在这里用模板产出（会被逐字校验），
#   人设链路只负责语气；一旦它改写词名、多塞英文词或提前报答案，就回退模板。
# =============================================================================

def fact_line(card: dict) -> str:
    """这个词/这道题的事实句（词形、音标、释义、例句），会被要求原样出现在文案里。"""
    word = (card or {}).get("content") or (card or {}).get("word") or ""
    extra = (card or {}).get("extra") or {}
    if extra.get("learning_focus"):
        return "题目：%s" % (extra.get("prompt") or word)
    parts = [word]
    if extra.get("phonetic"):
        parts.append(extra["phonetic"])
    if card.get("answer"):
        parts.append("释义：%s" % card["answer"])
    if extra.get("example_en"):
        parts.append("例句：%s" % extra["example_en"])
    if extra.get("example_cn"):
        parts.append(extra["example_cn"])
    return " | ".join(part for part in parts if part)


def _squash(text: str) -> str:
    """去掉空白与标点后再比对（发送链路会整理标点，不该因此误判"事实丢了"）。"""
    return re.sub(r"[\s，。、；：,.;:!！?？…\"'“”‘’（）()\[\]「」/|·-]", "", text or "")


def _has_phonetic(text: str, phonetic: str) -> bool:
    """音标可能被发送链路的标点整理改过（'·' 之类），只比对纯字母数字部分。"""
    wanted = re.sub(r"[^0-9a-z]", "", (phonetic or "").lower())
    return len(wanted) >= 2 and wanted in re.sub(r"[^0-9a-z]", "", (text or "").lower())


def missing_facts(card: dict, sent_text: str, expect: list = None) -> list:
    """发送后校验：这一轮必须送达的事实是不是真的发出去了（防发送链路截断）。

    真机事故：词卡事实句被按 30 字硬切，"释义：改善，提高"被切成"…改善，提"，
    例句整段被丢掉，用户在手机上看到的就是半截话。
    """
    value = sent_text or ""
    text = _squash(value)
    word = (card or {}).get("content") or ""
    extra = (card or {}).get("extra") or {}
    lost = []
    for item in expect or []:
        if item == "word":
            if word and word.lower() not in value.lower():
                lost.append("word:" + word)
        elif item == "answer":
            if (card or {}).get("answer") and card["answer"] not in value:
                lost.append("answer")
        elif item == "example":
            example = _squash(extra.get("example_en"))
            if example and example not in text:
                lost.append("example")
        elif item == "prompt":
            prompt = _squash(extra.get("prompt"))
            if prompt and prompt not in text:
                lost.append("prompt")
        elif item == "phonetic":
            if extra.get("phonetic") and not _has_phonetic(value, extra["phonetic"]):
                lost.append("phonetic")
    return lost


def present_template(card: dict, lead: str = "") -> str:
    """出场文案的模板兜底：一定包含词形与例句，不含答案。"""
    word = (card or {}).get("content") or ""
    extra = (card or {}).get("extra") or {}
    if extra.get("learning_focus"):
        return "%s，来看这道题：%s。你先说说看" % (lead or "好，接着来", extra.get("prompt") or word)
    head = "%s，我们看这一个：%s。" % (lead or "好", word)
    if extra.get("phonetic"):
        head += "%s。" % extra["phonetic"]
    example = (extra.get("example_en") or "").rstrip(".?!")
    if example:
        head += "例句 %s。" % example
    return head + "念两遍给你听"


def explain_template(card: dict, lead: str = "") -> str:
    """讲解文案的模板兜底：到这一步才允许把答案说清楚。"""
    word = (card or {}).get("content") or ""
    meaning = (card or {}).get("answer") or ""
    extra = (card or {}).get("extra") or {}
    if extra.get("learning_focus"):
        return "%s，这题是「%s」，答案是「%s」。%s" % (
            lead or "没事", extra.get("prompt") or word, meaning,
            (extra.get("explanation") or "").rstrip("。") + "。")
    text = "%s，「%s」就是「%s」的意思。" % (lead or "没事", word, meaning)
    example = (extra.get("example_en") or "").rstrip(".?!")
    if example:
        text += "例句是 %s。" % example
    return text + "记住了没"


def retry_template(card: dict, lead: str = "", hint: str = "") -> str:
    """答错时的模板兜底：只给线索，绝不出现这个词和它的释义。"""
    clue = (hint or derive_hint(card)).rstrip("。")
    return "%s，%s，你再想想" % (lead or "没事", clue)


def fallback_template(card: dict, event: str = "", lead: str = "", hint: str = "") -> str:
    """按事件挑兜底模板：出场/讲解说事实，答错轮只给线索。"""
    if event == "retry":
        return retry_template(card, lead, hint)
    if event in ("explain", "repeat"):
        return explain_template(card, lead)
    return present_template(card, lead)


def question_template(question: str, lead: str = "") -> str:
    """提问文案的模板兜底：必须原样包含问题（问题里不能有释义）。"""
    return "%s：%s" % (lead or "来，考你一个", question)


def study_fact_instruction(facts: str) -> str:
    """发布链路的事实约束：这一段必须逐字出现在最终文案里。"""
    value = (facts or "").strip()
    if not value:
        return ""
    return ("\n\n【必须原样出现】下面这段是这次的教学事实，必须一字不改地出现在你的回复里"
            "（可以放在任何位置）：" + value)


def _is_chinese(value: str) -> bool:
    return any("\u4e00" <= ch <= "\u9fff" for ch in value or "")


def lead_tail(lead: str, limit: int = 24) -> str:
    """开场白只取最后一句（多条消息被合并时，避免把上一张卡的话带进来）。"""
    value = re.sub(r"\s+", " ", (lead or "").strip())
    if not value:
        return ""
    tail = re.split(r"[。！？!?\n]", value)
    tail = [part.strip() for part in tail if part.strip()]
    return (tail[-1] if tail else "")[:limit]


def _english_words(text: str) -> set:
    return {word.lower() for word in re.findall(r"[A-Za-z][A-Za-z']{2,}", text or "")}


def _strip_phonetic(text: str) -> str:
    """去掉音标（/ˈpræktɪs/ 或 [..]）：音标不是"额外的英文词"，不该被当成换词。"""
    value = re.sub(r"/[^/\n]{2,40}/", " ", text or "")
    return re.sub(r"\[[^\]\n]{2,40}\]", " ", value)


def _means_leaked(meaning: str, reply: str) -> bool:
    """答错时，释义（或它的两字片段）出现在回复里就算提前报答案。

    覆盖"改善，提高"被说成"变好、提高"这种改写：整串不相等，但"提高"已经泄露了。
    """
    value = (meaning or "").strip()
    if not value or not _is_chinese(value):
        return False
    plain = re.sub(r"[^\u4e00-\u9fff]", "", value)
    if len(plain) <= 2:
        return value in reply or plain in reply
    for index in range(len(plain) - 1):
        if plain[index:index + 2] in reply:
            return True
    return False


def check_reply(card: dict, reply: str, event: str = "", asked: str = "") -> tuple:
    """文案校验：返回 (是否可用, 原因, 是否泄露答案)。

    只做三类硬检查，不追求覆盖所有问题：
    1. 词名一致性——出场/讲解文案里不得出现卡片之外的英文词（真机事故里
       提示词是 improve，模型却编了 accept / insight）；
    2. 答案泄露——retry（还没答对）时不得出现释义或把单词再念一遍；
    3. 事实缺失——文案里必须能找到这个词或它的释义。
    """
    value = (reply or "").strip()
    if not value:
        return False, "空文案", False
    word = ((card or {}).get("content") or (card or {}).get("word") or "").strip()
    meaning = ((card or {}).get("answer") or "").strip()
    extra = (card or {}).get("extra") or {}
    if extra.get("learning_focus"):
        # 语法/阅读/听力：只要求把题目带出来，不额外限制英文词
        prompt = extra.get("prompt") or word
        if event == "retry" and extra.get("explanation") and extra["explanation"] in value:
            return False, "答错时把讲解说了出来", True
        if prompt and prompt not in value:
            return False, "文案里没有这道题", False
        return True, "", False
    if not word:
        return False, "卡片缺少词形", False
    # 先查泄露（比"编了别的词"更严重），再查词名一致性。
    # 例句里本来就有这个词，所以先把原例句摘掉，只数"例句之外"的重复。
    example = extra.get("example_en") or ""
    outside = value.replace(example, " ") if example else value
    spoken = len(re.findall(r"\b%s\b" % re.escape(word), outside, re.IGNORECASE))
    if event == "retry" and spoken >= 1:
        return False, "答错时又把这个词说了一遍（等于报答案）", True
    if event == "retry" and _means_leaked(meaning, value):
        return False, "答错时把释义说了出来（等于报答案）", True
    if spoken >= 2:
        return False, "把这个词又强调了一遍（等于报答案）", True
    plain = _strip_phonetic(value)
    allowed = set(re.findall(r"[A-Za-z][A-Za-z']+", "%s %s" % (
        word, extra.get("example_en") or "")))
    allowed = {token.lower() for token in allowed}
    asked_words = {token.lower() for token in re.findall(r"[A-Za-z][A-Za-z']+", asked or "")}
    strangers = [token for token in _english_words(plain) - allowed - asked_words
                 if token not in ("ok", "okay")]
    if strangers:
        return False, "文案里出现了卡片之外的词：%s" % "、".join(sorted(strangers)[:4]), False
    if event == "retry":
        # 答错轮只给线索：既不许出现这个词，也不许出现释义（上面已查）
        return True, "", False
    if word.lower() not in value.lower() and (not meaning or meaning not in value):
        return False, "文案里没有这个词", False
    return True, "", False


def derive_hint(card: dict, attempt: int = 1) -> str:
    """提示只从卡片本地派生（不再让判分模型顺手编提示）。

    真机事故：判分模型编出"这个词是名词，首字母是 I（insight）"，而卡片是动词 improve。
    """
    word = ((card or {}).get("content") or "").strip()
    meaning = ((card or {}).get("answer") or "").strip()
    extra = (card or {}).get("extra") or {}
    if extra.get("learning_focus"):
        return (extra.get("explanation") or "再看一眼题干里的时间或关键词")[:40]
    first = (word[:1] or "").lower()
    pos = ""
    for name, marks in _POS_MARKS:
        if any(mark in meaning for mark in marks):
            pos = name
            break
    example = extra.get("example_en") or ""
    place = ""
    if example:
        position = example.lower().find(word.lower())
        if position >= 0:
            head = example[:position].count(" ")
            place = "它在例句里是第 %d 个词" % (head + 1)
    if attempt >= 2:
        bits = [bit for bit in (place, ("首字母是 %s" % first) if first else "") if bit]
        return ("；".join(bits) or "先想它在例句里的位置")[:40]
    bits = []
    if pos:
        bits.append("它是名词" if pos == "名词" else "它不是名词那种用法，是个%s" % pos)
    if place:
        bits.append(place)
    if first:
        bits.append("首字母是 %s" % first)
    return ("；".join(bits) or "先想它在例句里的位置")[:40]


def know_nothing(text: str) -> bool:
    """他是不是明确说不会（说了就不该再让他猜，也不能趁机报答案）。"""
    value = (text or "").strip().lower()
    if not value:
        return False
    return any(mark in value for mark in _DONT_KNOW_MARKS)


def clean_lead(text: str) -> str:
    """把模型给的开场白收拾干净：去掉残缺标点拼接，别让它拖一句没说完的话。"""
    value = re.sub(r"\s+", " ", (text or "").strip())
    value = value.strip("，。、；：,.;:")
    if not value:
        return ""
    if len(value) > 30:
        value = value[:30]
    return value


def touch_session(session_id: int):
    """记录一次真实互动（静置收尾以这个时间为准）。"""
    if not session_id:
        return
    adb.update_session(session_id, last_active_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"))


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
    logger.info("学习会话开始 [%s] 计划 %d 分钟", user_id, minutes)
    return adb.get_session(session_id)


def set_session_focus(session_id: int, focus: str) -> bool:
    """将本次会话锁定在一个方向，避免语法练到一半又跳回单词。"""
    return adb.update_session(session_id, summary="focus:%s" % (focus or "vocabulary"))


def session_focus(session: dict) -> str:
    summary = (session or {}).get("summary") or ""
    if summary.startswith("focus:"):
        return summary.split(":", 1)[1] or "vocabulary"
    return "vocabulary"


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
    logger.info("学习会话结束 [%s] 状态=%s 进度=%.2f 实际 %d 分钟",
                session.get("user_id") or "?", status, progress, actual)
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
    subject = (item.get("subject") or "").lower()
    if subject == "english" or subject.startswith("english_"):
        # 词卡、语法、阅读、听力分别回灌对应维度；不再把所有表现都误算成词汇。
        dimension = subject.split("_", 1)[1] if "_" in subject else "vocabulary"
        ep.record_practice_result(user_id, dimension, correct,
                                  source="word_card" if dimension == "vocabulary" else "practice")
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
    if extra.get("learning_focus"):
        return extra.get("prompt") or card.get("content") or ""
    parts = [card.get("content") or ""]
    if extra.get("phonetic"):
        parts.append(extra["phonetic"])
    if card.get("answer"):
        parts.append("释义：" + card["answer"])
    if extra.get("example_en"):
        parts.append("例句：" + extra["example_en"])
    return " ".join(parts)


def next_card(user_id: str, goal_id=None, focus: str = "vocabulary") -> dict:
    """取下一个该学的词（优先没学过的，其次最不熟的）。"""
    subject = "english" if focus == "vocabulary" else "english_%s" % focus
    pool = [row for row in adb.list_knowledge(user_id, goal_id=goal_id, limit=200)
            if (row.get("subject") or "").lower() == subject]
    unseen = [row for row in pool if row.get("status") == "unknown"]
    if unseen:
        return unseen[0]
    return pool[0] if pool else {}


async def ensure_focus_pool(user_id: str, goal, focus: str, llm_call=None,
                            want: int = WORDS_PER_SESSION) -> list:
    """准备语法/阅读/听力小练习；答案同样进入知识点与复习机制。"""
    focus = focus if focus in _FOCUS_ITEMS else "grammar"
    goal_id = (goal or {}).get("id")
    subject = "english_%s" % focus
    pool = [row for row in adb.list_knowledge(user_id, goal_id=goal_id, limit=200)
            if (row.get("subject") or "").lower() == subject]
    unseen = [row for row in pool if row.get("status") == "unknown"]
    if len(unseen) >= want:
        return unseen[:want]
    for item in _FOCUS_ITEMS[focus]:
        title, prompt, answer, explanation, *listen_text = item
        adb.add_knowledge(
            user_id, title, answer=answer, subject=subject, goal_id=goal_id,
            extra={"learning_focus": focus, "prompt": prompt, "explanation": explanation,
                   "listen_text": listen_text[0] if listen_text else ""},
        )
    pool = [row for row in adb.list_knowledge(user_id, goal_id=goal_id, limit=200)
            if (row.get("subject") or "").lower() == subject]
    return [row for row in pool if row.get("status") == "unknown"][:want]


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
    if extra.get("learning_focus"):
        return extra.get("prompt") or word
    if mastery >= 0.5:
        return "「%s」用英语怎么说？" % meaning if meaning else "这个词用英语怎么说？"
    if extra.get("example_en") and mastery < 0.2:
        return "例句里那个「%s」是什么意思？" % word
    return "「%s」是什么意思？" % word


def ask_question(session_id: int, card: dict, mastery: float = 0.0) -> str:
    """把某张卡设成"当前待答题目"，返回问题文本。"""
    question = build_question(card, mastery)
    adb.update_session(session_id, pending_knowledge_id=card.get("id"), ask_attempts=0)
    touch_session(session_id)
    # 提问即算"这次会话碰过这个内容"：会话进度以它为准，重启也不丢
    if card.get("id"):
        try:
            adb.update_knowledge(card["id"], last_seen=datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        except Exception as exc:  # noqa: BLE001
            logger.debug("标记提问时间失败: %s", exc)
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
    "只输出 JSON：{{\"correct\": true/false}}，不要输出提示、解释或任何多余内容。"
)


async def judge_with_llm(card: dict, user_text: str, llm_call) -> dict:
    """只判对错；提示一律由 derive_hint 本地派生（判分模型编过假提示）。"""
    if llm_call is None:
        return {"correct": False}
    try:
        raw = await llm_call(
            [{"role": "system", "content": JUDGE_PROMPT.format(
                word=card.get("content") or "", answer=card.get("answer") or "")},
             {"role": "user", "content": user_text}],
            temperature=0.1, max_tokens=60, disable_thinking=True,
        )
    except Exception as exc:
        logger.warning("判分调用失败: %s", exc)
        return {"correct": False}
    text = raw or ""
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        return {"correct": False}
    import json
    try:
        data = json.loads(text[start:end + 1])
    except (ValueError, TypeError):
        return {"correct": False}
    return {"correct": bool(data.get("correct"))}


async def grade_answer(user_id: str, session: dict, user_text: str, llm_call=None) -> dict:
    """判分 + 决定下一步（引导式：先提示、再让他试、最后才解释）。

    与真机事故相关的两条硬规则：
    - 他明确说"不知道/不会" → 不让他继续猜，也不趁机报答案，直接进入讲解；
    - 同一句话只判一次（消息重放时不再第二次扣掌握度）。
    """
    card = pending_card(session)
    if not card:
        return {"verdict": "no_question"}
    said = (user_text or "").strip()
    last = (session.get("last_judged_text") or "").strip()
    if last and said and said == last:
        logger.info("学习判分：检测到重复消息，改走讲解（词=%s）", card.get("content"))
        clear_question(session["id"])
        return {"verdict": "repeat", "card": card, "result": {}}
    if not know_nothing(said):
        correct = judge_local(card, user_text)
        if not correct:
            judged = await judge_with_llm(card, user_text, llm_call)
            correct = bool(judged.get("correct"))
    else:
        correct = False
    attempts = int(session.get("ask_attempts") or 0)
    if correct:
        result = record_answer(user_id, card["id"], correct=True)
        clear_question(session["id"])
        logger.info("学习判分：答对 %s（掌握度 %s）", card.get("content"),
                    result.get("mastery"))
        return {"verdict": "correct", "card": card, "result": result}
    # 答错：先记一次错误（掌握度下降、安排复习），但仍在本次会话里给他机会
    result = record_answer(user_id, card["id"], correct=False)
    attempts += 1
    adb.update_session(session["id"], ask_attempts=attempts,
                       last_judged_text=said[:80],
                       last_active_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    if know_nothing(said):
        # 明确说不会：直接讲清楚，不再追问（问第二次等于送分，也让他难受）
        clear_question(session["id"])
        logger.info("学习判分：他说不会，直接讲解 %s", card.get("content"))
        return {"verdict": "explain", "card": card, "result": result,
                "hint": derive_hint(card, attempts), "reason": "said_dont_know"}
    if attempts >= RETRY_LIMIT:
        clear_question(session["id"])
        logger.info("学习判分：两次未答对，讲解 %s", card.get("content"))
        return {"verdict": "explain", "card": card, "result": result,
                "hint": derive_hint(card, attempts)}
    logger.info("学习判分：答错，给提示 %s（第 %d 次）", card.get("content"), attempts)
    return {"verdict": "retry", "card": card, "result": result,
            "hint": derive_hint(card, attempts), "attempts": attempts}


def step_instruction(verdict: dict) -> str:
    """把判分结果翻译成"给她的内部指示"（不生成最终文案，也不含系统话说）。"""
    card = verdict.get("card") or {}
    word = card.get("content") or ""
    meaning = card.get("answer") or ""
    extra = card.get("extra") or {}
    example = extra.get("example_en") or ""
    kind = verdict.get("verdict")
    said_nothing = verdict.get("reason") == "said_dont_know"
    if extra.get("learning_focus"):
        focus_name = {"grammar": "语法", "reading": "阅读", "listening": "听力"}.get(
            extra.get("learning_focus"), "英语")
        prompt = extra.get("prompt") or word
        explanation = extra.get("explanation") or ""
        if kind == "correct":
            return (f"他刚才把这道{focus_name}小练习答对了。夸他一句，然后自然带到下一个，"
                    "不要重复讲成长篇课程。")
        if kind == "retry":
            hint = verdict.get("hint") or explanation
            return (f"他这道{focus_name}小练习还没想起来（题目是：{prompt}）。"
                    f"给一个不直接报答案的小提示：{hint}，然后等他再试。")
        if kind == "explain":
            return (f"他卡在这道{focus_name}小练习（题目是：{prompt}，答案是「{meaning}」）"
                    + ("，他说不会。" if said_nothing else "，连着两次没答对。")
                    + f"现在用生活化的方式讲清楚：{explanation}，一次只讲这一个点。")
    if kind == "correct":
        return (f"他刚才答对了（「{word}」）。用你自己的语气夸他一句，很短，"
                "然后说接着下一个；不要重复讲这个词的知识点。")
    if kind == "retry":
        hint = verdict.get("hint") or ""
        clue = f"只准用这个提示：{hint}。" if hint else "只提醒首字母或它在例句里的位置。"
        return (f"他刚才没答对（这个词是「{word}」，**绝对不要说出它的意思，"
                "也不要再把这个词念一遍**）。"
                f"{clue}鼓励他再试一次，一句话，不要直接报答案。")
    if kind == "repeat":
        return (f"他刚发的消息和上一条一样（他可能在复读或者没跟上）。"
                f"别当成又答错一次，也别再说他答错了；把「{word}」（意思是「{meaning}」"
                + (f"，例句 {example}" if example else "")
                + "）自然讲清楚，一句话，然后问他这样懂没懂。")
    if kind == "explain":
        return (f"他{'说不会' if said_nothing else '连着两次没答对'}（「{word}」，"
                f"意思是「{meaning}」"
                + (f"，例句 {example}" if example else "")
                + "）。现在把答案讲清楚：一次只讲这一个词，用你自己的语气解释一下，"
                "别像老师念课本，也别顺带塞一堆别的词。")
    return ""


def review_queue(user_id: str, limit: int = 3) -> list:
    """今天该复习的词（到期的优先）。"""
    return adb.due_reviews(user_id, limit=limit)


def session_seen(user_id: str, session: dict) -> list:
    """本次会话里已经考过的知识点（按 last_seen 判断，重启也不丢）。

    同一张卡可能在会话里被看到多次（出场 + 重问），必须按知识点去重，
    否则进度会虚高、提前触发"本次结束"。
    """
    started = (session or {}).get("started_at") or ""
    focus = session_focus(session)
    subject = "english" if focus == "vocabulary" else "english_%s" % focus
    rows = [row for row in adb.list_knowledge(user_id, limit=200)
            if (row.get("subject") or "").lower() == subject
            if row.get("last_seen") and str(row["last_seen"]) >= started]
    unique, seen = [], set()
    for row in rows:
        if row["id"] in seen:
            continue
        seen.add(row["id"])
        unique.append(row)
    return unique


# 学习会话静置多久算"他走开了"（超过就按已完成的部分收尾，不留挂着的会话）
STALL_MINUTES = 30


def stall_session(session: dict) -> dict:
    """把静置的会话按实际进度收尾；不做成"未完成"的责备，只如实记进度。"""
    if not session or not session.get("id"):
        return {}
    user_id = session.get("user_id") or ""
    done = len(session_seen(user_id, session))
    total = WORDS_PER_SESSION
    result = finish_session(session["id"], done=done, total=total,
                            summary="他离开了，本次过 %d 个内容" % done,
                            force_status=("completed" if done >= total else "partial"))
    logger.info("学习会话静置收尾 [%s] 过了 %d/%d", user_id, done, total)
    return result


def sweep_stalled_sessions(minutes: int = STALL_MINUTES, limit: int = 20) -> list:
    """定期清理静置的会话（只有超过 minutes 没有任何动静的才会被收尾）。"""
    cutoff = (datetime.now() - timedelta(minutes=max(5, int(minutes)))).strftime(
        "%Y-%m-%d %H:%M:%S")
    closed = []
    try:
        rows = adb.open_sessions_before(cutoff, limit=limit)
    except Exception as exc:  # noqa: BLE001
        logger.warning("查询静置学习会话失败: %s", exc)
        return closed
    for row in rows:
        session = dict(row)
        try:
            result = stall_session(session)
        except Exception as exc:  # noqa: BLE001
            logger.warning("收尾静置会话失败 [%s]: %s", session.get("id"), exc)
            continue
        if result:
            closed.append({"session": session, "result": result})
    return closed


def pick_next_for_session(user_id: str, session: dict, goal_id=None) -> dict:
    """下一次要考的内容：先复习到期的，再学没学过的，最后挑最不熟的。"""
    focus = session_focus(session)
    subject = "english" if focus == "vocabulary" else "english_%s" % focus
    pool = [row for row in adb.list_knowledge(user_id, goal_id=goal_id, limit=200)
            if (row.get("subject") or "").lower() == subject]
    seen_ids = {row["id"] for row in session_seen(user_id, session)}
    due = [row for row in adb.due_reviews(user_id, limit=20)
           if row["id"] not in seen_ids and (row.get("subject") or "").lower() == subject]
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
