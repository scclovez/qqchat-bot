# -*- coding: utf-8 -*-
"""对话记忆管理 — 按 QQ 用户维护上下文，支持超限压缩。

对话上下文（最近消息 + 压缩摘要）会持久化到 SQLite（conversation_state 表），
Bot 重启后自动恢复，避免重启丢失"一部分记忆"。
"""
import logging
import re
from collections import defaultdict
from config import runtime
from personality import build_system_prompt
import memory as longterm_memory

logger = logging.getLogger(__name__)

# 压缩触发阈值：本次运行新增多少条对话后才压缩一次。
# 重启恢复的旧上下文属于"已存档"内容，不立即压缩（否则模型第一轮只看到 6 条+摘要，
# 接不上上次对话）；等新增满该阈值，再把"恢复的原文 + 新增"一起融进摘要。
NEW_MSG_COMPRESS_TRIGGER = 50


def _clean_style_for_context(content: str) -> str:
    """把 bot 自己的历史回复"压平"，只用于送进模型的上下文副本（不落盘、不改原文）。

    背景：模型会模仿自己最近说过的话——一旦某几条回复染上"每句都拿……断句"
    的省略号腔，后续回复就会照着学，越说越碎（实测：一条 7 个"……"、41% 以
    "……"开头）。这里把历史里 bot 自己的消息做轻度净化，打断自模仿循环：
    - 去掉"……"式断句（保留真正的句读停顿，避免一句变 N 段）；
    - 行内多段用顿号/逗号或直接连接，不让模型看到"模板腔"作为榜样；
    - 只影响上下文展示，聊天记录原文与摘要原文不变。

    规则保守：只处理"省略号腔明显"的消息（含 ≥2 个省略号或连续 2 行以上省略号），
    正常口语（偶尔一个"……"）原样保留。
    """
    if not content:
        return content
    if content.count("……") + content.count("…") < 2:
        return content
    # 行内多段：把段落间的省略号腔压掉，只留段落分隔
    lines = re.split(r"\n+", content)
    cleaned_lines = []
    for ln in lines:
        ln = ln.strip()
        if not ln:
            continue
        # 去掉行首多余的省略号（"……嗯"→"嗯"）
        ln = re.sub(r"^[…\.]{1,8}\s*", "", ln)
        # 行内的省略号：若整行几乎全由省略号断开（碎句腔），去省略号改直连/顿号
        dots = ln.count("……") + ln.count("…")
        if dots >= 2 and len(ln) <= 60:
            # 例如"……你呀……是我的小狗狗……汪汪的那种……" → "你呀，是我的小狗狗，汪汪的那种"
            ln = re.sub(r"[…\.]{1,8}", "，", ln)
            ln = ln.strip("，、 ")
            ln = re.sub(r"[，、]{2,}", "，", ln)
        cleaned_lines.append(ln)
    return "\n".join(cleaned_lines)


def _clean_history_copy(history: list) -> list:
    """返回清洗后的历史副本（assistant 消息做省略号腔压平；user 原样）。"""
    out = []
    for m in history or []:
        if not isinstance(m, dict):
            out.append(m)
            continue
        if m.get("role") == "assistant":
            c = m.get("content") or ""
            cc = _clean_style_for_context(c)
            if cc != c:
                out.append({"role": m["role"], "content": cc})
                continue
        out.append(m)
    return out


