# -*- coding: utf-8 -*-
"""DeepSeek API 客户端 — 兼容 OpenAI SDK（继承统一接口 llm_base.BaseLLMClient）。"""
import logging

from openai import APIStatusError, AsyncOpenAI
from config import config, runtime
from llm_base import BaseLLMClient

logger = logging.getLogger(__name__)


class DeepSeekClient(BaseLLMClient):
    """封装 DeepSeek API（OpenAI 兼容接口）的异步客户端。

    仅保留 DeepSeek 差异点（思考模式参数、视觉模型特殊处理），
    工具调用循环 / 摘要 / 模型列表等通用逻辑在 BaseLLMClient。
    """

    def __init__(self, api_key=None, base_url=None, model=None):
        """api_key/base_url/model 可覆盖（多提供商场景）；留空/为空回退 .env 配置。"""
        self.api_key = (api_key or "").strip() or config.DEEPSEEK_API_KEY
        self.base_url = (base_url or "").strip() or config.DEEPSEEK_BASE_URL
        self.model = (model or "").strip() or config.DEEPSEEK_MODEL
        self.vision_model = config.DEEPSEEK_VISION_MODEL
        self._client = AsyncOpenAI(
            api_key=self.api_key,
            base_url=self.base_url,
            # SDK 默认 600s 总超时：网络半挂时一次 chat 可占住用户锁 10 分钟；
            # 显式收紧到 60s 并对瞬时错误自动重试 2 次
            timeout=60.0,
            max_retries=2,
        )

    def _thinking_body(self):
        """思考模式参数：THINKING_MODE=0 时关闭（避免思考吃光 token 导致空回复）。"""
        if not getattr(runtime, "THINKING_MODE", 1):
            return {"thinking": {"type": "disabled"}}
        return None

    async def _create(self, model, messages, temperature, max_tokens,
                      force_disable_thinking=False, tools=None):
        """调用 chat.completions.create；思考参数被 API 拒绝时自动回退重试一次。"""
        extra = self._thinking_body()
        # 视觉模型（看图/外貌总结/图片评论）是纯描述型任务：思考只会吃掉
        # max_tokens 配额，导致内容为空（finish_reason=length，实测视觉模型
        # 思考可消耗上千 token，300 限额下全部空回复），一律显式禁用 thinking。
        if force_disable_thinking or model == self.vision_model:
            extra = {"thinking": {"type": "disabled"}}
        kwargs = dict(model=model, messages=messages,
                      temperature=temperature, max_tokens=max_tokens)
        if tools:
            kwargs["tools"] = tools
        try:
            if extra:
                return await self._client.chat.completions.create(**kwargs, extra_body=extra)
            return await self._client.chat.completions.create(**kwargs)
        except APIStatusError as e:
            # 只有 API 明确拒绝请求（4xx：参数/鉴权/限流等）才可能是 thinking 参数问题，
            # 去掉 thinking 回退重试一次；网络连接/超时等错误直接抛出（SDK 已自动重试，
            # 不再误报"参数被拒绝"、不做无意义的回退请求）
            if extra and 400 <= (e.status_code or 0) < 500:
                logger.warning("API 拒绝（HTTP %s），去掉思考参数回退重试", e.status_code)
                return await self._client.chat.completions.create(**kwargs)
            raise
