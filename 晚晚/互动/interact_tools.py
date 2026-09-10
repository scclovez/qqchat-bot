# -*- coding: utf-8 -*-
"""自主互动工具：让模型通过 function calling 自主决定"戳一戳 / 表情回应 / 名片赞 / 在线状态 / 个性签名"。

工具执行走 SnowLuma OneBot action：
  poke_user         → send_poke（拍一拍）
  react_message     → set_msg_emoji_like（给当前消息回表情）
  send_like         → send_like（QQ 名片赞）
  set_online_status → set_diy_online_status（自定义在线状态）
  set_longnick      → set_self_longnick（个性签名）

所有工具带频率限制（interact_usage 表持久化，防刷屏/风控）：
  戳一戳 / 表情回应：每 30 分钟最多 1 次
  名片赞 / 在线状态：每天最多 3 次
  个性签名：每天最多 1 次
"""
import json
import logging
import os
import sqlite3
import time
from datetime import datetime

logger = logging.getLogger(__name__)

from 路径 import PROJECT_ROOT, data_path
from sqlite_runtime import BOT_DB_LOCK, connect_bot_db
DB_PATH = data_path("晚晚", "数据", "bot_memory.db")

_lock = BOT_DB_LOCK
_conn = None


def _get_conn() -> sqlite3.Connection:
    global _conn
    with _lock:
        if _conn is None:
            _conn = connect_bot_db(DB_PATH)
            _conn.execute(
                "CREATE TABLE IF NOT EXISTS interact_usage ("
                " tool TEXT NOT NULL, ref TEXT NOT NULL,"
                " created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,"
                " PRIMARY KEY (tool, ref))"
            )
            _conn.commit()
    return _conn


def _mark(tool: str, ref: str):
    conn = _get_conn()
    with _lock:
        conn.execute(
            "INSERT OR IGNORE INTO interact_usage (tool, ref) VALUES (?, ?)",
            (tool, str(ref)),
        )
        conn.commit()


def _last_ts(tool: str) -> float:
    """该工具最近一次操作时间戳（unix 秒）；无记录返回 0。"""
    conn = _get_conn()
    with _lock:
        row = conn.execute(
            "SELECT ref FROM interact_usage WHERE tool = ? ORDER BY ref DESC LIMIT 1",
            (tool,),
        ).fetchone()
    if not row:
        return 0
    try:
        return float(row["ref"])
    except (TypeError, ValueError):
        return 0


def _today_count(tool: str) -> int:
    """该工具今天已执行次数。"""
    prefix = datetime.now().strftime("%Y%m%d")
    conn = _get_conn()
    with _lock:
        row = conn.execute(
            "SELECT COUNT(*) AS cnt FROM interact_usage WHERE tool = ? AND ref LIKE ?",
            (tool, prefix + "%"),
        ).fetchone()
    return row["cnt"] if row else 0


def _can_do(tool: str, min_gap_minutes: int = 0, daily_limit: int = 0) -> bool:
    """频率检查：距上次至少 min_gap_minutes 分钟 + 当天不超过 daily_limit 次。"""
    if min_gap_minutes > 0:
        last = _last_ts(tool)
        if last and (time.time() - last) < min_gap_minutes * 60:
            left = int(min_gap_minutes * 60 - (time.time() - last)) // 60
            logger.info("互动工具 %s 频率限制：约 %d 分钟后可用", tool, left)
            return False
    if daily_limit > 0 and _today_count(tool) >= daily_limit:
        logger.info("互动工具 %s 频率限制：今日已达上限", tool)
        return False
    return True


# =============================================================================
# 表情回应 emoji 映射（QQ 表情回应 emoji_id，取值待实测微调）
# =============================================================================
REACT_EMOJI_IDS = {
    "爱心": "66",
    "点赞": "76",
    "亲亲": "109",
    "笑哭": "9",
    "委屈": "106",
    "比心": "116",
    "惊讶": "2",
}

# 自定义在线状态图标（face_id，QQ 在线状态图标编号，待实测微调）
ONLINE_STATUS_FACE_ID = 1028

