# -*- coding: utf-8 -*-
"""语音识别（MiMo-V2.5-ASR）— 识别用户发来的语音。

与语音合成同一个小米 API，走 /v1/chat/completions。
支持 wav / mp3，传入 base64（Data URL）。
"""
import logging

import httpx

from config import config, runtime
from 语音.tts import _get_api_key, _get_chat_url, _get_models_url

logger = logging.getLogger(__name__)

DEFAULT_ASR_MODEL = "mimo-v2.5-asr"


async def list_asr_models(api_key: str, timeout: float = 15) -> list[str]:
    """拉取小米语音模型列表，过滤 ASR 相关模型；失败返回默认列表。"""
    if not api_key:
        return [DEFAULT_ASR_MODEL]
    import re
    import httpx
    headers = {"Authorization": f"Bearer {api_key}"}
    ids = set()
    try:
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
            r = await client.get(_get_models_url(), headers=headers)
            if r.status_code == 200:
                data = r.json()
                for item in (data.get("data") or data.get("models") or []):
                    if isinstance(item, str):
                        ids.add(item)
                    elif isinstance(item, dict):
                        for k in ("model_id", "id", "name"):
                            v = item.get(k)
                            if v:
                                ids.add(str(v))
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning("ASR 模型列表获取异常: %s", e)
    asr_ids = sorted(i for i in ids if re.search(r"asr|speech|voice|audio|mimo", i, re.I))
    return asr_ids or sorted(ids) or [DEFAULT_ASR_MODEL]


async def recognize_audio(audio_base64: str, mime: str = "audio/wav",
                          language: str = "auto", model: str = "mimo-v2.5-asr",
                          timeout: float = 60) -> str:
    """识别音频，返回文本；失败返回空串。

    参数:
        audio_base64: 音频 base64 字符串（不含 data: 前缀）
        mime: audio/wav 或 audio/mpeg / audio/mp3
        language: auto / zh / en
        model: ASR 模型，默认 mimo-v2.5-asr
    """
    audio_base64 = (audio_base64 or "").strip()
    if not audio_base64:
        return ""
    api_key = _get_api_key()
    if not api_key:
        logger.error("ASR 未配置 MIMO_API_KEY（小米语音 Key），无法识别语音")
        return ""

    # 去掉可能的 data: 前缀
    if audio_base64.startswith("data:"):
        audio_base64 = audio_base64.split(",", 1)[-1]

    mime = mime or "audio/wav"
    if mime == "audio/mp3":
        mime = "audio/mpeg"

    body = {
        "model": model,
        "messages": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "input_audio",
                        "input_audio": {
                            "data": f"data:{mime};base64,{audio_base64}",
                        },
                    }
                ],
            }
        ],
        "asr_options": {
            "language": language or "auto",
        },
    }

    try:
        headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
            resp = await client.post(_get_chat_url(), headers=headers, json=body)
            if resp.status_code != 200:
                logger.error("ASR 识别 HTTP %s: %s", resp.status_code, resp.text[:300])
                return ""
            data = resp.json()
            try:
                text = data["choices"][0]["message"]["content"] or ""
            except (KeyError, IndexError, TypeError):
                logger.error("ASR 响应无文本: %s", str(data)[:300])
                return ""
            text = (text or "").strip()
            if text:
                logger.info("ASR 识别结果: %s", text[:100])
            return text
    except Exception as e:
        logger.error("ASR 识别异常: %s", e)
        return ""
