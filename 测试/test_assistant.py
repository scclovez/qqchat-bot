# -*- coding: utf-8 -*-
"""助理系统（行为规律 / 日程 / 学习）测试。

用法（在项目根运行）：
    python 测试/test_assistant.py          # 单独跑
    python 测试/test_all.py                # 作为综合测试的一组被调用

测试数据全部写入 DSH_DATA_ROOT 指定的一次性临时目录（由 test_all.py 或本文件自建），
不会污染真实数据。
"""
import os
import sys
import time
from datetime import datetime, timedelta

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

_TMP_BASE = os.path.join(ROOT, ".dsh_test_data_tmp")
if not os.environ.get("DSH_DATA_ROOT"):
    os.environ["DSH_DATA_ROOT"] = os.path.join(
        _TMP_BASE, f"assistant_{os.getpid()}_{int(time.time() * 1000)}")
    for _sub in ("晚晚", "配置"), ("晚晚", "数据"):
        os.makedirs(os.path.join(os.environ["DSH_DATA_ROOT"], *_sub), exist_ok=True)

import 路径  # noqa: E402

import assistant_db as adb  # noqa: E402
import behavior_profile as bp  # noqa: E402


def _ts(days_ago: int, hour: int, minute: int = 0) -> float:
    """构造「days_ago 天前的 hour 点」时间戳（避免落在未来导致误判"刚刚在聊天"）。"""
    base = datetime.now().replace(minute=minute, second=0, microsecond=0)
    base = base - timedelta(days=max(1, int(days_ago)))
    return base.replace(hour=int(hour) % 24).timestamp()


def _seed_regular_days(user_id: str, days: int, wake_hour: int = 8, last_msg_hour: int = 0,
                       busy_hour: int = None, study_hour: int = None):
    """造 days 天规律作息证据。

    一个「活跃日」= 当天 wake_hour 点说话 → 当天 12 点 / 21 点 → 次日凌晨 last_msg_hour。
    last_msg_hour < 4 时算作次日的凌晨消息（与真实熬夜跨零点一致）。
    全部样本至少落在 2 天前，确保不会被"15 分钟内说过话=清醒"的硬规则命中。
    """
    now = datetime.now()
    for offset in range(2, days + 2):
        day = (now - timedelta(days=offset)).replace(hour=0, minute=0, second=0, microsecond=0)

        def at(hour, minute=0):
            return (day + timedelta(hours=int(hour), minutes=int(minute))).timestamp()

        adb.add_evidence(user_id, "activity", ts=at(wake_hour), weight=2.0)
        adb.add_evidence(user_id, "activity", ts=at(12), weight=1.0)
        adb.add_evidence(user_id, "activity", ts=at(21), weight=2.0)
        late = day + timedelta(hours=int(last_msg_hour))
        if last_msg_hour < 4:
            late = late + timedelta(days=1)
        adb.add_evidence(user_id, "activity", ts=late.timestamp(), weight=1.0)
        if busy_hour is not None:
            adb.add_evidence(user_id, "busy_hint", ts=at(busy_hour), weight=1.0)
        if study_hour is not None:
            adb.add_evidence(user_id, "study_hint", ts=at(study_hour), weight=1.0)


def test_schema():
    """建表与迁移：9 张业务表齐备，重复初始化幂等，不破坏既有表。"""
    import memory  # 先让"老库"建好既有表，模拟真实升级场景
    import evolution_db
    import liveness
    memory.init_db(os.path.join(os.environ["DSH_DATA_ROOT"], "晚晚", "数据", "bot_memory.db"))
    evolution_db.init_db()
    liveness._get_conn()  # 触发活人感表建表
    adb.init_db()
    adb.init_db()
    names = {row["name"] for row in adb.query(
        "SELECT name FROM sqlite_master WHERE type = 'table'")}
    expected = {
        "behavior_evidence", "user_behavior_profile", "schedules", "goals", "tasks",
        "study_sessions", "knowledge_items", "review_records", "schema_meta",
    }
    missing = expected - names
    assert not missing, f"缺少表: {missing}"
    # 既有业务表仍在（升级不破坏老数据）
    assert {"chat_history", "personality_state", "liveness_state"} <= names
    row = adb.query("SELECT value FROM schema_meta WHERE key = 'schema_version'")
    assert row and int(row[0]["value"]) >= 1


