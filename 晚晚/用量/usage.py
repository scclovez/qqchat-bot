# -*- coding: utf-8 -*-
"""Token 用量统计 — 累计记录 API 调用的 token 消耗（线程安全，持久化到文件）。

数据存到项目目录 usage_stats.json，重启 Bot 不会清零。
GUI「Token 用量」标签页与 CLI 日志共用这一个计数器。
"""
import json
import logging
import os
import threading

logger = logging.getLogger(__name__)

from 路径 import PROJECT_ROOT, data_path
STATS_FILE = data_path("晚晚", "用量", "usage_stats.json")

_KEYS = ("calls", "prompt_tokens", "completion_tokens", "total_tokens")
_TOTAL_KEYS = ("prompt_tokens", "completion_tokens", "total_tokens")


class UsageTracker:
    """线程安全的累计计数器，自动持久化。"""

    def __init__(self, persist_path: str | None = None):
        self._lock = threading.Lock()
        self._path = persist_path or STATS_FILE
        self._calls = 0
        self._totals = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
        self._per_model = {}  # model -> 明细 dict
        self._local_calls = 0  # 本地模型（ComfyUI）调用次数
        self._dirty = False  # 有未落盘的变更（由 snapshot 周期触发写盘）
        self._load()

    # ---------- 持久化 ----------

    def _load(self):
        """启动时从文件恢复统计；文件缺失/损坏则从零开始。"""
        try:
            with open(self._path, "r", encoding="utf-8") as f:
                data = json.load(f)
            self._calls = int(data.get("calls", 0) or 0)
            self._totals = dict(data.get("totals") or {})
            for k in _TOTAL_KEYS:
                self._totals.setdefault(k, 0)
            self._local_calls = int(data.get("local_calls", 0) or 0)
            self._per_model = {}
            for k, v in (data.get("per_model") or {}).items():
                m = dict(v or {})
                for key in _KEYS:
                    m.setdefault(key, 0)
                self._per_model[str(k)] = m
            logger.info("已从 %s 恢复 Token 用量统计: 累计 %d 次调用", self._path, self._calls)
        except (FileNotFoundError, json.JSONDecodeError, TypeError, ValueError) as e:
            logger.debug("Token 用量统计文件不存在或损坏，从零开始: %s", e)

    def _save(self):
        """把当前统计写入文件（调用方需持有锁）。"""
        try:
            with open(self._path, "w", encoding="utf-8") as f:
                json.dump({
                    "calls": self._calls,
                    "totals": self._totals,
                    "per_model": self._per_model,
                    "local_calls": self._local_calls,
                }, f, ensure_ascii=False, indent=2)
        except OSError as e:
            logger.warning("Token 用量统计保存失败: %s", e)

    # ---------- 统计 ----------

    def add(self, model, usage):
        """记录一次 API 调用的 usage（OpenAI 返回的 CompletionUsage 或 dict）。

        即使 usage 为空也计调用次数（如图片生成可能不返回 token，但调用确实发生）。
        """
        prompt = completion = total = 0
        if usage:
            try:
                prompt = int(getattr(usage, "prompt_tokens", 0) or 0)
                completion = int(getattr(usage, "completion_tokens", 0) or 0)
                total = int(getattr(usage, "total_tokens", 0) or 0)
            except (TypeError, ValueError):
                prompt = completion = total = 0
        with self._lock:
            self._calls += 1
            self._totals["prompt_tokens"] += prompt
            self._totals["completion_tokens"] += completion
            self._totals["total_tokens"] += total
            m = self._per_model.setdefault(model or "unknown", {
                "calls": 0, "prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0,
            })
            m["calls"] += 1
            m["prompt_tokens"] += prompt
            m["completion_tokens"] += completion
            m["total_tokens"] += total
            # 延迟落盘：不每次 API 调用都写文件，由 snapshot() 周期触发
            self._dirty = True

    def add_local(self):
        """记录一次本地模型（ComfyUI）调用。"""
        with self._lock:
            self._local_calls += 1
            self._dirty = True

    def snapshot(self):
        """返回当前累计快照（dict，可安全展示）；有变更时顺便落盘。"""
        with self._lock:
            if self._dirty:
                self._save()
                self._dirty = False
            return {
                "calls": self._calls,
                "totals": dict(self._totals),
                "per_model": {k: dict(v) for k, v in self._per_model.items()},
                "local_calls": self._local_calls,
            }

    def reset(self):
        """清零所有统计并写入文件。"""
        with self._lock:
            self._calls = 0
            self._totals = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
            self._per_model = {}
            self._local_calls = 0
            self._save()


# 全局单例：DeepSeekClient / image_gen 每次调用后写入，GUI 读取展示
usage_tracker = UsageTracker()
