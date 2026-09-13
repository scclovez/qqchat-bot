# -*- coding: utf-8 -*-
"""用户行为规律推断：只根据「消息时间戳 + 消息内容」总结近期与长期作息规律。

设计原则（对应需求）：
- 一次行为不构成习惯：至少 MIN_DAYS 天证据才给出可用的推断，置信度随天数与稳定性增长；
- 近期优先：近 14 天规律优先影响当前行为，长期（近 90 天）仅在近期证据不足时兜底；
- 异常不覆盖长期：单次熬夜只影响「近期」的少数样本，长期窗口需要持续多日才会移动；
- 全部结论都是「推断」：value 里带 median/range/样本数，界面只展示文字档位，不暴露浮点。
- 这里只产出结构化状态，不生成任何 QQ 文案；文案仍由现有人设链路生成。
"""
import json
import logging
import re
import time
from datetime import datetime, timedelta, timezone

import assistant_db as adb

logger = logging.getLogger(__name__)

# =============================================================================
# 常量
# =============================================================================

RECENT_WINDOW_DAYS = 14        # 近期窗口
LONG_WINDOW_DAYS = 90          # 长期窗口
MIN_DAYS_FOR_SIGNAL = 3        # 少于 3 天证据 → 只给"证据不足"
FULL_CONFIDENCE_DAYS = 14      # 满 14 天证据才可能到最高置信度
ACTIVE_WINDOW_SECONDS = 15 * 60     # 15 分钟内说过话 → 直接视为清醒
WIND_DOWN_BEFORE_MIN = 60      # 睡前 60 分钟 → 准备睡了
WAKE_AFTER_MIN = 60            # 醒后 60 分钟 → 可能刚醒
STATE_CACHE_TTL = 60           # 用户状态缓存（秒）
RECOMPUTE_INTERVAL = 600       # 推断重算节流（秒）
RANGE_MIN_MINUTES = 20         # 展示区间最小宽度

# 内容线索关键词（只做轻量匹配，不调模型；完整语义交给后续阶段）
NIGHT_SAID_WORDS = ("晚安", "睡啦", "睡咯", "要睡了", "去睡了", "睡觉了", "睡觉去", "先睡了", "睡了哈")
WAKE_WORDS = ("起床", "醒了", "刚起", "睡醒", "早安", "早上好", "洗漱完", "到公司", "到教室")
SLEEPY_WORDS = ("困了", "好困", "想睡", "熬夜", "睡不着", "失眠", "眯一会", "打个盹")
BUSY_WORDS = ("上课", "有课", "开会", "上班", "加班", "忙", "考试", "答辩", "赶due", "赶ddl",
              "值班", "面试", "出差", "在实验室")
STUDY_WORDS = ("背单词", "学英语", "练听力", "做阅读", "写作业", "刷题", "复习", "预习",
               "学习", "看书", "做题", "背课文", "学python", "写代码", "上课笔记")

HINT_KINDS = {
    "night_said": NIGHT_SAID_WORDS,
    "wake_hint": WAKE_WORDS,
    "sleepy_hint": SLEEPY_WORDS,
    "busy_hint": BUSY_WORDS,
    "study_hint": STUDY_WORDS,
}

DIMENSIONS = (
    "sleep_pattern", "wake_pattern", "active_periods", "busy_periods",
    "study_preferred_period", "comfortable_study_duration",
    "procrastination_pattern", "response_pattern", "recent_changes",
)

_state_cache = {}       # user_id -> (ts, state_dict)
_last_recompute = {}    # user_id -> ts


# =============================================================================
# 采集
# =============================================================================

def _detect_hints(text: str) -> set:
    found = set()
    t = (text or "").lower()
    if not t:
        return found
    for kind, words in HINT_KINDS.items():
        if any(word.lower() in t for word in words):
            found.add(kind)
    return found


def observe_inbound(user_id: str, text: str, ts: float = None):
    """记录一条用户消息的观察（由机器人入站必经点调用；异常由调用方兜底）。"""
    user_id = str(user_id or "").strip()
    if not user_id:
        return
    ts = float(ts if ts is not None else time.time())
    text = text or ""
    adb.add_evidence(user_id, "activity", ts=ts, weight=1.0,
                     meta={"chars": len(text), "question": "?" in text or "？" in text})
    for kind in _detect_hints(text):
        adb.add_evidence(user_id, kind, ts=ts, weight=1.0,
                         meta={"sample": text[:60]})
    _state_cache.pop(user_id, None)
    if ts - _last_recompute.get(user_id, 0) >= RECOMPUTE_INTERVAL:
        try:
            recompute(user_id)
        except Exception as exc:  # 推断失败不影响采集与聊天
            logger.warning("行为规律重算失败 [%s]: %s", user_id, exc)


