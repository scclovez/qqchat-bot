# -*- coding: utf-8 -*-
"""开发环境综合测试：一键验证所有模块可导入、配置可加载、成长系统正常、GUI 可构建。

用法（在 开发 项目根运行）：
    python 测试/test_all.py

测试项：
    1. 全部业务模块导入（config / memory / qq_bot / tts / image_gen ...）
    2. 配置加载（.env 与 llm_providers.json 若存在则校验；纯开发版可跳过）
    3. 成长系统（性格阶段 / 淫乱度档位 / 称号 计算不抛异常）
    4. 提供商配置读写（llm_providers.json 可读写）
    5. GUI 冒烟（PySide6 离屏构建 5 个页面，不弹窗）
"""
import atexit
import os
import pathlib
import shutil
import sys
import tempfile
import time
import traceback

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, ROOT)

# 数据隔离：把可写数据根指到一次性临时目录后再 import 路径/业务模块，
# 防止测试读写、污染真实数据（.env / bot_memory.db / llm_providers.json / 日志等）。
# 以前测试直接落在项目目录（priv 版里因此留下 testuser/_tmp_test_* 残留）。
# 注意：不能用 tempfile.mkdtemp 后再建中文子目录（受限环境实测 WinError 5），
# 因此用单次 makedirs 直接创建多级目录。临时目录退出时自动删除。
_TMP_BASE = os.path.join(ROOT, ".dsh_test_data_tmp")
_TEST_DATA = os.path.join(_TMP_BASE, f"data_{os.getpid()}_{int(time.time() * 1000)}")
os.environ["DSH_DATA_ROOT"] = _TEST_DATA
for _sub in ("晚晚", "配置"), ("晚晚", "数据"), ("晚晚", "用量"), ("晚晚", "图片"):
    os.makedirs(os.path.join(_TEST_DATA, *_sub), exist_ok=True)


def _cleanup_test_data():
    shutil.rmtree(_TEST_DATA, ignore_errors=True)
    try:
        os.rmdir(_TMP_BASE)  # 空则删，非空（残留）保留待查
    except OSError:
        pass


atexit.register(_cleanup_test_data)

# Windows 控制台默认 GBK：让 stdout/stderr 按 UTF-8 输出，
# 否则结尾 print("全部通过 ✔") 会抛 UnicodeEncodeError 导致退出码 1（曾实测 5/5 通过却报失败）。
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

import 路径  # noqa: E402

PASS = []
FAIL = []


def check(name, fn):
    try:
        fn()
        PASS.append(name)
        print(f"  [PASS] {name}")
    except Exception as e:
        FAIL.append((name, e))
        print(f"  [FAIL] {name}: {e}")
        traceback.print_exc()


def test_imports():
    mods = [
        "config", "llm_providers", "personality", "liveness", "boundary",
        "conversation", "memory", "usage", "image_gen", "comfyui_client",
        "appearance_ref", "qq_bot", "llm_base", "openai_compat",
        "deepseek_client", "llm_factory", "live_info", "singleton", "tts",
        "asr", "diary", "personality_state", "evolution", "evolution_db",
        "active_pull", "qzone", "interact_tools", "tray",
    ]
    for m in mods:
        __import__(m)
    print(f"      已导入 {len(mods)} 个模块")


def test_config():
    from config import config, runtime
    runtime.load_from_file()
    assert config.DEEPSEEK_BASE_URL, "DeepSeek base url 缺失"
    print(f"      DeepSeek 模型: {config.DEEPSEEK_MODEL}")


def test_growth():
    import personality_state as pstate
    assert pstate.get_stage() >= 1
    assert pstate.stage_name()
    assert pstate.lewdness_tier_name() in ("害羞", "主动", "放开")
    assert pstate.get_title() is not None
    inj = pstate.build_injection()
    assert isinstance(inj, str) and inj


def test_providers():
    from llm_providers import load_providers, save_providers
    ps = load_providers()
    assert isinstance(ps, list)
    # 读回写（不改变内容）
    save_providers(ps)
    print(f"      提供商数量: {len(ps)}")


def test_gui():
    """GUI 冒烟：真实桌面构建 5 页 + 成长网格，1.5 秒后自动退出（不阻塞）。"""
    from PySide6.QtWidgets import QApplication
    from PySide6.QtCore import QTimer
    import gui_qt
    app = QApplication([])
    win = gui_qt.MainWindow()
    win.show()
    result = {}

    def verify():
        try:
            result["tabs"] = win.tabs.count()
            result["growth"] = len(win._growth_labels)
            win._refresh_growth_stats()
            app.processEvents()
            win.close()
        finally:
            app.quit()

    QTimer.singleShot(1500, verify)
    app.exec()
    assert result.get("tabs") == 5, "应有 5 个顶级页"
    assert result.get("growth") == 16, "成长网格应为 16 格（4×4）"
    print("      GUI 5 页构建 OK，成长网格 16 格 OK")


def main():
    print("=" * 50)
    print("  AI 电子女友 · 开发环境测试")
    print("=" * 50)
    print("[1/5] 模块导入")
    check("模块导入", test_imports)
    print("[2/5] 配置加载")
    check("配置加载", test_config)
    print("[3/5] 成长系统")
    check("成长系统", test_growth)
    print("[4/5] 提供商配置")
    check("提供商配置", test_providers)
    print("[5/5] GUI 冒烟")
    check("GUI 冒烟", test_gui)
    print("=" * 50)
    print(f"结果: {len(PASS)} 通过 / {len(FAIL)} 失败")
    if FAIL:
        for name, e in FAIL:
            print(f"  失败项: {name} -> {e}")
        sys.exit(1)
    print("全部通过 ✔")


if __name__ == "__main__":
    main()
