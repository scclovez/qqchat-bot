# -*- coding: utf-8 -*-
"""学习图片解析与打卡校验（Phase 5）。

原则（对应需求）：
- 复用现有视觉模型调用（llm_providers 的 vision 分派），不新造一套图片模型；
- 模型只负责"把图看结构化"，**任务是否完成由代码判定**（Verification Engine）；
- 置信度不足、日期不符、数量不够 → 一律不自动完成，最多记部分进度；
- 只产出结构化结果与"给她的内部指示"，不生成最终 QQ 文案。

图片类型：study_checkin（打卡）/ homework（作业）/ exam（试卷）/ textbook（教材）/
notes（笔记）/ other（与学习无关，交回原有图片逻辑）。
"""
import json
import logging
import re
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

IMAGE_TYPES = ("study_checkin", "homework", "exam", "textbook", "notes", "other")

CONFIDENCE_FLOOR = 0.75      # 低于此置信度不自动判定完成
DATE_TOLERANCE_DAYS = 1      # 打卡日期允许的偏差

VISION_PROMPT = (
    "你是学习图片结构化解析器。请判断这张图片属于哪一类，并抽取信息，只输出 JSON：\n"
    '{"type": "study_checkin|homework|exam|textbook|notes|other",'
    ' "confidence": 0~1 的小数,'
    ' "checkin": {"app": 应用名, "date": "YYYY-MM-DD" 或 "", "count": 已完成数量或 null,'
    '             "target": 目标数量或 null, "minutes": 学习分钟数或 null},'
    ' "questions": [{"index": 题号, "question": 题干（≤60字）, "user_answer": 他的答案或 "",'
    '                "correct_answer": 正确答案或 "", "topic": 知识点（≤12字）}],'
    ' "material": {"subject": 科目或主题, "topics": [要点，最多5条], "summary": "30字内概括"}}\n'
    "规则：不确定的字段填 null 或空字符串；questions 最多 3 条，只取图上真实可见的题目；"
    "不要编造。置信度表示你对分类与抽取结果的把握。"
)


def _extract_json(text: str):
    text = text or ""
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        data = json.loads(text[start:end + 1])
    except (ValueError, TypeError):
        return None
    return data if isinstance(data, dict) else None


def _to_int(value):
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return int(value)
    m = re.search(r"\d+", str(value))
    return int(m.group()) if m else None


def _to_float(value, default=0.0):
    try:
        return max(0.0, min(1.0, float(value)))
    except (TypeError, ValueError):
        return default


def _clean_date(value: str) -> str:
    text = str(value or "").strip()
    m = re.search(r"(\d{4})[-/年]?\s*(\d{1,2})[-/月]?\s*(\d{1,2})", text)
    if m:
        return "%04d-%02d-%02d" % (int(m.group(1)), int(m.group(2)), int(m.group(3)))
    m = re.search(r"(\d{1,2})[-/月](\d{1,2})", text)
    if m:
        return "%04d-%02d-%02d" % (datetime.now().year, int(m.group(1)), int(m.group(2)))
    return ""


