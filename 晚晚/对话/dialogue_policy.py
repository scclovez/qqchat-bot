# -*- coding: utf-8 -*-
"""每轮对话决策层。

在主模型生成回复前，用本地规则形成一份轻量策略：识别意图/情绪、从连续消息中挑出
必须回应的重点、决定回复方式与长度，并约束这一轮可使用的互动和多模态动作。
不额外请求大模型，不保存用户原话。
"""
from dataclasses import dataclass
import re
from typing import Iterable, Tuple


URGENT_WORDS = (
    "救命", "出事", "危险", "受伤", "流血", "报警", "急救", "医院",
    "自杀", "自残", "割腕", "跳楼", "不想活", "活不下去",
)
DISTRESS_WORDS = (
    "难受", "不舒服", "害怕", "焦虑", "崩溃", "委屈", "想哭", "哭了",
    "失眠", "睡不着", "压力大", "好累", "撑不住", "心情不好", "疼",
)
CONFLICT_WORDS = (
    "生气", "吵架", "不理你", "讨厌你", "烦你", "失望", "分手", "骗我",
    "敷衍", "你变了", "不喜欢你", "别烦我", "滚", "去死",
)
APOLOGY_WORDS = ("对不起", "抱歉", "我错了", "原谅我", "是我不好")
ADVICE_WORDS = (
    "怎么办", "怎么做", "怎么弄", "建议", "帮我想", "你觉得该", "应该怎么",
    "能不能帮", "如何", "选哪个", "哪个好",
)
QUESTION_WORDS = (
    "为什么", "怎么", "什么", "谁", "哪", "多少", "几点", "是不是", "有没有",
    "能不能", "可以吗", "在干嘛", "干什么", "你觉得", "知道吗",
)
AFFECTION_WORDS = (
    "喜欢你", "爱你", "想你", "亲亲", "抱抱", "老婆", "宝贝", "宝宝",
    "陪我", "舍不得", "要你", "想见你",
)
PLAY_WORDS = ("哈哈", "嘿嘿", "笑死", "逗你", "笨蛋", "坏蛋", "略略", "哼哼")
SHARE_WORDS = (
    "我今天", "我刚", "刚才", "刚刚", "跟你说", "告诉你", "你看", "我买了",
    "我吃了", "我去了", "我做了", "我看到", "发生了", "终于",
)
VISUAL_REQUEST_RE = re.compile(
    r"(?:想看|看看|给我看|让我看|画给我).{0,8}"
    r"(?:照片|图片|自拍|晚霞|风景|小猫|小狗|穿搭|穿什么)"
    r"|(?:发|拍|画)(?:张|个)?.{0,8}"
    r"(?:照片|图片|自拍|晚霞|风景|小猫|小狗|穿搭).{0,8}"
    r"(?:给我|我看|看看)"
    r"|(?:照片|图片|自拍).{0,6}(?:发给我|给我看)"
)


@dataclass(frozen=True)
class TurnPlan:
    intent: str
    emotion: str
    response_goal: str
    focus: Tuple[str, ...]
    multi_message: bool
    ask_followup: bool
    target_min: int
    target_max: int
    hard_max: int
    max_parts: int
    allow_voice: bool
    allow_image: bool
    allow_sticker: bool
    allow_afterthought: bool
    allow_dual_voice: bool
    allowed_tools: Tuple[str, ...]

    def injection(self) -> str:
        """把策略转换为主模型可执行、不可见的本轮提示。"""
        focus_text = "；".join(f"“{item}”" for item in self.focus)
        parts = [
            f"本轮主要意图：{self.intent}；对方情绪：{self.emotion}。",
            f"回应目标：{self.response_goal}。",
        ]
        if focus_text:
            parts.append(f"必须接住的重点：{focus_text}。")
        if self.multi_message:
            parts.append("这些内容来自他连续发出的多条消息，要合在一起理解；不要逐行回复或逐句复述。")
        if self.ask_followup:
            parts.append("可以在回应后自然问一个具体问题，但最多一个，不能连环追问。")
        else:
            parts.append("这轮不要为了延长聊天硬加问题；把眼前这件事回应完整即可。")
        parts.append(
            f"回复以{self.target_min}~{self.target_max}字为宜，最多{self.max_parts}条；"
            "先给最重要的那句，不写总结式套话。"
        )
        if not self.allow_image:
            parts.append("本轮不要输出【插图】标记，也不要主动承诺发照片。")
        if not self.allowed_tools:
            parts.append("本轮只用语言回应，不戳一戳、不点赞、不改状态或签名。")
        parts.append("这份策略只用于组织回复，绝不能向对方提到意图分类、策略或字数要求。")
        return "\n（【本轮对话策略】" + "".join(parts) + "）"


