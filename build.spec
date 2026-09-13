# -*- mode: python ; coding: utf-8 -*-
"""bot控制面板 — PyInstaller onefile 打包配置。

用法（在项目根运行）：
    python -m PyInstaller build.spec --noconfirm --clean
    产物：dist/bot控制面板.exe（101MB 左右）

要点：
- 入口 gui.py（顶层，指向 PySide6 版）
- 内置 node.exe（SnowLuma 运行时用，路径可改下方 BINARIES）
- 晚晚/ 与 snowluma/ 精挑资源（排除数据库/日志/缓存等运行数据）
- 配置文件（.env / llm_providers.json）存在才打包——开发版没有时跳过，
  exe 首次运行会读取 exe 旁的 .env（用户自建）
- exe 在 dist/ 子目录下运行时，数据根自动指向上一级源码项目（路径.py 逻辑），
  与源码版共用同一份个人数据
"""

import os

ROOT = os.path.abspath(os.getcwd())

FEATURE_DIRS = [
    "晚晚/配置", "晚晚/界面", "晚晚/人设", "晚晚/对话", "晚晚/用量", "晚晚/图片", "晚晚/外貌",
    "晚晚/机器人", "晚晚/客户端", "晚晚/实时", "晚晚/工具", "晚晚/语音",
    "晚晚/成长", "晚晚/数据", "晚晚/空间", "晚晚/互动", "晚晚", "snowluma",
]
PATHEX = [ROOT] + [os.path.join(ROOT, d) for d in FEATURE_DIRS]

# 中文目录模块（PyInstaller 分析不到动态 sys.path，显式列出）
HIDDEN = [
    "PySide6.QtMultimedia",
    "config", "llm_providers", "personality", "liveness", "emotion_state", "boundary",
    "conversation", "dialogue_policy", "episodic_memory", "memory", "usage", "image_gen", "comfyui_client",
    "appearance_ref", "qq_bot", "llm_base", "openai_compat",
    "deepseek_client", "llm_factory", "live_info", "life_state", "singleton", "tts",
    "asr", "diary", "personality_state", "evolution", "evolution_db",
    "active_pull", "qzone", "interact_tools", "sqlite_runtime", "tray",
    "assistant_db", "behavior_profile", "schedule_manager", "goal_manager", "study_session",
    "image_verifier", "emotion_history",
    "语音.asr", "语音.tts", "图片.image_gen", "图片.comfyui_client",
    "配置.config", "配置.llm_providers", "界面.gui_qt", "界面.tray",
    "人设.personality", "人设.liveness", "人设.emotion_state", "人设.boundary",
    "对话.conversation", "对话.dialogue_policy", "对话.episodic_memory", "对话.memory", "机器人.qq_bot",
    "客户端.llm_factory", "客户端.deepseek_client", "客户端.openai_compat",
    "客户端.llm_base", "实时.live_info", "实时.life_state", "工具.singleton", "用量.usage",
    "空间.qzone", "互动.interact_tools", "工具.sqlite_runtime", "外貌.appearance_ref",
    "成长.diary", "成长.personality_state", "成长.evolution",
    "成长.evolution_db", "成长.active_pull",
    "助理.assistant_db", "助理.behavior_profile", "助理.schedule_manager",
    "助理.goal_manager", "助理.study_session", "助理.image_verifier", "助理.emotion_history",
]

DATAS = [
    ("晚晚/界面/icon.ico", "晚晚/界面"),
    ("晚晚/图片/comfy_workflow.json", "晚晚/图片"),
    ("snowluma/index.mjs", "snowluma"),
    ("snowluma/check-node-version.cjs", "snowluma"),
    ("snowluma/decode_voice.cjs", "snowluma"),
    ("snowluma/EULA.md", "snowluma"),
    ("snowluma/PRIVACY.md", "snowluma"),
    ("snowluma/config-DwoxthVc.js", "snowluma"),
    ("snowluma/package-lock.json", "snowluma"),
    ("snowluma/package.json", "snowluma"),
    ("snowluma/server-D5vO0tcc.js", "snowluma"),
    ("snowluma/utils-tSVKpzEf.js", "snowluma"),
    ("snowluma/client", "snowluma/client"),
    ("snowluma/config", "snowluma/config"),
    ("snowluma/native", "snowluma/native"),
]
# 配置文件存在才打包（开发版没有 .env 时跳过，不影响打包）
for _f in (".env", ".runtime_config.json", "llm_providers.json"):
    _src = os.path.join("晚晚", "配置", _f)
    if os.path.isfile(_src):
        DATAS.append((_src, "晚晚/配置"))

BINARIES = [
    # node.exe 内置路径（SnowLuma 运行时用；如本机 node 位置不同请修改）
    ("C:/Program Files/nodejs/node.exe", "."),
]

a = Analysis(
    ["gui.py"],
    pathex=PATHEX,
    binaries=BINARIES,
    datas=DATAS,
    hiddenimports=HIDDEN,
    hookspath=[],
    runtime_hooks=[],
    excludes=["tkinter", "customtkinter", "PIL", "matplotlib", "numpy", "pandas", "scipy"],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="bot控制面板",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon="晚晚/界面/icon.ico",
)