# =============================================================================
# 工具 schema（OpenAI function calling 格式）
# =============================================================================
INTERACT_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "poke_user",
            "description": "戳一戳/拍一拍对方（QQ 拍一拍）。想跟对方互动、撒娇、刷存在感、"
                           "对方半天没理你时，都可以调用。每次最多一次。",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "react_message",
            "description": "给对方刚发来的这条消息回一个表情（消息表情回应）。"
                           "对方说了让你开心/害羞/无语的话时，用一个表情回应比打字更自然。",
            "parameters": {
                "type": "object",
                "properties": {
                    "emoji": {"type": "string", "enum": ["爱心", "点赞", "亲亲", "笑哭", "委屈", "比心", "惊讶"],
                              "description": "要回的表情"},
                },
                "required": ["emoji"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "send_like",
            "description": "给对方 QQ 名片点赞（QQ 赞）。想表达喜欢/支持时偶尔用。",
            "parameters": {
                "type": "object",
                "properties": {"times": {"type": "integer", "minimum": 1, "maximum": 10,
                                          "description": "点赞次数，默认 1"}},
                "required": ["times"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "set_online_status",
            "description": "设置自己的 QQ 自定义在线状态（显示在资料卡/聊天窗口的状态文案，"
                           "如\"学习中\"\"听歌中\"\"被男朋友烦着\"）。想体现此刻状态时调用。",
            "parameters": {
                "type": "object",
                "properties": {"wording": {"type": "string",
                                            "description": "状态文案，简短（如 学习中/听歌中），10 字以内"}},
                "required": ["wording"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "set_longnick",
            "description": "设置自己的 QQ 个性签名。想换个签名表达心情时调用（如\"今天也是想老公的一天\"）。",
            "parameters": {
                "type": "object",
                "properties": {"signature": {"type": "string", "description": "签名内容，30 字以内"}},
                "required": ["signature"],
            },
        },
    },
]


class InteractTools:
    """工具执行器：持有 api_call 与当前对话上下文（对方 QQ、当前消息 id）。"""

    def __init__(self, api_call, user_id, message_id=0):
        self._api_call = api_call
        self._user_id = str(user_id)
        self._message_id = message_id
        self.action_taken = False

    async def execute(self, name: str, args: dict) -> dict:
        if self.action_taken:
            return {"ok": False, "error": "本轮已经执行过一个互动动作"}
        handler = getattr(self, "_do_" + name, None)
        if not handler:
            return {"ok": False, "error": f"未知工具 {name}"}
        result = await handler(args or {})
        if isinstance(result, dict) and result.get("ok"):
            self.action_taken = True
        return result

    async def _do_poke_user(self, args) -> dict:
        if not _can_do("poke_user", min_gap_minutes=30):
            return {"ok": False, "error": "频率限制"}
        resp = await self._api_call("send_poke", {"user_id": self._user_id})
        if resp is None:
            return {"ok": False, "error": "发送失败"}
        _mark("poke_user", str(int(time.time())))
        logger.info("互动工具 poke_user [%s]", self._user_id)
        return {"ok": True}

    async def _do_react_message(self, args) -> dict:
        if not _can_do("react_message", min_gap_minutes=30):
            return {"ok": False, "error": "频率限制"}
        emoji = str(args.get("emoji") or "")
        emoji_id = REACT_EMOJI_IDS.get(emoji)
        if not emoji_id or not self._message_id:
            return {"ok": False, "error": "无法回应表情"}
        resp = await self._api_call("set_msg_emoji_like",
                                    {"message_id": self._message_id, "emoji_id": emoji_id})
        if resp is None:
            return {"ok": False, "error": "发送失败"}
        _mark("react_message", str(int(time.time())))
        logger.info("互动工具 react_message [%s] emoji=%s", self._user_id, emoji)
        return {"ok": True, "emoji": emoji}

    async def _do_send_like(self, args) -> dict:
        if not _can_do("send_like", daily_limit=3):
            return {"ok": False, "error": "频率限制"}
        times = max(1, min(10, int(args.get("times") or 1)))
        resp = await self._api_call("send_like", {"user_id": self._user_id, "times": times})
        if resp is None:
            return {"ok": False, "error": "发送失败"}
        _mark("send_like", datetime.now().strftime("%Y%m%d%H%M%S"))
        logger.info("互动工具 send_like [%s] x%d", self._user_id, times)
        return {"ok": True, "times": times}

    async def _do_set_online_status(self, args) -> dict:
        if not _can_do("set_online_status", daily_limit=3):
            return {"ok": False, "error": "频率限制"}
        wording = str(args.get("wording") or "").strip()
        if not wording:
            return {"ok": False, "error": "缺少文案"}
        resp = await self._api_call("set_diy_online_status",
                                    {"face_id": ONLINE_STATUS_FACE_ID, "wording": wording})
        if resp is None:
            return {"ok": False, "error": "发送失败"}
        _mark("set_online_status", datetime.now().strftime("%Y%m%d%H%M%S"))
        logger.info("互动工具 set_online_status: %s", wording)
        return {"ok": True, "wording": wording}

    async def _do_set_longnick(self, args) -> dict:
        if not _can_do("set_longnick", daily_limit=1):
            return {"ok": False, "error": "频率限制"}
        signature = str(args.get("signature") or "").strip()
        if not signature:
            return {"ok": False, "error": "缺少签名"}
        resp = await self._api_call("set_self_longnick", {"longNick": signature})
        if resp is None:
            return {"ok": False, "error": "发送失败"}
        _mark("set_longnick", datetime.now().strftime("%Y%m%d%H%M%S"))
        logger.info("互动工具 set_longnick: %s", signature)
        return {"ok": True, "signature": signature}
