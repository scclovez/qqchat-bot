# -*- coding: utf-8 -*-
"""语音合成 — 调用小米 MiMo mimo-v2.5-tts-voicedesign 生成语音。

真实接口（已实测）：
POST {base}/chat/completions
{
  "model": "mimo-v2.5-tts-voicedesign",
  "messages": [
    {"role": "user", "content": "音色描述"},
    {"role": "assistant", "content": "要朗读的文本"}
  ],
  "modalities": ["audio"],
  "audio": {"format": "wav"}
}
返回 choices[0].message.audio.data 为 base64 音频。
"""
import asyncio
import base64
import logging
import os
import types
import uuid
from datetime import datetime

import httpx

from config import config, runtime
from usage import usage_tracker
from 路径 import data_path

logger = logging.getLogger(__name__)

# 语音缓存目录（本文件所在目录/语音缓存）
CACHE_DIR = data_path("晚晚", "语音", "语音缓存")

# 语音缓存保留上限：超出后删除最旧的 tts_* 文件（控制磁盘占用）
MAX_CACHE_FILES = 200


def _prune_cache():
    """删除缓存目录中最旧的 tts_* 文件，保持目录规模受控。"""
    try:
        entries = []
        for name in os.listdir(CACHE_DIR):
            p = os.path.join(CACHE_DIR, name)
            if name.startswith("tts_") and os.path.isfile(p):
                entries.append(p)
        if len(entries) > MAX_CACHE_FILES:
            for p in sorted(entries, key=os.path.getmtime)[:len(entries) - MAX_CACHE_FILES]:
                try:
                    os.remove(p)
                except OSError:
                    pass
    except OSError:
        pass

DEFAULT_TTS_MODELS = ["mimo-v2.5-tts-voicedesign"]
DEFAULT_VOICE_DESC = "温柔软糯的年轻女声，带一点撒娇，语速偏慢"

# 情感标签 → 具体语气描述（让 TTS 真正带情绪，而不是只写“情感：xxx”）
EMOTION_DESCRIPTIONS = {
    "慵懒": "用慵懒的语气说话，声音软软的、慢悠悠的，像刚睡醒不想动，带一点鼻音",
    "撒娇": "用撒娇的语气说话，带一点嗲和黏人，像在跟男朋友撒娇，尾音轻轻上扬",
    "开心": "用开心的语气说话，声音明朗轻快，带着笑意，语速稍快",
    "温柔": "用温柔的语气说话，轻声细语，柔和亲切，语速偏慢",
    "委屈": "用委屈的语气说话，声音低低的，带一点鼻音和哽咽感，语速慢",
    "害羞": "用害羞的语气说话，声音轻轻的，偶尔停顿，带一点糯和不好意思",
    "认真": "用认真关切的语气说话，语气柔和而专注，平稳清晰",
    "生气": "用假装生气的语气说话，带一点小情绪和娇嗔，尾音稍重",
    "疲惫": "用疲惫的语气说话，声音略低、慢吞吞的，像没什么精神",
    "惊讶": "用惊讶的语气说话，声音提高一点，带着意外和好奇",
}



def _get_api_key():
    """获取小米语音 API Key：runtime（GUI 保存）优先，其次 .env（config 启动时已加载）。"""
    return runtime.MIMO_API_KEY or config.MIMO_API_KEY


def _time_voice_modifier() -> str:
    """按当前时刻返回音色应有的状态修饰（深夜困倦、白天清醒），保证语音符合时间。"""
    hour = datetime.now().hour
    if hour < 5:
        return "深夜了，声音很轻很困，带着困倦和慵懒，像强撑着不想睡"
    if hour < 7:
        return "刚醒，声音软绵绵的，带着起床气的慵懒沙哑"
    if hour < 8:
        return "刚起床不久，声音还有点懒洋洋的"
    if hour < 12:
        return "上午，声音清醒自然，精神正好"
    if hour < 14:
        return "中午，声音放松随意，像刚吃完饭"
    if hour < 17:
        return "下午，声音明朗自然，状态不错"
    if hour < 19:
        return "傍晚，声音放松随性，像刚下课"
    if hour < 23:
        return "晚上，声音放松温柔，精神正好"
    return "深夜了，声音轻软慵懒，带着困意，像快睡着"


def _speed_modifier(emotion: str = "") -> str:
    """按情感标签/当前时段生成语速修饰，让语音节奏随内容自然变化、不呆板。

    有情感标签时用情感对应语速（开心轻快、委屈缓慢等）；
    无情感标签时按时段 + 随机给一个自然语速（避免每次都一个速度，听感"怪怪的"）。
    """
    import random
    emotion_speed = {
        "开心": "语速轻快一些，带着笑意",
        "兴奋": "语速稍快，雀跃一点",
        "惊讶": "语速稍快，带着惊讶",
        "生气": "语速稍快，语气带着点气",
        "着急": "语速偏快，有点急促",
        "撒娇": "语速适中，尾音带一点点拖",
        "害羞": "语速偏慢，声音软一点，不结巴不卡顿",
        "委屈": "语速偏慢，声音低低的",
        "难过": "语速缓慢，低沉一点",
        "伤心": "语速缓慢，声音发闷",
        "疲惫": "语速缓慢，拖着一点",
        "慵懒": "语速缓慢，懒洋洋的",
        "认真": "语速平稳适中",
        "温柔": "语速适中偏慢，轻柔",
    }
    base = emotion_speed.get((emotion or "").strip(), "")
    if base:
        return base
    hour = datetime.now().hour
    if hour < 5 or hour >= 23:
        return random.choice(["语速缓慢", "语速偏慢", "语速缓慢，带一点困意"])
    if hour < 8:
        return random.choice(["语速偏慢", "语速适中偏慢"])
    if hour < 12:
        return random.choice(["语速适中", "语速轻快"])
    if hour < 18:
        return random.choice(["语速适中", "语速轻快", "语速稍快"])
    return random.choice(["语速适中", "语速轻快"])


