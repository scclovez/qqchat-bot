# -*- coding: utf-8 -*-
"""多 LLM 提供商管理（模仿 Harness 的模型设置）：配置文件 + 读写 + 模块分派。

配置文件 `晚晚/配置/llm_providers.json`（v2 格式）：
{
  "providers": [
    {"id": "deepseek", "name": "DeepSeek", "type": "deepseek",
     "base_url": "", "api_key": "", "model": "", "active": true},
    {"id": "local", "name": "本地 Ollama", "type": "openai",
     "base_url": "http://127.0.0.1:11434/v1", "api_key": "", "model": "qwen2.5:7b", "active": false}
  ],
  "assignments": {
    "chat":   {"provider": "", "model": ""},
    "task":   {"provider": "", "model": ""},
    "vision": {"provider": "", "model": ""}
  }
}

type:
- deepseek：DeepSeek 官方（字段留空时自动回退用 .env 里的 DEEPSEEK_*）
- openai：任意 OpenAI 兼容服务（本地 Ollama / LM Studio / vLLM / one-api 等）

模块分派（assignments）——不同模块用不同提供商/模型：
- chat   对话：聊天回复、主动消息、碎碎念、语音小尾巴等"她说的话"
- task   文字任务：成长（日记/演化）、空间（说说/评论/动态）、记忆提炼/摘要、图片提示词工程
- vision 看图：图片识别、发图评论、外貌总结、空间配图检查（视觉任务）
provider 留空 = 用当前激活的提供商；model 留空 = 用该提供商的默认模型
（vision 且 deepseek 类型时默认用 DEEPSEEK_VISION_MODEL）
"""
import json
import logging
import os
import uuid

from config import config
from 路径 import data_path

logger = logging.getLogger(__name__)

PROVIDERS_FILE = data_path("晚晚", "配置", "llm_providers.json")

# 首次运行默认：一个 DeepSeek 提供商（回退用 .env 的 key）
DEFAULT_PROVIDERS = [
    {"id": "deepseek", "name": "DeepSeek", "type": "deepseek",
     "base_url": "", "api_key": "", "model": "", "active": True},
]

# 模块分类（GUI"模块分派"用）
CATEGORIES = {
    "chat": "对话（聊天回复）",
    "task": "文字任务（成长/空间/记忆/提示词工程）",
    "vision": "看图（图片识别/评论/外貌总结）",
}

# 默认分派：全部用当前激活的提供商（空 = 激活）
DEFAULT_ASSIGNMENTS = {
    "chat": {"provider": "", "model": ""},
    "task": {"provider": "", "model": ""},
    "vision": {"provider": "", "model": ""},
}

# 添加模板（GUI"添加提供商"下拉）：自动带默认地址
TEMPLATES = {
    "deepseek": {"label": "DeepSeek 官方", "name": "DeepSeek", "type": "deepseek",
                 "base_url": "", "model": "deepseek-v4-flash"},
    "ollama": {"label": "本地 Ollama", "name": "本地 Ollama", "type": "openai",
               "base_url": "http://127.0.0.1:11434/v1", "model": ""},
    "lmstudio": {"label": "LM Studio", "name": "LM Studio", "type": "openai",
                 "base_url": "http://127.0.0.1:1234/v1", "model": ""},
    "vllm": {"label": "vLLM", "name": "vLLM", "type": "openai",
             "base_url": "http://127.0.0.1:8000/v1", "model": ""},
    "oneapi": {"label": "One-API / 中转", "name": "One-API 中转", "type": "openai",
               "base_url": "", "model": ""},
    "custom": {"label": "自定义 OpenAI 兼容", "name": "自定义模型", "type": "openai",
               "base_url": "", "model": ""},
}


def _sanitize(p):
    """清洗单个提供商字段（防止旧数据/手改损坏）。"""
    p = dict(p or {})
    pid = str(p.get("id") or "").strip()
    return {
        "id": pid or "p" + uuid.uuid4().hex[:8],
        "name": str(p.get("name") or pid or "未命名").strip(),
        "type": "deepseek" if p.get("type") == "deepseek" else "openai",
        "base_url": str(p.get("base_url") or "").strip(),
        "api_key": str(p.get("api_key") or "").strip(),
        "model": str(p.get("model") or "").strip(),
        "active": bool(p.get("active")),
    }


def _defaults_assignments():
    return {k: dict(v) for k, v in DEFAULT_ASSIGNMENTS.items()}


