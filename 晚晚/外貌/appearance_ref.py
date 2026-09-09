# -*- coding: utf-8 -*-
"""外貌设定参考图 — 调用 DeepSeek 视觉模型总结人物外貌，锚定外貌特征。

用户把人物参考图放入「外貌设定」文件夹（默认 外貌设定/）。
点击生成后，会对文件夹下所有图片逐一调用视觉模型概括外貌，
再把所有概括合并成一份稳定的“外貌锚定文本”单独保存到 外貌总结.txt。
生成自拍/人物图时，会自动把这份总结合并进提示词，避免每次 AI 生图外貌漂移。
"""
import asyncio
import base64
import logging
import os
from pathlib import Path

from config import config, runtime

logger = logging.getLogger("appearance_ref")

from 路径 import PROJECT_ROOT, data_path
BASE_DIR = data_path("晚晚", "外貌")

# 支持的图片后缀
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp"}

SUMMARY_FILE_NAME = "外貌总结.txt"

# DeepSeekClient.chat() 在 API 调用失败时返回的兜底文案（对话场景用）。
# 外貌总结必须识别并跳过它：否则 Key 失效/网络抖动时会把
# "抱歉呀，我这边网络有点不太好" 当成外貌描述写进总结，污染后续生图提示词。
FALLBACK_REPLY_MARKS = ("网络有点不太好", "抱歉呀")

# 单图失败后等待重试的秒数（网络抖动通常短暂，隔几秒重试常能成功）
RETRY_DELAY = 2.0
# 连续失败熔断：连续 N 张失败说明网络/服务不可用，提前停止，不再逐张干等
MAX_CONSECUTIVE_FAILURES = 3

# 单图外貌描述提示词：只锚定稳定、可长期一致的特征
SINGLE_IMAGE_PROMPT = (
    "请仔细观察这张人物参考图，用中文概括该人物的外貌特征。"
    "只描述稳定、可长期锚定的特征，重点包括：脸型、五官（眼睛/鼻子/嘴唇/眉毛，"
    "含眼型与单双眼皮）、肤色、发色与发长、体型。"
    "不要描述易变细节：发型扎法（马尾/披肩/刘海样式）、穿搭、配饰、表情、姿势、环境。"
    "要求具体、客观、可复现，方便后续文生图模型稳定还原该人物。"
    "控制在120字以内，直接输出外貌描述，不要任何前缀或解释。"
)

# 多图合并提示词：只保留稳定特征，显式丢弃易变/冲突细节
MERGE_PROMPT = (
    "请把以下多张参考图的外貌描述整合成一段统一、稳定、适合文生图模型的人物外貌锚定文本。"
    "要求：只保留稳定、一致的特征——脸型、五官（眼型/鼻型/唇型/眉型/双眼皮等）、肤色、"
    "发色与发长、体型；"
    "对不同图片间冲突或易变的细节（发型扎法、刘海、穿搭、配饰、表情）一律不写入；"
    "按脸型/五官/肤色/发色/体型分点描述，不要遗漏任何能稳定人物外观的关键信息。"
    "直接输出整合后的文本，不要任何前缀或解释。\n\n"
)


def get_ref_dir():
    """返回外貌设定文件夹绝对路径。"""
    d = runtime.APPEARANCE_REF_DIR or "外貌设定"
    if not os.path.isabs(d):
        d = os.path.join(BASE_DIR, d)
    return d


def ensure_ref_dir():
    """确保外貌设定文件夹存在。"""
    os.makedirs(get_ref_dir(), exist_ok=True)


def get_summary_file():
    """返回外貌总结文件路径。"""
    return os.path.join(get_ref_dir(), SUMMARY_FILE_NAME)


def list_ref_images():
    """返回外貌设定文件夹下所有图片文件（按文件名排序）。"""
    d = get_ref_dir()
    if not os.path.isdir(d):
        return []
    return [
        os.path.join(d, f)
        for f in sorted(os.listdir(d))
        if os.path.splitext(f)[1].lower() in IMAGE_EXTS and os.path.isfile(os.path.join(d, f))
    ]


