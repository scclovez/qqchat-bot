# -*- coding: utf-8 -*-
"""全局配置，从环境变量或 .env 文件加载。"""
import logging
import os
import json
from dotenv import load_dotenv

logger = logging.getLogger(__name__)

from 路径 import data_path, code_path, PROJECT_ROOT
_env_file = data_path("晚晚", "配置", ".env")
if not os.path.isfile(_env_file):
    _env_file = code_path("晚晚", "配置", ".env")  # 兜底：打包资源里的初始 .env
load_dotenv(_env_file)


def env_file_path() -> str:
    """当前生效的 .env 文件路径（可能不存在：开发版纯净无 .env）。"""
    return _env_file


def save_env_overrides(overrides: dict) -> bool:
    """把 GUI 连接页的 Key/URL/模型等覆盖项写回 .env（**仅当 .env 已存在**）。

    返回是否真的写入（.env 不存在时返回 False，调用方据此提示用户手动建 .env）。
    空值/None 跳过（不清空已有 Key）；写入后同步更新内存 config 属性立即生效。
    """
    if not os.path.isfile(_env_file):
        logger.info("未找到 .env（%s），跳过写回；需先复制 .env.example 为 .env", _env_file)
        return False
    from dotenv import set_key
    changed = 0
    for key, value in (overrides or {}).items():
        if value is None or str(value).strip() == "":
            continue
        try:
            # 默认 quote_mode=always：值含 # / 空格等特殊字符时加引号，避免被当注释截断
            set_key(_env_file, key, str(value).strip())
            changed += 1
        except Exception as e:
            logger.warning("写入 .env 失败 [%s]: %s", key, e)
    # 同步内存：新值立即生效（无需重启）；仅当目标是 Config 类字段时覆盖，
    # 其余键（运行时环境变量）不在此列。
    for key, value in (overrides or {}).items():
        if value is None or str(value).strip() == "":
            continue
        if hasattr(Config, key):
            setattr(config, key, str(value).strip())
    logger.info("已写回 %d 项连接设置到 .env", changed)
    return True

RUNTIME_CONFIG_FILE = data_path("晚晚", "配置", ".runtime_config.json")

# 字段类型定义：int / float / str
_FIELD_TYPES = {
    "MAX_HISTORY_LENGTH": int, "TEMPERATURE": float, "MAX_TOKENS": int,
    "REPLY_COOLDOWN_MIN": float, "REPLY_COOLDOWN_MAX": float,
    "STICKER_PROBABILITY": float, "STICKER_DIR": str,
    "PROACTIVE_INTERVAL_MIN": int,
    "PROACTIVE_INTERVAL_MAX": int,
    "PROACTIVE_PROBABILITY": float, "PROACTIVE_GAP_MIN": int,
    "PROACTIVE_ONLY_USER_ID": str,
    "PROACTIVE_FOLLOWUP_HOURS": float,
    "PROACTIVE_FOLLOWUP_MAX": int,
    "NIGHT_SILENCE_HOURS": float,
    "QZONE_SELF_UIN": str, "QZONE_FEED_COMMENT_PROB": float,
    "QZONE_POSTS_PER_DAY": int, "QZONE_POST_IMAGE_PROB": float,
    "QZONE_POST_INTERVAL_MIN": float, "QZONE_POST_INTERVAL_MAX": float,
    "QZONE_IMAGE_CHECK": int,
    "WEATHER_CITY": str,
    "THINKING_MODE": int, "IMAGE_GEN_ENABLED": int,
    "IMAGE_GEN_SIZE": str,
    "IMAGE_BACKEND": str, "DASHSCOPE_BASE_URL": str, "COMFYUI_URL": str, "COMFYUI_WORKFLOW_FILE": str,
    "IMAGE_PROMPT_USE_LLM": int, "IMAGE_COMMENT_PROBABILITY": float,
    "AUTO_ILLUSTRATE_PROBABILITY": float,
    "ANNIVERSARY_DATE": str,
    "TTS_PROBABILITY": float, "TTS_VOICE_DESCRIPTION": str, "TTS_MODEL": str,
    "MIMO_API_KEY": str, "MIMO_API_BASE_URL": str,
    "ASR_ENABLED": int, "ASR_LANGUAGE": str, "ASR_MODEL": str,
    "GIRLFRIEND_NAME": str, "GIRLFRIEND_AGE": str, "GIRLFRIEND_BIRTHDATE": str,
    "GIRLFRIEND_IDENTITY": str, "GIRLFRIEND_CHARACTER": str,
    "GIRLFRIEND_APPEARANCE": str,
    "SYSTEM_PROMPT": str,
    "GIRLFRIEND_LANGUAGE_STYLE": str,
    "GIRLFRIEND_SPECIAL_REACTIONS": str,
    "GIRLFRIEND_CONSTRAINTS": str,
    "GIRLFRIEND_SCENARIO": str,
    "GUI_THEME_MODE": str,
    "APPEARANCE_REF_DIR": str,
    "FRIEND_APPROVE_UIDS": str,
}