_TIME_RE = re.compile(r"(\d{1,2})\s*[:：点]\s*(\d{1,2})?")


def record_user_correction(user_id: str, text: str) -> bool:
    """用户手动纠正作息（如"我一般 12 点半就睡了"）。

    解析出的时间作为高权重证据参与推断；解析不出时间时只留一条原始记录，
    绝不把纠正当成唯一事实来源。
    """
    text = (text or "").strip()
    if not text:
        return False
    adb.add_user_correction(user_id, text)
    hour, minute = _parse_stated_time(text)
    if hour is None:
        return True
    if any(w in text for w in NIGHT_SAID_WORDS) or "睡" in text:
        kind = "user_sleep_stated"
        if hour == 12:  # "12点睡" = 半夜 0 点
            hour = 0
    else:
        kind = "user_wake_stated"
    adb.add_evidence(user_id, kind, ts=_ts_today_at(hour, minute), weight=3.0,
                     meta={"text": text[:200]})
    recompute(user_id, force=True)
    return True


def _parse_stated_time(text: str):
    """"12点半睡" / "01:30 左右睡" → (hour, minute)；解析失败返回 (None, None)。

    返回值是 0-23 的钟点。"12点"在睡眠语境里由调用方折算成 0 点。
    """
    text = text or ""
    m = _TIME_RE.search(text)
    if not m:
        # 支持"十二点"这类中文数字的常见写法
        zh = {"十一": 11, "十二": 12, "十": 10, "九": 9, "八": 8, "七": 7,
              "六": 6, "五": 5, "四": 4, "三": 3, "二": 2, "两": 2, "一": 1}
        for word, value in zh.items():
            if word + "点" in text:
                return value, 0
        return None, None
    hour = int(m.group(1))
    minute = 0
    if m.group(2):
        minute = min(59, int(m.group(2)))
    elif "半" in text[m.end():m.end() + 2]:
        minute = 30
    if hour > 24:
        return None, None
    return hour % 24, int(minute / 5) * 5


def _ts_today_at(hour: int, minute: int) -> float:
    now = datetime.now()
    target = now.replace(hour=int(hour) % 24, minute=int(minute) % 60, second=0, microsecond=0)
    return target.timestamp()


# =============================================================================
# 推断
# =============================================================================

def _wrap_bedtime(hour: int) -> float:
    """入睡时间换算成"午后起算"的小时，避免跨零点导致中位数错误（01:00 → 25:00）。"""
    hour = int(hour) % 24
    return float(hour + 24) if hour < 12 else float(hour)


def _weighted_median(samples: list):
    """samples: [(value, weight)] → (median, mad, count)；无样本返回 (None, None, 0)。"""
    pairs = [(float(v), float(w)) for v, w in samples if w > 0]
    if not pairs:
        return None, None, 0
    pairs.sort(key=lambda item: item[0])
    total = sum(w for _, w in pairs)
    acc = 0.0
    median = pairs[0][0]
    for value, weight in pairs:
        acc += weight
        if acc >= total / 2:
            median = value
            break
    mad = sum(abs(value - median) * weight for value, weight in pairs) / total
    return median, mad, len(pairs)