class ConversationMemory:
    """管理多个用户的对话历史，每个用户独立维护上下文。"""

    def __init__(self):
        self._histories = defaultdict(list)
        self._summaries = {}
        # 每个用户"上次压缩/恢复后的基准长度"：compress 只对超出基准的新增对话计数，
        # 避免重启恢复的旧上下文被立即压缩/裁剪
        self._resumed_len = {}
        self._restore_from_disk()

    def _restore_from_disk(self):
        """启动时恢复对话上下文：优先读持久化状态，其次用最近聊天记录兜底。"""
        try:
            states = longterm_memory.load_all_conversation_states()
        except Exception as e:
            logger.warning("读取对话上下文失败: %s", e)
            states = {}
        for uid, st in states.items():
            history = st.get("history") or []
            summary = st.get("summary") or ""
            if history:
                self._histories[uid] = list(history)
            if summary:
                self._summaries[uid] = summary
        # 兜底：老版本从未持久化过状态，用 SQLite 里最近的聊天记录补一段上下文，
        # 让升级前的用户也能找回最近聊过的话题
        try:
            limit = max(2, int(runtime.MAX_HISTORY_LENGTH or 20) * 2)
            for uid in longterm_memory.get_all_user_ids():
                if uid in self._histories:
                    continue
                recent = longterm_memory.get_recent_history(uid, limit)
                if recent:
                    self._histories[uid] = recent
        except Exception as e:
            logger.warning("恢复最近聊天记录失败: %s", e)
        # 恢复的内容是"已存档"状态：记录基准长度，compress/trim 不把它们当新增处理
        for uid in self._histories:
            self._resumed_len[uid] = len(self._histories[uid])

    def _limit(self, user_id):
        """该用户上下文的实际保留上限：MAX_HISTORY_LENGTH*2 与「基准+新增阈值」取大。

        重启恢复的旧上下文（_resumed_len 基准）是已存档内容，
        在 compress 触发前允许它继续留在内存/落盘，不被 trim/persist 提前裁掉。
        """
        max_len = max(2, int(runtime.MAX_HISTORY_LENGTH or 20) * 2)
        base = int(self._resumed_len.get(user_id, 0))
        return max(max_len, base + NEW_MSG_COMPRESS_TRIGGER)

    def _persist(self, user_id):
        """把该用户的上下文写回磁盘（尽力而为，失败不影响回复）。

        落盘前强制截断到 _limit（防止 conversation_state 无限膨胀），
        但不裁掉"恢复的旧上下文 + 新增阈值"范围内的内容。
        """
        try:
            history = self._histories.get(user_id, [])
            limit = self._limit(user_id)
            if len(history) > limit:
                history = history[-limit:]
            longterm_memory.save_conversation_state(
                user_id, history, self._summaries.get(user_id, ""),
            )
        except Exception as e:
            logger.warning("持久化对话上下文失败 [%s]: %s", user_id, e)

    def get_messages(self, user_id):
        """返回完整的消息列表。摘要合并到系统提示词中，只有一条 system 消息。

        注意：历史中 bot 自己的消息会先做省略号腔"压平"（_clean_history_copy），
        防止模型模仿自己最近说过的碎句模板（自模仿放大）；原文不落盘、不改写。
        """
        prompt = build_system_prompt()
        summary = self._summaries.get(user_id, "")
        if summary:
            prompt += f"\n\n【之前的对话摘要】{summary}"
        msgs = [{"role": "system", "content": prompt}]
        msgs.extend(_clean_history_copy(self._histories.get(user_id, [])))
        return msgs

    def add_message(self, user_id, role, content):
        self._histories[user_id].append({"role": role, "content": content})
        self._persist(user_id)

    def trim(self, user_id):
        """裁剪超限历史（compress 失败的兜底，防无限增长）。

        上限用 _limit（基准+新增阈值）：不裁"恢复的旧上下文 + 新增阈值"范围内
        尚未进摘要的内容；只有超过该上限才裁最旧的。
        """
        history = self._histories.get(user_id, [])
        limit = self._limit(user_id)
        if len(history) <= limit:
            return 0
        removed = len(history) - limit
        self._histories[user_id] = history[-limit:]
        self._persist(user_id)
        logger.info("用户 %s 对话已裁剪，移除 %d 条消息", user_id, removed)
        return removed

    async def compress(self, user_id, client):
        history = self._histories.get(user_id, [])
        keep_recent = 6
        if len(history) <= keep_recent:
            return
        # 只在本次运行新增了足够多的新对话后才压缩：
        # 基准 = 上次压缩/恢复后的长度；重启恢复的旧上下文不参与计数，
        # 所以刚重启后模型能看到完整的恢复原文（接上上次对话），
        # 新增满 NEW_MSG_COMPRESS_TRIGGER 条后再把"恢复原文 + 新增"一起融进摘要。
        base = int(self._resumed_len.get(user_id, 0))
        if len(history) - base < NEW_MSG_COMPRESS_TRIGGER:
            return

        old = history[:-keep_recent]
        existing = self._summaries.get(user_id, "")

        # 把旧摘要也放进压缩输入，让 AI 融合成一份更完整的摘要（旧摘要累积，不丢主线）
        parts = []
        if existing:
            parts.append(f"【之前的摘要】{existing}")
        parts.append("\n".join(
            f"{'男友' if m['role'] == 'user' else runtime.GIRLFRIEND_NAME}: {m['content']}"
            for m in old
        ))
        history_text = "\n\n".join(parts)

        new_summary = await client.summarize(history_text)
        if not new_summary:
            # 压缩失败：保留现有历史，绝不静默裁剪（否则旧对话既无摘要又直接丢失）
            logger.warning("用户 %s 对话压缩失败，保留现有历史不裁剪", user_id)
            return
        self._summaries[user_id] = new_summary  # 融合替换（旧摘要已并入新摘要）
        # await 期间可能有未持锁的写入者（自拍/画图/记录）追加消息；
        # 重新读取当前列表再裁剪，避免旧快照整体替换把并发消息永久抹掉。
        current = self._histories.get(user_id, [])
        self._histories[user_id] = current[-keep_recent:]
        self._resumed_len[user_id] = keep_recent  # 压缩后从保留的几条重新计数
        self._persist(user_id)
        logger.info("用户 %s 对话已压缩，保留 %d 条消息", user_id, keep_recent)

    def save_all(self):
        """把当前所有用户的对话上下文一次性写入磁盘（Bot 停止/重启前调用）。

        消息本身已实时落盘（add_message/trim/compress），这里做全量双保险：
        遍历所有用户把内存中最新的历史 + 摘要强制写一遍，确保重启后一条不丢。
        """
        for user_id in list(self._histories.keys()):
            self._persist(user_id)
        if self._histories:
            logger.info("已全量保存 %d 个用户的对话上下文", len(self._histories))

    def clear(self, user_id):
        self._histories.pop(user_id, None)
        self._summaries.pop(user_id, None)
        self._resumed_len.pop(user_id, None)
        try:
            longterm_memory.delete_conversation_state(user_id)
        except Exception as e:
            logger.warning("删除对话上下文失败 [%s]: %s", user_id, e)

    def get_stats(self):
        return {"active_users": len(self._histories), "users_with_summary": len(self._summaries)}

    def get_active_users(self):
        """返回本会话内有对话记录的用户 ID 列表（主动消息只发给这些人）。"""
        return list(self._histories.keys())