class Config:
    """只读静态配置，启动时从环境变量加载。"""
    DEEPSEEK_API_KEY: str = os.getenv("DEEPSEEK_API_KEY", "")
    DEEPSEEK_BASE_URL: str = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
    DEEPSEEK_MODEL: str = os.getenv("DEEPSEEK_MODEL", "deepseek-v4-flash")
    DEEPSEEK_VISION_MODEL: str = os.getenv("DEEPSEEK_VISION_MODEL", "deepseek-v4-flash-vision-exp")
    ONEBOT_WS_URL: str = os.getenv("ONEBOT_WS_URL", "ws://localhost:3001")
    ONEBOT_ACCESS_TOKEN: str = os.getenv("ONEBOT_ACCESS_TOKEN", "")
    # 阿里云百炼 DashScope（通义万相图片生成）
    DASHSCOPE_API_KEY: str = os.getenv("DASHSCOPE_API_KEY", "")
    DASHSCOPE_BASE_URL: str = os.getenv("DASHSCOPE_BASE_URL", "https://dashscope.aliyuncs.com")
    DASHSCOPE_IMAGE_MODEL: str = os.getenv("DASHSCOPE_IMAGE_MODEL", "qwen-image-3.0-pro")
    # 小米（MiMo）语音 API Key：GUI 保存到 runtime 配置；这里兜底读 .env
    # （tts/asr 会引用 config.MIMO_API_KEY，属性不存在会 AttributeError）
    MIMO_API_KEY: str = os.getenv("MIMO_API_KEY", "")

    @classmethod
    def validate(cls):
        missing = []
        if not cls.DEEPSEEK_API_KEY:
            missing.append("DEEPSEEK_API_KEY")
        return missing


