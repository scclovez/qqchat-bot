# -*- coding: utf-8 -*-
"""项目路径与启动引导：把各中文功能目录加入 sys.path，并提供项目根目录。

所有入口（main.py / gui.py）在导入业务模块前先 import 本模块，
这样移动进中文文件夹的模块仍能以原名（如 config、qq_bot）被导入。

打包（PyInstaller）支持：
- CODE_ROOT：只读代码/资源根。未打包 = 项目目录；打包后 = 解压临时目录 sys._MEIPASS
- DATA_ROOT：可写数据根。未打包 = 项目目录；打包后 = exe 所在目录（.env / 数据库 /
  配置 / 日志 / 生成图片 / 语音缓存 等可写数据放这里，随 exe 一起分发）
"""
import logging
import os
import sys


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def _code_root() -> str:
    if is_frozen():
        return sys._MEIPASS  # noqa: SLF001  PyInstaller 解压目录（只读）
    return os.path.dirname(os.path.abspath(__file__))


def _data_root() -> str:
    if is_frozen():
        d = os.path.dirname(sys.executable)
        # 若 exe 位于源码项目的子目录（如 dist/），上一级就是项目根（含 晚晚/配置）：
        # 直接共用源码的个人数据（.env / 数据库 / 配置），不在 exe 旁另建一份
        parent = os.path.dirname(d)
        if os.path.isdir(os.path.join(parent, "晚晚", "配置")):
            return parent
        return d  # 独立分发时兜底用 exe 所在目录
    # 测试隔离：设置了 DSH_DATA_ROOT 时（如 测试/test_all.py 自建一次性临时目录），
    # 可写数据根指向它，防止测试读写/污染真实数据（.env / 数据库 / 配置 / 日志）。
    env_root = os.environ.get("DSH_DATA_ROOT")
    if env_root:
        return os.path.abspath(env_root)
    return os.path.dirname(os.path.abspath(__file__))


CODE_ROOT = _code_root()
DATA_ROOT = _data_root()

# 兼容旧引用：旧代码用 PROJECT_ROOT 读资源（打包后自动落到 _MEIPASS）
PROJECT_ROOT = CODE_ROOT


def code_path(*parts) -> str:
    """只读资源路径（打包后 = _MEIPASS 内的资源）。"""
    return os.path.join(CODE_ROOT, *parts)


def data_path(*parts) -> str:
    """可写数据路径（打包后 = exe 旁；未打包 = 项目目录内）。"""
    return os.path.join(DATA_ROOT, *parts)


def ensure_runtime_data():
    """启动时数据准备（入口在 import 业务模块前调用）。

    用户明确要求：exe 不做任何配置/数据准备，直接用源码目录（项目根）已有的
    个人数据（.env / llm_providers.json / 数据库 / snowluma 等），绝不复制、
    不创建、不覆盖。因此这里对打包模式同样零操作。
    """
    return


# 所有中文功能目录（按需加入 sys.path）
FEATURE_DIRS = [
    "晚晚",
    "晚晚/配置", "晚晚/界面", "晚晚/人设", "晚晚/对话", "晚晚/用量", "晚晚/图片", "晚晚/外貌",
    "晚晚/机器人", "晚晚/客户端", "晚晚/实时", "晚晚/工具", "晚晚/语音",
    "晚晚/成长", "晚晚/数据", "晚晚/空间", "晚晚/互动", "snowluma",
]

for _d in FEATURE_DIRS:
    _p = os.path.join(CODE_ROOT, _d)
    if os.path.isdir(_p) and _p not in sys.path:
        sys.path.insert(0, _p)
