# -*- coding: utf-8 -*-
"""图片生成 — 阿里云百炼 DashScope 通义万相（qwen-image 系列）。

接口：POST https://dashscope.aliyuncs.com/api/v1/services/aigc/multimodal-generation/generation
模型列表：GET /api/v1/models（兼容 /compatible-mode/v1/models）
"""
import asyncio
import logging
import os
import re
import types
import uuid
from datetime import datetime

from config import config, runtime
from usage import usage_tracker

logger = logging.getLogger(__name__)

def _base_url():
    return (runtime.DASHSCOPE_BASE_URL or config.DASHSCOPE_BASE_URL or "https://dashscope.aliyuncs.com").rstrip("/")

def _gen_url():
    return _base_url() + "/api/v1/services/aigc/multimodal-generation/generation"

def _task_url(task_id):
    return _base_url() + f"/api/v1/tasks/{task_id}"

def _model_list_urls():
    return (_base_url() + "/api/v1/models", _base_url() + "/compatible-mode/v1/models")
from 路径 import PROJECT_ROOT, data_path
SAVE_DIR = data_path("晚晚", "图片", "生成图片")

# 生成图片目录保留上限：超出后删除最旧的 img_* 文件（控制磁盘占用）
MAX_SAVED_IMAGES = 200


def _prune_save_dir():
    """删除 生成图片 目录中最旧的 img_* 文件，保持目录规模受控。"""
    try:
        entries = []
        for name in os.listdir(SAVE_DIR):
            p = os.path.join(SAVE_DIR, name)
            if name.startswith("img_") and os.path.isfile(p):
                entries.append(p)
        if len(entries) > MAX_SAVED_IMAGES:
            for p in sorted(entries, key=os.path.getmtime)[:len(entries) - MAX_SAVED_IMAGES]:
                try:
                    os.remove(p)
                except OSError:
                    pass
    except OSError:
        pass

DEFAULT_MODELS = ["qwen-image-3.0-pro", "qwen-image-3.0", "wanx2.1-t2i-turbo"]


def _extract_image_url(output: dict) -> str:
    """从生成响应里提取图片 URL（兼容多种返回结构）。"""
    if not isinstance(output, dict):
        return ""
    results = output.get("results") or []
    if results and isinstance(results[0], dict) and results[0].get("url"):
        return results[0]["url"]
    for ch in output.get("choices") or []:
        msg = ch.get("message") or {}
        for c in msg.get("content") or []:
            if isinstance(c, dict):
                for key in ("image", "image_url"):
                    v = c.get(key)
                    if isinstance(v, str) and v.startswith("http"):
                        return v
                    if isinstance(v, dict) and v.get("url"):
                        return v["url"]
    for key in ("image", "image_url"):
        v = output.get(key)
        if isinstance(v, str) and v.startswith("http"):
            return v
    return ""


def _extract_usage(data: dict):
    """提取 DashScope 响应里的 usage 并转成 usage_tracker 认识的格式（无则 None）。"""
    usage = (data or {}).get("usage")
    if not isinstance(usage, dict) or not usage:
        return None
    try:
        inp = int(usage.get("input_tokens") or usage.get("prompt_tokens") or 0)
        out = int(usage.get("output_tokens") or usage.get("completion_tokens") or 0)
        total = int(usage.get("total_tokens") or (inp + out))
        return types.SimpleNamespace(prompt_tokens=inp, completion_tokens=out, total_tokens=total)
    except (TypeError, ValueError):
        return None


async def generate_image(api_key: str, prompt: str, model: str = "qwen-image-3.0-pro",
                         size: str | None = None, timeout: float = 180) -> str:
    """生成图片，返回图片 URL。失败返回空串。

    size: 输出尺寸，如 "1024*1024"（1K，省钱）、"720*1280"（竖屏）；None 用 API 默认。
    """
    if not api_key:
        logger.error("DASHSCOPE_API_KEY 未配置，无法生成图片")
        return ""
    import httpx
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    parameters = {"prompt_extend": True}
    if size:
        parameters["size"] = size
    body = {
        "model": model,
        "input": {"messages": [{"role": "user", "content": [{"text": prompt}]}]},
        "parameters": parameters,
    }
    try:
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
            resp = await client.post(_gen_url(), headers=headers, json=body)
            if resp.status_code != 200:
                logger.error("图片生成 HTTP %s: %s", resp.status_code, resp.text[:300])
                return ""
            data = resp.json()
            output = data.get("output") or {}
            url = _extract_image_url(output)
            if url:
                usage_tracker.add(model, _extract_usage(data))
                return url
            task_id = output.get("task_id") or data.get("task_id")
            if not task_id:
                logger.error("图片生成响应无图片也无 task_id: %s", str(data)[:400])
                return ""
            # 异步任务：轮询直到完成
            for _ in range(90):
                await asyncio.sleep(2)
                tr = await client.get(_task_url(task_id), headers=headers)
                if tr.status_code != 200:
                    continue
                tdata = tr.json().get("output") or {}
                status = tdata.get("task_status", "")
                if status == "SUCCEEDED":
                    url = _extract_image_url(tdata)
                    if url:
                        usage_tracker.add(model, _extract_usage(tr.json()))
                        return url
                    logger.error("生成任务成功但无图片: %s", str(tdata)[:300])
                    return ""
                if status in ("FAILED", "CANCELED", "UNKNOWN"):
                    logger.error("图片生成任务 %s: %s", status, tdata.get("message", ""))
                    return ""
            logger.error("图片生成任务超时")
            return ""
    except Exception as e:
        logger.error("图片生成异常: %s", e)
        return ""


async def download_image(url: str, timeout: float = 60) -> str:
    """下载图片到本地 生成图片/ 文件夹，返回本地路径；失败返回空串。"""
    try:
        import httpx
        os.makedirs(SAVE_DIR, exist_ok=True)
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
            r = await client.get(url)
            r.raise_for_status()
            ctype = r.headers.get("content-type", "")
            ext = ".png"
            if "jpeg" in ctype or "jpg" in ctype:
                ext = ".jpg"
            elif "webp" in ctype:
                ext = ".webp"
            elif "gif" in ctype:
                ext = ".gif"
            elif "png" not in ctype:
                m = re.search(r"\.(png|jpg|jpeg|webp|gif)", url, re.I)
                if m:
                    ext = "." + m.group(1).lower()
            # 文件名带 uuid 后缀，防止同一秒内并发下载互相覆盖
            path = os.path.join(SAVE_DIR, f"img_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}{ext}")
            with open(path, "wb") as f:
                f.write(r.content)
            _prune_save_dir()
            logger.info("图片已下载: %s", path)
            return path
    except Exception as e:
        logger.error("图片下载失败: %s", e)
        return ""


async def list_image_models(api_key: str, timeout: float = 15) -> list[str]:
    """拉取 DashScope 模型列表（优先图片/多模态模型）；失败返回默认列表。"""
    if not api_key:
        return DEFAULT_MODELS
    import httpx
    headers = {"Authorization": f"Bearer {api_key}"}
    ids = set()
    try:
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
            for url in _model_list_urls():
                try:
                    r = await client.get(url, headers=headers)
                    if r.status_code != 200:
                        continue
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
                    logger.warning("模型列表获取失败 %s: %s", url, e)
    except Exception as e:
        logger.warning("模型列表获取异常: %s", e)
    image_ids = sorted(i for i in ids if re.search(r"image|wanx|t2i|multimodal", i, re.I))
    return image_ids or sorted(ids) or DEFAULT_MODELS