def _window_stats(user_id: str, window_days: int) -> dict:
    """在给定窗口内统计作息/活跃/忙碌/学习偏好。"""
    since = (datetime.now() - timedelta(days=int(window_days))).strftime("%Y-%m-%d")
    daily = adb.get_daily_activity(user_id, since)
    sleep_samples = [(_wrap_bedtime(int(row["last_hour"]) + 1), 1.0) for row in daily]
    wake_samples = [(float(int(row["first_hour"])), 1.0) for row in daily]
    for row in adb.get_evidence(user_id, since, kinds=("user_sleep_stated",)):
        sleep_samples.append((_wrap_bedtime(int(row["hour"])), float(row["weight"] or 1.0)))
    for row in adb.get_evidence(user_id, since, kinds=("user_wake_stated",)):
        wake_samples.append((float(int(row["hour"])), float(row["weight"] or 1.0)))

    sleep_median, sleep_mad, sleep_n = _weighted_median(sleep_samples)
    wake_median, wake_mad, wake_n = _weighted_median(wake_samples)

    activity_hist = adb.get_hour_histogram(user_id, since, ("activity",))
    busy_hist = adb.get_hint_hours(user_id, "busy_hint", since)
    study_hist = adb.get_hint_hours(user_id, "study_hint", since)
    night_hist = adb.get_hint_hours(user_id, "night_said", since)
    wake_hist = adb.get_hint_hours(user_id, "wake_hint", since)

    days = len(daily)
    sleep_conf = _confidence(days, sleep_mad)
    wake_conf = _confidence(days, wake_mad)
    return {
        "days": days,
        "sleep_hour": sleep_median, "sleep_mad": sleep_mad, "sleep_n": sleep_n,
        "sleep_confidence": sleep_conf,
        "wake_hour": wake_median, "wake_mad": wake_mad, "wake_n": wake_n,
        "wake_confidence": wake_conf,
        "activity_hours": activity_hist,
        "active_periods": _ranges_from_histogram(activity_hist),
        "busy_periods": _ranges_from_histogram(busy_hist, min_weight=2.0),
        "study_periods": _ranges_from_histogram(study_hist, min_weight=1.0),
        "night_said_hours": night_hist,
        "wake_hint_hours": wake_hist,
        "messages": sum(activity_hist.values()),
    }


def _confidence(days: int, mad) -> float:
    """置信度 = 证据天数因子 × 稳定度；天数不足时给出很小的值（不构成结论）。"""
    if not days:
        return 0.0
    day_factor = min(1.0, days / float(FULL_CONFIDENCE_DAYS))
    if mad is None:
        stability = 0.5
    else:
        stability = 1.0 / (1.0 + float(mad) / 1.5)
    confidence = day_factor * stability
    if days < MIN_DAYS_FOR_SIGNAL:
        confidence = min(confidence, 0.15 * days / float(MIN_DAYS_FOR_SIGNAL))
    return round(max(0.0, min(1.0, confidence)), 3)


def _ranges_from_histogram(hist: dict, min_weight: float = 1.0, max_ranges: int = 3) -> list:
    """把小时分布压成"时段区间"（连续小时合并），按权重从高到低取前几段。"""
    hours = sorted(h for h, weight in (hist or {}).items() if weight >= min_weight)
    if not hours:
        return []
    groups = [[hours[0]]]
    for hour in hours[1:]:
        if hour == groups[-1][-1] + 1:
            groups[-1].append(hour)
        else:
            groups.append([hour])
    scored = []
    for group in groups:
        weight = sum(float(hist.get(h, 0)) for h in group)
        scored.append((weight, group[0], group[-1] + 1))
    scored.sort(reverse=True)
    return [{"start": start, "end": end, "weight": round(weight, 1)}
            for weight, start, end in scored[:max_ranges]]


def recompute(user_id: str, force: bool = False) -> dict:
    """重算并保存近期/长期两套推断（节流，可由采集或面板触发）。"""
    user_id = str(user_id or "").strip()
    if not user_id:
        return {}
    now = time.time()
    if not force and now - _last_recompute.get(user_id, 0) < RECOMPUTE_INTERVAL:
        return adb.get_profiles(user_id)
    _last_recompute[user_id] = now

    recent = _window_stats(user_id, RECENT_WINDOW_DAYS)
    long_term = _window_stats(user_id, LONG_WINDOW_DAYS)
    _save_window(user_id, "recent", recent)
    _save_window(user_id, "long", long_term)
    _save_recent_changes(user_id, recent, long_term)
    _state_cache.pop(user_id, None)
    return adb.get_profiles(user_id)


def _save_window(user_id: str, window: str, stats: dict):
    adb.save_profile(user_id, "sleep_pattern", window, {
        "hour": stats["sleep_hour"], "mad": stats["sleep_mad"],
        "range": _range_minutes(stats["sleep_hour"], stats["sleep_mad"]),
    }, stats["sleep_confidence"], stats["days"])
    adb.save_profile(user_id, "wake_pattern", window, {
        "hour": stats["wake_hour"], "mad": stats["wake_mad"],
        "range": _range_minutes(stats["wake_hour"], stats["wake_mad"]),
    }, stats["wake_confidence"], stats["days"])
    adb.save_profile(user_id, "active_periods", window, {
        "ranges": stats["active_periods"], "messages": stats["messages"],
    }, _confidence(stats["days"], None), stats["days"])
    adb.save_profile(user_id, "busy_periods", window, {
        "ranges": stats["busy_periods"],
    }, _confidence(stats["days"], None) if stats["busy_periods"] else 0.0,
        len(stats["busy_periods"]))
    adb.save_profile(user_id, "study_preferred_period", window, {
        "ranges": stats["study_periods"] or stats["active_periods"][:1],
        "from_keyword": bool(stats["study_periods"]),
    }, _confidence(stats["days"], None), len(stats["study_periods"]))