def normalize(data: dict, now: datetime = None) -> dict:
    """把模型输出规整成可信结构（逐字段校验，越界即丢弃）。"""
    now = now or datetime.now()
    if not isinstance(data, dict):
        return {}
    kind = str(data.get("type") or "").strip().lower()
    if kind not in IMAGE_TYPES:
        return {}
    result = {"type": kind, "confidence": _to_float(data.get("confidence"), 0.0),
              "checkin": {}, "questions": [], "material": {}}
    checkin = data.get("checkin") if isinstance(data.get("checkin"), dict) else {}
    if checkin:
        result["checkin"] = {
            "app": str(checkin.get("app") or "").strip()[:20],
            "date": _clean_date(checkin.get("date")),
            "count": _to_int(checkin.get("count")),
            "target": _to_int(checkin.get("target")),
            "minutes": _to_int(checkin.get("minutes")),
        }
    for item in (data.get("questions") or [])[:3]:
        if not isinstance(item, dict):
            continue
        question = str(item.get("question") or "").strip()[:120]
        if len(question) < 2:
            continue
        result["questions"].append({
            "index": str(item.get("index") or len(result["questions"]) + 1)[:6],
            "question": question,
            "user_answer": str(item.get("user_answer") or "").strip()[:60],
            "correct_answer": str(item.get("correct_answer") or "").strip()[:60],
            "topic": str(item.get("topic") or "").strip()[:20],
        })
    material = data.get("material") if isinstance(data.get("material"), dict) else {}
    if material:
        result["material"] = {
            "subject": str(material.get("subject") or "").strip()[:20],
            "topics": [str(t).strip()[:30] for t in (material.get("topics") or [])[:5] if str(t).strip()],
            "summary": str(material.get("summary") or "").strip()[:80],
        }
    if kind == "study_checkin" and not result["checkin"]:
        return {}
    if kind in ("homework", "exam") and not result["questions"]:
        return {}
    if kind in ("textbook", "notes") and not (result["material"].get("topics")
                                              or result["material"].get("summary")):
        return {}
    return result


async def analyze(vision_call, image_b64: str, mime: str, hint: str = "", now: datetime = None) -> dict:
    """调用视觉模型解析学习图片；失败或与学习无关时返回 {}（调用方回退原逻辑）。"""
    if vision_call is None or not image_b64:
        return {}
    messages = [
        {"role": "system", "content": VISION_PROMPT},
        {"role": "user", "content": [
            {"type": "text", "text": hint or "请解析这张图片。"},
            {"type": "image_url",
             "image_url": {"url": "data:%s;base64,%s" % (mime or "image/png", image_b64)}},
        ]},
    ]
    try:
        raw = await vision_call(messages)
    except Exception as exc:
        logger.warning("学习图片解析失败: %s", exc)
        return {}
    return normalize(_extract_json(raw), now)


# =============================================================================
# Verification Engine：由代码判定，绝不让模型直接改任务状态
# =============================================================================

def verify_checkin(checkin: dict, confidence: float, task=None, now: datetime = None) -> dict:
    """校验打卡是否成立，返回 {"verified", "progress", "reason", "count", "target"}。

    - 置信度不足 → 不判定完成（progress 仍可记录，供人工/后续确认）
    - 日期不是今天（含 1 天容差）→ 不判定完成
    - 数量不足 → 记部分进度（如 32/50）
    """
    now = now or datetime.now()
    checkin = checkin or {}
    target = _to_int((task or {}).get("target")) or _to_int(checkin.get("target")) or 0
    count = _to_int(checkin.get("count"))
    date_text = checkin.get("date") or ""
    result = {"verified": False, "progress": 0.0, "count": count, "target": target,
              "reason": "", "date": date_text}
    if confidence < CONFIDENCE_FLOOR:
        result["reason"] = "识别置信度不足（%.2f < %.2f），不自动判定完成" % (confidence, CONFIDENCE_FLOOR)
        return result
    if date_text:
        try:
            shot = datetime.strptime(date_text, "%Y-%m-%d").date()
        except ValueError:
            shot = None
        if shot is not None and abs((now.date() - shot).days) > DATE_TOLERANCE_DAYS:
            result["reason"] = "打卡日期不是今天（%s）" % date_text
            return result
    if count is None:
        result["reason"] = "没识别出完成数量，需要他确认一下"
        return result
    if target and target > 0:
        result["progress"] = max(0.0, min(1.0, count / float(target)))
        if count >= target:
            result["verified"] = True
        else:
            result["reason"] = "只完成了 %d/%d，记部分完成" % (count, target)
        return result
    # 没有目标数量：有明确数量就算完成
    result["verified"] = count > 0
    result["progress"] = 1.0 if result["verified"] else 0.0
    if not result["verified"]:
        result["reason"] = "没有可判定的完成量"
    return result


