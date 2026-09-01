# -*- coding: utf-8 -*-
"""本地 ComfyUI 图生成客户端（配合任意文生图工作流，如 Z-Image Turbo）。

工作流文件（API 格式 JSON，ComfyUI「保存(API 格式)」导出）中用占位符：
  {{prompt}}   提示词
  {{width}}    输出宽（数字，默认 1024）
  {{height}}   输出高（数字，默认 1024）

流程：替换占位符 → POST /prompt → 轮询 /history/{id} → /view 下载图片。
"""
import asyncio
import json
import logging
import os
import uuid
from datetime import datetime

logger = logging.getLogger(__name__)

from 路径 import PROJECT_ROOT, data_path
SAVE_DIR = data_path("晚晚", "图片", "生成图片")

# 生成图片目录保留上限：超出后删除最旧的 comfy_* 文件（控制磁盘占用）
MAX_SAVED_IMAGES = 200


def _prune_save_dir():
    """删除 生成图片 目录中最旧的 comfy_* 文件，保持目录规模受控。"""
    try:
        entries = []
        for name in os.listdir(SAVE_DIR):
            p = os.path.join(SAVE_DIR, name)
            if name.startswith("comfy_") and os.path.isfile(p):
                entries.append(p)
        if len(entries) > MAX_SAVED_IMAGES:
            for p in sorted(entries, key=os.path.getmtime)[:len(entries) - MAX_SAVED_IMAGES]:
                try:
                    os.remove(p)
                except OSError:
                    pass
    except OSError:
        pass


def _apply_placeholders(raw: str, prompt: str, width: int, height: int) -> str:
    """在原始 JSON 文本上替换占位符（{{width}} 可出现在数字位置）。"""
    raw = raw.replace("{{prompt}}", json.dumps(prompt, ensure_ascii=False)[1:-1])
    raw = raw.replace("{{width}}", str(int(width)))
    raw = raw.replace("{{height}}", str(int(height)))
    return raw


async def generate(workflow_file: str, prompt: str, base_url: str = "http://127.0.0.1:8188",
                   width: int = 1024, height: int = 1024, timeout: float = 600) -> str:
    """提交工作流到本地 ComfyUI 生成图片，返回本地路径；失败返回空串。"""
    try:
        with open(workflow_file, "r", encoding="utf-8") as f:
            raw = f.read()
        workflow = json.loads(_apply_placeholders(raw, prompt, width, height))
    except Exception as e:
        logger.error("读取 ComfyUI 工作流失败 %s: %s", workflow_file, e)
        return ""
    import httpx
    base = base_url.rstrip("/")
    try:
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
            r = await client.post(f"{base}/prompt", json={"prompt": workflow})
            if r.status_code != 200:
                logger.error("ComfyUI 提交失败 %s: %s", r.status_code, r.text[:300])
                return ""
            prompt_id = (r.json() or {}).get("prompt_id")
            if not prompt_id:
                logger.error("ComfyUI 无 prompt_id: %s", r.text[:300])
                return ""
            for _ in range(max(1, int(timeout // 2))):
                await asyncio.sleep(2)
                hr = await client.get(f"{base}/history/{prompt_id}")
                if hr.status_code != 200:
                    continue
                hist = (hr.json() or {}).get(prompt_id) or {}
                status = hist.get("status", {})
                if status.get("completed") or status.get("status_str") in ("success", "completed"):
                    images = []
                    for node_out in (hist.get("outputs") or {}).values():
                        images.extend(node_out.get("images") or [])
                    if not images:
                        logger.error("ComfyUI 任务完成但无输出图")
                        return ""
                    img = images[0]
                    vr = await client.get(f"{base}/view", params={
                        "filename": img.get("filename"),
                        "subfolder": img.get("subfolder", ""),
                        "type": img.get("type", "output"),
                    })
                    if vr.status_code != 200:
                        logger.error("ComfyUI 下载失败 %s", vr.status_code)
                        return ""
                    os.makedirs(SAVE_DIR, exist_ok=True)
                    ext = os.path.splitext(img.get("filename", "x.png"))[1] or ".png"
                    path = os.path.join(SAVE_DIR,
                                        f"comfy_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}{ext}")
                    with open(path, "wb") as f:
                        f.write(vr.content)
                    _prune_save_dir()
                    logger.info("ComfyUI 图片已保存: %s", path)
                    return path
                if status.get("status_str") in ("error", "failed"):
                    logger.error("ComfyUI 任务失败: %s", str(status)[:300])
                    return ""
            logger.error("ComfyUI 任务超时")
            return ""
    except Exception as e:
        logger.error("ComfyUI 生成异常: %s", e)
        return ""
