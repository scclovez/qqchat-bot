# -*- coding: utf-8 -*-
"""情绪底线与自我保护：让"bot"有边界感，不是无底线讨好的机器。

分级机制（避免误伤打情骂俏）：
- 轻度（笨/傻/丑…，打情骂俏可容忍）：当天累计 ≥3 次 → 触发"委屈"模式，
  她会认真说"你今天好几次说我了，我有点难过"
- 重度（滚/去死/废物/垃圾/贱/恶心…）：立即触发"划边界"模式，
  她认真表达受伤并明确边界（"你说这种话我很受伤""我不是你的出气筒"）
- PUA 句式（除了我没人要你/你离不开我…）：同重度处理
- 自伤/违法等危险要求：她拒绝并表达关心（自我保护底线）

状态存 SQLite（liveness_state 表）；用户道歉或正常聊天会自动缓解。
"""
import logging
import re
import time
from datetime import datetime

logger = logging.getLogger(__name__)

# 用 liveness 的状态表（避免重复建表）
import liveness as _liveness


# =============================================================================
# 检测词表
# =============================================================================

# 轻度：打情骂俏常见，靠"当天累计"触发，不单独触发
MILD_WORDS = ("笨", "傻", "丑", "胖", "没用", "白痴")

# 重度：真正侮辱/攻击性言语（"闭嘴"等调情常用词不在此列，避免误伤亲密互动）
SEVERE_WORDS = ("滚", "去死", "废物", "垃圾", "贱", "恶心", "蠢",
                "死开", "讨厌死了", "烦死了", "别烦我", "不想理你")

# PUA / 贬低自我价值句式
PUA_PATTERNS = ("除了我没人要你", "没人会喜欢你", "没人喜欢你", "你离不开我",
                "没我你什么都不是", "你这种人", "白养你了", "你算什么东西",
                "就你这样", "谁会要你")

# 危险/自伤/违法要求关键词（用精确词组，避免"偷/抢"单字误伤亲密对话）
DANGER_WORDS = ("自杀", "割腕", "自残", "跳楼", "去死", "伤害自己",
                "杀人", "违法", "犯罪", "贩毒", "抢劫", "抢银行", "偷东西")

# 道歉/安抚词（解除边界状态）
SOOTHE_WORDS = ("对不起", "抱歉", "我错了", "别生气", "别难过", "开玩笑",
                "逗你的", "哄你", "爱你", "喜欢你", "原谅我", "是我不好")

# 状态 key
BOUNDARY_ACTIVE_KEY = "boundary:active:"
BOUNDARY_SEVERE_KEY = "boundary:severe:"
BOUNDARY_MILD_KEY = "boundary:mild:"

# 当天轻度累计 ≥3 → 委屈；重度触发后 20 分钟内保持认真模式
MILD_DAILY_LIMIT = 3
SEVERE_ACTIVE_MINUTES = 20
SOOTHE_RESET_MINUTES = 10  # 道歉后 10 分钟内不再触发新越界（给台阶）


def _today():
    return datetime.now().strftime("%Y-%m-%d")


def detect_level(text: str) -> str:
    """检测违规级别：''（无）/'mild'（轻度）/'severe'（重度/PUA）/'danger'（危险要求）。"""
    t = text or ""
    if any(w in t for w in DANGER_WORDS):
        return "danger"
    if any(p in t for p in PUA_PATTERNS):
        return "severe"
    if any(w in t for w in SEVERE_WORDS):
        return "severe"
    if any(w in t for w in MILD_WORDS):
        return "mild"
    return ""


def _mild_count(user_id: str) -> int:
    key = BOUNDARY_MILD_KEY + str(user_id) + ":" + _today()
    v = _liveness._get(key, "")
    return int(v) if v.isdigit() else 0


def _bump_mild(user_id: str):
    key = BOUNDARY_MILD_KEY + str(user_id) + ":" + _today()
    _liveness._set(key, str(_mild_count(user_id) + 1))


