# -*- coding: utf-8 -*-
"""持续情绪状态机的纯标准库回归测试。"""
import os
import sys
import tempfile
import unittest


PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for relative in ("", os.path.join("晚晚", "人设"), os.path.join("晚晚", "工具"),
                 os.path.join("晚晚", "对话")):
    path = os.path.join(PROJECT_ROOT, relative)
    if path not in sys.path:
        sys.path.insert(0, path)

import emotion_state  # noqa: E402
import dialogue_policy  # noqa: E402


class EmotionStateTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        emotion_state.init_db(os.path.join(self._tmp.name, "emotion.db"))
        self.user = self.id().rsplit(".", 1)[-1]

    def tearDown(self):
        emotion_state.close_db()
        self._tmp.cleanup()

    def test_each_emotion_decays_by_its_half_life(self):
        start = 1_000_000.0
        for emotion in emotion_state.EMOTIONS:
            emotion_state.apply_event(self.user, emotion, 60, "test", start)
            later = start + emotion_state.HALF_LIFE_SECONDS[emotion]
            self.assertAlmostEqual(
                emotion_state.snapshot(self.user, later)[emotion], 30.0, delta=0.05,
            )

    def test_soothing_reduces_but_does_not_clear_negative_emotion(self):
        now = 2_000_000.0
        emotion_state.apply_event(self.user, "angry", 60, "dismissive", now)
        emotion_state.apply_event(self.user, "hurt", 40, "dismissive", now)
        result = emotion_state.soothe(self.user, now + 1)
        self.assertTrue(result["changed"])
        self.assertGreater(result["after"]["angry"], 0)
        self.assertLess(result["after"]["angry"], result["before"]["angry"])
        self.assertGreater(result["after"]["hurt"], 0)

    def test_repeated_event_reaches_long_term_milestone(self):
        now = 3_000_000.0
        events = [
            emotion_state.apply_event(self.user, "hurt", 5, "trust_hurt", now + index * 60)
            for index in range(3)
        ]
        self.assertEqual(events[-1]["repeat_count"], 3)
        self.assertTrue(events[-1]["milestone"])
        self.assertGreater(events[-1]["intensity"], 15)

    def test_fatigue_cooldown_prevents_message_spam_from_stacking(self):
        now = 4_000_000.0
        first = emotion_state.ensure_fatigue(self.user, "今天很累", now)
        second = emotion_state.ensure_fatigue(self.user, "今天很累", now + 30)
        self.assertFalse(first["ignored"])
        self.assertTrue(second["ignored"])

    def test_strong_negative_mood_constrains_turn_actions(self):
        plan = dialogue_policy.plan_turn(
            "宝宝",
            mood_state={"happy": 0, "angry": 45, "hurt": 20, "tired": 5},
        )
        self.assertEqual(plan.max_parts, 1)
        self.assertFalse(plan.allow_sticker)
        self.assertFalse(plan.allow_afterthought)
        self.assertFalse(plan.allow_dual_voice)
        self.assertEqual(plan.allowed_tools, ())


if __name__ == "__main__":
    unittest.main()