def checkin_instruction(verified: dict, task_title: str = "") -> str:
    """把校验结果翻译成给她的内部指示（不含系统话术）。"""
    count, target = verified.get("count"), verified.get("target")
    title = task_title or "今天的任务"
    if verified.get("verified"):
        return (f"他刚发来打卡截图，{title}完成了"
                + (f"（{count}/{target}）" if target else "")
                + "。用你自己的语气夸他一句，别像系统播报。")
    if verified.get("progress", 0) > 0:
        return (f"他发来打卡截图，但{title}只完成了一部分"
                + (f"（{count}/{target}）" if target else "")
                + f"。原因是：{verified.get('reason')}。用你自己的语气说一句，"
                "肯定他做了的部分，轻轻提一下还差多少，不要责备。")
    return (f"他发来一张打卡截图，但还不能算完成：{verified.get('reason')}。"
            "用你自己的语气问清楚（比如让他确认一下数量），一句话，别像审核。")


def questions_to_items(questions: list, goal_id=None, subject: str = "") -> list:
    """把作业/试卷里的题目转成知识点（正确答案内部留存，用于后续复习与纠错）。"""
    items = []
    for question in questions or []:
        if not question.get("question"):
            continue
        items.append({
            "content": question["question"],
            "answer": question.get("correct_answer") or "",
            "user_answer": question.get("user_answer") or "",
            "subject": subject or question.get("topic") or "作业",
            "goal_id": goal_id,
            "extra": {"topic": question.get("topic") or "",
                      "index": question.get("index") or "",
                      "user_answer": question.get("user_answer") or ""},
        })
    return items


def homework_instruction(items: list) -> str:
    """作业图片的引导式指示：只给第一题的题干，**不告诉他答案**。"""
    if not items:
        return ""
    first = items[0]
    rest = len(items) - 1
    tail = f"另外还有 {rest} 道题我先记下来了，等他处理完这题再说。" if rest else ""
    return (f"他发来了作业/试卷照片，我先收下了。现在**只讲第一题**：{first['content']}"
            + (f"（他的答案：{first['user_answer']}）" if first.get("user_answer") else "")
            + "请用你自己的语气引导他自己再想一遍——先给一个不透露答案的小提示，"
              "让他先说答案，**这次绝对不要直接报正确答案**。"
            + tail)


def material_items(material: dict, goal_id=None) -> list:
    """教材/笔记 → 本次材料（存成知识点，作为学习会话的输入材料）。"""
    material = material or {}
    if not (material.get("topics") or material.get("summary")):
        return []
    return [{
        "content": material.get("summary") or "、".join(material.get("topics") or [])[:60],
        "answer": "",
        "subject": material.get("subject") or "教材",
        "goal_id": goal_id,
        "extra": {"type": "material", "topics": material.get("topics") or [],
                  "summary": material.get("summary") or ""},
    }]


def material_instruction(material: dict) -> str:
    """教材/笔记 → 今天就学这个：先确认材料，再问一个最小的起点问题。"""
    material = material or {}
    topics = "、".join(material.get("topics") or [])
    subject = material.get("subject") or "这份材料"
    return (f"他说今天就学这个：{subject}"
            + (f"（要点：{topics}）" if topics else "")
            + "。用你自己的语气接下这份材料，然后只问一个最小的起点问题"
              "（比如从哪个部分开始），一句话，别一次列一堆计划。")


STUDY_IMAGE_WORDS = ("打卡", "今天学这个", "学这个", "作业", "试卷", "这张题", "这几道",
                     "题目", "笔记", "课本", "教材", "单词表", "错题")


def is_study_context(has_image: bool, text: str, session: dict) -> bool:
    """这张图该不该按"学习图片"处理？

    - 正在学习会话中 → 是；
    - 或者他嘴里明确提到打卡/作业/教材之类 → 是；
    - 其余一律交回原有图片逻辑（普通聊天发图不受影响）。
    """
    if not has_image:
        return False
    if session and str(session.get("status")) in ("active", "paused"):
        return True
    body = text or ""
    return any(word in body for word in STUDY_IMAGE_WORDS)
