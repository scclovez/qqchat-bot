# -*- coding: utf-8 -*-
"""情景记忆的规范化、合并、召回与自然遗忘。

本模块只处理普通 dict，不直接访问数据库，方便旧数据兼容与独立测试。情景记忆用于
保存有起因和结果的共同经历，不替代姓名、偏好等稳定画像。
"""
from __future__ import annotations

from datetime import datetime, timedelta
import re
import uuid


ACTIVE_STATUSES = {"pending", "ongoing"}
TERMINAL_STATUSES = {"completed", "cancelled"}
STATUS_MAP = {
    "待发生": "pending", "等待": "pending", "pending": "pending",
    "进行中": "ongoing", "ongoing": "ongoing",
    "已完成": "completed", "完成": "completed", "completed": "completed",
    "已取消": "cancelled", "取消": "cancelled", "cancelled": "cancelled",
}
CERTAINTY_MAP = {
    "confirmed": "confirmed", "确认": "confirmed", "明确": "confirmed",
    "mentioned": "mentioned", "提及": "mentioned", "可能": "mentioned",
}
_TERM_STOP = {
    "这个", "那个", "然后", "已经", "还是", "就是", "一下", "事情", "今天",
    "明天", "昨天", "刚刚", "现在", "用户", "对方", "自己",
}


def _now(now=None) -> datetime:
    return now if isinstance(now, datetime) else datetime.now()


def _iso(value: datetime) -> str:
    return value.replace(microsecond=0).isoformat(sep=" ")


def _parse_time(value, fallback=None):
    if isinstance(value, datetime):
        return value
    raw = str(value or "").strip().replace("Z", "+00:00")
    if raw:
        try:
            parsed = datetime.fromisoformat(raw)
            return parsed.replace(tzinfo=None) if parsed.tzinfo else parsed
        except ValueError:
            pass
    return fallback


def _text(value, limit: int) -> str:
    value = re.sub(r"\s+", " ", str(value or "")).strip()
    return value[:limit]


def _terms(text: str) -> set[str]:
    raw = str(text or "").lower()
    terms = set(re.findall(r"[a-z0-9_]{2,}", raw))
    for segment in re.findall(r"[\u4e00-\u9fff]{2,}", raw):
        for size in (2, 3, 4):
            terms.update(segment[i:i + size] for i in range(len(segment) - size + 1))
    return {term for term in terms if term not in _TERM_STOP}


def normalize_episode(item: dict, now=None) -> dict | None:
    """把模型或旧版本产生的事件整理为稳定结构；无主题时丢弃。"""
    if not isinstance(item, dict):
        return None
    current = _now(now)
    topic = _text(item.get("topic") or item.get("event") or item.get("title"), 48)
    if not topic:
        return None
    status = STATUS_MAP.get(str(item.get("status") or "").strip(), "pending")
    certainty = CERTAINTY_MAP.get(str(item.get("certainty") or "").strip(), "mentioned")
    try:
        importance = max(1, min(5, int(item.get("importance") or 2)))
    except (TypeError, ValueError):
        importance = 2
    try:
        followup_count = max(0, min(1, int(item.get("followup_count") or 0)))
    except (TypeError, ValueError):
        followup_count = 0
    created = _parse_time(item.get("created_at"), current)
    updated = _parse_time(item.get("updated_at"), created)
    followup_after = _parse_time(item.get("follow_up_after"), created + timedelta(hours=8))
    return {
        "id": _text(item.get("id"), 32) or uuid.uuid4().hex[:16],
        "topic": topic,
        "detail": _text(item.get("detail") or item.get("description"), 140),
        "time_hint": _text(item.get("time_hint") or item.get("time"), 36),
        "emotion": _text(item.get("emotion"), 24),
        "status": status,
        "result": _text(item.get("result"), 120),
        "importance": importance,
        "certainty": certainty,
        "created_at": _iso(created),
        "updated_at": _iso(updated),
        "follow_up_after": _iso(followup_after),
        "followup_count": followup_count,
    }


def prune_episodes(items, now=None, limit: int = 80) -> list[dict]:
    """让低价值小事随时间退出；重要经历保留更久，但总量仍有上限。"""
    current = _now(now)
    kept = []
    for raw in items or []:
        item = normalize_episode(raw, current)
        if not item:
            continue
        updated = _parse_time(item["updated_at"], current)
        age_days = max(0, (current - updated).days)
        if item["certainty"] == "mentioned":
            retention = 7 if item["importance"] <= 2 else 30
        elif item["status"] in TERMINAL_STATUSES:
            retention = 21 if item["importance"] <= 2 else 180
        else:
            retention = 45 if item["importance"] <= 2 else 180
        if age_days <= retention:
            kept.append(item)
    if len(kept) <= limit:
        return kept
    ranked = sorted(
        kept,
        key=lambda item: (
            item["importance"],
            item["certainty"] == "confirmed",
            item["status"] in ACTIVE_STATUSES,
            _parse_time(item["updated_at"], current),
        ),
        reverse=True,
    )[:limit]
    return sorted(ranked, key=lambda item: _parse_time(item["created_at"], current))


