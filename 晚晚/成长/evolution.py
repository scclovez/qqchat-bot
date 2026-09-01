# -*- coding: utf-8 -*-
"""每日性格演化：凌晨 4:30 结合当天聊天 / 日记 / 情绪标签，让 AI 判断性格演化方向。

比"情绪标签固定加减分"更聪明：让 DeepSeek 阅读当天 90% 的对话原文 + 日记，
自主分析今天相处经历对性格的影响，输出：
  - 三个特征值的增减（-5~+5，代码再 clamp ±10，防失控）
  - personality_shift：一句性格变化描述（写入演化记录，之后每句回复都注入）
  - language_style_tweak：可选说话方式微调（同样写入演化记录）

防漂移设计：
  - 提示词要求"大部分日子小幅波动（0~±2），重大事件才到 ±5"
  - 代码对 delta 二次 clamp ±10
  - 当天已演化过则跳过（防 Bot 重启重复执行）
"""
import json
import logging
from datetime import datetime

import evolution_db as db
import personality_state
import diary as growth_diary

logger = logging.getLogger(__name__)

# 特征值单次变化上限（代码兜底，即使模型不听话也不失控）
DELTA_LIMIT = 10

EVOLUTION_PROMPT = """你是「bot」的性格演化引擎。请根据她今天和男友的相处（聊天记录）、她的日记、
情绪标签和当前性格特征，判断她今天的经历应该让性格往哪个方向演化。
要求判断有依据：变化必须能在今天的聊天内容里找到对应的事，不要凭空编。

当前性格特征：
- 亲密度（0~51=礼貌试探期，51~151=热情升温期，151+=深度绑定期）：{affection}
- 依赖度：{dependency}
- 醋意倾向：{jealousy}
当前阶段：{stage}

今天的情绪标签：{mood_tags}

她今天的日记：
{diary}

今天的一部分聊天记录（截取当日约90%，{chat_count}条）：
{chat_log}

请输出严格 JSON（不要任何解释文字，不要 ```json 包裹），格式如下：
{{
  "affection_delta": 整数,
  "dependency_delta": 整数,
  "jealousy_delta": 整数,
  "personality_shift": "一句话总结她今天性格/心态上的变化（20字以内，第一人称，如"今天开始我更依赖你了"）",
  "language_style_tweak": "可选：一句她想在说话方式上做出的改变（30字以内，没有则空字符串）"
}}

要求：
- 变化必须克制：大部分日子只是小幅波动（0~±2），只有发生重大事件
  （激烈争吵、甜蜜表白、长时间深度相处、吃醋风波等）才到 ±5
- 三个 delta 都是整数
- personality_shift 必须对应今天聊天里的具体情节
- 没有明显性格变化时 delta 全为 0、personality_shift 写"今天没有明显变化"
"""


def _parse_evolution_response(raw: str) -> dict:
    """解析演化 JSON；容错 ```json 包裹、损坏 JSON。失败返回空 dict。"""
    if not raw:
        return {}
    text = raw.strip()
    # 去掉 ```json ... ``` 包裹
    if text.startswith("```"):
        text = text.split("\n", 1)[-1]
        if text.endswith("```"):
            text = text[:-3]
        text = text.strip()
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        logger.error("性格演化 JSON 解析失败: %s\n原始输出: %s", e, raw[:400])
        return {}
    if not isinstance(data, dict):
        logger.error("性格演化返回的不是 dict: %r", type(data))
        return {}
    # 字段归一化 + clamp
    out = {}
    for key in ("affection_delta", "dependency_delta", "jealousy_delta"):
        try:
            out[key] = max(-DELTA_LIMIT, min(DELTA_LIMIT, int(data.get(key, 0) or 0)))
        except (TypeError, ValueError):
            out[key] = 0
    out["personality_shift"] = str(data.get("personality_shift", "") or "").strip()
    out["language_style_tweak"] = str(data.get("language_style_tweak", "") or "").strip()
    return out


async def run_daily_evolution(llm_call):
    """执行一次每日性格演化；llm_call 为 async (messages, **kw) -> str（传 bot._deepseek.chat）。

    流程：取当天 90% 聊天 + 日记 + 情绪标签 → LLM 分析出 JSON →
    应用特征值增减 → 写入演化记录（供后续回复注入）。返回演化描述；失败/跳过返回空串。
    """
    chats = growth_diary._today_chat()
    if not chats:
        logger.info("今日无聊天记录，跳过性格演化")
        return ""
    # 目标日 = 刚过去的完整自然日（与 diary 一致：凌晨执行总结"昨天"）
    day = growth_diary._target_day()
    if db.has_evolution_today(day):
        logger.info("目标日已完成性格演化，跳过（%s）", day)
        return ""

    # 目标日日记（可能还没有 → 空串）
    diary_content = ""
    try:
        conn = db.get_conn()
        with db._lock:
            row = conn.execute(
                "SELECT content FROM diary WHERE date = ?", (day,),
            ).fetchone()
        if row:
            diary_content = row["content"] or ""
    except Exception as e:
        logger.warning("读取目标日日记失败（跳过日记部分）: %s", e)

    tags = growth_diary.get_today_mood_tags()
    chat_text = "\n".join(f"{'我' if r == 'user' else '她'}: {c}" for r, c in chats)

    prompt = (EVOLUTION_PROMPT
              .replace("{affection}", str(personality_state.get_affection()))
              .replace("{dependency}", str(personality_state.get_dependency()))
              .replace("{jealousy}", str(personality_state.get_jealousy()))
              .replace("{stage}", personality_state.stage_name())
              .replace("{mood_tags}", json.dumps(tags, ensure_ascii=False))
              .replace("{diary}", diary_content or "（今天还没有日记）")
              .replace("{chat_count}", str(len(chats)))
              .replace("{chat_log}", chat_text))

    try:
        raw = await llm_call(
            [{"role": "user", "content": prompt}],
            temperature=0.5, max_tokens=500, disable_thinking=True,
        )
    except Exception as e:
        logger.error("性格演化 LLM 调用失败: %s", e)
        return ""

    data = _parse_evolution_response(raw)
    if not data:
        logger.warning("性格演化结果为空，跳过本轮")
        return ""

    # 应用特征值变化（防失控 clamp 已在解析时做）
    personality_state.add_affection(data["affection_delta"])
    personality_state.add_dependency(data["dependency_delta"])
    personality_state.add_jealousy(data["jealousy_delta"])

    # 写入演化记录（回复时注入，让变化"长进"对话里）
    shift = data["personality_shift"] or ""
    if not shift or shift == "今天没有明显变化":
        shift = ""
    tweak = data["language_style_tweak"] or ""
    if shift:
        db.add_evolution_note(day, shift)
    if tweak:
        db.add_evolution_note(day, f"说话方式：{tweak}")

    logger.info("性格演化完成 %s: 亲密%+d 依赖%+d 醋意%+d | %s",
                day, data["affection_delta"], data["dependency_delta"],
                data["jealousy_delta"], shift or "(无描述)")
    return shift
