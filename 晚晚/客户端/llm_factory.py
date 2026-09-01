# -*- coding: utf-8 -*-
"""大模型后端工厂：按"模块分派"的提供商选择客户端（DeepSeek / 任意 OpenAI 兼容服务）。

提供商与模块分派配置在 `晚晚/配置/llm_providers.json`
（GUI「设置 → 连接与模型」里卡片式管理），这里只负责把它变成可用的客户端。

用法：
    from llm_factory import get_llm_client
    llm = get_llm_client("chat")    # 对话（默认）
    task = get_llm_client("task")   # 文字任务（成长/空间/记忆/提示词工程）
    vision = get_llm_client("vision")  # 看图（图片识别）

新增后端类型只需两步：
1. 写一个继承 llm_base.BaseLLMClient 的类，实现 _create()
2. 在 _build_client() 里加一个分支
"""
import logging

from llm_base import BaseLLMClient

logger = logging.getLogger(__name__)


def get_llm_client(category: str = "chat") -> BaseLLMClient:
    """按模块分派创建客户端。

    - type=deepseek：DeepSeek 官方（提供商字段留空 → 回退 .env 的 DEEPSEEK_*）
    - type=openai：任意 OpenAI 兼容服务（本地 Ollama / LM Studio / vLLM / one-api 等）
    """
    try:
        from llm_providers import resolve
        provider, model = resolve(category)
    except Exception as e:
        logger.warning("读取提供商分派失败，回退 DeepSeek: %s", e)
        provider, model = {"type": "deepseek", "base_url": "", "api_key": "", "model": ""}, ""
    ptype = str(provider.get("type") or "openai")
    if ptype == "deepseek":
        from deepseek_client import DeepSeekClient
        return DeepSeekClient(
            api_key=provider.get("api_key") or None,
            base_url=provider.get("base_url") or None,
            model=model or None,
        )
    from openai_compat import OpenAICompatClient
    return OpenAICompatClient(
        base_url=provider.get("base_url") or "",
        api_key=provider.get("api_key") or "",
        model=model or "",
    )