def test_single_late_night_does_not_change_pattern():
    """一次深夜聊天不得形成作息结论（证据不足时只显示"还在观察"）。"""
    user = "test_single_night"
    bp.clear_cache(user)
    adb.add_evidence(user, "activity", ts=_ts(1, 3), weight=1.0)
    bp.recompute(user, force=True)
    data = bp.summarize(user, force=True)
    assert data["insufficient"] is True, "单天证据不得形成结论"
    assert data["sleep"]["text"] == "—" and data["sleep"]["level"] == "还在观察"
    assert data["evidence_days"] == 1


def test_multi_day_raises_confidence():
    """连续多日行为逐渐提高置信度。"""
    user = "test_confidence_growth"
    bp.clear_cache(user)
    _seed_regular_days(user, 3)
    bp.recompute(user, force=True)
    three_day = bp.summarize(user)
    three_conf = adb.get_profiles(user)["recent"]["sleep_pattern"]["confidence"]

    _seed_regular_days(user, 12)
    bp.recompute(user, force=True)
    twelve = bp.summarize(user)
    twelve_conf = adb.get_profiles(user)["recent"]["sleep_pattern"]["confidence"]

    assert three_day["insufficient"] is False
    assert twelve["evidence_days"] >= 12
    assert twelve_conf > three_conf, "证据天数增加后置信度必须提高"
    assert twelve["sleep"]["level"] in ("比较确定", "较稳定")
    assert "00:" in twelve["sleep"]["text"] or "01:" in twelve["sleep"]["text"]


def test_recent_pattern_overrides_long_term():
    """近期规律优先影响当前行为，并被总结成"最近比以前睡得晚"。"""
    user = "test_recent_override"
    bp.clear_cache(user)
    # 长期：60 天 23:00 睡（最后一条消息 22 点）
    for day in range(15, 75):
        adb.add_evidence(user, "activity", ts=_ts(day, 8), weight=1.0)
        adb.add_evidence(user, "activity", ts=_ts(day, 22), weight=1.0)
    # 近期：10 天 01:00 睡（最后一条消息 0 点）
    for day in range(1, 11):
        adb.add_evidence(user, "activity", ts=_ts(day, 8), weight=1.0)
        adb.add_evidence(user, "activity", ts=_ts(day, 0), weight=1.0)
    bp.recompute(user, force=True)
    data = bp.summarize(user)
    assert data["window"] == "recent", "近期证据充足时应使用近期规律"
    assert data["sleep"]["text"].startswith("01:") or data["sleep"]["text"].startswith("00:")
    assert any("晚睡" in note for note in data["observations"]), data["observations"]


def test_active_user_is_not_sleeping():
    """用户刚发消息 → 立即判定清醒；即使此刻通常已入睡。"""
    user = "test_active_not_sleeping"
    bp.clear_cache(user)
    _seed_regular_days(user, 8, wake_hour=8, last_msg_hour=0)
    bp.recompute(user, force=True)
    now = datetime.now().replace(hour=3, minute=0, second=0, microsecond=0)
    before = bp.infer_user_state(user, now)
    assert before["state"] == "likely_sleeping", before
    bp.observe_inbound(user, "在吗，睡不着", ts=now.timestamp())
    after = bp.infer_user_state(user, now)
    assert after["state"] == "active", after
    assert bp.should_suppress_proactive(user, priority=30, now=now)["allow"] is True


def test_silence_priority_and_busy():
    """睡眠时压住普通主动与碎碎念，但不拦重要日程 / 考试提醒；忙碌时段压普通主动。"""
    user = "test_suppress_priority"
    bp.clear_cache(user)
    _seed_regular_days(user, 8, wake_hour=8, last_msg_hour=0, busy_hour=10)
    bp.recompute(user, force=True)
    night = datetime.now().replace(hour=4, minute=0, second=0, microsecond=0)
    low = bp.should_suppress_proactive(user, priority=30, now=night)
    murmur = bp.should_suppress_proactive(user, priority=10, now=night)
    exam = bp.should_suppress_proactive(user, priority=95, now=night)
    assert low["allow"] is False and murmur["allow"] is False
    assert exam["allow"] is True, "重要日程/考试提醒不应被睡眠压制"
    # 白天他常在这个点忙 → 普通主动被压住，学习任务提醒仍可发
    busy_time = datetime.now().replace(hour=10, minute=0, second=0, microsecond=0)
    assert bp.infer_user_state(user, busy_time)["state"] == "possibly_busy"
    assert bp.should_suppress_proactive(user, priority=30, now=busy_time)["allow"] is False
    assert bp.should_suppress_proactive(user, priority=80, now=busy_time)["allow"] is True


