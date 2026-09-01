# -*- coding: utf-8 -*-
"""通用 OpenAI 兼容客户端 — 可接任意 OpenAI 兼容服务（本地 Ollama / LM Studio / vLLM / one-api 等）。

本地 / 第三方服务大多提供 OpenAI 兼容 API（/v1/chat/completions），
只要填 base_url / api_key（本地常为空）/ model 即可接入。

差异点：
- 无"思考模式"参数（忽略 disable_thinking）
- 可能不支持 tools：BaseLLMClient.chat 在工具调用失败时会自动回退纯对话
- 本地模型响应慢，超时放宽到 120s
"""
import logging

from openai import AsyncOpenAI
from llm_base import BaseLLMClient

logger = logging.getLogger(__name__)


class OpenAICompatClient(BaseLLMClient):
    """OpenAI 兼容接口的通用客户端（含本地模型）。"""

    def __init__(self, base_url="", api_key="", model=""):
        self.base_url = (base_url or "").strip().rstrip("/")
        self.api_key = (api_key or "").strip()
        self.model = (model or "").strip()
        self._client = AsyncOpenAI(
            # 本地服务通常不需要 key；传占位符避免 SDK 报"未配置 api_key"
            api_key=self.api_key or "sk-local",
            base_url=self.base_url or "http://127.0.0.1:11434/v1",
            timeout=120.0,
            max_retries=1,
        )

    async def _create(self, model, messages, temperature, max_tokens,
                      force_disable_thinking=False, tools=None):
        kwargs = dict(model=model, messages=messages,
                      temperature=temperature, max_tokens=max_tokens)
        if tools:
            kwargs["tools"] = tools
        return await self._client.chat.completions.create(**kwargs)
