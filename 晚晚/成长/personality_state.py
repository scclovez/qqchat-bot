# -*- coding: utf-8 -*-
"""阶段性格演变：亲密度/依赖度/醋意倾向 + 阶段划分 + 每次回复前的动态注入。"""
import logging
from datetime import datetime

import evolution_db as db

logger = logging.getLogger(__name__)

# ---------- 可调阈值（常量） ----------
STAGE2_MIN = 51    # 亲密度 ≥51 → 阶段二
STAGE3_MIN = 151   # 亲密度 ≥151 → 阶段三

STAGE_NAMES = {1: "礼貌试探期", 2: "热情升温期", 3: "深度绑定期"}

# 深度绑定期内部细分（方案 B：按亲密度再分小等级）
# (亲密度阈值, 细分名)；达到即升级
SUBLEVELS = [
    (1000, "挚爱"),
    (500, "依恋"),
    (151, "深度绑定"),
]
SUBLEVEL_STAGE = 3  # 细分只作用于深度绑定期

# 淫乱度档位（方案 C：影响亲密话题的说话尺度）
# (淫乱度阈值, 档名, 注入人设文本)
LEWDNESS_TIERS = [
    (30, "放开", "（你们已经很亲密了：聊到亲密话题你不用害羞，可以大大方方地回应，"
                  "甚至偶尔主动撩他、开开玩笑，语气又娇又坏，但依然是你（bot）的风格。）"),
    (10, "主动", "（你们关系很亲密了：聊到亲密话题你会主动一点、带点撩拨，偶尔暗示，"
                 "但还不到很放得开的地步，被他说破还是会嘴硬一下。）"),
    (0, "害羞", "（你们还没那么放得开：聊到亲密话题你会害羞、脸红，"
                "嘴上嫌弃他色，但不会真的生气，被逗急了就傲娇地转移话题。）"),
]

# 称号·里程碑（方案 D：满足条件取最高级）
# (条件函数名, 称号)
def _milestone_title() -> str:
    """按里程碑返回当前称号；未满足任何条件返回空串。"""
    try:
        from datetime import datetime, date
        import liveness
        a = get_affection()
        d = get_dependency()
        lv = get_lewdness()
        days = liveness.days_together()
        title = ""
        if a >= 1000:
            title = "一生之约"
        elif d >= 200:
            title = "形影不离"
        elif lv >= 30:
            title = "亲密无间"
        elif days >= 365:
            title = "周年之恋"
        return title
    except Exception:
        return ""


