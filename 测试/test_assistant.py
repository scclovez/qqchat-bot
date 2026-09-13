# -*- coding: utf-8 -*-
"""助理系统（行为规律 / 日程 / 学习）测试。

用法（在项目根运行）：
    python 测试/test_assistant.py          # 单独跑
    python 测试/test_all.py                # 作为综合测试的一组被调用

测试数据全部写入 DSH_DATA_ROOT 指定的一次性临时目录（由 test_all.py 或本文件自建），
不会污染真实数据。
"""
import asyncio
import os
import sys
import time
from datetime import datetime, timedelta, timezone

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
    now = datetime.now().replace(minute=15, second=0, microsecond=0)
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
        "english_profiles", "english_assessment_events",
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
    now = datetime.now().replace(minute=15, second=0, microsecond=0).replace(hour=3, minute=0, second=0, microsecond=0)
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
    now = datetime.now().replace(minute=15, second=0, microsecond=0)
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
    now = datetime.now().replace(minute=15, second=0, microsecond=0)
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


def test_goal_and_tasks():
    """目标识别 + 任务拆解（无模型时走规则兜底）+ 进度统计。"""
    import goal_manager as gm
    assert gm.looks_like_goal("我要过四级") and gm.looks_like_goal("我要学 Python")
    assert not gm.looks_like_goal("我今天好累"), "普通聊天不是目标"
    assert not gm.looks_like_goal("我要过四级？"), "问句不是目标"
    assert gm.detect_category("我要过四级") == "english"
    assert gm.detect_category("我要学 Python") == "programming"
    user = "test_goal_user"
    created = asyncio.run(gm.create_goal(user, "我要过四级", llm_call=None))
    assert created and created["goal_id"]
    assert created["tasks"], "目标必须拆出任务"
    assert all(5 <= item["minutes"] <= 45 for item in created["tasks"]), created["tasks"]
    tasks = adb.list_tasks(user, goal_id=created["goal_id"])
    assert tasks and all(row["source"] == "ai" and row["movable"] == 1 for row in tasks)
    adb.update_task(tasks[0]["id"], status="done")
    progress = gm.goal_progress(user, created["goal_id"])
    assert progress["done_tasks"] == 1 and progress["total_tasks"] == len(tasks)
    assert 0 < progress["progress"] <= 1
    assert gm._parse_deadline("我要在 6 月过四级").endswith("-01")
    assert gm._parse_deadline("年底前考完").endswith("12-31")


def test_study_session_lifecycle():
    """会话可暂停、可继续，进度不丢；结束时按完成比例给状态。"""
    import study_session as ss
    import goal_manager as gm
    user = "test_session_user"
    created = asyncio.run(gm.create_goal(user, "我要学英语", llm_call=None))
    goal = gm.active_goal(user)
    session = ss.start_session(user, goal, planned_minutes=12)
    assert session and session["status"] == "active" and session["planned_minutes"] == 12
    assert ss.start_session(user, goal)["id"] == session["id"], "已有未结束会话不得重复开"
    assert ss.pause_session(session["id"], done=2, total=5)
    assert adb.get_session(session["id"])["status"] == "paused"
    assert adb.get_open_session(user)["id"] == session["id"], "暂停中的会话仍可续"
    assert ss.resume_session(session["id"])
    assert adb.get_session(session["id"])["status"] == "active"
    half = ss.finish_session(session["id"], done=2, total=5)
    assert half["status"] == "partial" and abs(half["progress"] - 0.4) < 0.01
    assert adb.get_session(session["id"])["actual_minutes"] >= 1
    # 中断不丢进度：下次会话能看到上次的 partial 记录
    assert adb.get_last_finished_session(user)["status"] == "partial"
    assert created and created["goal_id"]