def test_user_correction_is_evidence():
    """用户一句纠正会被解析成高权重证据，并改变推断（不做手工画像配置）。"""
    user = "test_user_correction"
    bp.clear_cache(user)
    _seed_regular_days(user, 2, wake_hour=9, last_msg_hour=2)
    bp.recompute(user, force=True)
    before = adb.get_profiles(user)["recent"]["sleep_pattern"]["value"]["hour"]
    bp.record_user_correction(user, "我一般 12 点半就睡了，7 点起")
    after = adb.get_profiles(user)["recent"]["sleep_pattern"]["value"]["hour"]
    assert before is not None and after is not None
    assert after < before, "纠正后入睡推断应提前"
    corrections = adb.get_user_corrections(user)
    assert corrections and "12" in corrections[0]["text"]


def test_hint_detection_and_prompt():
    """内容线索识别 + prompt 注入文本（只描述推断，不生成文案）。"""
    user = "test_hints"
    bp.clear_cache(user)
    bp.observe_inbound(user, "今天上课好累，晚上还想背单词", ts=_ts(1, 20))
    kinds = {row["kind"] for row in adb.get_evidence(user, "", ("busy_hint", "study_hint"))}
    assert "busy_hint" in kinds and "study_hint" in kinds
    _seed_regular_days(user, 6)
    bp.recompute(user, force=True)
    text = bp.profile_prompt(user)
    assert "你对他的作息观察" in text and "通常" in text


def test_schedule_parse_rules():
    """自然语言日程解析：明天/每天/周五、口语时刻、问句不误判。"""
    import schedule_manager as sm
    now = datetime.now()
    tomorrow = now + timedelta(days=1)
    parsed = sm.parse_text("明天下午三点有课", now)
    assert parsed, "明天下午三点有课 应能解析"
    assert parsed["day"] == tomorrow.strftime("%Y-%m-%d")
    assert parsed["start_time"].endswith("15:00:00"), parsed
    assert "课" in parsed["title"]

    daily = sm.parse_text("以后每天晚上八点学英语", now)
    assert daily["repeat_rule"] == "daily" and daily["start_time"].endswith("20:00:00")
    assert "英语" in daily["title"]

    exam = sm.parse_text("周五考试", now)
    assert exam and exam["priority"] >= sm.PRIORITY_EXAM, exam
    assert exam["day"] == (now + timedelta(days=(4 - now.weekday()) % 7 or 7)).strftime("%Y-%m-%d")

    assert not sm.looks_like_schedule("明天几点上课？"), "询问不是日程"
    assert not sm.looks_like_schedule("我今天好累"), "普通聊天不是日程"


def test_fixed_schedule_not_overridden():
    """用户固定日程不会被 AI 安排覆盖；AI 任务可移动且冲突时标出来。"""
    import schedule_manager as sm
    user = "test_schedule_fixed"
    day = datetime.now() + timedelta(days=1)
    start = day.replace(hour=15, minute=0, second=0, microsecond=0)
    sid = adb.add_schedule(user, "上课", start_time=start.strftime("%Y-%m-%d %H:%M:%S"),
                           source="user", priority=sm.PRIORITY_SCHEDULE)
    assert sid
    # AI 试图在同一时间塞一个学习任务
    ai_id = adb.add_task(user, "背单词", planned_minutes=20,
                         due_time=start.strftime("%Y-%m-%d %H:%M:%S"),
                         source="ai", movable=1)
    assert ai_id
    plan = sm.plan_day(user, day)
    kinds = [item["kind"] for item in plan]
    assert kinds[0] == "user", "用户固定事项必须排在最前"
    assert plan[0]["movable"] is False and plan[0]["title"] == "上课"
    row = adb.get_schedule(sid)
    assert row["status"] == "pending" and row["start_time"].startswith(day.strftime("%Y-%m-%d")), \
        "固定日程不能被 AI 任务改写"
    ai_rows = [item for item in plan if item["kind"] == "ai"]
    assert ai_rows and ai_rows[0]["movable"] is True
    assert ai_rows[0].get("conflict") is True, "与固定事项撞车时 AI 任务应被标记冲突"


