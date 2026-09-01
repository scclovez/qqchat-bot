# -*- coding: utf-8 -*-
"""统一大模型客户端接口 — 支持 DeepSeek / 任意 OpenAI 兼容服务（含本地模型）。

所有上层（qq_bot / 成长 / 空间 / 记忆 / 界面…）只依赖这个接口：
    chat(messages, temperature, max_tokens, model, disable_thinking, tools, execute_tool)
    summarize(history_text)
    list_models(api_key, base_url)
    aclose()

具体后端（DeepSeek / Ollama / LM Studio / vLLM / one-api / 任意 OpenAI 兼容服务）
由子类实现差异点 _create()，工具调用循环等通用逻辑在这里统一处理。
"""
import json
import logging

from config import runtime
from usage import usage_tracker

logger = logging.getLogger(__name__)

# 工具调用循环最大轮数（模型可自主决定调用工具，结果回传后继续）
MAX_TOOL_ROUNDS = 4


class BaseLLMClient:
    """大模型客户端统一接口基类。子类需实现 _create() 并设置 self.model。"""

    #: 默认模型名（子类在 __init__ 里设置）
    model = ""
    #: OpenAI 兼容客户端（子类在 __init__ 里创建 AsyncOpenAI 实例）
    _client = None
    #: 兼容 OpenAI 接口的统一错误提示（本地服务挂掉/超时等）
    error_reply = "抱歉呀，我这边网络有点不太好，等会儿再找我聊嘛~ (._.)"

    async def aclose(self):
        """显式关闭底层 httpx 连接池（必须在同一 event loop 内调用）。"""
        try:
            if self._client is not None:
                await self._client.close()
        except Exception:
            pass

    async def _create(self, model, messages, temperature, max_tokens,
                      force_disable_thinking=False, tools=None):
        """后端差异点：调用 chat.completions.create。子类必须实现。"""
        raise NotImplementedError

    async def chat(self, messages, temperature=None, max_tokens=None, model=None,
                   disable_thinking=False, tools=None, execute_tool=None):
        """调用后端 chat；支持 function calling（tools + execute_tool）。

        - tools: OpenAI 兼容的 function 定义列表（模型可自主决定调用）
        - execute_tool: async (name, args) -> dict，工具执行回调；结果回传给模型
        - 工具调用循环最多 4 轮，之后取最后一次回复
        - 后端不支持 tools（本地模型常见）时自动去掉 tools 重试一次纯对话
        """
        temp = float(temperature) if temperature is not None else float(runtime.TEMPERATURE or 0.9)
        tokens = int(max_tokens) if max_tokens is not None else int(runtime.MAX_TOKENS or 256)
        tokens = max(1, min(tokens, 8192))
        mdl = model or self.model
        try:
            for _round in range(MAX_TOOL_ROUNDS):
                resp = await self._create(mdl, messages, temp, tokens,
                                          force_disable_thinking=disable_thinking,
                                          tools=tools)
                msg = resp.choices[0].message
                usage = getattr(resp, "usage", None)
                if usage is not None:
                    usage_tracker.add(mdl, usage)
                    logger.info(
                        "Token 用量: model=%s prompt=%s completion=%s total=%s",
                        mdl, usage.prompt_tokens, usage.completion_tokens, usage.total_tokens,
                    )
                tool_calls = getattr(msg, "tool_calls", None) or []
                if not tool_calls:
                    content = msg.content
                    return content.strip() if content else ""
                if not execute_tool:
                    break  # 有工具调用但无执行器：不循环（避免死循环）
                # 记录 assistant 的工具调用，供 API 关联 tool 结果
                messages.append({
                    "role": "assistant",
                    "content": msg.content or "",
                    "tool_calls": [
                        {"id": tc.id, "type": "function",
                         "function": {"name": tc.function.name,
                                      "arguments": tc.function.arguments}}
                        for tc in tool_calls
                    ],
                })
                for tc in tool_calls:
                    try:
                        args = json.loads(tc.function.arguments or "{}")
                        result = await execute_tool(tc.function.name, args)
                    except Exception as e:
                        logger.warning("工具 %s 执行失败: %s", tc.function.name, e)
                        result = {"ok": False, "error": str(e)}
                    messages.append({
                        "role": "tool", "tool_call_id": tc.id,
                        "content": json.dumps(result, ensure_ascii=False),
                    })
            return ""
        except Exception as e:
            # 后端不支持 tools（本地模型/精简服务常见）→ 去掉 tools 回退纯对话一次
            if tools:
                logger.warning("LLM 调用带工具失败（%s），回退纯对话重试", e)
                try:
                    return await self.chat(messages, temperature=temp, max_tokens=tokens,
                                           model=mdl, disable_thinking=disable_thinking,
                                           tools=None, execute_tool=execute_tool)
                except Exception as e2:
                    logger.error("纯对话重试也失败: %s", e2)
            logger.error("LLM 调用失败 [%s]: %s", type(self).__name__, e)
            return self.error_reply

    async def summarize(self, history_text):
        """把聊天记录压缩成摘要（对话上下文压缩用）。"""
        from personality import SUMMARY_PROMPT
        # 用 replace 而非 str.format：history 里的 { } 会触发 KeyError/ValueError
        prompt = SUMMARY_PROMPT.replace("{history}", history_text)
        try:
            resp = await self._create(self.model,
                                      [{"role": "user", "content": prompt}], 0.5, 1000)
            usage = getattr(resp, "usage", None)
            if usage is not None:
                usage_tracker.add(self.model, usage)
            return resp.choices[0].message.content.strip()
        except Exception as e:
            logger.error("摘要生成失败: %s", e)
            return ""

    async def list_models(self, api_key=None, base_url=None) -> list[str]:
        """查询 API 支持的模型列表（GET {base_url}/models，OpenAI 兼容接口）。

        api_key/base_url 非空时用它们建临时客户端（GUI"刷新模型"场景）；
        为空时使用本客户端自身的配置。
        """
        from openai import AsyncOpenAI
        client = self._client
        own = bool(api_key or base_url)
        if own:
            client = AsyncOpenAI(
                api_key=api_key or getattr(self, "api_key", ""),
                base_url=base_url or getattr(self, "base_url", ""),
            )
        try:
            resp = await client.models.list()
            return sorted(m.id for m in resp.data)
        finally:
            # 临时客户端必须关闭，否则 httpx 连接池泄漏
            if own:
                try:
                    await client.close()
                except Exception:
                    pass