def load_summary():
    """读取已生成的外貌总结；没有则返回空串。"""
    try:
        with open(get_summary_file(), "r", encoding="utf-8") as f:
            return f.read().strip()
    except (FileNotFoundError, OSError):
        return ""


def save_summary(summary: str):
    """保存人工修订后的外貌锚定文本，供后续人物生图直接使用。"""
    text = (summary or "").strip()
    ensure_ref_dir()
    with open(get_summary_file(), "w", encoding="utf-8") as f:
        f.write(text)
    logger.info("外貌总结已保存到：%s", get_summary_file())


def _image_to_data_url(path):
    """把图片转成 base64 data URL，供视觉模型读取。"""
    ext = os.path.splitext(path)[1].lower()
    mime = {
        ".png": "image/png", ".gif": "image/gif", ".webp": "image/webp",
        ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".bmp": "image/bmp",
    }.get(ext, "image/jpeg")
    with open(path, "rb") as f:
        b64 = base64.b64encode(f.read()).decode("ascii")
    return f"data:{mime};base64,{b64}"


async def summarize_appearance(client):
    """调用视觉模型总结「外貌设定」文件夹中的图片，保存总结并返回。

    参数 client: DeepSeekClient 实例。
    """
    ensure_ref_dir()
    images = list_ref_images()
    logger.info("开始总结外貌参考图：共 %d 张", len(images))
    if not images:
        logger.warning("外貌设定文件夹中没有图片，跳过外貌总结")
        return ""

    per_image_parts = []
    consecutive_fails = 0
    for img in images:
        logger.info("正在总结参考图：%s", os.path.basename(img))
        data_url = _image_to_data_url(img)
        content = [
            {"type": "text", "text": SINGLE_IMAGE_PROMPT},
            {"type": "image_url", "image_url": {"url": data_url}},
        ]
        desc = ""
        # 单图最多尝试 2 次：网络抖动时隔几秒重试常能救回来（chat() 失败不抛异常，
        # 而是返回兜底文案，命中 FALLBACK_REPLY_MARKS 即视为失败）
        for attempt in range(2):
            try:
                desc = await client.chat(
                    [{"role": "user", "content": content}],
                    model=config.DEEPSEEK_VISION_MODEL,
                    temperature=0.3,
                    max_tokens=300,
                )
            except Exception as e:
                desc = ""
                logging.getLogger("appearance_ref").error(
                    "图片外貌总结失败 %s（第 %d 次）: %s", os.path.basename(img), attempt + 1, e)
            if (desc or "").strip() and not any(m in desc for m in FALLBACK_REPLY_MARKS):
                break
            desc = ""
            if attempt == 0:
                logger.warning("参考图 %s 总结失败，%s 秒后重试一次",
                               os.path.basename(img), RETRY_DELAY)
                await asyncio.sleep(RETRY_DELAY)
        # 成功 → 记录并清零连续失败计数
        if (desc or "").strip() and not any(m in desc for m in FALLBACK_REPLY_MARKS):
            per_image_parts.append(f"参考图 {os.path.basename(img)}：{desc.strip()}")
            consecutive_fails = 0
            continue
        # 失败 → 连续失败达到阈值则熔断（网络/服务不可用，不再逐张干等）
        consecutive_fails += 1
        logger.warning("参考图 %s 外貌总结失败，跳过", os.path.basename(img))
        if consecutive_fails >= MAX_CONSECUTIVE_FAILURES:
            logger.error("连续 %d 张参考图总结失败，停止任务（请检查网络连接或 API 配置）",
                         MAX_CONSECUTIVE_FAILURES)
            break

    if not per_image_parts:
        return ""

    combined = "\n".join(per_image_parts)
    # 合并是纯文本加工任务：禁用 thinking，避免思考吃光 max_tokens=400 导致空结果
    final = await client.chat(
        [{"role": "user", "content": MERGE_PROMPT + combined}],
        temperature=0.3,
        max_tokens=400,
        disable_thinking=True,
    )
    final = (final or "").strip()
    # 合并失败（空返回或 API 兜底文案）→ 退回直接拼接的各图描述，避免污染总结文件
    if not final or any(m in final for m in FALLBACK_REPLY_MARKS):
        final = combined

    save_summary(final)
    return final