def test_word_cards_and_aloud_text():
    """词卡校验 + 朗读文本顺序（先例句、再把单词重读两遍）。"""
    import study_session as ss

    async def fake_llm(messages, **kwargs):
        return ('[{"word":"coffee","phonetic":"/ˈkɒfi/","meaning":"咖啡",'
                '"example_en":"I would like a cup of coffee, please.","example_cn":"我想要一杯咖啡。"},'
                '{"word":"tea","meaning":"茶","example_en":"She drinks tea every morning.","example_cn":"她每天早晨喝茶。"},'
                '{"word":"broken","meaning":"坏了","example_en":"no english word here","example_cn":"无效"},'
                '{"word":"123","meaning":"无效","example_en":"123 123","example_cn":"无效"}]')

    cards = asyncio.run(ss.generate_word_cards("四级词汇", 4, fake_llm))
    assert len(cards) == 2, "缺例句或单词不合法必须丢弃"
    assert cards[0]["word"] == "coffee"
    text = ss.read_aloud_text(cards[0])
    assert text.index("please") < text.index("coffee."), "必须先读例句再强调单词"
    assert text.count("coffee") >= 3, "单词要重复强调"
    assert "只读这个单词" in ss.word_only_instruction("温柔女声")


def test_record_answer_updates_mastery():
    """答错进薄弱、答对提升掌握度并安排复习。"""
    import study_session as ss
    user = "test_mastery_user"
    kid = adb.add_knowledge(user, "coffee", answer="咖啡", subject="english",
                            extra={"example_en": "I would like a coffee."})
    assert kid
    wrong = ss.record_answer(user, kid, correct=False)
    assert wrong["wrong_count"] == 1 and wrong["status"] in ("unstable", "learning")
    assert wrong["next_review"], "答错必须安排复习"
    assert "coffee" in [row["content"] for row in ss.gm.weak_points(user)] or \
        adb.get_knowledge(kid)["mastery"] < 0.6
    for _ in range(4):
        ss.record_answer(user, kid, correct=True)
    final = adb.get_knowledge(kid)
    assert final["mastery"] > wrong["mastery"]
    assert final["next_review"] and final["last_seen"]


def test_guided_grading_flow():
    """引导式纠错：答错先提示（不泄露答案）→ 再试 → 两次后才解释。"""
    import study_session as ss
    import goal_manager as gm
    user = "test_guided_user"
    created = asyncio.run(gm.create_goal(user, "我要学英语", llm_call=None))
    goal = gm.active_goal(user)
    kid = adb.add_knowledge(user, "coffee", answer="咖啡", subject="english", goal_id=goal["id"],
                            extra={"example_en": "I would like a coffee.", "example_cn": "我想要杯咖啡。"})
    session = ss.start_session(user, goal, planned_minutes=10)
    card = adb.get_knowledge(kid)
    question = ss.ask_question(session["id"], card)
    assert "coffee" in question or "咖啡" in question

    first = asyncio.run(ss.grade_answer(user, adb.get_session(session["id"]), "奶茶"))
    assert first["verdict"] == "retry", first
    instruction = ss.step_instruction(first)
    assert "不要" in instruction and "咖啡" not in instruction, \
        "第一次答错只能给提示，不能把答案说出来"
    assert first["result"]["wrong_count"] == 1 and first["result"]["next_review"]

    second = asyncio.run(ss.grade_answer(user, adb.get_session(session["id"]), "还是不知道"))
    assert second["verdict"] == "explain", second
    explain = ss.step_instruction(second)
    assert "咖啡" in explain, "两次之后才可以把答案讲清楚"
    assert not adb.get_session(session["id"])["pending_knowledge_id"]

    ss.ask_question(session["id"], adb.get_knowledge(kid))
    ok = asyncio.run(ss.grade_answer(user, adb.get_session(session["id"]), "咖啡"))
    assert ok["verdict"] == "correct" and ok["result"]["correct_count"] >= 1
    assert not adb.get_session(session["id"])["pending_knowledge_id"]