def _save_recent_changes(user_id: str, recent: dict, long_term: dict):
    """近期相对长期的变化（异常单日不足以形成变化结论）。"""
    changes = []
    sleep_shift = None
    if (recent.get("sleep_hour") is not None and long_term.get("sleep_hour") is not None
            and recent.get("days", 0) >= MIN_DAYS_FOR_SIGNAL):
        sleep_shift = round((recent["sleep_hour"] - long_term["sleep_hour"]) * 60)
        if abs(sleep_shift) >= 20:
            direction = "晚" if sleep_shift > 0 else "早"
            changes.append(f"最近比以前{direction}睡约 {abs(sleep_shift)} 分钟")
    wake_shift = None
    if (recent.get("wake_hour") is not None and long_term.get("wake_hour") is not None
            and recent.get("days", 0) >= MIN_DAYS_FOR_SIGNAL):
        wake_shift = round((recent["wake_hour"] - long_term["wake_hour"]) * 60)
        if abs(wake_shift) >= 20:
            direction = "晚" if wake_shift > 0 else "早"
            changes.append(f"最近比以前{direction}起约 {abs(wake_shift)} 分钟")
    if recent.get("sleep_mad") is not None and recent["sleep_mad"] <= 0.75 and recent.get("days", 0) >= MIN_DAYS_FOR_SIGNAL:
        changes.append("最近的入睡时间比较稳定")
    adb.save_profile(user_id, "recent_changes", "recent", {
        "sleep_shift_minutes": sleep_shift,
        "wake_shift_minutes": wake_shift,
        "notes": changes,
    }, _confidence(recent.get("days", 0), recent.get("sleep_mad")), recent.get("days", 0))


def _range_minutes(median, mad):
    """中位数 ± 波动 → 展示用区间（分钟，跨零点已归一）。"""
    if median is None:
        return None
    half = max(RANGE_MIN_MINUTES / 2.0, (float(mad) if mad else 0.0) * 60.0)
    return [round(median * 60 - half), round(median * 60 + half)]


def _effective(user_id: str) -> dict:
    """取生效推断：近期够用就用近期，否则退回长期。"""
    profiles = adb.get_profiles(user_id)
    recent = profiles.get("recent", {})
    long_term = profiles.get("long", {})
    sleep_recent = recent.get("sleep_pattern", {})
    if sleep_recent.get("evidence_count", 0) >= MIN_DAYS_FOR_SIGNAL:
        return {"window": "recent", "dims": recent}
    if long_term:
        return {"window": "long", "dims": long_term}
    return {"window": "recent", "dims": recent}


# =============================================================================
# 用户状态（智能静默用）
# =============================================================================

def _last_inbound_ts(user_id: str):
    since = (datetime.now() - timedelta(days=2)).strftime("%Y-%m-%d")
    rows = adb.get_evidence(user_id, since, kinds=("activity",))
    stamps = [float(row["last_ts"]) for row in rows if row.get("last_ts")]
    return max(stamps) if stamps else 0.0