def _contains(text: str, words: Iterable[str]) -> bool:
    return any(word in text for word in words)


def _looks_question(text: str) -> bool:
    return bool(re.search(r"[？?]", text)) or _contains(text, QUESTION_WORDS)


def _clean_focus(text: str, limit: int = 34) -> str:
    value = re.sub(r"\s+", " ", (text or "").strip())
    if len(value) <= limit:
        return value
    cut = max((value.rfind(mark, 0, limit + 1) for mark in "，。！？!?；;"), default=-1)
    return value[:cut if cut >= 8 else limit].rstrip("，。！？!?；; ") + "…"


def _line_score(line: str, index: int, total: int) -> int:
    score = 10 + index  # 相同条件下稍偏向最后说出的内容
    if _contains(line, URGENT_WORDS):
        score += 100
    if _contains(line, DISTRESS_WORDS):
        score += 80
    if _contains(line, CONFLICT_WORDS):
        score += 70
    if _looks_question(line):
        score += 55
    if _contains(line, APOLOGY_WORDS):
        score += 50
    if _contains(line, AFFECTION_WORDS):
        score += 35
    if index == total - 1:
        score += 8
    return score


def _pick_focus(lines) -> Tuple[str, ...]:
    if not lines:
        return ()
    ranked = sorted(
        enumerate(lines), key=lambda item: _line_score(item[1], item[0], len(lines)), reverse=True,
    )
    picked = [ranked[0]]
    # 多条消息只有第二个重点分数足够高时才再接一个，避免逐条回执。
    if len(ranked) > 1 and _line_score(ranked[1][1], ranked[1][0], len(lines)) >= 58:
        picked.append(ranked[1])
    picked.sort(key=lambda item: item[0])
    return tuple(_clean_focus(line) for _, line in picked if line.strip())


def _classify(text: str):
    if _contains(text, URGENT_WORDS):
        return "紧急关怀", "高压/可能需要立即接住"
    if _contains(text, DISTRESS_WORDS):
        return "倾诉安慰", "低落或不适"
    if _contains(text, CONFLICT_WORDS):
        return "矛盾修复", "生气或受伤"
    if _contains(text, APOLOGY_WORDS):
        return "回应道歉", "歉疚/求和"
    if _contains(text, ADVICE_WORDS):
        return "一起解决问题", "需要判断或建议"
    if _looks_question(text):
        return "直接回答", "好奇或等待答复"
    if _contains(text, AFFECTION_WORDS):
        return "亲密回应", "亲近"
    if _contains(text, PLAY_WORDS):
        return "接梗玩闹", "轻松"
    if _contains(text, SHARE_WORDS) or len(text) >= 28:
        return "回应分享", "想被听见"
    if len(text.strip()) <= 5:
        return "轻量应答", "随口互动"
    return "自然接话", "平常"