class RuntimeConfig:
    """可运行时修改的配置，通过 GUI 编辑。"""
    MAX_HISTORY_LENGTH: int = int(os.getenv("MAX_HISTORY_LENGTH", "500"))
    TEMPERATURE: float = float(os.getenv("TEMPERATURE", "0.9"))
    MAX_TOKENS: int = int(os.getenv("MAX_TOKENS", "256"))
    GIRLFRIEND_NAME: str = "bot"
    GIRLFRIEND_AGE: str = "22"
    GIRLFRIEND_BIRTHDATE: str = ""
    GIRLFRIEND_IDENTITY: str = "在读艺术系研究生，喜欢画画和摄影"
    GIRLFRIEND_CHARACTER: str = "温柔、有点粘人、偶尔撒娇、善良体贴、带一点小俏皮"
    GIRLFRIEND_APPEARANCE: str = ""
    APPEARANCE_REF_DIR: str = "外貌设定"  # 用户放置人物参考图 + 外貌总结的文件夹
    SYSTEM_PROMPT: str = ""
    GIRLFRIEND_LANGUAGE_STYLE: str = ""
    GIRLFRIEND_SPECIAL_REACTIONS: str = ""
    GIRLFRIEND_CONSTRAINTS: str = ""
    GIRLFRIEND_SCENARIO: str = ""
    REPLY_COOLDOWN_MIN: float = 0.0
    REPLY_COOLDOWN_MAX: float = 3.0  # 回复冷却：在 MIN~MAX 秒区间随机等待
    # 功能开关：全部强制开启（不提供关闭开关，GUI 只调概率/强度）
    # 表情包：发送概率（0-1）、图库文件夹
    STICKER_ENABLED: int = 1
    STICKER_PROBABILITY: float = 0.12
    STICKER_DIR: str = "晚晚/图片/表情包"
    # 主动消息：每隔 INTERVAL_MIN 分钟检查一次，对最近聊过天的用户按概率主动发一条
    PROACTIVE_ENABLED: int = 1
    PROACTIVE_INTERVAL_MIN: int = 45
    PROACTIVE_INTERVAL_MAX: int = 75
    PROACTIVE_PROBABILITY: float = 0.6
    PROACTIVE_GAP_MIN: int = 10  # 用户最近 GAP_MIN 分钟内发过消息则跳过，避免打扰
    # 只对指定 QQ 号发主动消息（主动消息/撩人/追问）；留空 = 不限（对所有用户）
    PROACTIVE_ONLY_USER_ID: str = ""
    # 好友申请白名单：允许自动同意加好友的 QQ 号（逗号分隔，如 "10001,10002"）。
    # 留空 = 拒绝所有好友申请（防陌生人私聊骚扰/偷看人设，隐私安全默认值）
    FRIEND_APPROVE_UIDS: str = ""
    # 未回复追问：主动发消息后，对方长时间没回时按病娇傲娇人设追一句（真人感）
    PROACTIVE_FOLLOWUP_ENABLED: int = 1
    PROACTIVE_FOLLOWUP_HOURS: float = 2.0  # 多久没回复（小时）触发追问
    PROACTIVE_FOLLOWUP_MAX: int = 2        # 每个沉默期最多追问几次
    # 晚安静默：用户道晚安后，N 小时内不主动发消息/撩人/追问（模拟真人已睡）；0=关闭
    NIGHT_SILENCE_HOURS: float = 8.0
    # QQ 空间：主动发说说 / 评论自动回复 / 好友动态自动点赞评论（需空间已开通）
    QZONE_ENABLED: int = 1
    QZONE_SELF_UIN: str = ""  # 机器人自己的 QQ 号（留空则从消息 self_id 自动获取）
    # 好友动态评论概率（0=只点赞不评论；点赞总是做）
    QZONE_FEED_COMMENT_PROB: float = 0.35
    # 每天自动发说说的条数（条间至少间隔 2 小时）
    QZONE_POSTS_PER_DAY: int = 3
    # 发说说配图概率（0=纯文字；配图用现有图片后端：本地 ComfyUI / 云端百炼）
    QZONE_POST_IMAGE_PROB: float = 0.6
    # 说说的条间间隔（小时）：发完一条后，在 [MIN, MAX] 区间随机取一个时间作为下次可发时间
    QZONE_POST_INTERVAL_MIN: float = 2.0
    QZONE_POST_INTERVAL_MAX: float = 4.0
    # 说说配图质量检查：用视觉模型确认图片清晰/有人物，不合格自动重新生成
    QZONE_IMAGE_CHECK: int = 1
    # 实时信息：联网搜索开关、天气城市
    WEB_SEARCH_ENABLED: int = 1
    WEATHER_CITY: str = "南昌"
    # 正在输入：私聊收到消息后显示"对方正在输入"，回复发完后停止
    TYPING_ENABLED: int = 1
    # 思考模式：0=关闭（女友闲聊场景更快更省，避免思考吃光 token 导致空回复）；1=开启
    THINKING_MODE: int = 0
    # 图片生成：允许用户说"画一张xxx"时调用通义万相生成并发图
    IMAGE_GEN_ENABLED: int = 1
    # 图片输出尺寸（1K = 1024*1024，省钱；自拍可换 720*1280 竖屏）
    IMAGE_GEN_SIZE: str = "1024*1024"
    # 图片生成后端（二选一）：dashscope = 百炼 API；comfyui = 本地 ComfyUI
    IMAGE_BACKEND: str = "dashscope"
    DASHSCOPE_BASE_URL: str = "https://dashscope.aliyuncs.com"
    COMFYUI_URL: str = "http://127.0.0.1:8188"
    COMFYUI_WORKFLOW_FILE: str = "comfy_workflow.json"
    # 是否用大模型（DS）润色图片提示词：1=用，0=不用（直接用原始要求）
    IMAGE_PROMPT_USE_LLM: int = 1
    # 发图后视觉评论概率（0-1）：0=不评论，1=每次都评论
    IMAGE_COMMENT_PROBABILITY: float = 0.5
    # 自动配图概率（0-1）：模型自主决定配图（输出【插图】标记）后，再按此概率真正生成；
    # 0=关闭自动配图，1=每次标记都生成
    AUTO_ILLUSTRATE_PROBABILITY: float = 0.4
    # 自主互动工具：模型可通过 function calling 自主决定 戳一戳/回表情/名片赞/改在线状态/换签名
    INTERACT_ENABLED: int = 1
    # 活人感增强：分场景延迟 / 深夜困意 / 情绪日 / 闹脾气 / 生日祝福 / 纪念日 / 碎碎念 等
    LIVENESS_ENABLED: int = 1
    # 纪念日起始日期（在一起的第一天，YYYY-MM-DD）；用于"第 N 天"/周年庆祝
    ANNIVERSARY_DATE: str = "2025-07-20"
    # 情绪底线与自我保护：强制开启（越界时她会认真划边界、拒绝伤害），不提供关闭开关
    BOUNDARY_ENABLED: int = 1
    # 语音回复：按概率用 mimo-v2.5-tts-voicedesign 发语音条；0=从不发语音，1=每次发语音
    TTS_PROBABILITY: float = 0.3
    TTS_VOICE_DESCRIPTION: str = ""
    TTS_MODEL: str = "mimo-v2.5-tts-voicedesign"
    # 小米（MiMo）语音 API 独立 Key，和百炼图片 Key 分开
    MIMO_API_KEY: str = ""
    MIMO_API_BASE_URL: str = "https://api.xiaomimimo.com/v1"
    # 语音识别（MiMo-V2.5-ASR）：识别用户发来的语音
    ASR_ENABLED: int = 1
    ASR_LANGUAGE: str = "auto"  # auto / zh / en
    ASR_MODEL: str = "mimo-v2.5-asr"
    # 控制面板外观：twilight=暮光陪伴、night=深夜模式、system=跟随 Windows 外观。
    GUI_THEME_MODE: str = "twilight"

    _PERSIST_KEYS = [
        "MAX_HISTORY_LENGTH", "TEMPERATURE", "MAX_TOKENS",
        "GIRLFRIEND_NAME", "GIRLFRIEND_AGE", "GIRLFRIEND_BIRTHDATE", "GIRLFRIEND_IDENTITY",
        "GIRLFRIEND_CHARACTER", "GIRLFRIEND_APPEARANCE", "APPEARANCE_REF_DIR", "SYSTEM_PROMPT",
        "GIRLFRIEND_LANGUAGE_STYLE", "GIRLFRIEND_SPECIAL_REACTIONS",
        "GIRLFRIEND_CONSTRAINTS", "GIRLFRIEND_SCENARIO",
        "REPLY_COOLDOWN_MIN", "REPLY_COOLDOWN_MAX",
        "STICKER_PROBABILITY", "STICKER_DIR",
        "PROACTIVE_INTERVAL_MIN", "PROACTIVE_INTERVAL_MAX",
        "PROACTIVE_PROBABILITY", "PROACTIVE_GAP_MIN", "PROACTIVE_ONLY_USER_ID",
        "FRIEND_APPROVE_UIDS",
        "PROACTIVE_FOLLOWUP_HOURS",
        "PROACTIVE_FOLLOWUP_MAX", "NIGHT_SILENCE_HOURS",
        "QZONE_SELF_UIN", "QZONE_FEED_COMMENT_PROB",
        "QZONE_POSTS_PER_DAY", "QZONE_POST_IMAGE_PROB",
        "QZONE_POST_INTERVAL_MIN", "QZONE_POST_INTERVAL_MAX", "QZONE_IMAGE_CHECK",
        "WEATHER_CITY", "THINKING_MODE",
        "IMAGE_GEN_ENABLED", "IMAGE_GEN_SIZE",
        "IMAGE_BACKEND", "DASHSCOPE_BASE_URL", "COMFYUI_URL", "COMFYUI_WORKFLOW_FILE",
        "IMAGE_PROMPT_USE_LLM", "IMAGE_COMMENT_PROBABILITY", "AUTO_ILLUSTRATE_PROBABILITY",
        "ANNIVERSARY_DATE",
        "TTS_PROBABILITY", "TTS_VOICE_DESCRIPTION", "TTS_MODEL",
        "MIMO_API_KEY", "MIMO_API_BASE_URL",
        "ASR_ENABLED", "ASR_LANGUAGE", "ASR_MODEL",
        "GUI_THEME_MODE",
    ]

    @classmethod
    def save_to_file(cls):
        data = {k: getattr(cls, k) for k in cls._PERSIST_KEYS}
        with open(RUNTIME_CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    @classmethod
    def load_from_file(cls):
        try:
            with open(RUNTIME_CONFIG_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            for key, value in data.items():
                if key in cls._PERSIST_KEYS:
                    desired_type = _FIELD_TYPES.get(key, str)
                    try:
                        if desired_type == int:
                            setattr(cls, key, int(float(value)))
                        elif desired_type == float:
                            setattr(cls, key, float(value))
                        else:
                            setattr(cls, key, str(value))
                    except (TypeError, ValueError):
                        # 单个字段损坏只跳过该字段，不导致整个程序启动崩溃
                        logger.warning("配置项 %s 解析失败，保留默认值: %r", key, value)
        except (FileNotFoundError, json.JSONDecodeError):
            pass

    @classmethod
    def to_dict(cls):
        return {k: getattr(cls, k) for k in cls._PERSIST_KEYS}

    @classmethod
    def update(cls, data):
        for key, value in data.items():
            if key in cls._PERSIST_KEYS:
                desired_type = _FIELD_TYPES.get(key, str)
                try:
                    if desired_type == int:
                        setattr(cls, key, int(float(value)))
                    elif desired_type == float:
                        setattr(cls, key, float(value))
                    else:
                        setattr(cls, key, str(value))
                except (TypeError, ValueError):
                    logger.warning("配置项 %s 更新失败，保留原值: %r", key, value)


config = Config()
runtime = RuntimeConfig()
runtime.load_from_file()