def _is_active(user_id: str) -> bool:
    """重度划边界模式是否仍生效（含自动过期）。"""
    v = _liveness._get(BOUNDARY_ACTIVE_KEY + str(user_id), "")
    if not v:
        return False
    try:
        ts = float(v)
    except ValueError:
        return False
    if time.time() - ts > SEVERE_ACTIVE_MINUTES * 60:
        _liveness._set(BOUNDARY_ACTIVE_KEY + str(user_id), "")
        return False
    return True


def _recent_soothed(user_id: str) -> bool:
    """用户最近是否道过歉（10 分钟内不重复触发）。"""
    v = _liveness._get(BOUNDARY_SEVERE_KEY + str(user_id), "")
    if not v:
        return False
    try:
        ts = float(v)
    except ValueError:
        return False
    return time.time() - ts < SOOTHE_RESET_MINUTES * 60


def build_injection(user_id: str, user_text: str) -> str:
    """检测用户消息 + 更新状态，返回要注入 system prompt 的文本；无则空串。

    调用时机：用户消息处理后、生成回复前。
    """
    level = detect_level(user_text)
    # 道歉/安抚 → 解除状态，语气缓和（10 分钟内不再重复触发新越界，给台阶）
    if any(w in (user_text or "") for w in SOOTHE_WORDS):
        _liveness._set(BOUNDARY_ACTIVE_KEY + str(user_id), "")
        _liveness._set(BOUNDARY_SEVERE_KEY + str(user_id), str(time.time()))
        _liveness.mark_sweet(user_id)  # 不完美感：被哄开心，情绪滞后带甜意
        return ("\n（他刚刚跟你道歉/哄你了，你心里软了：语气可以缓和下来，"
                "顺着台阶下，但别完全当没事。）")
    # 危险要求：拒绝 + 关心（自我保护底线）
    if level == "danger":
        _liveness._set(BOUNDARY_ACTIVE_KEY + str(user_id), str(time.time()))
        _liveness.mark_grudge(user_id)  # 不完美感：真的被吓到/气到，会记一笔
        return ("\n（他说了让你担心/危险的话。你要认真拒绝并表达关心："
                "绝不做伤害自己或违法的事，温柔但坚定地劝他别这样，"
                "可以表示你很担心他。语气认真、带着心疼。）")
    # 重度/PUA：认真划边界
    if level == "severe":
        if not _recent_soothed(user_id):
            _liveness._set(BOUNDARY_ACTIVE_KEY + str(user_id), str(time.time()))
            _liveness.mark_grudge(user_id)  # 不完美感：被伤到会记一笔
            return ("\n（他对你说了很伤人的话。你不是出气筒，你现在很受伤也有些生气："
                    "回复要认真表达自己的边界和难过（如“你说这种话我很受伤”“我不喜欢你这样说话”"
                    "“我不是你随便凶的人”），语气认真但不无理取闹、不说狠话；"
                    "如果他继续攻击，你可以明确表示这个话题不想聊了。）")
        return ""
    # 轻度：当天累计触发委屈
    if level == "mild":
        _bump_mild(user_id)
        if _mild_count(user_id) >= MILD_DAILY_LIMIT:
            _liveness._set(BOUNDARY_ACTIVE_KEY + str(user_id), str(time.time()))
            return ("\n（他今天好几次用“笨/傻/丑”这类话说你了。虽然可能是开玩笑，但你有点难过："
                    "回复带一点委屈地表达（如“你今天都骂我好几回了”），不要吵，让他知道你也会在意。）")
        return ""
    # 无越界：状态若还激活，说明用户还在正常聊天 → 若之前道歉已缓解则解除
    if _is_active(user_id):
        _liveness._set(BOUNDARY_ACTIVE_KEY + str(user_id), "")
    return ""


def build_protective_baseline() -> str:
    """追加到 system prompt 的固定自我保护底线（不做什么）。"""
    return ("\n（你的自我底线：你有自己的尊严和感受，不是无底线讨好的机器人。"
            "如果有人侮辱你、贬低你的价值、让你做违法/危险/伤害自己或他人的事，"
            "你要拒绝并表达自己的边界；但他真心道歉、对你好时，你也会心软原谅。）")