def test_schedule_reminder_dedup():
    """到点提醒只发一次（防止同一日程反复轰炸）。"""
    import schedule_manager as sm
    user = "test_schedule_remind"
    now = datetime.now()
    start = now + timedelta(minutes=20)
    sid = adb.add_schedule(user, "开会", start_time=start.strftime("%Y-%m-%d %H:%M:%S"),
                           source="user", remind_before=30)
    first = sm.reminder_candidates(user, now)
    assert any(item["id"] == sid for item in first), first
    assert sm.claim_reminder(sid, now)
    second = sm.reminder_candidates(user, now)
    assert not any(item["id"] == sid for item in second), "已提醒过的日程不得重复提醒"


def test_proactive_gate_cooldown():
    """统一闸门：刚发过主动消息 → 低优先级被压住，考试提醒仍可穿透。"""
    from qq_bot import (QQGirlfriendBot, PRIORITY_MURMUR, PRIORITY_EXAM, PRIORITY_PROACTIVE)
    bot = object.__new__(QQGirlfriendBot)
    bot._last_proactive_any = {}
    bot._last_proactive_msg = {}
    bot._mark_proactive_sent("gate_user")
    assert bot._proactive_gate("gate_user", PRIORITY_MURMUR)["allow"] is False
    assert bot._proactive_gate("gate_user", PRIORITY_PROACTIVE)["allow"] is False
    assert bot._proactive_gate("gate_user", PRIORITY_EXAM)["allow"] is True


def test_old_night_silence_removed():
    """旧固定晚安静默必须彻底移除（改由时间戳+内容推断）。"""
    base = os.path.join(ROOT, "晚晚")
    qq = open(os.path.join(base, "机器人", "qq_bot.py"), encoding="utf-8").read()
    assert "_last_night_said" not in qq
    assert "_is_in_night_silence" not in qq
    cfg = open(os.path.join(base, "配置", "config.py"), encoding="utf-8").read()
    assert "NIGHT_SILENCE_HOURS" not in cfg
    gui = open(os.path.join(base, "界面", "gui_qt.py"), encoding="utf-8").read()
    assert "NIGHT_SILENCE_HOURS" not in gui
    assert "_proactive_gate" in qq, "主动消息必须经过统一闸门"


def run_into(check):
    """供 测试/test_all.py 调用的统一入口。"""
    check("助理库建表与迁移", test_schema)
    check("单次深夜聊天不改规律", test_single_late_night_does_not_change_pattern)
    check("多日行为提高置信度", test_multi_day_raises_confidence)
    check("近期规律覆盖长期", test_recent_pattern_overrides_long_term)
    check("发消息即判定清醒", test_active_user_is_not_sleeping)
    check("智能静默优先级", test_silence_priority_and_busy)
    check("用户纠正作为证据", test_user_correction_is_evidence)
    check("内容线索与注入文本", test_hint_detection_and_prompt)
    check("日程自然语言解析", test_schedule_parse_rules)
    check("固定日程不被 AI 覆盖", test_fixed_schedule_not_overridden)
    check("到点提醒不重复轰炸", test_schedule_reminder_dedup)
    check("主动消息统一闸门", test_proactive_gate_cooldown)
    check("旧固定静默已移除", test_old_night_silence_removed)


def main():
    passed, failed = [], []

    def check(name, fn):
        try:
            fn()
            passed.append(name)
            print(f"  [PASS] {name}")
        except Exception as exc:  # noqa: BLE001
            failed.append((name, exc))
            import traceback
            print(f"  [FAIL] {name}: {exc}")
            traceback.print_exc()

    print("=" * 50)
    print("  助理系统（行为规律）测试")
    print("=" * 50)
    run_into(check)
    print("=" * 50)
    print(f"结果: {len(passed)} 通过 / {len(failed)} 失败")
    if failed:
        return 1
    print("全部通过 ✔")
    return 0


if __name__ == "__main__":
    sys.exit(main())