def test_review_priority_and_interval_growth():
    """复习优先：到期的先复习；连续答对逐步延长复习间隔。"""
    import study_session as ss
    user = "test_review_user"
    due_id = adb.add_knowledge(user, "apple", answer="苹果", subject="english")
    new_id = adb.add_knowledge(user, "banana", answer="香蕉", subject="english")
    adb.update_knowledge(due_id, status="learning", mastery=0.2,
                         next_review=(datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d %H:%M:%S"))
    assert [row["id"] for row in ss.review_queue(user)] == [due_id], "到期的先复习"
    session = ss.start_session(user, {"id": None}, planned_minutes=10)
    picked = ss.pick_next_for_session(user, session, None)
    assert picked["id"] == due_id, "会话应优先安排到期的复习"
    intervals = []
    for _ in range(3):
        ss.record_answer(user, due_id, correct=True)
        intervals.append(adb.get_knowledge(due_id)["next_review"])
    assert intervals == sorted(intervals), "连续答对复习间隔必须递增"
    assert adb.get_knowledge(due_id)["mastery"] > 0.2
    assert adb.get_knowledge(new_id)["status"] == "unknown"


def test_normal_chat_not_hijacked_in_study_mode():
    """没有进行中的会话时，普通消息绝不被当成学习作答。"""
    from qq_bot import QQGirlfriendBot
    bot = object.__new__(QQGirlfriendBot)
    handled = asyncio.run(bot._handle_study_answer(
        "private", "nobody", "no_session_user", 0, "今天好累啊", None))
    assert handled is False


def test_companion_strategy_keeps_learning_from_hijacking_chat():
    """学习途中：累了应暂停、日常聊天应放行，只有像答案的话才判分。"""
    import companion_strategy as cs
    assert cs.classify_pending_turn("今天好累，先聊会儿").kind == "pause"
    assert cs.classify_pending_turn("你在干嘛？").kind == "chat"
    assert cs.classify_pending_turn("咖啡").kind == "answer"
    prompt = cs.learning_relationship_instruction("retry")
    assert "首先始终是他的女友" in prompt
    assert "别说“错了”" in prompt


def test_active_study_pauses_for_user_state():
    """进行中的学习里说累了，应实际暂停并交给女友式回应。"""
    import companion_strategy  # 确保 qq_bot 能加载新增模块
    import study_session as ss
    from qq_bot import QQGirlfriendBot

    user = "test_pause_for_state_user"
    kid = adb.add_knowledge(user, "coffee", answer="咖啡", subject="english")
    sid = adb.add_session(user, planned_minutes=10, status="active")
    session = adb.get_session(sid)
    ss.ask_question(sid, adb.get_knowledge(kid))

    class Memory:
        def add_message(self, *args, **kwargs):
            pass

    bot = object.__new__(QQGirlfriendBot)
    bot._memory = Memory()
    events = []

    async def fake_persona(user_id, instruction, event=""):
        events.append(event)
        return "好，那先歇会"

    async def fake_split(*args, **kwargs):
        return "好，那先歇会"

    bot._study_persona_reply = fake_persona
    bot._reply_split = fake_split
    handled = asyncio.run(bot._handle_study_answer(
        "private", user, user, 0, "今天好累，先聊会儿", None))
    assert handled is True
    assert adb.get_session(sid)["status"] == "paused"
    assert events == ["pause"]


def test_checkin_verification_engine():
    """打卡由代码判定：低置信度不自动完成、日期不符不算、部分完成记进度。"""
    import image_verifier as iv
    now = datetime.now().replace(minute=15, second=0, microsecond=0)
    today = now.strftime("%Y-%m-%d")
    ok = iv.verify_checkin({"date": today, "count": 50, "target": 50}, 0.92, {})
    assert ok["verified"] is True and ok["progress"] == 1.0
    part = iv.verify_checkin({"date": today, "count": 32, "target": 50}, 0.9, {})
    assert part["verified"] is False and abs(part["progress"] - 0.64) < 0.01
    assert "部分" in part["reason"]
    low = iv.verify_checkin({"date": today, "count": 50, "target": 50}, 0.5, {})
    assert low["verified"] is False and "置信度" in low["reason"], "低置信度绝不能自动完成"
    old = iv.verify_checkin({"date": (now - timedelta(days=3)).strftime("%Y-%m-%d"),
                             "count": 50, "target": 50}, 0.95, {})
    assert old["verified"] is False and "日期" in old["reason"]
    unknown = iv.verify_checkin({"date": today, "count": None, "target": None}, 0.9, {})
    assert unknown["verified"] is False
    assert "夸他" in iv.checkin_instruction(ok, "背单词")
    assert "还差" in iv.checkin_instruction(part, "背单词")


def test_study_image_context_gate():
    """学习状态发图走学习逻辑，普通聊天发图仍走原有逻辑。"""
    import image_verifier as iv
    assert iv.is_study_context(False, "打卡", None) is False, "没有图片就不是学习图片"
    assert iv.is_study_context(True, "随便拍的照片", None) is False, "普通发图不得被截走"
    assert iv.is_study_context(True, "这是我的打卡截图", None) is True
    assert iv.is_study_context(True, "", {"status": "active"}) is True
    assert iv.is_study_context(True, "", {"status": "completed"}) is False


def test_homework_and_material_parsing():
    """图片结构化：作业题存正确答案但引导回复不泄露；教材作为学习材料。"""
    import image_verifier as iv
    raw = {
        "type": "homework", "confidence": 0.88,
        "questions": [
            {"index": "3", "question": "选择正确的选项：She ___ to school every day.",
             "user_answer": "go", "correct_answer": "goes", "topic": "一般现在时"},
            {"index": "4", "question": "翻译：我喜欢咖啡。", "user_answer": "",
             "correct_answer": "I like coffee.", "topic": "翻译"},
        ],
    }
    parsed = iv.normalize(raw)
    assert parsed["type"] == "homework" and len(parsed["questions"]) == 2
    items = iv.questions_to_items(parsed["questions"])
    assert items[0]["answer"] == "goes", "正确答案要内部留存（供复习）"
    instruction = iv.homework_instruction(items)
    assert "goes" not in instruction, "引导式纠错不得把答案直接说出去"
    assert "不要直接报正确答案" in instruction
    assert "选择正确的选项" in instruction
    assert iv.normalize({"type": "homework", "confidence": 0.9, "questions": []}) == {}
    assert iv.normalize({"type": "unknown_type", "confidence": 0.9}) == {}
    text = iv.normalize({"type": "textbook", "confidence": 0.8,
                         "material": {"subject": "英语", "topics": ["时态", "从句"],
                                      "summary": "语法复习"}})
    assert iv.material_items(text["material"])[0]["extra"]["type"] == "material"
    assert "今天就学这个" in iv.material_instruction(text["material"])


def test_history_backfill():
    """启动回填：把已有聊天记录读一遍形成推断；重复执行不重复计数。"""
    import behavior_profile as bp
    user = "test_backfill_user"
    bp.clear_cache(user)
    # 造 12 天历史聊天记录（UTC 存储，08:00 与次日 00:30 各一条）
    db_path = os.path.join(os.environ["DSH_DATA_ROOT"], "晚晚", "数据", "bot_memory.db")
    import sqlite3
    conn = sqlite3.connect(db_path)
    now = datetime.now().replace(minute=15, second=0, microsecond=0).replace(minute=0, second=0, microsecond=0)
    rows = []
    for offset in range(2, 14):
        for hour, minute in ((8, 0), (12, 30), (23, 0)):
            local = (now - timedelta(days=offset)).replace(hour=hour, minute=minute)
            utc = datetime.fromtimestamp(local.timestamp(), tz=timezone.utc)
            rows.append((user, "user", "在吗，今天上课好累", utc.strftime("%Y-%m-%d %H:%M:%S")))
        late = (now - timedelta(days=offset - 1)).replace(hour=0, minute=40)
        utc = datetime.fromtimestamp(late.timestamp(), tz=timezone.utc)
        rows.append((user, "user", "晚安，我先睡了", utc.strftime("%Y-%m-%d %H:%M:%S")))
    conn.executemany(
        "INSERT INTO chat_history (user_id, role, content, created_at) VALUES (?, ?, ?, ?)", rows)
    conn.commit()
    conn.close()

    first = bp.backfill_user(user)
    assert first["processed"] >= 30, first
    assert first["days"] >= 11, first
    data = bp.summarize(user)
    assert data["insufficient"] is False, "回填后必须立刻形成可用结论"
    assert data["coverage"]["days"] >= 11
    assert data["sleep"]["text"] != "—" and data["wake"]["text"] != "—"
    sleep_text = data["sleep"]["text"]
    assert sleep_text.startswith("01:") or sleep_text.startswith("00:"), sleep_text
    # 内容线索也被回填（上课 → busy_hint；晚安 → night_said）
    kinds = {row["kind"] for row in adb.get_evidence(user, "", ("busy_hint", "night_said"))}
    assert "busy_hint" in kinds and "night_said" in kinds, kinds
    # 幂等：再跑一次不再重复计数
    second = bp.backfill_user(user)
    assert second["processed"] == 0, second
    after = bp.summarize(user)
    assert after["messages"] == data["messages"], "重复回填不得让证据翻倍"


def test_active_periods_not_all_day():
    """聊天量大时"活跃时段"必须收敛到峰值附近，不能显示成 0-24 点。"""
    import behavior_profile as bp
    # 每个小时都有消息（低量），但 20-21 点明显是峰值
    hist = {hour: 3.0 for hour in range(24)}
    hist[20] = 40.0
    hist[21] = 36.0
    ranges = bp._active_ranges(hist)
    assert ranges, ranges
    covered = sum(int(item["end"]) - int(item["start"]) for item in ranges)
    assert covered <= 14, "活跃时段不得覆盖全天：%s" % ranges
    assert any(int(item["start"]) <= 20 < int(item["end"]) for item in ranges), ranges
    assert not (len(ranges) == 1 and ranges[0]["start"] == 0 and ranges[0]["end"] == 24)
    # 跨零点合并：23 点与 0 点都活跃 → 一段 23:00-01:00
    late = {23: 30.0, 0: 28.0, 20: 6.0}
    late_ranges = bp._ranges_from_histogram(late, min_weight=1.0, min_ratio=0.6)
    merged = [item for item in late_ranges if item["start"] == 23]
    assert merged and merged[0]["end"] == 25, late_ranges


def test_own_routine_gradually_adapts_to_user():
    """小晚保留自己的基线，但对稳定的长期作息会缓慢靠近。"""
    import life_state
    user = "test_own_routine_adapts"
    _seed_regular_days(user, 14, wake_hour=9, last_msg_hour=1)
    bp.clear_cache(user)
    bp.recompute(user, force=True)
    base_wake, base_sleep, base_weight = life_state.own_routine_minutes()
    wake, sleep, weight = life_state.own_routine_minutes(user)
    assert base_weight == 0.0 and weight > 0
    assert wake > base_wake, "用户稳定晚起时，她应逐渐晚起"
    assert sleep > base_sleep, "用户稳定晚睡时，她应逐渐晚睡"


def test_emotion_history_and_block_log():
    """情绪按小时采样（同小时覆盖）+ 压制原因账（含来源与归类）。"""
    import emotion_history as eh
    import assistant_db as adb2
    from qq_bot import QQGirlfriendBot, PRIORITY_PROACTIVE, PRIORITY_EXAM
    user = "test_emotion_curve"
    now = datetime.now().replace(minute=15, second=0, microsecond=0)
    assert eh.record(user, {"happy": 20, "angry": 0, "hurt": 0, "tired": 10}, ts=now.timestamp())
    assert eh.record(user, {"happy": 55, "angry": 5, "hurt": 0, "tired": 10},
                     ts=(now + timedelta(minutes=10)).timestamp())
    series = eh.series(user, 6)
    assert len(series) == 6
    last = series[-1]
    assert last["happy"] == 55 and last["angry"] == 5, "同一小时内应覆盖为最新值"
    points = adb2.emotion_series(user, 6)
    assert len(points) == 6 and sum(p["happy"] for p in points) == 55

    # 压制原因：造一个"此刻通常在睡"的用户，闸门压住时必须落一条带来源的记录
    sleepy_user = "test_block_log"
    bp.clear_cache(sleepy_user)
    _seed_regular_days(sleepy_user, 8, wake_hour=8, last_msg_hour=0)
    bp.recompute(sleepy_user, force=True)
    bot = object.__new__(QQGirlfriendBot)
    bot._last_proactive_any = {}
    bot._last_proactive_msg = {}
    night = datetime.now().replace(hour=4, minute=0, second=0, microsecond=0)
    verdict = bot._proactive_gate(sleepy_user, PRIORITY_PROACTIVE, now=night, source="普通主动")
    assert verdict["allow"] is False and verdict["block_kind"] == "sleeping", verdict
    exam = bot._proactive_gate(sleepy_user, PRIORITY_EXAM, now=night, source="考试提醒")
    assert exam["allow"] is True, "考试提醒不该被记成压制"
    blocks = adb2.proactive_blocks_today(sleepy_user, day=night.strftime("%Y-%m-%d"))
    assert blocks, "压制必须留下原因记录"
    assert blocks[0]["source"] == "普通主动" and blocks[0]["block_kind"] == "sleeping"
    stats = adb2.get_proactive_stats(sleepy_user, day=night.strftime("%Y-%m-%d"))
    assert stats["blocked"] == 1 and stats["sent"] == 0


def test_english_profile_onboarding_and_adaptive_plan():
    """英语先画像再学习：三题自然摸底后，AI 待办应按薄弱项改成第一周试运行计划。"""
    import english_profile as ep
    import goal_manager as gm
    user = "test_english_profile"
    created = asyncio.run(gm.create_goal(user, "我要过四级", llm_call=None))
    assert created and created["category"] == "english"
    assert created["tasks"][0]["title"] == "英语基础摸底", created["tasks"]
    initial = adb.get_english_profile(user)
    assert initial and initial["overall_level"] == "待评估"

    first = ep.pending_question(user)
    assert first["dimension"] == "vocabulary" and "borrow" in first["text"]
    result = ep.grade_pending_answer(user, "不知道")
    assert result["score"] == 0 and result["next_question"]["dimension"] == "grammar"
    assert ep.grade_pending_answer(user, "went")["score"] == 1
    assert ep.looks_like_pending_answer(user, "没赶上公交车，所以走路去学校"), \
        "阅读题的正常回答不能被聊天保护误拦"
    final = ep.grade_pending_answer(user, "没赶上公交车，所以走路去学校")
    profile = final["profile"]
    assert not final["next_question"], "三道核心小题后应停止，不拉成长测试"
    assert profile["skills"]["vocabulary"]["score"] == 0
    assert profile["skills"]["grammar"]["score"] == 1
    assert profile["recommendation"]["focus"] == "vocabulary", profile["recommendation"]

    changed = gm.refresh_english_goal_plan(user, profile, created["goal_id"])
    tasks = adb.list_tasks(user, goal_id=created["goal_id"])
    assert changed and tasks[0]["title"] == "核心词汇巩固", tasks
    events = adb.list_english_assessment_events(user)
    assert len(events) == 3 and {row["dimension"] for row in events} == set(ep.CORE_DIMENSIONS)


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
    check("学习目标与任务拆解", test_goal_and_tasks)
    check("学习会话可暂停续做", test_study_session_lifecycle)
    check("词卡校验与朗读顺序", test_word_cards_and_aloud_text)
    check("掌握度与复习安排", test_record_answer_updates_mastery)
    check("引导式纠错流程", test_guided_grading_flow)
    check("复习优先与间隔递增", test_review_priority_and_interval_growth)
    check("普通聊天不被学习拦截", test_normal_chat_not_hijacked_in_study_mode)
    check("陪学关系优先与聊天保护", test_companion_strategy_keeps_learning_from_hijacking_chat)
    check("学习中按用户状态暂停", test_active_study_pauses_for_user_state)
    check("图片打卡校验引擎", test_checkin_verification_engine)
    check("学习图片上下文判定", test_study_image_context_gate)
    check("作业纠错与教材解析", test_homework_and_material_parsing)
    check("历史聊天记录回填", test_history_backfill)
    check("活跃时段不覆盖全天", test_active_periods_not_all_day)
    check("小晚作息渐进靠近用户", test_own_routine_gradually_adapts_to_user)
    check("情绪曲线与压制原因账", test_emotion_history_and_block_log)
    check("英语画像与自适应计划", test_english_profile_onboarding_and_adaptive_plan)


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
