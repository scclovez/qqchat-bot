# -*- coding: utf-8 -*-
"""情绪轨迹：按小时采样持续情绪，供面板画 24 小时曲线。

- 只做"采样 + 读取"，情绪本身的判定仍在 人设/emotion_state.py 与 liveness.py；
- 采样写入主库（assistant_db 统一管 SQL），失败只记日志、不影响聊天；
- 曲线用于"看趋势"，不作为任何文案来源（对外文案仍走人设链路）。
"""
import logging
import time

import assistant_db as adb

logger = logging.getLogger(__name__)

EMOTION_KEYS = ("happy", "angry", "hurt", "tired")
EMOTION_NAMES = {"happy": "开心", "angry": "生气", "hurt": "委屈", "tired": "疲惫"}


def record(user_id: str, values: dict = None, ts: float = None) -> bool:
    """采一次样；values 为空时自行去 emotion_state 读当前强度。"""
    user_id = str(user_id or "").strip()
    if not user_id:
        return False
    if values is None:
        try:
            import emotion_state
            values = emotion_state.snapshot(user_id)
        except Exception as exc:
            logger.debug("读取情绪快照失败 [%s]: %s", user_id, exc)
            return False
    data = {key: float(values.get(key) or 0) for key in EMOTION_KEYS}
    # 全 0 也照记：曲线需要"这段时间没有情绪"的信息，一天最多 24 行
    try:
        return adb.record_emotion_sample(user_id, data, ts=ts)
    except Exception as exc:
        logger.debug("情绪采样失败 [%s]: %s", user_id, exc)
        return False


def series(user_id: str, hours: int = 24) -> list:
    """最近 N 小时的情绪序列（给面板画曲线）。"""
    try:
        return adb.emotion_series(user_id, hours=hours)
    except Exception as exc:
        logger.debug("读取情绪曲线失败 [%s]: %s", user_id, exc)
        return []


def peak_summary(user_id: str, hours: int = 24) -> str:
    """一句话概括这段时间的情绪峰值（面板文字用，不用于聊天文案）。"""
    points = series(user_id, hours)
    if not points:
        return "还没有采样"
    peaks = {}
    for key in EMOTION_KEYS:
        peaks[key] = max(float(point.get(key) or 0) for point in points)
    top = sorted(((value, key) for key, value in peaks.items()), reverse=True)
    value, key = top[0]
    if value < 5:
        return "这段时间挺平静"
    return "最强的是%s（%d）" % (EMOTION_NAMES.get(key, key), int(value))


def sample_now(user_id: str, values: dict = None) -> bool:
    """对外别名（语义更清楚：立刻采一次）。"""
    return record(user_id, values, ts=time.time())