def _sleep_probability(user_id: str, now: datetime, eff: dict) -> tuple:
    """当前时刻"他大概在睡"的概率 + 理由。

    先验（小时）只是冷启动兜底，一旦有证据就以推断为准；说晚安会临时抬升概率，
    但他只要再发消息，_last_inbound_ts 立即把状态拉回清醒（不依赖任何遗留变量）。
    """
    dims = eff.get("dims", {})
    sleep = dims.get("sleep_pattern", {})
    wake = dims.get("wake_pattern", {})
    sleep_hour = (sleep.get("value") or {}).get("hour")
    wake_hour = (wake.get("value") or {}).get("hour")
    conf = float(sleep.get("confidence") or 0.0)
    hour = now.hour + now.minute / 60.0

    prior = 0.05
    if 0.5 <= hour < 6.0:
        prior = 0.75
    elif hour >= 23.0 or hour < 0.5:
        prior = 0.45
    elif 22.0 <= hour < 23.0:
        prior = 0.25
    elif 6.0 <= hour < 7.5:
        prior = 0.20

    reasons = []
    probability = prior
    if sleep_hour is not None and conf > 0:
        # 用「钟点环形区间」判断此刻是否处于睡眠窗口，跨零点/昼夜都安全：
        # 睡眠时长 = (起床 - 入睡) mod 24h，此刻距入睡点不足这个时长即视为在睡。
        asleep = _minutes_into_sleep(hour, sleep_hour, wake_hour)
        evidence_prob = 0.85 if asleep else 0.08
        probability = (1 - conf) * prior + conf * evidence_prob
        if wake_hour is not None:
            reasons.append("作息推断：通常 %s 睡、%s 起" % (_hhmm(sleep_hour), _hhmm(wake_hour)))
        else:
            reasons.append("作息推断：通常 %s 睡" % _hhmm(sleep_hour))
    else:
        reasons.append("作息证据还不足，先用时段先验")

    # 内容线索：说晚安 / 说困了 → 临时抬升；说刚起床 → 压低
    since = (datetime.now() - timedelta(hours=6)).strftime("%Y-%m-%d")
    for row in adb.get_evidence(user_id, since, kinds=("night_said", "sleepy_hint", "wake_hint")):
        if not row.get("last_ts"):
            continue
        if now.timestamp() - float(row["last_ts"]) > 6 * 3600:
            continue
        if row["kind"] == "wake_hint":
            probability = min(probability, 0.05)
            reasons.append("他刚说过起床/早上好")
        else:
            boost = 0.35 if row["kind"] == "night_said" else 0.15
            probability = min(1.0, probability + boost * min(1.0, float(row["weight"])))
            reasons.append("他刚说过%s" % ("晚安" if row["kind"] == "night_said" else "困了"))
    return round(max(0.0, min(1.0, probability)), 3), reasons


