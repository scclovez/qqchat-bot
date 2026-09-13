# -*- coding: utf-8 -*-
"""开发环境综合测试：一键验证所有模块可导入、配置可加载、成长系统正常、GUI 可构建。

用法（在项目根运行）：
    python 测试/test_all.py

测试项：
    1. 全部业务模块导入（config / memory / qq_bot / tts / image_gen / behavior_profile ...）
    2. 配置加载（.env 与 llm_providers.json 若存在则校验；纯开发版可跳过）
    3. 成长系统（性格阶段 / 淫乱度档位 / 称号 / 生活线 / 对话策略 计算不抛异常）
    4. 提供商配置读写（llm_providers.json 可读写）
    5. GUI 冒烟（PySide6 构建 8 个页面，校验素材删除边界与「生活与学习」页并自动关闭）
    6. 助理系统（行为规律推断：单次异常不形成结论、多日提高置信度、近期覆盖长期、智能静默优先级等）
"""
import atexit
import asyncio
import os
import pathlib
import shutil
import sys
import tempfile
import time
import traceback

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, ROOT)

# 数据隔离：把可写数据根指到一次性临时目录后再 import 路径/业务模块，
# 防止测试读写、污染真实数据（.env / bot_memory.db / llm_providers.json / 日志等）。
# 以前测试直接落在项目目录（priv 版里因此留下 testuser/_tmp_test_* 残留）。
# 注意：不能用 tempfile.mkdtemp 后再建中文子目录（受限环境实测 WinError 5），
# 因此用单次 makedirs 直接创建多级目录。临时目录退出时自动删除。
_TMP_BASE = os.path.join(ROOT, ".dsh_test_data_tmp")
_TEST_DATA = os.path.join(_TMP_BASE, f"data_{os.getpid()}_{int(time.time() * 1000)}")
os.environ["DSH_DATA_ROOT"] = _TEST_DATA
for _sub in ("晚晚", "配置"), ("晚晚", "数据"), ("晚晚", "用量"), ("晚晚", "图片"):
    os.makedirs(os.path.join(_TEST_DATA, *_sub), exist_ok=True)


def _cleanup_test_data():
    shutil.rmtree(_TEST_DATA, ignore_errors=True)
    try:
        os.rmdir(_TMP_BASE)  # 空则删，非空（残留）保留待查
    except OSError:
        pass


atexit.register(_cleanup_test_data)

# Windows 控制台默认 GBK：让 stdout/stderr 按 UTF-8 输出，
# 否则结尾 print("全部通过 ✔") 会抛 UnicodeEncodeError 导致退出码 1（曾实测 5/5 通过却报失败）。
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

import 路径  # noqa: E402

PASS = []
FAIL = []


def check(name, fn):
    try:
        fn()
        PASS.append(name)
        print(f"  [PASS] {name}")
    except Exception as e:
        FAIL.append((name, e))
        print(f"  [FAIL] {name}: {e}")
        traceback.print_exc()


def test_imports():
    mods = [
        "config", "llm_providers", "personality", "liveness", "emotion_state", "boundary",
        "conversation", "dialogue_policy", "episodic_memory", "memory", "usage", "image_gen", "comfyui_client",
        "appearance_ref", "qq_bot", "llm_base", "openai_compat",
        "deepseek_client", "llm_factory", "live_info", "life_state", "singleton", "tts",
        "asr", "diary", "personality_state", "evolution", "evolution_db",
        "active_pull", "qzone", "interact_tools", "tray",
        "assistant_db", "behavior_profile",
    ]
    for m in mods:
        __import__(m)
    print(f"      已导入 {len(mods)} 个模块")


def test_config():
    from config import config, runtime
    runtime.load_from_file()
    assert config.DEEPSEEK_BASE_URL, "DeepSeek base url 缺失"
    print(f"      DeepSeek 模型: {config.DEEPSEEK_MODEL}")