def _get_base_url():
    """获取小米语音 API Base URL（OpenAI 兼容，默认 https://api.xiaomimimo.com/v1）。"""
    return (runtime.MIMO_API_BASE_URL or config.MIMO_API_BASE_URL or "https://api.xiaomimimo.com/v1").rstrip("/")


def _get_chat_url():
    return _get_base_url() + "/chat/completions"


def _get_models_url():
    return _get_base_url() + "/models"


async def synthesize(text: str, voice_desc: str = "", model: str = "mimo-v2.5-tts-voicedesign",
                     timeout: float = 120, emotion: str = "") -> str:
    """合成语音，返回本地音频文件路径；失败返回空串。

    参数:
        text: 要合成的文本（由 DS 生成）
        voice_desc: 音色描述（用户在设置界面填写）
        model: TTS 模型，默认 mimo-v2.5-tts-voicedesign
        emotion: 情感标签（如 撒娇/开心/温柔），会并入音色描述
    """
    text = (text or "").strip()
    if not text:
        return ""
    api_key = _get_api_key()
    if not api_key:
        logger.error("TTS 未配置 MIMO_API_KEY（小米语音 Key），无法合成语音")
        return ""

    voice = (voice_desc or runtime.TTS_VOICE_DESCRIPTION or "").strip() or DEFAULT_VOICE_DESC
    emotion = (emotion or "").strip()
    if emotion:
        emotion_desc = EMOTION_DESCRIPTIONS.get(emotion) or f"用{emotion}的语气说话"
        voice = f"{voice}。{emotion_desc}"
    # 按当前时刻给音色加"此刻应有的状态"（深夜困倦慵懒、白天清醒），
    # 避免凌晨发语音还中气十足
    time_mod = _time_voice_modifier()
    if time_mod:
        voice = f"{voice}。{time_mod}"
    # 按情感/内容/时段给语速（追加在最后，覆盖固定"语速中等"），
    # 让语音节奏随内容变化、每次不重样
    speed_mod = _speed_modifier(emotion)
    if speed_mod:
        voice = f"{voice}。{speed_mod}"
    logger.info("TTS 开始合成 model=%s emotion=%r text=%s", model, emotion, text[:50])
    body = {
        "model": model,
        "messages": [
            {"role": "user", "content": voice},
            {"role": "assistant", "content": text},
        ],
        "modalities": ["audio"],
        "audio": {"format": "wav"},
    }

    try:
        os.makedirs(CACHE_DIR, exist_ok=True)
        headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
        logger.info("TTS 发送 voice=%s emotion 已并入", voice)
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
            resp = await client.post(_get_chat_url(), headers=headers, json=body)
            if resp.status_code != 200:
                logger.error("TTS 合成 HTTP %s: %s", resp.status_code, resp.text[:300])
                return ""
            data = resp.json()
            try:
                audio_data = data["choices"][0]["message"]["audio"]["data"]
            except (KeyError, IndexError, TypeError):
                logger.error("TTS 响应无音频数据: %s", str(data)[:300])
                return ""

            if audio_data.startswith("data:"):
                audio_data = audio_data.split(",", 1)[-1]
            raw = base64.b64decode(audio_data)
            # 文件名带 uuid 后缀，防止同一秒内多次合成互相覆盖
            filename = f"tts_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}.wav"
            path = os.path.join(CACHE_DIR, filename)
            with open(path, "wb") as f:
                f.write(raw)
            _prune_cache()
            # 统计语音生成模型调用
            usage = None
            try:
                u = data.get("usage") or {}
                if u:
                    usage = types.SimpleNamespace(
                        prompt_tokens=int(u.get("prompt_tokens", 0) or 0),
                        completion_tokens=int(u.get("completion_tokens", 0) or 0),
                        total_tokens=int(u.get("total_tokens", 0) or 0),
                    )
            except Exception:
                usage = None
            usage_tracker.add(model, usage)
            logger.info("TTS 语音已生成 model=%s path=%s emotion=%r", model, path, emotion)
            return path
    except Exception as e:
        logger.error("TTS 合成异常: %s", e)
        return ""


async def list_tts_models(api_key: str, timeout: float = 15) -> list[str]:
    """拉取小米语音模型列表，过滤语音/TTS 相关模型；失败返回默认列表。"""
    if not api_key:
        return DEFAULT_TTS_MODELS
    import re
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
        logger.warning("TTS 模型列表获取异常: %s", e)
    tts_ids = sorted(i for i in ids if re.search(r"tts|voice|speech|audio|mimo|cosyvoice", i, re.I))
    return tts_ids or sorted(ids) or DEFAULT_TTS_MODELS