def _read_file() -> dict:
    """读取整个配置文件（v2: {providers, assignments}）；兼容旧版纯列表。"""
    try:
        with open(PROVIDERS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            providers = [_sanitize(p) for p in (data.get("providers") or [])]
            assignments = dict(DEFAULT_ASSIGNMENTS)
            for k, v in (data.get("assignments") or {}).items():
                if k in assignments and isinstance(v, dict):
                    assignments[k] = {
                        "provider": str(v.get("provider") or "").strip(),
                        "model": str(v.get("model") or "").strip(),
                    }
            if providers:
                return {"providers": providers, "assignments": assignments}
        elif isinstance(data, list) and data:
            # 旧版纯列表 → 迁移为 v2
            return {"providers": [_sanitize(p) for p in data],
                    "assignments": _defaults_assignments()}
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        pass
    return {"providers": [_sanitize(p) for p in DEFAULT_PROVIDERS],
            "assignments": _defaults_assignments()}


def _write_file(data: dict):
    providers = [_sanitize(p) for p in data.get("providers") or []]
    if not providers:
        providers = [_sanitize(p) for p in DEFAULT_PROVIDERS]
    active = [p for p in providers if p.get("active")]
    if len(active) != 1:
        for i, p in enumerate(providers):
            p["active"] = (i == 0)
    data = {"providers": providers, "assignments": data.get("assignments") or _defaults_assignments()}
    with open(PROVIDERS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


# =============================================================================
# 提供商 CRUD
# =============================================================================

def load_providers() -> list:
    """读取全部提供商；文件缺失/损坏时返回默认（DeepSeek）。"""
    return _read_file()["providers"]


def save_providers(providers: list):
    data = _read_file()
    data["providers"] = providers
    _write_file(data)


def get_active_provider() -> dict:
    """当前激活的提供商（也是 chat 分派的兜底）；没有激活项时自动设第一个。"""
    providers = load_providers()
    if not any(p.get("active") for p in providers):
        providers[0]["active"] = True
        save_providers(providers)
    return next((p for p in providers if p.get("active")), providers[0])


def set_active(pid: str):
    """把指定 id 设为当前激活（写入文件）。"""
    providers = load_providers()
    for p in providers:
        p["active"] = (p.get("id") == pid)
    save_providers(providers)


def add_provider(name, ptype, base_url="", api_key="", model="") -> dict:
    """新增一个提供商并保存；返回新提供商。"""
    providers = load_providers()
    if not any(p.get("active") for p in providers):
        for p in providers:
            p["active"] = False
    new = _sanitize({
        "id": "p" + uuid.uuid4().hex[:8],
        "name": name, "type": ptype,
        "base_url": base_url, "api_key": api_key, "model": model,
        "active": False,
    })
    providers.append(new)
    save_providers(providers)
    return new


def remove_provider(pid: str):
    """删除一个提供商；删除激活项后自动把第一个设为激活。"""
    providers = [p for p in load_providers() if p.get("id") != pid]
    save_providers(providers)


def update_provider(pid: str, name=None, base_url=None, api_key=None, model=None):
    """原地更新一个提供商（None 表示不改该字段）。"""
    providers = load_providers()
    for p in providers:
        if p.get("id") == pid:
            if name is not None:
                p["name"] = str(name).strip()
            if base_url is not None:
                p["base_url"] = str(base_url).strip()
            if api_key is not None:
                p["api_key"] = str(api_key).strip()
            if model is not None:
                p["model"] = str(model).strip()
            break
    save_providers(providers)


# =============================================================================
# 模块分派
# =============================================================================

def get_assignment(category: str) -> dict:
    """某模块的分派（{"provider": id或"", "model": ""}）。"""
    data = _read_file()
    return data["assignments"].get(category) or dict(DEFAULT_ASSIGNMENTS.get(category, {}))


def set_assignment(category: str, provider_id: str, model: str = ""):
    """设置某模块用哪个提供商（model 可单独指定；空 = 用提供商默认模型）。"""
    data = _read_file()
    assignments = data.get("assignments") or _defaults_assignments()
    assignments[category] = {
        "provider": str(provider_id or "").strip(),
        "model": str(model or "").strip(),
    }
    data["assignments"] = assignments
    _write_file(data)


def resolve(category: str):
    """解析某模块实际使用的 (提供商, 模型名)。

    - 分派指定了提供商 → 用它；否则用当前激活的提供商
    - 模型：分派 model > 提供商默认 model > deepseek 官方默认/视觉默认
    """
    data = _read_file()
    providers = data["providers"]
    if not providers:
        providers = [_sanitize(p) for p in DEFAULT_PROVIDERS]
    ass = (data.get("assignments") or {}).get(category) or {}
    provider = next((p for p in providers if p.get("id") == ass.get("provider")), None)
    if provider is None:
        provider = next((p for p in providers if p.get("active")), providers[0])
    model = str(ass.get("model") or "").strip() or str(provider.get("model") or "").strip()
    if not model and provider.get("type") == "deepseek":
        if category == "vision":
            model = config.DEEPSEEK_VISION_MODEL
        else:
            model = config.DEEPSEEK_MODEL
    return provider, model