def test_growth():
    from datetime import datetime, timedelta
    from config import runtime
    import personality_state as pstate
    import liveness
    import emotion_state
    import evolution
    import life_state
    import dialogue_policy
    import episodic_memory
    import memory as longterm_memory
    from memory import _drop_superseded, _select_relevant_memory
    from qzone import _extract_feed_identity
    from qq_bot import (
        MESSAGE_DEBOUNCE_SECONDS, QQGirlfriendBot, _compact_image_followup,
        split_reply_text,
    )
    assert pstate.get_stage() >= 1
    assert pstate.stage_name()
    assert pstate.lewdness_tier_name() in ("害羞", "主动", "放开")
    assert pstate.get_title() is not None
    assert set(pstate.get_axes()) == set(pstate.PERSONALITY_AXES)
    assert all(0 <= value <= 100 for value in pstate.get_axes().values())
    inj = pstate.build_injection()
    assert isinstance(inj, str) and inj
    # 真人感：关系余温只记抽象状态；记忆只在话题有关时被选中。
    liveness.record_relationship_turn("test_relationship", "明天要去考试，谢谢你陪我")
    relation = liveness.relationship_injection("test_relationship")
    assert "关系节奏" in relation and "轻轻问一次进展" in relation
    selected = _select_relevant_memory(["他喜欢咖啡", "他最近在准备考试"], "考试怎么样", 2)
    assert selected == ["他最近在准备考试"]
    parts = split_reply_text("第一句稍微有一点长，需要自然切开。\n第二句是补充。\n第三句。\n第四句不应发送。")
    assert 1 <= len(parts) <= 3 and all(len(part) <= 32 for part in parts)
    assert split_reply_text("好呀~我在呢～") == ["好呀，我在呢"]
    image_followup = _compact_image_followup(
        "图片短评：刚才镜头有点歪，不过窗边那束暖光真的特别好看，像傍晚落在桌上的一小块糖。"
    )
    assert image_followup and len(image_followup) <= 22 and "~" not in image_followup
    assert _compact_image_followup("【不补充】") == ""
    feed_html = (
        '<div id="feed_123456_311_0_1789000000_0_1" data-key="abcdef0123456789abcdef01">'
        '<i name="feed_data" data-tid="abcdef0123456789abcdef01" data-uin="123456"></i></div>'
    )
    assert _extract_feed_identity({
        "tid": "wrongshort", "key": "abcdef0123456789abcdef01",
        "uin": "999999", "html": feed_html,
    }) == ("abcdef0123456789abcdef01", "123456")
    merged = QQGirlfriendBot._merge_debounced_messages([
        {"raw_message": "第一句", "message_id": 1},
        {"raw_message": "第二句", "message_id": 2},
    ])
    assert merged["raw_message"] == "第一句\n第二句" and merged["message_id"] == 2
    # 回复版本：重复事件不推进轮次；新消息会让旧回答在发送前失效。
    turn_bot = object.__new__(QQGirlfriendBot)
    turn_bot._seen_message_ids = {}
    turn_bot._inbound_revisions = {}
    event1 = {"message_type": "private", "user_id": "turn_user", "message_id": 101}
    assert turn_bot._claim_message_id(event1)
    prepared1 = turn_bot._register_inbound_turn(event1)
    assert prepared1["_turn_revision"] == 1
    assert not turn_bot._claim_message_id(event1)
    assert turn_bot._inbound_revisions["turn_user"] == 1
    event2 = {"message_type": "private", "user_id": "turn_user", "message_id": 102}
    assert turn_bot._claim_message_id(event2)
    prepared2 = turn_bot._register_inbound_turn(event2)
    assert prepared2["_turn_revision"] == 2
    assert turn_bot._turn_is_stale("turn_user", prepared1["_turn_revision"])
    assert not turn_bot._turn_is_stale("turn_user", prepared2["_turn_revision"])
    assert asyncio.run(turn_bot._reply_split(
        "private", "turn_user", "turn_user", 101, "旧回答",
        expected_revision=prepared1["_turn_revision"],
    )) == ""
    sent_calls = []

    async def fake_reply(msg_type, target_id, user_id, message_id, text):
        sent_calls.append(text)
        # 模拟第一条刚发完，对方立刻补发了一条新消息。
        turn_bot._inbound_revisions["turn_user"] = 3
        return 11

    turn_bot._reply = fake_reply
    turn_bot._observe_life_reply = lambda *args: None
    old_liveness = runtime.LIVENESS_ENABLED
    old_cd_min = runtime.REPLY_COOLDOWN_MIN
    old_cd_max = runtime.REPLY_COOLDOWN_MAX
    try:
        runtime.LIVENESS_ENABLED = False
        runtime.REPLY_COOLDOWN_MIN = 0
        runtime.REPLY_COOLDOWN_MAX = 0
        actually_sent = asyncio.run(turn_bot._reply_split(
            "private", "turn_user", "turn_user", 102,
            "第一条。\n第二条。", force_voice=False,
            expected_revision=prepared2["_turn_revision"],
        ))
    finally:
        runtime.LIVENESS_ENABLED = old_liveness
        runtime.REPLY_COOLDOWN_MIN = old_cd_min
        runtime.REPLY_COOLDOWN_MAX = old_cd_max
    assert sent_calls == ["第一条。"] and actually_sent == "第一条。"
    assert MESSAGE_DEBOUNCE_SECONDS == 6.0
    assert liveness.debounce_seconds_for("晚晚") == 6.0
    assert liveness.debounce_seconds_for("你在吗？") < 6.0
    assert liveness.debounce_seconds_for("救命，我受伤了") <= 1.2
    assert "同一轮表达" in liveness.response_style_injection("第一句\n第二句")
    nick1 = liveness.nickname_for_stage(3, "默契相守", "test_relationship", "嘿嘿")
    nick2 = liveness.nickname_for_stage(3, "默契相守", "test_relationship", "嘿嘿")
    assert nick1 == nick2
    parsed = evolution._parse_evolution_response(
        '{"affection_delta":1,"warmth_delta":2,"initiative_delta":-1}'
    )
    assert parsed["warmth_delta"] == 2 and parsed["initiative_delta"] == -1
    assert parsed["playfulness_delta"] == 0
    # 连续生活线：稳定日程 → 明确当前动作 + 近期计划 → 到点自动切换 → 结束后过渡。
    base_time = datetime(2026, 9, 10, 10, 0, 0)
    scheduled = life_state.get_snapshot(base_time)
    assert scheduled["activity"] and scheduled["location"]
    assert life_state.get_snapshot(base_time)["started_at"] == scheduled["started_at"]
    assert not life_state._get_conn().in_transaction, "生活状态读取后不应遗留写事务"
    changed = life_state.observe_assistant_reply(
        "我正在画画，等下去洗澡。", base_time,
    )
    assert changed == {"state": "画画", "plan": "洗澡"}
    assert life_state.get_snapshot(base_time + timedelta(minutes=1))["activity"] == "画画"
    plan = life_state.get_next_plan(base_time)
    assert plan["activity"] == "洗澡"
    assert life_state.get_snapshot(base_time + timedelta(minutes=16))["activity"] == "洗澡"
    assert life_state.get_snapshot(base_time + timedelta(minutes=51))["activity"] == "吹头发"
    assert "生活线" in life_state.status_line(base_time + timedelta(minutes=16))
    # 她自己的起床/睡前仪式只看生活线，不依赖用户作息或学习状态。
    own_morning = life_state.own_routine_candidate(datetime(2026, 9, 11, 8, 0, 0))
    own_night = life_state.own_routine_candidate(datetime(2026, 9, 11, 22, 50, 0))
    assert own_morning.get("key") == "morning" and own_night.get("key") == "night"
    assert life_state.own_routine_minutes()[2] == 0.0
    assert not life_state.own_routine_sent(own_morning)
    assert life_state.mark_own_routine_sent(own_morning)
    assert life_state.own_routine_sent(own_morning)
    # 对话决策层：连续消息整体理解，严肃问题收敛动作，轻松互动才开放相应出口。
    urgent_plan = dialogue_policy.plan_turn("我现在很难受\n有点撑不住了")
    assert urgent_plan.multi_message and urgent_plan.intent in ("紧急关怀", "倾诉安慰")
    assert not urgent_plan.allow_image and not urgent_plan.allow_sticker
    assert urgent_plan.max_parts <= 2 and urgent_plan.focus
    advice_plan = dialogue_policy.plan_turn("这个事情应该怎么办？")
    assert advice_plan.intent == "一起解决问题"
    assert not advice_plan.allowed_tools and not advice_plan.allow_voice
    playful_plan = dialogue_policy.plan_turn("嘿嘿，喜欢你")
    assert playful_plan.intent == "亲密回应" and playful_plan.allowed_tools
    assert playful_plan.preferred_parts == 2 and "空行" in playful_plan.injection()
    short_question_plan = dialogue_policy.plan_turn("在干嘛")
    assert short_question_plan.preferred_parts == 1
    comfort_plan = dialogue_policy.plan_turn("我今天真的好累")
    assert comfort_plan.preferred_parts == 2
    image_plan = dialogue_policy.plan_turn("发张自拍给我看看")
    assert image_plan.allow_image and not image_plan.allowed_tools
    mention_plan = dialogue_policy.plan_turn("我今天拍了一张照片")
    assert not mention_plan.allow_image
    assert "本轮对话策略" in playful_plan.injection()
    mood_plan = dialogue_policy.plan_turn(
        "宝宝", mood_state={"angry": 42, "hurt": 10, "tired": 5, "happy": 0},
    )
    assert mood_plan.max_parts == 1 and not mood_plan.allow_sticker
    assert set(emotion_state.snapshot("test_emotion")) == set(emotion_state.EMOTIONS)
    emotion_state.apply_event("test_emotion", "hurt", 45, "test_hurt")
    assert not liveness.emotion_allows_playful_media("test_emotion")
    assert liveness.emotion_sticker_factor("test_emotion") < 0.2
    assert "委屈" in liveness.selfie_mood_modifier("test_emotion")
    assert len(split_reply_text("第一条。\n第二条。", max_parts=1)) == 1
    clipped = split_reply_text("这是一条明显超过极小总长度上限的回复", max_total=8)
    assert clipped and len(clipped[0]) <= 8
    # 情景记忆：同一事件合并进展，结果关闭事件，小事自然淡出，回访最多一次。
    event_now = datetime(2026, 9, 10, 9, 0, 0)
    episodes = episodic_memory.merge_episodes([], [{
        "topic": "考试", "detail": "明天下午考试", "time_hint": "明天下午",
        "emotion": "紧张", "status": "pending", "importance": 3,
        "certainty": "confirmed", "follow_up_after": "2026-09-11 18:00:00",
    }], event_now)
    assert len(episodes) == 1 and episodes[0]["status"] == "pending"
    assert episodic_memory.select_relevant_episodes(episodes, "考试考得怎么样", now=event_now)
    due = episodic_memory.get_due_followup(episodes, datetime(2026, 9, 11, 19, 0, 0))
    assert due and due["followup_count"] == 0
    episodes = episodic_memory.mark_followed_up(episodes, due["id"], datetime(2026, 9, 11, 19, 0, 0))
    assert episodic_memory.get_due_followup(episodes, datetime(2026, 9, 12, 19, 0, 0)) is None
    episodes = episodic_memory.merge_episodes(episodes, [{
        "topic": "考试", "status": "completed", "result": "考完了，发挥不错",
        "importance": 3, "certainty": "confirmed", "replaces": "考试",
    }], datetime(2026, 9, 11, 20, 0, 0))
    assert len(episodes) == 1 and episodes[0]["status"] == "completed"
    separate = episodic_memory.merge_episodes([], [
        {"topic": "数学考试", "detail": "明天下午", "status": "pending"},
        {"topic": "英语考试", "detail": "后天下午", "status": "pending"},
    ], event_now)
    assert len(separate) == 2
    faded = episodic_memory.prune_episodes([{
        "topic": "随口提到的小事", "status": "completed", "importance": 1,
        "certainty": "mentioned", "updated_at": "2026-08-01 09:00:00",
    }], event_now)
    assert faded == []
    assert _drop_superseded(["用户在旧公司工作", "用户喜欢咖啡"], ["用户在旧公司工作"]) == ["用户喜欢咖啡"]
    episode_user = "test_episode_memory"
    assert longterm_memory.observe_user_episode(episode_user, "我明天要考试，有点紧张", event_now) == "created"
    assert longterm_memory.observe_user_episode(episode_user, "我考完了，发挥还不错", event_now + timedelta(days=1)) == "closed"
    stored = longterm_memory.get_user_memory(episode_user).get("episodes", [])
    assert len(stored) == 1 and stored[0]["status"] == "completed"