def _hhmm(hour_float) -> str:
    if hour_float is None:
        return "--"
    total = int(round(float(hour_float) * 60)) % (24 * 60)
    return "%02d:%02d" % (total // 60, total % 60)


def _minutes_into_sleep(now_hour: float, sleep_hour: float, wake_hour) -> bool:
    """此刻是否落在"入睡点 → 起床点"这段环形区间内。"""
    now_min = int(round(float(now_hour) * 60)) % (24 * 60)
    sleep_min = int(round(float(sleep_hour) * 60)) % (24 * 60)
    if wake_hour is None:
        # 没有起床证据时，默认按 8 小时睡眠窗口判断
        duration = 8 * 60
    else:
        duration = (int(round(float(wake_hour) * 60)) % (24 * 60) - sleep_min) % (24 * 60)
        if duration <= 0:
            duration = 8 * 60
    return ((now_min - sleep_min) % (24 * 60)) < duration


def infer_user_state(user_id: str, now: datetime = None, use_cache: bool = True) -> dict:
    """推断用户当前状态：active / possibly_busy / winding_down / likely_sleeping / possibly_awake。"""
    user_id = str(user_id or "").strip()
    explicit_now = now is not None
    now = now or datetime.now()
    if not user_id:
        return {"state": "active", "confidence": 0.0, "label": "未知",
                "sleep_probability": 0.0, "reasons": ["没有指定用户"], "window": ""}
    # 显式传入 now（回放/测试/调度器补算）时绕过缓存，避免拿到别的时间点的状态
    if use_cache and not explicit_now:
        cached = _state_cache.get(user_id)
        if cached and time.time() - cached[0] < STATE_CACHE_TTL:
            return cached[1]

    eff = _effective(user_id)
    dims = eff.get("dims", {})
    sleep = dims.get("sleep_pattern", {})
    wake = dims.get("wake_pattern", {})
    active = dims.get("active_periods", {})
    busy = dims.get("busy_periods", {})
    sleep_hour = (sleep.get("value") or {}).get("hour")
    wake_hour = (wake.get("value") or {}).get("hour")
    confidence = float(sleep.get("confidence") or 0.0)
    probability, reasons = _sleep_probability(user_id, now, eff)
    hour = now.hour + now.minute / 60.0

    # 硬规则：刚说过话就是清醒（用传入的 now 作基准，便于测试与回放）
    last_in = _last_inbound_ts(user_id)
    if last_in and now.timestamp() - last_in <= ACTIVE_WINDOW_SECONDS:
        state = {"state": "active", "confidence": 1.0, "label": "清醒",
                 "sleep_probability": 0.0, "window": eff.get("window", ""),
                 "reasons": ["他刚刚还在聊天"]}
        if not explicit_now:
            _state_cache[user_id] = (time.time(), state)
        return state

    if probability >= 0.6:
        state_name, label = "likely_sleeping", "大概睡了"
    elif sleep_hour is not None and _minutes_until(hour, sleep_hour) <= WIND_DOWN_BEFORE_MIN:
        state_name, label = "winding_down", "准备睡了"
    elif wake_hour is not None and _minutes_since(hour, wake_hour) <= WAKE_AFTER_MIN:
        state_name, label = "possibly_awake", "可能刚醒"
    elif _in_ranges(hour, (busy.get("value") or {}).get("ranges") or []):
        state_name, label = "possibly_busy", "可能在忙"
    elif probability <= 0.25:
        state_name, label = "active", "清醒"
    else:
        state_name, label = "possibly_awake", "不确定（也许还醒着）"

    result = {
        "state": state_name, "confidence": confidence, "label": label,
        "sleep_probability": probability, "window": eff.get("window", ""),
        "sleep_hour": sleep_hour, "wake_hour": wake_hour,
        "active_ranges": (active.get("value") or {}).get("ranges") or [],
        "reasons": reasons,
    }
    if not explicit_now:
        _state_cache[user_id] = (time.time(), result)
    return result


def _minutes_until(hour: float, target: float) -> float:
    """距离目标时刻还有多少分钟（跨零点安全）。"""
    delta = (float(target) - hour) % 24
    return delta * 60.0


def _minutes_since(hour: float, target: float) -> float:
    return ((hour - float(target)) % 24) * 60.0


def _in_ranges(hour: float, ranges: list) -> bool:
    for item in ranges or []:
        start, end = float(item.get("start", 0)), float(item.get("end", 0))
        if start <= hour < end:
            return True
    return False


def should_suppress_proactive(user_id: str, priority: int = 30, now: datetime = None) -> dict:
    """是否应该压住这条主动消息（优先级越高越有资格叫醒他）。

    只压"普通主动/碎碎念"这类低优先级；重要日程与考试提醒（≥95）不受睡眠压制。
    本函数不依赖任何遗留静默变量，全部结论来自时间戳与内容推断。
    """
    state = infer_user_state(user_id, now)
    name = state.get("state")
    probability = float(state.get("sleep_probability") or 0)
    allow = True
    reason = state.get("label", "")
    if name == "likely_sleeping" and int(priority) < 95:
        allow = False
    elif name == "possibly_busy" and int(priority) <= 30:
        allow = False
    elif probability >= 0.85 and int(priority) < 95:
        allow = False
    return {
        "allow": allow, "state": name, "sleep_probability": probability,
        "priority": int(priority), "reason": reason,
        "reasons": state.get("reasons", []),
    }


# =============================================================================
# 面板展示 / prompt 注入
# =============================================================================

def confidence_label(confidence: float) -> str:
    """浮点置信度 → 用户可读档位（界面不显示小数）。"""
    value = float(confidence or 0.0)
    if value >= 0.66:
        return "较稳定"
    if value >= 0.33:
        return "比较确定"
    if value >= 0.1:
        return "初步判断"
    return "还在观察"


def _fmt_range(range_minutes, fallback_hour=None):
    if range_minutes:
        start, end = int(range_minutes[0]), int(range_minutes[1])
        return "%s-%s" % (_hhmm(start / 60.0), _hhmm(end / 60.0))
    if fallback_hour is not None:
        return _hhmm(fallback_hour) + " 左右"
    return "—"


def summarize(user_id: str, force: bool = False) -> dict:
    """给控制面板的展示数据（含文字档位与观察结论，不含浮点）。"""
    user_id = str(user_id or "").strip()
    if not user_id:
        return {"has_user": False, "insufficient": True, "observations": [],
                "wake": {}, "sleep": {}, "active": {}, "study": {},
                "evidence_days": 0, "messages": 0, "updated": "", "window": "",
                "corrections": []}
    profiles = recompute(user_id, force=force) if force else adb.get_profiles(user_id)
    if not profiles:
        profiles = recompute(user_id, force=True)
    eff = _effective(user_id)
    dims = eff["dims"]
    sleep = dims.get("sleep_pattern", {})
    wake = dims.get("wake_pattern", {})
    active = dims.get("active_periods", {})
    study = dims.get("study_preferred_period", {})
    changes = profiles.get("recent", {}).get("recent_changes", {})
    days = int(sleep.get("evidence_count") or active.get("evidence_count") or 0)
    insufficient = days < MIN_DAYS_FOR_SIGNAL
    observations = list((changes.get("value") or {}).get("notes") or [])

    study_ranges = (study.get("value") or {}).get("ranges") or []
    active_ranges = (active.get("value") or {}).get("ranges") or []
    if study_ranges and active_ranges:
        if study_ranges[0]["start"] != active_ranges[0]["start"]:
            observations.append("学习更常发生在 %s" % _range_text(study_ranges[0]))
    busy_ranges = (dims.get("busy_periods", {}).get("value") or {}).get("ranges") or []
    for item in busy_ranges[:1]:
        observations.append("这个时段高概率在忙：%s" % _range_text(item))
    if days and days < MIN_DAYS_FOR_SIGNAL:
        observations.append("才开始观察，结论还只能当参考")

    def _metric(dim, level_when_empty="还在观察"):
        if insufficient:
            return {"text": "—", "level": level_when_empty}
        return {
            "text": _fmt_range((dim.get("value") or {}).get("range"),
                               (dim.get("value") or {}).get("hour")),
            "level": confidence_label(dim.get("confidence")),
        }

    return {
        "has_user": True,
        "insufficient": insufficient,
        "window": eff.get("window", ""),
        "evidence_days": days,
        "messages": int((active.get("value") or {}).get("messages") or 0),
        "updated": sleep.get("last_updated") or active.get("last_updated") or "",
        "coverage": adb.evidence_span(user_id),
        "sleep": _metric(sleep),
        "wake": _metric(wake),
        "active": {
            "text": ("—" if insufficient else
                     (" / ".join(_range_text(item) for item in active_ranges[:2]) or "—")),
            "level": "还在观察" if insufficient else confidence_label(active.get("confidence")),
        },
        "study": {
            "text": ("—" if insufficient else
                     (" / ".join(_range_text(item) for item in study_ranges[:2]) or "—")),
            "level": ("还在观察" if (insufficient or not study_ranges)
                      else confidence_label(study.get("confidence"))),
            "from_keyword": bool((study.get("value") or {}).get("from_keyword")),
        },
        "observations": observations[:6],
        "corrections": adb.get_user_corrections(user_id, 5),
    }


def _range_text(item: dict) -> str:
    return "%s-%s" % (_hhmm(item.get("start", 0)), _hhmm(item.get("end", 0)))


def profile_prompt(user_id: str, now: datetime = None) -> str:
    """给对话链路注入的内部状态文本（只描述事实与推断，不规定说法）。"""
    user_id = str(user_id or "").strip()
    if not user_id:
        return ""
    data = summarize(user_id)
    if not data.get("has_user"):
        return ""
    state = infer_user_state(user_id, now)
    parts = []
    if data["sleep"]["text"] != "—":
        parts.append("通常 %s 睡（%s）" % (data["sleep"]["text"], data["sleep"]["level"]))
    if data["wake"]["text"] != "—":
        parts.append("%s 起（%s）" % (data["wake"]["text"], data["wake"]["level"]))
    if data["active"]["text"] != "—":
        parts.append("比较活跃 %s" % data["active"]["text"])
    if data["study"]["text"] != "—":
        parts.append("更容易学习 %s" % data["study"]["text"])
    if not parts:
        return ""
    parts.append("他现在可能：%s" % state.get("label", ""))
    return "（你对他的作息观察：%s。这些只是观察，不要背课文式复述，也不要每条都提。）" % "；".join(parts)


def clear_cache(user_id: str = ""):
    """清空进程内缓存（测试与调试用）。"""
    if user_id:
        _state_cache.pop(str(user_id), None)
        _last_recompute.pop(str(user_id), None)
    else:
        _state_cache.clear()
        _last_recompute.clear()


# =============================================================================
# 历史回填：启动时把已有的聊天记录读一遍，立刻形成作息推断
# =============================================================================

BACKFILL_KEEP_DAYS = 180
BACKFILL_BATCH_LIMIT = 20000      # 单次回填最多读多少条（防止超大库卡启动）


def _parse_history_ts(text: str):
    """chat_history.created_at 是 UTC（SQLite CURRENT_TIMESTAMP）→ 本地时间戳。"""
    raw = str(text or "").strip()
    if not raw:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M:%S.%f"):
        try:
            naive = datetime.strptime(raw[:26], fmt)
        except ValueError:
            continue
        try:
            return naive.replace(tzinfo=timezone.utc).timestamp()
        except (OverflowError, OSError, ValueError):
            return None
    return None


def backfill_user(user_id: str, keep_days: int = BACKFILL_KEEP_DAYS, force: bool = False) -> dict:
    """把某个用户已有的聊天记录回填成行为证据，并立即重算规律。

    - 幂等：用 schema_meta 里的 checkpoint 记录已处理到的 chat_history.id，
      重复调用（含每次启动）只处理新增记录，不会重复计数；
    - 只读原表，不改动聊天记录；回填失败不影响聊天。
    """
    user_id = str(user_id or "").strip()
    if not user_id:
        return {"user_id": "", "processed": 0}
    key = "behavior_backfill_last_id:%s" % user_id
    last_id = 0 if force else int(adb.get_meta(key, "0") or 0)
    since_time = (datetime.now() - timedelta(days=max(7, int(keep_days)))).strftime("%Y-%m-%d %H:%M:%S")
    rows = adb.fetch_chat_history(role="user", since_id=last_id, since_time=since_time,
                                  user_id=user_id)
    if not rows:
        if last_id == 0:
            recompute(user_id, force=True)
        return {"user_id": user_id, "processed": 0, "days": 0}
    if len(rows) > BACKFILL_BATCH_LIMIT:
        rows = rows[-BACKFILL_BATCH_LIMIT:]
    now_ts = time.time()
    buckets = {}    # (day, hour) -> [count, first_ts, last_ts, chars]
    hints = {}      # (kind, day, hour) -> count
    max_id = last_id
    for row in rows:
        ts = _parse_history_ts(row.get("created_at"))
        max_id = max(max_id, int(row.get("id") or 0))
        if ts is None or ts > now_ts + 86400:
            continue
        day, hour, _slot = adb.local_day_hour(ts)
        slot_key = (day, hour)
        info = buckets.get(slot_key)
        text = str(row.get("content") or "")
        if info is None:
            buckets[slot_key] = [1, ts, ts, len(text)]
        else:
            info[0] += 1
            info[1] = min(info[1], ts)
            info[2] = max(info[2], ts)
            info[3] += len(text)
        for kind in _detect_hints(text):
            hints[(kind, day, hour)] = hints.get((kind, day, hour), 0) + 1
    for (day, hour), (count, first_ts, last_ts, chars) in buckets.items():
        adb.add_evidence_bucket(user_id, "activity", day, hour, count, first_ts, last_ts,
                                meta={"chars": chars, "backfill": True})
    for (kind, day, hour), count in hints.items():
        adb.add_evidence_bucket(user_id, kind, day, hour, count,
                                _ts_at(day, hour), _ts_at(day, hour), meta={"backfill": True})
    adb.set_meta(key, str(max_id))
    recompute(user_id, force=True)
    days = len({day for (day, _hour) in buckets})
    logger.info("行为规律回填 [%s]：处理 %d 条记录、%d 天、%d 个时段",
                user_id, len(rows), days, len(buckets))
    return {"user_id": user_id, "processed": len(rows), "days": days,
            "buckets": len(buckets), "hints": len(hints), "last_id": max_id}


def _ts_at(day: str, hour: int) -> float:
    """活跃日 + 钟点 → 时间戳（凌晨属于次日，换算回去）。"""
    try:
        base = datetime.strptime(day, "%Y-%m-%d")
    except ValueError:
        return time.time()
    if int(hour) < adb.DAY_START_HOUR:
        base = base + timedelta(days=1)
    return base.replace(hour=int(hour) % 24).timestamp()


def backfill_all(user_ids=None, keep_days: int = BACKFILL_KEEP_DAYS) -> dict:
    """回填全部（或指定）用户；启动时调用一次即可。"""
    targets = [str(u) for u in (user_ids or []) if str(u).strip()]
    if not targets:
        try:
            targets = adb.history_users()
        except Exception as exc:
            logger.warning("读取聊天记录用户失败: %s", exc)
            return {"users": 0, "processed": 0}
    total = 0
    done = 0
    for user_id in targets:
        try:
            result = backfill_user(user_id, keep_days=keep_days)
            total += int(result.get("processed") or 0)
            done += 1
        except Exception as exc:
            logger.warning("回填失败 [%s]: %s", user_id, exc)
    if total:
        logger.info("行为规律历史回填完成：%d 个用户、%d 条记录", done, total)
    return {"users": done, "processed": total}