def _match_index(existing: list[dict], new: dict, now: datetime) -> int | None:
    needle = _text(new.get("replaces"), 80) or new["topic"]
    needle_terms = _terms(needle)
    best = None
    for index, old in enumerate(existing):
        # 一件已经结束的事与后来再次发生的同类事件分开保存。
        if old["status"] in TERMINAL_STATUSES and new["status"] in ACTIVE_STATUSES:
            continue
        old_terms = _terms(old["topic"])
        overlap = len(needle_terms & old_terms)
        topic_related = (
            new["topic"] == old["topic"]
            or needle == old["topic"]
            or (min(len(new["topic"]), len(old["topic"])) >= 2
                and (new["topic"] in old["topic"] or old["topic"] in new["topic"]))
        )
        if not topic_related and overlap < 2:
            continue
        age = max(0, (now - _parse_time(old["updated_at"], now)).days)
        score = (20 if topic_related else overlap) + (4 if old["status"] in ACTIVE_STATUSES else 0) - age
        if best is None or score > best[0]:
            best = (score, index)
    return best[1] if best is not None else None


def merge_episodes(existing, updates, now=None) -> list[dict]:
    """合并新进展；结果会关闭原事件，重复信息不会生成多份记忆。"""
    current = _now(now)
    merged = prune_episodes(existing, current)
    for raw in updates or []:
        if not isinstance(raw, dict):
            continue
        new = normalize_episode(raw, current)
        if not new:
            continue
        new["replaces"] = _text(raw.get("replaces"), 80)
        index = _match_index(merged, new, current)
        new.pop("replaces", None)
        if index is None:
            merged.append(new)
            continue
        old = merged[index]
        old["topic"] = new["topic"] or old["topic"]
        for key in ("detail", "time_hint", "emotion", "result"):
            if new.get(key):
                old[key] = new[key]
        old["status"] = new["status"]
        old["importance"] = max(old["importance"], new["importance"])
        if new["certainty"] == "confirmed":
            old["certainty"] = "confirmed"
        old["updated_at"] = _iso(current)
        if raw.get("follow_up_after"):
            old["follow_up_after"] = new["follow_up_after"]
        # 同一未结束事件最多只主动回访一次；更新进展不会重置次数。
        old["followup_count"] = max(old["followup_count"], new["followup_count"])
    return prune_episodes(merged, current)


def select_relevant_episodes(items, query: str, limit: int = 2, now=None) -> list[dict]:
    """只召回与当前话题词义有交集的少量经历。"""
    current = _now(now)
    query_terms = _terms(query)
    if not query_terms:
        return []
    scored = []
    for item in prune_episodes(items, current):
        event_terms = _terms(" ".join((item["topic"], item["detail"], item["result"])))
        overlap = len(query_terms & event_terms)
        if not overlap:
            continue
        age = max(0, (current - _parse_time(item["updated_at"], current)).days)
        score = overlap * 10 + item["importance"] * 2
        score += 4 if item["status"] in ACTIVE_STATUSES else 0
        score -= min(8, age // 7)
        scored.append((score, item["updated_at"], item))
    scored.sort(key=lambda row: (row[0], row[1]), reverse=True)
    return [row[2] for row in scored[:max(0, int(limit))]]


def get_due_followup(items, now=None) -> dict | None:
    """找出一个到期、已确认且从未回访的未结束事件，不修改原列表。"""
    current = _now(now)
    candidates = []
    for item in prune_episodes(items, current):
        if (item["status"] not in ACTIVE_STATUSES
                or item["certainty"] != "confirmed"
                or item["followup_count"] >= 1
                or _parse_time(item["follow_up_after"], current) > current):
            continue
        candidates.append(item)
    if not candidates:
        return None
    candidates.sort(key=lambda item: (-item["importance"], item["follow_up_after"]))
    return dict(candidates[0])


def mark_followed_up(items, episode_id: str, now=None) -> list[dict]:
    current = _now(now)
    out = prune_episodes(items, current)
    for item in out:
        if item["id"] == str(episode_id):
            item["followup_count"] = 1
            item["updated_at"] = _iso(current)
            break
    return out


def render_episode(item: dict) -> str:
    """生成为模型提供的简短资料行，不包含内部 ID 和计数字段。"""
    status = {"pending": "待发生", "ongoing": "进行中", "completed": "已结束", "cancelled": "已取消"}.get(
        item.get("status"), "状态未知",
    )
    parts = [item.get("topic", ""), status]
    if item.get("time_hint"):
        parts.append("时间：" + item["time_hint"])
    if item.get("emotion"):
        parts.append("当时情绪：" + item["emotion"])
    if item.get("result"):
        parts.append("结果：" + item["result"])
    elif item.get("detail"):
        parts.append(item["detail"])
    if item.get("certainty") == "mentioned":
        parts.append("仅提到过，尚未确认")
    return "；".join(part for part in parts if part)