def test_providers():
    from llm_providers import load_providers, save_providers
    ps = load_providers()
    assert isinstance(ps, list)
    # 读回写（不改变内容）
    save_providers(ps)
    print(f"      提供商数量: {len(ps)}")


def test_gui():
    """GUI 冒烟：构建 8 页，校验陪伴页与素材删除权限后自动退出。"""
    from PySide6.QtWidgets import QApplication, QFrame, QPushButton
    from PySide6.QtCore import QTimer
    import gui_qt
    app = QApplication([])
    win = gui_qt.MainWindow()
    win.show()
    result = {}

    def verify():
        message_box_methods = {
            name: getattr(gui_qt.QMessageBox, name)
            for name in ("question", "information", "warning", "critical")
        }
        gui_qt.QMessageBox.question = (
            lambda *args, **kwargs: gui_qt.QMessageBox.StandardButton.Yes)
        for name in ("information", "warning", "critical"):
            setattr(
                gui_qt.QMessageBox, name,
                lambda *args, **kwargs: gui_qt.QMessageBox.StandardButton.Ok,
            )
        try:
            result["tabs"] = win.tabs.count()
            result["growth"] = len(win._growth_labels)
            result["growth_groups"] = len(win.findChildren(QFrame, "GrowthGroup"))
            result["has_axes"] = hasattr(win, "_personality_axes_var")
            result["compact_dashboard"] = all(
                card.maximumHeight() <= 155 for card in win._dashboard_status_cards
            )
            win._refresh_growth_stats()
            # 陪伴状态新增指标：给一个测试用户 + 一条日程，确认"她眼里的你/今天"能取到真实数据
            from config import runtime as _rt
            old_uid = _rt.PROACTIVE_ONLY_USER_ID
            try:
                _rt.PROACTIVE_ONLY_USER_ID = "gui_status_user"
                import assistant_db as _adb
                from datetime import datetime as _dt, timedelta as _td
                _start = (_dt.now() + _td(minutes=40)).strftime("%Y-%m-%d %H:%M:%S")
                _adb.add_evidence("gui_status_user", "activity")
                _adb.add_schedule("gui_status_user", "测试日程", start_time=_start,
                                  source="user", remind_before=15)
                win._refresh_growth_stats()
                result["status_user_state"] = win._growth_labels["user_state"].text()
                result["status_schedule"] = win._growth_labels["schedule_next"].text()
                result["status_msgs"] = win._growth_labels["msgs_today"].text()
                result["status_proactive"] = win._growth_labels["proactive_today"].text()
                result["emotion_bars"] = len(win._emotion_bars._rows)
            finally:
                _rt.PROACTIVE_ONLY_USER_ID = old_uid
                win._refresh_growth_stats()
            # 助理系统：新一级页「生活与学习」及其四个子页签必须存在且可刷新
            result["assistant_tabs"] = win._life_study_tabs.count()
            result["assistant_metrics"] = len(win._assistant_metric_labels)
            win._assistant_refresh_ts = 0.0
            win._refresh_assistant_panel()
            result["assistant_status"] = win._assistant_status.text()

            # 模拟接口已返回模型列表：选中下拉项应立即写入模块分派。
            provider_combo, model_combo = win._assign_vars["chat"]
            assign_status = win._assign_status_labels["chat"]
            requested_provider = provider_combo.currentData() or ""
            win._on_assign_models_loaded(
                "chat", provider_combo, model_combo, assign_status, requested_provider,
                ["test-model-a", "test-model-b"], None, "测试提供商", True,
            )
            model_index = model_combo.findText("test-model-b")
            model_combo.setCurrentIndex(model_index)
            model_combo.activated.emit(model_index)
            from llm_providers import get_assignment
            result["model_selectable"] = (
                model_combo.isEnabled()
                and model_combo.currentText() == "test-model-b"
                and get_assignment("chat").get("model") == "test-model-b"
                and assign_status.text() == "已应用"
            )

            generated_dir = pathlib.Path(gui_qt.data_path("晚晚", "图片", "生成图片"))
            audio_dir = pathlib.Path(gui_qt.data_path("晚晚", "语音", "语音缓存"))
            appearance_dir = pathlib.Path(win._media_folder("appearance"))
            for folder in (generated_dir, audio_dir, appearance_dir):
                folder.mkdir(parents=True, exist_ok=True)
            generated_file = generated_dir / "gui_delete_test.png"
            audio_file = audio_dir / "gui_delete_test.wav"
            appearance_file = appearance_dir / "gui_keep_test.png"
            generated_file.write_bytes(b"test-image")
            audio_file.write_bytes(b"test-audio")
            appearance_file.write_bytes(b"test-appearance")
            win._refresh_media_library()

            generated_card = win._media_gallery_layouts["generated"].itemAt(0).widget()
            appearance_card = win._media_gallery_layouts["appearance"].itemAt(0).widget()
            result["generated_delete_button"] = any(
                button.text() == "删除" for button in generated_card.findChildren(QPushButton)
            )
            result["appearance_has_no_delete"] = all(
                button.text() != "删除" for button in appearance_card.findChildren(QPushButton)
            )
            audio_actions = win._media_audio_table.cellWidget(0, 3)
            result["audio_delete_button"] = any(
                button.text() == "删除" for button in audio_actions.findChildren(QPushButton)
            )

            result["generated_deleted"] = win._delete_media_file("generated", generated_file)
            result["audio_deleted"] = win._delete_media_file("audio", audio_file)
            result["appearance_protected"] = (
                not win._delete_media_file("appearance", appearance_file)
                and appearance_file.exists()
            )
            app.processEvents()
            win.close()
        finally:
            for name, method in message_box_methods.items():
                setattr(gui_qt.QMessageBox, name, method)
            app.quit()

    QTimer.singleShot(1500, verify)
    app.exec()
    assert result.get("tabs") == 8, "新增「生活与学习」后应有 8 个顶级页"
    assert result.get("growth") == 33, "陪伴状态应保留原有 16 项并加入今天/内部状态共 33 项指标"
    assert result.get("growth_groups") == 18, "陪伴状态应为 18 张卡片（含情绪走势与压制原因）"
    assert result.get("has_axes"), "性格轮廓应位于分组卡片内"
    assert result.get("status_user_state") not in (None, "", "—"), "「她眼里的你」应能推断出状态"
    assert "测试日程" in (result.get("status_schedule") or ""), "「下一项」应显示刚建的日程与倒计时"
    assert " / " in (result.get("status_msgs") or ""), "「今天的互动」应显示 你/她 两边的条数"
    assert result.get("status_proactive"), "「主动消息」应显示今天的条数"
    assert result.get("emotion_bars") == 4, "持续情绪应有 4 条（开心/生气/委屈/疲惫）"
    assert result.get("compact_dashboard"), "首页三张运行状态卡应保持紧凑"
    assert result.get("assistant_tabs") == 4, "「生活与学习」应有今日/学习/日程/她对你的了解四个子页签"
    assert result.get("assistant_metrics") == 4, "「她对你的了解」应展示起床/睡觉/活跃/学习四项推断"
    assert result.get("assistant_status"), "「她对你的了解」应给出观察状态说明"
    assert result.get("model_selectable"), "获取后的模型必须可选并立即保存"
    assert result.get("generated_delete_button"), "生成图片应显示删除按钮"
    assert result.get("audio_delete_button"), "语音缓存应显示删除按钮"
    assert result.get("appearance_has_no_delete"), "人设图片不应显示删除按钮"
    assert result.get("generated_deleted"), "生成图片删除功能应生效"
    assert result.get("audio_deleted"), "语音缓存删除功能应生效"
    assert result.get("appearance_protected"), "删除逻辑必须保护人设图片"
    print("      GUI 8 页、紧凑首页、模型分派、素材删除权限与生活学习页 OK")


def test_assistant():
    """助理系统（行为规律 / 日程 / 学习）专项测试。"""
    import test_assistant
    test_assistant.run_into(check)


def main():
    print("=" * 50)
    print("  AI 电子女友 · 开发环境测试")
    print("=" * 50)
    print("[1/6] 模块导入")
    check("模块导入", test_imports)
    print("[2/6] 配置加载")
    check("配置加载", test_config)
    print("[3/6] 成长系统")
    check("成长系统", test_growth)
    print("[4/6] 提供商配置")
    check("提供商配置", test_providers)
    print("[5/6] GUI 冒烟")
    check("GUI 冒烟", test_gui)
    print("[6/6] 助理系统（行为规律 / 日程 / 学习）")
    check("助理系统", test_assistant)
    print("=" * 50)
    print(f"结果: {len(PASS)} 通过 / {len(FAIL)} 失败")
    if FAIL:
        for name, e in FAIL:
            print(f"  失败项: {name} -> {e}")
        sys.exit(1)
    print("全部通过 ✔")


if __name__ == "__main__":
    main()