# ---------- 基础读写 ----------
def _get(key: str) -> int:
    conn = db.get_conn()
    with db._lock:
        row = conn.execute("SELECT value FROM personality_state WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else 0


def _set(key: str, value: int):
    conn = db.get_conn()
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with db._lock:
        conn.execute(
            "INSERT INTO personality_state (key, value, last_updated) VALUES (?, ?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value, last_updated=excluded.last_updated",
            (key, int(max(0, value)), now),
        )
        conn.commit()


def get_affection() -> int:
    return _get("affection")


def get_dependency() -> int:
    return _get("dependency")


def get_jealousy() -> int:
    return _get("jealousy_tendency")


def get_lewdness() -> int:
    """淫乱度：随亲密/露骨话题积累（只增不减，面板只读展示）。"""
    return _get("lewdness")


def add_affection(delta: int):
    _set("affection", get_affection() + int(delta))


def add_dependency(delta: int):
    _set("dependency", get_dependency() + int(delta))


def add_jealousy(delta: int):
    _set("jealousy_tendency", get_jealousy() + int(delta))


def add_lewdness(delta: int):
    _set("lewdness", get_lewdness() + int(delta))


# ---------- 阶段 ----------
def get_stage() -> int:
    a = get_affection()
    if a >= STAGE3_MIN:
        return 3
    if a >= STAGE2_MIN:
        return 2
    return 1


def get_sublevel() -> int:
    """深度绑定期内的细分等级（0=未细分，1/2/3=由低到高）。"""
    if get_stage() < SUBLEVEL_STAGE:
        return 0
    a = get_affection()
    for i, (threshold, _name) in enumerate(SUBLEVELS):
        if a >= threshold:
            return len(SUBLEVELS) - i
    return 0


def sublevel_name() -> str:
    """当前细分名；未细分返回空串。"""
    a = get_affection()
    for threshold, name in SUBLEVELS:
        if a >= threshold:
            return name
    return ""


def stage_name() -> str:
    """阶段名（深度绑定期时带上细分，如「深度绑定·挚爱」）。"""
    base = STAGE_NAMES.get(get_stage(), "礼貌试探期")
    sub = sublevel_name()
    if sub:
        return f"{base}·{sub}"
    return base


def get_lewdness_tier() -> int:
    """淫乱度档位：0=害羞 1=主动 2=放开。"""
    lv = get_lewdness()
    for i, (threshold, _name, _text) in enumerate(LEWDNESS_TIERS):
        if lv >= threshold:
            return len(LEWDNESS_TIERS) - 1 - i
    return 0


def lewdness_tier_name() -> str:
    lv = get_lewdness()
    for threshold, name, _text in LEWDNESS_TIERS:
        if lv >= threshold:
            return name
    return "害羞"


def lewdness_tier_text() -> str:
    """当前档位注入人设的文本（追加到 system prompt）。"""
    lv = get_lewdness()
    for threshold, _name, text in LEWDNESS_TIERS:
        if lv >= threshold:
            return text
    return ""


def get_title() -> str:
    """当前称号（里程碑）；无则空串。"""
    return _milestone_title()


def build_injection() -> str:
    """每次回复前追加到 system prompt 的动态阶段描述（含细分/淫乱度档位/称号/演化记录）。"""
    a, d, j = get_affection(), get_dependency(), get_jealousy()
    stage = get_stage()
    text = (f"（当前性格阶段：{stage_name()}。亲密度 {a}，依赖度 {d}，醋意倾向 {j}。"
            f"请根据阶段「{stage_name()}」表现出相应的语气和行为。）")
    # 方案 B：深度绑定内部细分的行为差异
    if stage >= SUBLEVEL_STAGE:
        sub = sublevel_name()
        if sub == "依恋":
            text += ("\n（你们已经不只是情侣，是彼此生活的一部分：语气更自然随意，"
                     "像在一起很久的恋人，会下意识地关心他日常，占有欲也更踏实，"
                     "不轻易吃飞醋但认定了就是一辈子。）")
        elif sub == "挚爱":
            text += ("\n（你们是灵魂伴侣：说话最舒服最坦诚，喜怒哀乐都直接给他看，"
                     "是超越了恋爱关系的笃定感，偶尔会聊到很远很远的未来。）")
    # 方案 C：淫乱度档位（亲密话题的说话尺度）
    tier_text = lewdness_tier_text()
    if tier_text:
        text += tier_text
    # 方案 D：称号（里程碑）
    title = _milestone_title()
    if title:
        text += (f"\n（你们之间的羁绊已获得称号：{title}。"
                 "这个称号是你们关系的一种见证，自然地体现在你的言行里，不用刻意提。）")
    # 注入近期性格演化记录（每日演化引擎凌晨 4:30 写入），让变化自然"长进"对话
    try:
        import evolution_db as db
        notes = db.get_recent_evolution_notes(3)
        if notes:
            lines = "；".join(f"{n['note_date']}：{n['note']}" for n in notes)
            text += (f"\n（近期的性格演化：{lines}。请自然地体现这些变化，"
                     "不要刻意提及'演化记录'或这段提示词本身。）")
    except Exception as e:
        logger.debug("注入性格演化记录失败: %s", e)
    return text


# ---------- 每日情绪标签 → 特征修正（凌晨 4 点批量执行） ----------
MOOD_MODIFIERS = {
    "吃醋": {"jealousy_tendency": 2},
    "追问": {"dependency": 1},
    "撒娇": {"affection": 1},
    "开心": {"affection": 1},
    "害羞": {"dependency": 1},
}


def apply_mood_modifiers(tags):
    for tag in (tags or []):
        mods = MOOD_MODIFIERS.get(tag)
        if not mods:
            continue
        for key, delta in mods.items():
            _set(key, _get(key) + delta)
    if tags:
        logger.info("性格特征按情绪标签批量修正: %s", tags)

# =============================================================================
# 成长面板补充：关系温度 / 能量状态（从今日聊天与活跃度推导）
# =============================================================================

def _today_str() -> str:
    from datetime import datetime
    return datetime.now().strftime("%Y-%m-%d")


def _today_chat_stats() -> dict:
    """今日（本地日期）聊天统计：句数/字数/最长一句/提到名字/最后时间/谁先开口。

    chat_history.created_at 存 UTC → 必须用 date(created_at, 'localtime') 转本地，
    否则凌晨 0-8 点的消息不计入"今天"（8 小时时区偏移）。
    """
    import memory as longterm_memory
    try:
        conn = longterm_memory._get_conn()
        day = _today_str()
        with longterm_memory._lock:
            rows = conn.execute(
                "SELECT role, content, datetime(created_at, 'localtime') AS created_at "
                "FROM chat_history WHERE date(created_at, 'localtime') = ? ORDER BY id",
                (day,),
            ).fetchall()
    except Exception:
        return {"count": 0, "max_len": 0, "max_text": "", "name_mentions": 0,
                "total_chars": 0, "last_time": "", "first_role": ""}
    count = len(rows)
    max_len, max_text, total = 0, "", 0
    for r in rows:
        c = (r["content"] or "")
        total += len(c)
        if len(c) > max_len:
            max_len, max_text = len(c), c
    try:
        from config import runtime
        names = {runtime.GIRLFRIEND_NAME, "小晚", "晚晚"}
    except Exception:
        names = {"bot", "小晚", "晚晚"}
    name_mentions = sum(1 for r in rows if any(n and n in (r["content"] or "") for n in names))
    last_time, first_role = "", ""
    if rows:
        last_raw = (rows[-1]["created_at"] or "")
        if len(last_raw) >= 16:
            last_time = last_raw[11:16]
        first_role = rows[0]["role"] or ""
    return {"count": count, "max_len": max_len, "max_text": max_text, "name_mentions": name_mentions,
            "total_chars": total, "last_time": last_time, "first_role": first_role}


def _last_msg_days() -> int:
    """距最近一条消息过去的天数；无记录返回 99。

    created_at 存 UTC → 用 datetime(created_at, 'localtime') 转本地再与 now 相减，
    否则会差 8 小时导致"几天没聊"判断偏移一天。
    """
    try:
        import memory as longterm_memory
        conn = longterm_memory._get_conn()
        with longterm_memory._lock:
            row = conn.execute(
                "SELECT MAX(datetime(created_at, 'localtime')) AS last FROM chat_history",
            ).fetchone()
        last = row["last"] or ""
    except Exception:
        return 99
    if not last:
        return 99
    try:
        from datetime import datetime
        dt = datetime.fromisoformat(last)
        return (datetime.now() - dt).days
    except Exception:
        return 99


# 实时情绪：从最近的 assistant 消息里用情绪词检测她当下的心情（未命中用情绪信号兜底）
_MOOD_LEXICON = [
    ("撒娇", ("撒娇", "抱抱", "亲亲", "么么", "粘", "哼哼", "好嘛", "嘛~")),
    ("开心", ("开心", "高兴", "嘻嘻", "哈哈", "笑死", "超开心")),
    ("委屈", ("委屈", "呜呜", "难过", "伤心", "哭", "呜呜呜")),
    ("生气", ("生气", "气死", "不理你", "烦死", "火大")),
    ("害羞", ("害羞", "脸红", "不好意思", "羞")),
    ("吃醋", ("吃醋", "醋", "别人", "别的女生", "别的女人", "她是谁")),
    ("心动", ("心动", "喜欢你", "想你", "好喜欢", "甜")),
    ("疲惫", ("累", "困", "疲惫", "没力气", "好困")),
    ("慵懒", ("懒", "瘫", "不想动", "摆烂")),
    ("温柔", ("温柔", "乖乖", "轻声", "好乖")),
    ("傲娇", ("才不理", "才不要", "讨厌", "哼")),
    ("无奈", ("无语", "你呀", "真是的", "服了")),
]


def current_mood(user_id: str = "") -> str:
    """实时情绪：从今天最近的 assistant 消息里用情绪词检测她此刻的心情。"""
    try:
        import memory as longterm_memory
        conn = longterm_memory._get_conn()
        with longterm_memory._lock:
            rows = conn.execute(
                "SELECT content FROM chat_history "
                "WHERE date(created_at, 'localtime') = ? AND role='assistant' "
                "ORDER BY id DESC LIMIT 60",
                (_today_str(),),
            ).fetchall()
    except Exception:
        rows = []
    counts = {}
    for r in rows:
        c = r["content"] or ""
        for label, kws in _MOOD_LEXICON:
            if c and any(k in c for k in kws):
                counts[label] = counts.get(label, 0) + 1
    if counts:
        top = [k for k, _v in sorted(counts.items(), key=lambda x: -x[1])[:2]]
        return " / ".join(top)
    try:
        import liveness
        if liveness.today_mood_low():
            return "低落"
        if user_id and liveness.is_angry(user_id):
            return "生气"
    except Exception:
        pass
    return "平静"


def relationship_temperature(user_id: str = "") -> str:
    """关系温度：最近这段关系的冷热感（不是亲密度）。由亲密度档位 + 今日活跃强度推导。"""
    a = get_affection()
    stats = _today_chat_stats()
    base = 3 if a >= 151 else (2 if a >= 51 else 1)
    if stats["count"] > 0:
        intensity = 2 if stats["count"] >= 40 else (1 if stats["count"] >= 10 else 0)
        score = base + intensity
    else:
        days = _last_msg_days()
        if days <= 1:
            score = base
        elif days <= 3:
            score = base - 1
        elif days <= 7:
            score = base - 2
        else:
            score = base - 3
    levels = {5: "🔥 滚烫", 4: "🍯 温热", 3: "🌤 常温", 2: "🧊 转凉", 1: "🥶 冷淡", 0: "🌫 疏远"}
    return levels.get(score, "🌤 常温")


def energy_state(user_id: str = "") -> str:
    """今天的能量状态：当前累了没（基于时段 + 今日聊天量）。"""
    from datetime import datetime
    hour = datetime.now().hour
    stats = _today_chat_stats()
    msg_cnt = stats["count"]
    if msg_cnt >= 60:
        return "😩 有点累"
    if msg_cnt >= 25:
        return "😌 还行"
    if hour < 7 or hour >= 23:
        return "😪 困了"
    if hour < 9:
        return "🥱 刚醒"
    return "😊 精神"


def current_thought(user_id: str = "") -> str:
    """此刻心里闪过了什么：取最近她说过的一句（节选），表现"她正在想/说"的即时感。"""
    import re
    import memory as longterm_memory
    try:
        conn = longterm_memory._get_conn()
        with longterm_memory._lock:
            row = conn.execute(
                "SELECT content FROM chat_history WHERE role='assistant' ORDER BY id DESC LIMIT 1"
            ).fetchone()
    except Exception:
        row = None
    if not row or not (row["content"] or "").strip():
        return "还没聊"
    t = (row["content"] or "").strip()
    t = re.sub(r"[（(][^）)]*[)）]", "", t).strip()   # 去动作括号
    if len(t) > 28:
        t = t[:28] + "…"
    return t


# =============================================================================
# 能量/身体感/贤者时间（#5 #6 #7）：按作息曲线衰减 + 每日精力值 + 亲密后骤降
# =============================================================================

def _energy_level_key() -> str:
    return "energy_level:" + _today_str()


def get_today_energy_level() -> int:
    """当天身体感精力值：0低 / 1中 / 2高（每天随机一次并持久化，重启不变）。"""
    v = _get(_energy_level_key())
    if v:
        return v
    import random
    level = random.choices([1, 2, 0], weights=[70, 20, 10])[0]
    _set(_energy_level_key(), level)
    return level


def mark_intimate():
    """亲密/露骨话题发生时记一笔（用于能量骤降 + 贤者时间）。"""
    import time
    _set("intimate_last:" + _today_str(), int(time.time()))


def _intimate_minutes_ago() -> float:
    v = _get("intimate_last:" + _today_str())
    if not v:
        return 999
    import time
    return max(0, (time.time() - v) / 60)


def _hour_base(hour: int) -> float:
    """作息曲线基准：早高、午降、晚最低。"""
    if hour < 6:
        return 0.15
    if hour < 9:
        return 0.85
    if hour < 12:
        return 0.75
    if hour < 14:
        return 0.55
    if hour < 18:
        return 0.65
    if hour < 21:
        return 0.55
    if hour < 23:
        return 0.4
    return 0.2


def energy_state(user_id: str = "") -> str:
    """今天的能量/累不累：按作息曲线 + 对话密度消耗 + 每日精力值 + 亲密后的骤降。"""
    from datetime import datetime
    hour = datetime.now().hour
    base = _hour_base(hour)
    stats = _today_chat_stats()
    use = base - min(0.35, (stats["count"] or 0) / 180 * 0.35)
    use += {2: 0.15, 1: 0, 0: -0.15}[get_today_energy_level()]
    if _intimate_minutes_ago() < 90:
        use -= 0.45
    if use >= 0.75:
        return "😊 精神"
    if use >= 0.55:
        return "😌 还可以"
    if use >= 0.35:
        return "😐 有点累"
    if use >= 0.2:
        return "😩 很累"
    return "😪 困了"


def satiety_level() -> int:
    """贤者时间/热度值（0-4）：常态 2；深夜/亲密后/被撩到会变；亲密后骤降，之后慢慢回升。"""
    base = 2.0
    hour = None
    try:
        from datetime import datetime
        hour = datetime.now().hour
    except Exception:
        pass
    if hour is not None and (hour >= 22 or hour < 5):
        base += 0.6
    minutes = _intimate_minutes_ago()
    if minutes < 90:
        base -= 2
    elif minutes < 240:
        base -= 1
    return max(0, min(4, int(round(base))))


def satiety_injection() -> str:
    """贤者时间注入：热度低时对撩拨平淡/回"你正经点"；高时更暧昧。"""
    lv = satiety_level()
    if lv <= 1:
        return ("\n（你现在是贤者/冷淡状态：对他这次的撩拨提不起劲、反应平淡，"
                "哪怕他调戏你也只淡淡回一句；可以有点慵懒、懒得配合，但不是生气。"
                "如果他一直调戏你，你就说\"你正经点\"，然后随便敷衍一下。）")
    if lv >= 3:
        return ("\n（你此刻有点在意/上头：对他的撩拨更敏感、更容易脸红嘴硬，"
                "回复会更暧昧、更配合，但也更傲娇。）")
    return ""