def plan_turn(user_text: str, *, user_sent_voice: bool = False,
              user_sent_image: bool = False, is_intimate: bool = True) -> TurnPlan:
    """生成本轮策略。返回值只存在于当前处理过程，不写数据库。"""
    text = (user_text or "").strip()
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    intent, emotion = _classify(text)
    multi = len(lines) >= 2
    focus = _pick_focus(lines)
    # 仅把明确的视觉请求交给配图机制；普通地提到“照片/小猫”仍按聊天处理。
    visual_requested = bool(VISUAL_REQUEST_RE.search(text)) and not user_sent_image

    configs = {
        "紧急关怀": dict(goal="先确认当下是否安全，再给一个立即可做的动作；不撒娇、不玩梗",
                         ask=True, size=(22, 65, 72, 2)),
        "倾诉安慰": dict(goal="先回应具体感受，再陪他往下说；不急着讲道理",
                         ask=True, size=(18, 56, 64, 2)),
        "矛盾修复": dict(goal="回应具体不满或伤害，清楚表达感受与边界；不拿甜话糊弄",
                         ask=False, size=(16, 52, 60, 2)),
        "回应道歉": dict(goal="接住道歉，说明自己真实的感受和是否缓和；不反复翻旧账",
                         ask=False, size=(12, 42, 50, 2)),
        "一起解决问题": dict(goal="先给直接判断，再给一个最有用、能执行的建议",
                         ask=False, size=(20, 62, 72, 2)),
        "直接回答": dict(goal="先直接回答问题；不知道就坦白，不绕到恋爱套话上",
                         ask=False, size=(8, 45, 54, 2)),
        "亲密回应": dict(goal="自然接住并回给他，不升级成大段誓言或固定等待剧情",
                         ask=False, size=(6, 32, 42, 2)),
        "接梗玩闹": dict(goal="顺着当前梗接一句，节奏轻快，不解释笑点",
                         ask=False, size=(3, 24, 32, 2)),
        "回应分享": dict(goal="抓住一个具体细节回应，让他感到你真的听见了",
                         ask=True, size=(10, 42, 52, 2)),
        "轻量应答": dict(goal="对称地回一个自然短句，不自行扩写新剧情",
                         ask=False, size=(2, 12, 24, 1)),
        "自然接话": dict(goal="回应当前这句话最有信息量的部分，不额外总结或转移话题",
                         ask=False, size=(6, 30, 40, 2)),
    }
    cfg = configs[intent]
    min_len, max_len, hard_max, max_parts = cfg["size"]

    serious = intent in ("紧急关怀", "倾诉安慰", "矛盾修复", "回应道歉")
    task_like = intent in ("一起解决问题", "直接回答")
    playful = intent in ("亲密回应", "接梗玩闹")
    # 连续多条通常需要一份稍完整的回复，但仍不允许按行拆成很多条。
    if multi:
        max_len = min(62, max_len + 8)
        hard_max = min(72, hard_max + 8)
        max_parts = min(2, max_parts)

    if not is_intimate:
        allowed_tools = ()
    elif intent == "接梗玩闹":
        allowed_tools = ("react_message", "poke_user")
    elif intent == "亲密回应":
        allowed_tools = ("react_message", "send_like")
    else:
        allowed_tools = ()

    allow_voice = not (intent == "紧急关怀" or task_like) or user_sent_voice
    allow_image = bool(visual_requested and not serious and not task_like)
    # 一轮只留一个额外动作出口，配图与戳一戳/点赞不并行。
    if allow_image:
        allowed_tools = ()
    allow_sticker = playful or intent in ("轻量应答", "回应分享")
    allow_afterthought = intent in ("亲密回应", "回应分享") and not multi
    allow_dual_voice = intent in ("亲密回应", "回应道歉") and not multi

    return TurnPlan(
        intent=intent,
        emotion=emotion,
        response_goal=cfg["goal"],
        focus=focus,
        multi_message=multi,
        ask_followup=bool(cfg["ask"] and not _looks_question(text)),
        target_min=min_len,
        target_max=max_len,
        hard_max=hard_max,
        max_parts=max_parts,
        allow_voice=allow_voice,
        allow_image=allow_image,
        allow_sticker=allow_sticker,
        allow_afterthought=allow_afterthought,
        allow_dual_voice=allow_dual_voice,
        allowed_tools=allowed_tools,
    )
