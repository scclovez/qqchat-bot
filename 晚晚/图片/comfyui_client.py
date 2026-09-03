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

# 提交瞬时失败自动重试：ComfyUI 忙（正在跑长任务/视频生成）时 /prompt 可能短暂 5xx/无响应，
# 一次失败就放弃会让 bot 白白道歉。共尝试 MAX_POST_RETRIES+1 次，间隔递增。
MAX_POST_RETRIES = 2
POST_RETRY_DELAYS = (3.0, 6.0)


async def _post_prompt(client, base: str, workflow: dict):
    """提交工作流到 /prompt；5xx/429/网络异常自动重试，4xx 直接返回（工作流本身问题，重试无意义）。

    返回: 成功时的 httpx.Response（status 200）；重试耗尽返回 None。
    """
    last_err = ""
    for i in range(MAX_POST_RETRIES + 1):
        try:
            r = await client.post(f"{base}/prompt", json={"prompt": workflow})
            if r.status_code == 200:
                return r
            if r.status_code < 500 and r.status_code != 429:
                logger.error("ComfyUI 提交被拒（工作流问题，不重试）%s: %s",
                             r.status_code, r.text[:300])
                return r  # 4xx：工作流校验失败，重试无用，原样交回上层报错
            last_err = f"HTTP {r.status_code}: {r.text[:200]}"
            logger.warning("ComfyUI 提交返回 %s（第 %d/%d 次）: %s",
                           r.status_code, i + 1, MAX_POST_RETRIES + 1, r.text[:100])
        except Exception as e:
            last_err = f"{type(e).__name__}: {e}"
            logger.warning("ComfyUI 提交网络异常（第 %d/%d 次）: %s",
                           i + 1, MAX_POST_RETRIES + 1, e)
        if i < MAX_POST_RETRIES:
            await asyncio.sleep(POST_RETRY_DELAYS[min(i, len(POST_RETRY_DELAYS) - 1)])
    logger.error("ComfyUI 提交失败（已重试 %d 次仍失败）: %s", MAX_POST_RETRIES, last_err)
    return None


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
        # trust_env=False：强制直连本地 ComfyUI，不走系统代理。
        # 症状：开着 Clash 等系统代理时，httpx 把 127.0.0.1:8188 也丢给代理
        # （httpx 用 getproxies() 读代理，但不尊重 ProxyOverride 里的 127.* 绕过列表），
        # 代理转发到远端连不回本机 → POST /prompt 恒 502，GET 却正常。
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True, trust_env=False) as client:
            r = await _post_prompt(client, base, workflow)
            if r is None:
                return ""
            prompt_id = (r.json() or {}).get("prompt_id")
            if not prompt_id:
                logger.error("ComfyUI 无 prompt_id: %s", r.text[:300])
                return ""
            for _ in range(max(1, int(timeout // 2))):
                await asyncio.sleep(2)
                try:
                    hr = await client.get(f"{base}/history/{prompt_id}")
                except Exception:
                    continue  # 轮询瞬时网络抖动：跳过本次，继续等
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
