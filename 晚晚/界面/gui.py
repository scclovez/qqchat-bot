# -*- coding: utf-8 -*-
"""
功能：
  * 首页仪表盘：检测 QQ 是否安装及版本（支持自定义路径）、一键启动/停止 SNOWLUMA、启动/停止 AI Bot
  * 人设编辑、API 参数、设置、Token 用量、运行日志
"""
import ctypes
import importlib.util
import json
import logging
import os
import queue
import shutil
import subprocess
import threading
import tkinter as tk
from tkinter import ttk, messagebox
from pathlib import Path

# ---------------------------------------------------------------------------
# 启动前检测必需 Python 模块（在导入任何第三方模块之前执行）
# ---------------------------------------------------------------------------
REQUIRED_MODULES = ["customtkinter", "dotenv", "openai", "websockets", "pydantic", "httpx"]


class ToolTip:
    """简易悬停提示：鼠标悬停在控件上显示说明小窗，移开自动消失。"""

    def __init__(self, widget, text):
        self.widget = widget
        self.text = text
        self._tip = None
        widget.bind("<Enter>", self._show, add="+")
        widget.bind("<Leave>", self._hide, add="+")

    def _show(self, _event=None):
        if self._tip is not None or not self.text:
            return
        x = self.widget.winfo_rootx() + 18
        y = self.widget.winfo_rooty() + 26
        self._tip = tk.Toplevel(self.widget)
        self._tip.wm_overrideredirect(True)
        self._tip.wm_geometry(f"+{x}+{y}")
        self._tip.attributes("-topmost", True)
        label = tk.Label(self._tip, text=self.text, justify="left",
                         bg=TIP_BG, fg=TIP_FG,
                         font=("Microsoft YaHei UI", 10),
                         padx=10, pady=7, wraplength=280)
        label.pack()
        self._tip.update_idletasks()

    def _hide(self, _event=None):
        if self._tip is not None:
            self._tip.destroy()
            self._tip = None


# 成长状态各参数的说明（悬停 ⓘ 显示）
GROWTH_HINTS = {
    "stage": "性格阶段：随相处推进，从礼貌试探 → 热情升温 → 深度绑定，影响她的语气、状态和行为",
    "affection": "亲密度：每聊一句 +1，反映你们关系的亲密程度；到达阈值会进入下一阶段",
    "dependency": "依赖度：她有多依赖你；主动找你聊天、追问、撩人都会增加",
    "jealousy": "醋意倾向：你提到别人、久不回复时会增加，影响她吃醋的表现",
    "lewdness": "淫乱度：亲密互动积累的程度；档位（害羞/主动/放开）影响亲密话题的开放尺度",
    "days": "在一起第几天（从纪念日起始日期算起）",
    "nickname": "她当前对你的称呼，随性格阶段与深度绑定细分变化：你 → 宝 → 老公 → 老公公/亲爱的 → 达令/我的宝",
    "state": "她此刻的状态（在睡觉 / 在画画 / 正在吃饭…）：剧情优先取最近对话里她自述的状态，没有则按时段兜底",
    "memory": "她长期记忆里关于你的事（提炼的事实 + 偏好数量）",
    "chats": "累计聊天的消息条数",
    "mood": "今日情绪标签：今天互动中积累的情绪（吃醋 / 撒娇 / 开心…）",
    "mood_delta": "今日亲密度变化：今天通过聊天涨了多少亲密度",
    "mood_state": "今日心情：情绪低落日 / 闹脾气 / 今天被惹几次等状态",
}


def _missing_modules():
    return [m for m in REQUIRED_MODULES if importlib.util.find_spec(m) is None]


def _split_hours(v) -> tuple:
    v = float(v or 0)
    h = int(v)
    m = int(round((v - h) * 60))
    if m == 60:
        h += 1
        m = 0
    return h, m


def _report_missing_modules():
    missing = _missing_modules()
    if not missing:
        return True
    try:
        root = tk.Tk()
        root.withdraw()
        messagebox.showerror(
            "缺少依赖模块",
            "启动检测到以下 Python 模块未安装：\n\n"
            + "、".join(missing)
            + "\n\n请先安装依赖：\npip install -r requirements.txt customtkinter",
        )
        root.destroy()
    except Exception:
        print("缺少 Python 模块：", ", ".join(missing))
        print("请运行：pip install -r requirements.txt customtkinter")
    return False


if not _report_missing_modules():
    raise SystemExit(1)

import customtkinter as ctk

import tray

from config import config, runtime, RUNTIME_CONFIG_FILE
from personality import build_system_prompt

# 启用 Windows DPI 感知，避免界面模糊
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(1)
except Exception:
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass

ctk.set_appearance_mode("light")
ctk.set_default_color_theme("blue")

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------
from 路径 import PROJECT_ROOT
BASE_DIR = PROJECT_ROOT
GUI_LOCK_FILE = os.path.join(BASE_DIR, "gui.lock")
SNOWLUMA_DIR = os.path.join(BASE_DIR, "snowluma")

# 配色（天空蓝浅色主题；如需恢复亚克力透明，可调用 _apply_acrylic_theme(root)）
BG = "#f3f6fa"            # 主背景（浅灰蓝白）
CARD = "#ffffff"          # 卡片白
PANEL = "#e9eef5"         # 面板浅灰蓝
INPUT_BG = "#ffffff"      # 输入框白底
PRIMARY = "#3d7fd9"       # 天空蓝（白底可读）
PRIMARY_HOVER = "#3369b8" # 悬停加深蓝
PRIMARY_DARK = "#2b5a9e"  # 更深蓝（强调）
PRIMARY_LIGHT = "#e1edfa" # 浅蓝点缀
TEXT = "#1f2328"          # 近黑文字
MUTED = "#64748b"         # 灰蓝文字
BORDER = "#d3dbe6"        # 灰蓝边框
SUCCESS = "#16a34a"
WARNING = "#d97706"
DANGER = "#dc2626"
HEADER_BG = "#ffffff"     # 标题栏白
HEADER_BG2 = "#e1edfa"    # 浅蓝点缀

# 浅蓝辅助色：按钮/滑块/格子/提示图标统一用这一套
SOFT_BG = "#eaf2fb"       # 浅蓝底（按钮底色）
SOFT_HOVER = "#d8e8f7"    # 浅蓝悬停
TRACK = "#d3e3f5"         # 滑块轨道
CELL_BG = "#f2f7fc"       # 成长状态格子
CELL_BORDER = "#dbe9f8"   # 格子边框
HINT = "#7fa8d9"          # ⓘ 提示图标
LIST_BTN = "#eef4fb"      # 模型列表按钮
LIST_BTN_HOVER = "#e0ecf9"
TIP_BG = "#2d3748"        # 悬停提示底色（深蓝灰）
TIP_FG = "#f0f6ff"        # 悬停提示文字


TRANSPARENT_KEY = "#010203"


def _enable_acrylic(hwnd) -> bool:
    """为窗口开启 Windows 亚克力模糊背景；失败返回 False。

    优先级：
      1. SetWindowCompositionAttribute(ACCENT_ENABLE_ACRYLICBLURBEHIND) — Win10 1803+ / Win11
      2. DwmSetWindowAttribute(DWMWA_SYSTEMBACKDROP_TYPE=TRANSIENTWINDOW) — Win11 22H2+
      3. DwmEnableBlurBehindWindow — 旧版 Win10
    全部失败时由调用方退化为整体 alpha 半透明。
    """
    if os.name != "nt" or not hwnd:
        return False
    try:
        # 颜色键：让 TRANSPARENT_KEY 像素完全透明（露出亚克力）
        GWL_EXSTYLE = -20
        WS_EX_LAYERED = 0x00080000
        LWA_COLORKEY = 1
        ex = ctypes.windll.user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
        ctypes.windll.user32.SetWindowLongW(hwnd, GWL_EXSTYLE, ex | WS_EX_LAYERED)
        rr = int(TRANSPARENT_KEY[1:3], 16)
        gg = int(TRANSPARENT_KEY[3:5], 16)
        bb = int(TRANSPARENT_KEY[5:7], 16)
        colorref = (bb << 16) | (gg << 8) | rr  # COLORREF 顺序是 0x00BBGGRR
        ctypes.windll.user32.SetLayeredWindowAttributes(hwnd, colorref, 0, LWA_COLORKEY)
    except Exception:
        pass

    try:
        class ACCENT_POLICY(ctypes.Structure):
            _fields_ = [
                ("nAccentState", ctypes.c_int),
                ("nFlags", ctypes.c_int),
                ("nColor", ctypes.c_int),   # ABGR，高位字节为透明度
                ("nAnimationId", ctypes.c_int),
            ]

        class WINDOWCOMPOSITIONATTRIBDATA(ctypes.Structure):
            _fields_ = [
                ("dwAttrib", ctypes.c_int),
                ("pvData", ctypes.POINTER(ACCENT_POLICY)),
                ("cbData", ctypes.c_size_t),
            ]

        accent = ACCENT_POLICY(4, 0, 0xCC25291E, 0)
        data = WINDOWCOMPOSITIONATTRIBDATA(19, ctypes.pointer(accent), ctypes.sizeof(accent))
        setter = ctypes.windll.user32.SetWindowCompositionAttribute
        setter.argtypes = [ctypes.c_void_p, ctypes.POINTER(WINDOWCOMPOSITIONATTRIBDATA)]
        if setter(hwnd, ctypes.byref(data)):
            return True
    except Exception:
        pass

    try:
        value = ctypes.c_int(3)
        hr = ctypes.windll.dwmapi.DwmSetWindowAttribute(
            hwnd, 38, ctypes.byref(value), ctypes.sizeof(value))
        if hr >= 0:  # SUCCEEDED(hr)
            return True
    except Exception:
        pass

    try:
        class DWM_BLURBEHIND(ctypes.Structure):
            _fields_ = [
                ("dwFlags", ctypes.c_uint32),
                ("fEnable", ctypes.c_int),
                ("hRgnBlur", ctypes.c_void_p),
                ("fTransitionOnMaximized", ctypes.c_int),
            ]

        bb = DWM_BLURBEHIND(1, 1, None, 0)  # DWM_BB_ENABLE = 1
        if ctypes.windll.dwmapi.DwmEnableBlurBehindWindow(hwnd, ctypes.byref(bb)) == 0:
            return True
    except Exception:
        pass
    return False


def _apply_acrylic_theme(root) -> bool:
    """尝试应用亚克力主题；成功则把背景色常量换成透明键并返回 True。"""
    global BG, CARD, PANEL
    if os.name != "nt":
        return False
    try:
        hwnd = ctypes.windll.user32.GetParent(root.winfo_id())
    except Exception:
        return False
    if not _enable_acrylic(hwnd):
        return False
    try:
        root.wm_attributes("-transparentcolor", TRANSPARENT_KEY)
    except Exception:
        return False
    BG = CARD = PANEL = TRANSPARENT_KEY
    return True


# ---------------------------------------------------------------------------
# 工具函数
# ---------------------------------------------------------------------------
def _get_file_version(path):
    """通过 PowerShell 读取 exe 的版本号。"""
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             f"(Get-Item -LiteralPath '{path}').VersionInfo.FileVersion"],
            capture_output=True, text=True, timeout=10,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        return out.stdout.strip()
    except Exception:
        return ""


def _registry_qq_paths():
    """通过 Windows 注册表查找 QQ 安装路径，返回可能的 QQ.exe 候选列表。"""
    import winreg
    candidates = []

    def add(raw):
        if not raw:
            return
        raw = raw.strip().strip('"')
        if not raw:
            return
        p = Path(raw)
        if p.suffix.lower() == ".exe" and p.name.lower() == "qq.exe":
            if p.exists():
                candidates.append(str(p))
            return
        for rel in ("QQ.exe", "Bin/QQ.exe", "QQNT/QQ.exe", "QQ/QQ.exe", "Tencent/QQ/Bin/QQ.exe"):
            c = p / rel
            if c.exists():
                candidates.append(str(c))

    reg_keys = [
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Tencent\QQ"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Tencent\QQ"),
        (winreg.HKEY_CURRENT_USER, r"SOFTWARE\Tencent\QQ"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Tencent\QQNT"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Tencent\QQNT"),
        (winreg.HKEY_CURRENT_USER, r"SOFTWARE\Tencent\QQNT"),
    ]
    value_names = ["Install", "InstallPath", "InstallDir", "Default", "InstallLocation",
                   "Path", "AppPath", "Location"]
    for hive, key in reg_keys:
        try:
            k = winreg.OpenKey(hive, key)
        except OSError:
            continue
        try:
            for vn in value_names:
                try:
                    val, _ = winreg.QueryValueEx(k, vn)
                    add(val)
                except OSError:
                    pass
            try:
                add(winreg.QueryValue(k, None))  # 默认值
            except OSError:
                pass
        finally:
            winreg.CloseKey(k)

    uninstall_roots = [
        r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall",
        r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall",
    ]
    for hive in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
        for root in uninstall_roots:
            try:
                k = winreg.OpenKey(hive, root)
            except OSError:
                continue
            try:
                i = 0
                while True:
                    try:
                        sub = winreg.EnumKey(k, i)
                        i += 1
                    except OSError:
                        break
                    try:
                        sk = winreg.OpenKey(k, sub)
                        try:
                            try:
                                name, _ = winreg.QueryValueEx(sk, "DisplayName")
                            except OSError:
                                name = ""
                            if name and "QQ" in name:
                                for vn in ("InstallLocation", "DisplayIcon", "UninstallString"):
                                    try:
                                        val, _ = winreg.QueryValueEx(sk, vn)
                                        add(val)
                                    except OSError:
                                        pass
                        finally:
                            winreg.CloseKey(sk)
                    except OSError:
                        continue
            finally:
                winreg.CloseKey(k)

    # 去重
    return list(dict.fromkeys(candidates))


def detect_qq():
    """检测本机是否安装 QQ，返回 (是否安装, 路径, 版本号)。

    检测方式：
      1. 注册表（腾讯 QQ / QQNT / 卸载信息）——最可靠
      2. 常见安装目录兜底
    """
    seen = set()

    def check(path):
        p = Path(path)
        key = str(p).lower()
        if key in seen:
            return None
        seen.add(key)
        if p.exists():
            return str(p)
        return None

    for path in _registry_qq_paths():
        found = check(path)
        if found:
            return True, found, _get_file_version(found)

    candidates = []
    for env in ("LOCALAPPDATA", "PROGRAMFILES", "PROGRAMFILES(X86)", "ProgramW6432", "APPDATA"):
        v = os.environ.get(env)
        if v:
            candidates.append(Path(v))

    patterns = [
        "Tencent/QQNT/QQ.exe",
        "Tencent/QQNT/QQ/QQ.exe",
        "Tencent/QQ/Bin/QQ.exe",
        "Tencent/QQ/QQ.exe",
    ]
    for base in candidates:
        for pat in patterns:
            found = check(base / pat)
            if found:
                return True, found, _get_file_version(found)

    return False, "", ""


# ---------------------------------------------------------------------------
# 日志处理器
# ---------------------------------------------------------------------------
class LogQueueHandler(logging.Handler):
    """将日志推送到线程安全队列，供 GUI 轮询显示。"""

    def __init__(self, log_queue):
        super().__init__()
        self.log_queue = log_queue
        self.setFormatter(logging.Formatter(
            "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
            datefmt="%H:%M:%S",
        ))

    def emit(self, record):
        self.log_queue.put(self.format(record))


# ---------------------------------------------------------------------------
# 主界面
# ---------------------------------------------------------------------------
class GirlfriendGUI:
    def __init__(self):
        self.root = ctk.CTk()
        self.root.title("控制面板")
        self.root.geometry("1080x780")
        self.root.overrideredirect(True)
        # 默认白底黑字主题（不启用亚克力透明；如需恢复：_apply_acrylic_theme(self.root)）
        self.root.configure(fg_color=BG)
        self._drag_x = 0
        self._drag_y = 0

        self.log_queue = queue.Queue()
        self._setup_logging()

        self._text_widgets = {}
        self._usage_vars = {}
        self._bot_thread = None
        self._bot_task = None
        self._loop = None
        self._stop_event = threading.Event()

        self._snowluma_proc = None
        self._snowluma_output_thread = None

        self._build_ui()
        ico = self._app_icon_path()
        if ico:
            try:
                self.root.iconbitmap(ico)
            except Exception:
                pass
        self._tray = None
        self._create_tray()
        self._poll_logs()
        self._poll_usage()
        self._poll_snowluma_status()
        self.root.after(300, lambda: self._refresh_models(silent=True))
        self.root.after(500, self._detect_qq)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        try:
            hwnd = ctypes.windll.user32.GetParent(self.root.winfo_id())
            GWL_EXSTYLE = -20
            WS_EX_APPWINDOW = 0x00040000
            style = ctypes.windll.user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
            ctypes.windll.user32.SetWindowLongW(hwnd, GWL_EXSTYLE, style | WS_EX_APPWINDOW)
        except Exception:
            pass

    def _setup_logging(self):
        handler = LogQueueHandler(self.log_queue)
        root_logger = logging.getLogger()
        root_logger.addHandler(handler)
        if not any(isinstance(h, logging.StreamHandler) for h in root_logger.handlers):
            root_logger.addHandler(logging.StreamHandler())

    # ===================== UI 构建 =====================

    def _build_ui(self):
        header = ctk.CTkFrame(self.root, fg_color=HEADER_BG, corner_radius=0, height=58)
        header.pack(fill="x")
        header.pack_propagate(False)
        self._header = header

        self._header_title = ctk.CTkLabel(header, text="🌸 控制面板",
                                          text_color=TEXT, font=ctk.CTkFont(size=17, weight="bold"),
                                          fg_color="transparent")
        self._header_title.pack(side="left", padx=18)

        ctk.CTkButton(header, text="—", width=36, height=30,
                      fg_color="transparent", hover_color=PRIMARY_HOVER,
                      text_color=TEXT, font=ctk.CTkFont(size=16, weight="bold"),
                      command=self._minimize_window).pack(side="right", padx=(0, 4), pady=12)
        ctk.CTkButton(header, text="✕", width=36, height=30,
                      fg_color="transparent", hover_color=DANGER,
                      text_color=TEXT, font=ctk.CTkFont(size=15, weight="bold"),
                      command=self._on_close).pack(side="right", padx=(0, 12), pady=12)

        # 保存按钮：检测到设置改动后自动显示（_on_setting_changed）
        self._save_btn = ctk.CTkButton(header, text="💾 保存", width=80, height=30,
                                       fg_color=PRIMARY, hover_color=PRIMARY_HOVER,
                                       text_color="white", font=ctk.CTkFont(size=13, weight="bold"),
                                       command=self._save_from_bar)
        self._save_btn.place(relx=1.0, rely=0.5, anchor="e", x=-140)
        self._save_btn.place_forget()

        header.bind("<Button-1>", self._start_move)
        header.bind("<B1-Motion>", self._on_move)
        self._header_title.bind("<Button-1>", self._start_move)
        self._header_title.bind("<B1-Motion>", self._on_move)

        self._notebook = ctk.CTkTabview(self.root, fg_color=BG, segmented_button_fg_color=BG,
                                        segmented_button_selected_color="#ffffff",
                                        segmented_button_selected_hover_color="#e8e8e8",
                                        segmented_button_unselected_color="#f0f0f0",
                                        text_color="#000000",
                                        corner_radius=12)
        self._notebook.pack(fill="both", expand=True, padx=14, pady=14)
        self._resize_handle = tk.Label(self.root, text="◢", bg=HEADER_BG, fg=TEXT,
                                       cursor="size_nw_se", font=("Microsoft YaHei UI", 14))
        self._resize_handle.place(relx=1.0, rely=1.0, anchor="se", x=-2, y=-2)
        self._resize_handle.bind("<Button-1>", self._start_resize)
        self._resize_handle.bind("<B1-Motion>", self._on_resize)

        # 顶级选项卡：首页 / 人设编辑 / 模型与连接 / 设置 / 运行日志
        self._build_dashboard_tab()
        self._build_personality_tab()
        self._build_connection_tab(self._notebook)
        self._build_settings_tab()
        self._build_log_tab()

        # 设置改动检测：任何配置控件被修改 → 显示保存按钮
        self._hook_dirty_tracking()

    def _card(self, parent, title=None):
        card = ctk.CTkFrame(parent, fg_color=CARD, corner_radius=14,
                            border_width=1, border_color=BORDER)
        if title:
            ctk.CTkLabel(card, text=title, text_color=PRIMARY,
                         font=ctk.CTkFont(size=15, weight="bold"),
                         fg_color="transparent").pack(anchor="w", padx=16, pady=(14, 8))
        return card

    # ===================== 首页仪表盘 =====================

    def _build_dashboard_tab(self):
        tab = self._notebook.add("🏠 首页")

        ctk.CTkLabel(tab, text="控制面板",
                     text_color=PRIMARY, font=ctk.CTkFont(size=22, weight="bold"),
                     fg_color="transparent").pack(anchor="w", pady=(0, 16))

        cols = ctk.CTkFrame(tab, fg_color="transparent")
        cols.pack(fill="both", expand=True)
        cols.grid_columnconfigure((0, 1, 2), weight=1, uniform="card")

        # ---------- QQ 检测卡片 ----------
        qq_card = self._card(cols, "QQ")
        qq_card.grid(row=0, column=0, sticky="nsew", padx=(0, 8))

        self._qq_status_var = tk.StringVar(value="未检测")
        self._qq_status_label = ctk.CTkLabel(qq_card, textvariable=self._qq_status_var,
                                             text_color=MUTED, font=ctk.CTkFont(size=14, weight="bold"),
                                             fg_color="transparent")
        self._qq_status_label.pack(anchor="w", padx=16, pady=(2, 8))

        self._qq_version_var = tk.StringVar(value="-")
        self._qq_path_var = tk.StringVar(value="-")

        ctk.CTkLabel(qq_card, text="版本：", text_color=MUTED, font=ctk.CTkFont(size=12),
                     fg_color="transparent").pack(anchor="w", padx=16)
        ctk.CTkLabel(qq_card, textvariable=self._qq_version_var, text_color=TEXT,
                     font=ctk.CTkFont(size=13), fg_color="transparent").pack(anchor="w", padx=16, pady=(0, 8))
        ctk.CTkLabel(qq_card, text="路径：", text_color=MUTED, font=ctk.CTkFont(size=12),
                     fg_color="transparent").pack(anchor="w", padx=16)
        ctk.CTkLabel(qq_card, textvariable=self._qq_path_var, text_color=TEXT,
                     font=ctk.CTkFont(size=12), fg_color="transparent",
                     wraplength=270, justify="left").pack(anchor="w", padx=16, pady=(0, 10))

        ctk.CTkButton(qq_card, text="重新检测", height=34, fg_color=SOFT_BG,
                      hover_color=SOFT_HOVER, text_color=PRIMARY, font=ctk.CTkFont(size=13),
                      command=self._detect_qq).pack(anchor="w", padx=16, pady=(0, 16))

        # ---------- SNOWLUMA 卡片 ----------
        sl_card = self._card(cols, "SNOWLUMA")
        sl_card.grid(row=0, column=1, sticky="nsew", padx=8)

        self._snowluma_status_var = tk.StringVar(value="● 已停止")
        self._snowluma_status_label = ctk.CTkLabel(sl_card, textvariable=self._snowluma_status_var,
                                                   text_color=MUTED, font=ctk.CTkFont(size=14, weight="bold"),
                                                   fg_color="transparent")
        self._snowluma_status_label.pack(anchor="w", padx=16, pady=(2, 8))
        
        self._sl_btn_start = ctk.CTkButton(sl_card, text="▶ 启动", height=38,
                                           fg_color=SUCCESS, hover_color="#278552", text_color="white",
                                           font=ctk.CTkFont(size=14, weight="bold"), command=self._start_snowluma)
        self._sl_btn_start.pack(fill="x", padx=16, pady=(0, 8))
        self._sl_btn_stop = ctk.CTkButton(sl_card, text="■ 停止", height=38,
                                          fg_color=DANGER, hover_color="#c9302c", text_color="white",
                                          font=ctk.CTkFont(size=14, weight="bold"), command=self._stop_snowluma,
                                          state="disabled")
        self._sl_btn_stop.pack(fill="x", padx=16, pady=(0, 8))
        self._sl_btn_restart = ctk.CTkButton(sl_card, text="↻ 重启", height=36,
                                             fg_color=SOFT_BG, hover_color=SOFT_HOVER, text_color=PRIMARY,
                                             font=ctk.CTkFont(size=13, weight="bold"), command=self._restart_snowluma)
        self._sl_btn_restart.pack(fill="x", padx=16, pady=(0, 16))

        # ---------- Bot卡片 ----------
        bot_card = self._card(cols, "bot")
        bot_card.grid(row=0, column=2, sticky="nsew", padx=(8, 0))

        self._status_label = ctk.CTkLabel(bot_card, text="● 已停止", text_color=MUTED,
                                          font=ctk.CTkFont(size=14, weight="bold"), fg_color="transparent")
        self._status_label.pack(anchor="w", padx=16, pady=(2, 8))

        self._btn_start = ctk.CTkButton(bot_card, text="▶ 启动", height=38,
                                        fg_color=SUCCESS, hover_color="#278552", text_color="white",
                                        font=ctk.CTkFont(size=14, weight="bold"), command=self._start_bot)
        self._btn_start.pack(fill="x", padx=16, pady=(0, 8))
        self._btn_stop = ctk.CTkButton(bot_card, text="■ 停止", height=38,
                                       fg_color=DANGER, hover_color="#c9302c", text_color="white",
                                       font=ctk.CTkFont(size=14, weight="bold"), command=self._stop_bot,
                                       state="disabled")
        self._btn_stop.pack(fill="x", padx=16, pady=(0, 8))
        self._btn_restart = ctk.CTkButton(bot_card, text="↻ 重启", height=36,
                                          fg_color=SOFT_BG, hover_color=SOFT_HOVER, text_color=PRIMARY,
                                          font=ctk.CTkFont(size=13, weight="bold"), command=self._restart_bot)
        self._btn_restart.pack(fill="x", padx=16, pady=(0, 16))

        # ---------- 成长状态（参数网格，每 5 秒自动刷新） ----------
        growth_card = self._card(tab, "成长状态")
        growth_card.pack(fill="x", pady=(16, 0))

        _ggrid = ctk.CTkFrame(growth_card, fg_color="transparent")
        _ggrid.pack(fill="x", padx=12, pady=(8, 4))
        # 参数格子：(key, 名称, 是否总是显示)；值为空且非总是显示 → 隐藏该格
        self._growth_items = [
            ("stage", "性格阶段", True),
            ("affection", "亲密度", True),
            ("dependency", "依赖度", True),
            ("jealousy", "醋意倾向", True),
            ("lewdness", "淫乱度", True),
            ("days", "在一起", True),
            ("nickname", "称呼", True), ("state", "状态", True),
            ("memory", "记得你", True),
            ("chats", "聊天记录", True),
            ("mood", "今日情绪", False),
            ("mood_delta", "今日亲密度", False),
            ("mood_state", "今日心情", True),
        ]
        self._growth_labels = {}
        self._growth_cell_kw = {}
        self._growth_tips = {}
        _gcols = 4
        for _i, (_key, _name, _always) in enumerate(self._growth_items):
            _cell = ctk.CTkFrame(_ggrid, fg_color=CELL_BG, corner_radius=10,
                                 border_width=1, border_color=CELL_BORDER)
            _row, _col = _i // _gcols, _i % _gcols
            _kw = dict(row=_row, column=_col, sticky="nsew", padx=3, pady=3)
            _cell.grid(**_kw)
            # 参数名 + ⓘ（悬停显示说明）
            _name_row = ctk.CTkFrame(_cell, fg_color="transparent")
            _name_row.pack(anchor="w", padx=10, pady=(6, 0))
            ctk.CTkLabel(_name_row, text=_name, text_color=MUTED,
                         font=ctk.CTkFont(size=11),
                         fg_color="transparent").pack(side="left")
            _hint = ctk.CTkLabel(_name_row, text="ⓘ", text_color=HINT,
                                 font=ctk.CTkFont(size=10, weight="bold"),
                                 fg_color="transparent", cursor="question_arrow")
            _hint.pack(side="left", padx=(3, 0))
            _val = ctk.CTkLabel(_cell, text="—", text_color=TEXT,
                                font=ctk.CTkFont(size=15, weight="bold"),
                                fg_color="transparent", anchor="w")
            _val.pack(fill="x", padx=10, pady=(0, 6))
            self._growth_labels[_key] = _val
            self._growth_cell_kw[_key] = (_cell, _kw, _always)
            self._growth_tips[_key] = ToolTip(_hint, GROWTH_HINTS.get(_key, ""))
        for _c in range(_gcols):
            _ggrid.grid_columnconfigure(_c, weight=1, uniform="g")

        # 最近性格演化（整行）
        self._growth_evo_var = tk.StringVar(value="读取中...")
        ctk.CTkLabel(growth_card, textvariable=self._growth_evo_var,
                     text_color=MUTED, font=ctk.CTkFont(size=12),
                     fg_color="transparent", justify="left", wraplength=920
                     ).pack(anchor="w", padx=16, pady=(2, 12))
        self._refresh_growth_stats()
        self._growth_after_id = self.root.after(5000, self._growth_tick)

    # ===================== 人设编辑 =====================

    def _refresh_growth_stats(self):
        """刷新首页"成长状态"参数网格（特征值 + 今日情绪 + 记忆数据 + 性格演化）。"""
        try:
            import personality_state as pstate
            import liveness
            import live_info
            import diary as growth_diary
            import evolution_db as edb
            import memory as longterm_memory
            from config import runtime as _rt
            stage = pstate.get_stage()
            a = pstate.get_affection()
            d = pstate.get_dependency()
            j = pstate.get_jealousy()
            lv = pstate.get_lewdness()
            days = liveness.days_together()
            nickname = liveness.nickname_for_stage(stage, pstate.sublevel_name())
            vals = {
                "stage": pstate.stage_name(),
                "affection": str(a),
                "dependency": str(d),
                "jealousy": str(j),
                "lewdness": f"{lv}（{pstate.lewdness_tier_name()}）",
                "days": f"第 {days} 天",
                "nickname": nickname,
            }
            boyfriend = str(_rt.PROACTIVE_ONLY_USER_ID or "").strip()
            # 状态：她此刻在做什么（剧情优先 + 时段兜底）
            vals["state"] = live_info.current_activity_for(boyfriend)
            # 今日情绪标签 / 今日亲密度变化
            tags = growth_diary.get_today_mood_tags()
            vals["mood"] = "、".join(str(t) for t in list(tags)[:6]) if tags else ""
            delta = getattr(growth_diary, "_today_affection_delta", 0) or 0
            vals["mood_delta"] = f"{'+' if delta > 0 else ''}{delta}" if delta else ""
            # 今日心情状态
            moods = []
            if liveness.today_mood_low():
                moods.append("心情低落")
            if boyfriend:
                try:
                    if liveness.is_angry(boyfriend):
                        moods.append("闹脾气")
                    gr = liveness.grudge_count_today(boyfriend)
                    if gr > 0:
                        moods.append(f"被惹 {gr} 次")
                except Exception:
                    pass
            vals["mood_state"] = " / ".join(moods) if moods else "正常"
            # 记忆与数据
            if boyfriend:
                try:
                    mem = longterm_memory.get_user_memory(boyfriend)
                    vals["memory"] = (
                        f"{len(mem.get('facts') or []) + len(mem.get('preferences') or {})} 件")
                except Exception:
                    vals["memory"] = "—"
            try:
                conn = longterm_memory._get_conn()
                with longterm_memory._lock:
                    total = conn.execute(
                        "SELECT COUNT(*) AS cnt FROM chat_history").fetchone()["cnt"]
                vals["chats"] = f"{total} 条"
            except Exception:
                vals["chats"] = "—"
            # 逐个更新格子：值为空显示 —（格子固定，保持 12 格整齐排满）
            for key, lbl in self._growth_labels.items():
                v = vals.get(key, "")
                lbl.configure(text=v if v else "—")
            # 称号：并入「性格阶段」格子的悬停说明
            try:
                title = pstate.get_title()
                tip = self._growth_tips["stage"]
                tip.text = GROWTH_HINTS.get("stage", "") + (f"　当前称号：{title}" if title else "")
            except Exception:
                pass
            # 最近性格演化（整行）
            evo = []
            try:
                notes = edb.get_recent_evolution_notes(2)
                evo = [f"{n['note_date']}：{n['note']}" for n in notes]
            except Exception:
                pass
            self._growth_evo_var.set("　|　".join(evo) if evo else "（暂无性格演化记录）")
        except Exception as e:
            self._growth_evo_var.set(f"成长状态读取失败：{e}")

    def _growth_tick(self):
        """成长状态定时刷新（每 5 秒）；窗口关闭后自动停止。"""
        self._refresh_growth_stats()
        try:
            if self.root.winfo_exists():
                self._growth_after_id = self.root.after(5000, self._growth_tick)
        except Exception:
            pass

    def _build_personality_tab(self):
        tab = self._notebook.add("💃 人设编辑")

        sub_nb = ctk.CTkTabview(tab, fg_color=BG, segmented_button_fg_color=BG,
                                segmented_button_selected_color="#ffffff",
                                segmented_button_selected_hover_color="#e8e8e8",
                                segmented_button_unselected_color="#f0f0f0",
                                text_color="#000000",
                                corner_radius=10)
        sub_nb.pack(fill="both", expand=True)

        # --- 子标签1：角色设定（全部人设字段，内容可滚动） ---
        sub1_raw = sub_nb.add("角色设定")
        sub1_raw.configure(fg_color=PANEL)
        sub1 = ctk.CTkScrollableFrame(sub1_raw, fg_color=PANEL, corner_radius=0)
        sub1.pack(fill="both", expand=True)
        fields = [
            ("名字", "GIRLFRIEND_NAME", 30),
            ("年龄", "GIRLFRIEND_AGE", 8),
            ("出生日期", "GIRLFRIEND_BIRTHDATE", 20),
            ("身份", "GIRLFRIEND_IDENTITY", 60),
            ("性格", "GIRLFRIEND_CHARACTER", 70),
        ]
        self._entries = {}
        for i, (label, key, width) in enumerate(fields):
            ctk.CTkLabel(sub1, text=label + "：", text_color=TEXT, font=ctk.CTkFont(size=14),
                         fg_color="transparent").grid(row=i, column=0, sticky="w", pady=6)
            if key == "GIRLFRIEND_BIRTHDATE":
                date_frame = ctk.CTkFrame(sub1, fg_color="transparent")
                date_frame.grid(row=i, column=1, sticky="w", padx=(12, 0), pady=6)
                import datetime as _dt
                now_y = _dt.date.today().year
                self._birth_vars = {
                    "y": tk.StringVar(value=""),
                    "m": tk.StringVar(value=""),
                    "d": tk.StringVar(value=""),
                }
                current = str(getattr(runtime, "GIRLFRIEND_BIRTHDATE", "") or "")
                if current:
                    try:
                        _y, _m, _d = current.split("-")
                        self._birth_vars["y"].set(_y)
                        self._birth_vars["m"].set(str(int(_m)))
                        self._birth_vars["d"].set(str(int(_d)))
                    except ValueError:
                        pass
                for tag, values, w in (
                    ("y", [str(y) for y in range(now_y, 1969, -1)], 90),
                    ("m", [f"{m:02d}" for m in range(1, 13)], 70),
                    ("d", [f"{d:02d}" for d in range(1, 32)], 70),
                ):
                    ctk.CTkComboBox(
                        date_frame, variable=self._birth_vars[tag], width=w, height=34,
                        values=values,
                        fg_color=INPUT_BG, border_color=BORDER, button_color=PRIMARY,
                        button_hover_color=PRIMARY_HOVER,
                        command=lambda _c, t=tag: self._sync_birthdate(),
                    ).pack(side="left", padx=(0, 4))
                ctk.CTkLabel(date_frame, text="年 / 月 / 日", text_color=MUTED,
                             font=ctk.CTkFont(size=12), fg_color="transparent").pack(side="left", padx=(2, 0))
                birth_var = tk.StringVar(value=current)
                self._entries["GIRLFRIEND_BIRTHDATE"] = birth_var
                self._sync_birthdate()
                continue
            var = tk.StringVar(value=str(getattr(runtime, key, "")))
            ctk.CTkEntry(sub1, textvariable=var, width=width, height=38,
                         fg_color=INPUT_BG, border_color=BORDER, text_color=TEXT).grid(
                row=i, column=1, sticky="ew", padx=(12, 0), pady=6)
            self._entries[key] = var

        self._text_widgets = {}
        text_fields = [
            ("语言风格", "GIRLFRIEND_LANGUAGE_STYLE", 150),
            ("外貌设定", "GIRLFRIEND_APPEARANCE", 130),
            ("音色描述", "TTS_VOICE_DESCRIPTION", 110),
        ]
        r = len(fields)
        for label, key, height in text_fields:
            ctk.CTkLabel(sub1, text=label + "：", text_color=TEXT, font=ctk.CTkFont(size=14),
                         fg_color="transparent").grid(row=r, column=0, sticky="nw", pady=(14, 0))
            w = ctk.CTkTextbox(sub1, height=height, wrap="word",
                               fg_color=INPUT_BG, border_color=BORDER, border_width=1,
                               text_color=TEXT, font=ctk.CTkFont(size=13))
            w.grid(row=r, column=1, sticky="nsew", padx=(12, 12), pady=(14, 0))
            w.insert("1.0", str(getattr(runtime, key, "") or ""))
            self._text_widgets[key] = w
            if key == "TTS_VOICE_DESCRIPTION":
                self._tts_voice_desc_text = w
            r += 1
        sub1.grid_columnconfigure(1, weight=1)
        sub1.grid_rowconfigure(len(fields) + len(text_fields) - 1, weight=1)

        ctk.CTkFrame(sub1, fg_color=BORDER, height=1).grid(
            row=r + 1, column=0, columnspan=2, sticky="ew", padx=12, pady=(16, 8))
        app_frame = ctk.CTkFrame(sub1, fg_color="transparent")
        app_frame.grid(row=r + 2, column=0, columnspan=2, sticky="ew", padx=0, pady=(0, 12))
        self._build_appearance_content(app_frame)

        sub3 = sub_nb.add("特殊反应")
        sub3.configure(fg_color=PANEL)
        sr_text = ctk.CTkTextbox(sub3, wrap="word", fg_color=INPUT_BG,
                                 border_color=BORDER, border_width=1, text_color=TEXT,
                                 font=ctk.CTkFont(size=14))
        sr_text.pack(fill="both", expand=True, padx=12, pady=12)
        sr_text.insert("1.0", runtime.GIRLFRIEND_SPECIAL_REACTIONS)
        self._text_widgets["GIRLFRIEND_SPECIAL_REACTIONS"] = sr_text

        sub4 = sub_nb.add("底层约束")
        sub4.configure(fg_color=PANEL)
        lc_text = ctk.CTkTextbox(sub4, wrap="word", fg_color=INPUT_BG,
                                 border_color=BORDER, border_width=1, text_color=TEXT,
                                 font=ctk.CTkFont(size=14))
        lc_text.pack(fill="both", expand=True, padx=12, pady=12)
        lc_text.insert("1.0", runtime.GIRLFRIEND_CONSTRAINTS)
        self._text_widgets["GIRLFRIEND_CONSTRAINTS"] = lc_text

        # 音色助手：音色描述属于人设的一部分，并入本页
        self._build_voice_assistant_tab(sub_nb)

    # ===================== API 参数 =====================

    def _build_params_tab(self, nb):
        tab = nb.add("API 参数")

        params = [
            ("Temperature (0-2)", "TEMPERATURE", 0.0, 2.0),
            ("最大 Token 数", "MAX_TOKENS", 64, 8192),
            ("对话记忆轮数", "MAX_HISTORY_LENGTH", 2, 500),
        ]
        self._sliders = {}
        self._slider_labels = {}
        for i, (label, key, lo, hi) in enumerate(params):
            ctk.CTkLabel(tab, text=label + "：", text_color=TEXT, font=ctk.CTkFont(size=13),
                         fg_color="transparent").grid(row=i, column=0, sticky="w", pady=8)
            var = tk.DoubleVar(value=float(getattr(runtime, key, lo)))
            val_label = ctk.CTkLabel(tab, text=str(getattr(runtime, key, lo)), width=70,
                                     text_color=PRIMARY, font=ctk.CTkFont(size=14, weight="bold"),
                                     fg_color="transparent")
            val_label.grid(row=i, column=2, padx=10, pady=8)
            slider = ctk.CTkSlider(tab, from_=lo, to=hi, variable=var,
                                   number_of_steps=1000 if isinstance(lo, float) else int(hi - lo),
                                   fg_color=TRACK, progress_color=PRIMARY,
                                   button_color=PRIMARY, button_hover_color=PRIMARY_HOVER,
                                   command=lambda v, k=key, vl=val_label: self._on_slider_change(k, v, vl))
            slider.grid(row=i, column=1, sticky="ew", pady=8)
            self._sliders[key] = var
            self._slider_labels[key] = val_label
            tab.grid_columnconfigure(1, weight=1)

        r = len(params)
        self._reply_cd_min_var = tk.StringVar(value=str(float(runtime.REPLY_COOLDOWN_MIN or 0)))
        ctk.CTkLabel(tab, text="最短冷却（秒）：", text_color=TEXT, font=ctk.CTkFont(size=13),
                     fg_color="transparent").grid(row=r, column=0, sticky="w", pady=6)
        ctk.CTkEntry(tab, textvariable=self._reply_cd_min_var, width=100, height=32,
                     fg_color=INPUT_BG, border_color=BORDER, text_color=TEXT).grid(row=r, column=1, sticky="w", padx=8, pady=6)
        self._reply_cd_max_var = tk.StringVar(value=str(float(runtime.REPLY_COOLDOWN_MAX or 3)))
        ctk.CTkLabel(tab, text="最长冷却（秒）：", text_color=TEXT, font=ctk.CTkFont(size=13),
                     fg_color="transparent").grid(row=r + 1, column=0, sticky="w", pady=6)
        ctk.CTkEntry(tab, textvariable=self._reply_cd_max_var, width=100, height=32,
                     fg_color=INPUT_BG, border_color=BORDER, text_color=TEXT).grid(row=r + 1, column=1, sticky="w", padx=8, pady=6)

        self._thinking_mode_var = tk.BooleanVar(value=bool(runtime.THINKING_MODE))
        ctk.CTkCheckBox(tab, text="开启思考模式",
                        variable=self._thinking_mode_var, text_color=TEXT,
                        fg_color=PRIMARY, hover_color=PRIMARY_HOVER,
                        font=ctk.CTkFont(size=13)).grid(
            row=r + 3, column=0, columnspan=3, sticky="w", pady=(8, 0))

    @staticmethod
    def _on_slider_change(key, value, label):
        v = float(value)
        if abs(v - round(v)) < 1e-9:
            label.configure(text=str(int(round(v))))
        else:
            label.configure(text=f"{v:.1f}")

    # ===================== 设置 =====================

    # ===================== 音色描述助手 =====================

    VOICE_DESIGN_SYSTEM = (
        "你是音色设计（voice design prompt）专家。"
        "用户会给你一个粗略的音色想法，请根据以下官方约定，优化成 1~4 句可直接用于 mimo-v2.5-tts-voicedesign 的音色描述。\n\n"
        "【关键维度】\n"
        "- 性别与年龄：如 young woman in her mid-20s、五十多岁的中年男性\n"
        "- 音色/质感：如 deep and gravelly、丝滑醇厚、带着磁性\n"
        "- 情绪/语气：如 warm and confident、温柔但带着一丝疲惫\n"
        "- 语速/节奏：如 slow and deliberate、语速极快，像连珠炮\n\n"
        "【可选加分维度】\n"
        "- 角色/人设：narrator、podcast host、评书先生、深夜电台DJ\n"
        "- 说话风格：casual and colloquial、一本正经地、压低嗓音像在密谋\n"
        "- 场景描写：narrating a nature documentary、在给投资人路演\n"
        "- 年代参照：1940s film noir、八十年代译制片配音\n\n"
        "【写法建议】\n"
        "- 可用简洁描述型或专业描述型，1~4 句即可，不要写长文\n"
        "- 核心特征描述清楚比堆砌维度更重要\n"
        "- 避免冲突：不要同时要求矛盾特征（如稚嫩童声 + CEO气场）\n"
        "- 避免音质效果词：不要写混响、回声、EQ、压缩等后期处理\n"
        "- 避免模糊词：不要用普通的、正常的、外国的等缺乏具体指向的描述\n"
        "- 中英文均可，选择最能精确表达的语言\n\n"
        "请直接输出优化后的音色描述，不要解释、不要前后缀。"
    )

    def _build_voice_assistant_tab(self, nb):
        tab = nb.add("音色助手")
        tab.configure(fg_color=PANEL)

        ctk.CTkLabel(tab, text="音色描述助手", text_color=PRIMARY,
                     font=ctk.CTkFont(size=20, weight="bold"),
                     fg_color="transparent").pack(anchor="w", padx=16, pady=(12, 4))
        # 输入区
        input_card = self._card(tab, "你的音色想法")
        input_card.pack(fill="x", padx=16, pady=(0, 10))
        self._voice_input_text = ctk.CTkTextbox(input_card, height=110, wrap="word",
                                               fg_color=INPUT_BG, border_color=BORDER,
                                               border_width=1, text_color=TEXT,
                                               font=ctk.CTkFont(size=13))
        self._voice_input_text.pack(fill="x", padx=14, pady=12)
        self._voice_input_text.insert("1.0", "例如：温柔软糯的年轻女声，带一点撒娇，语速慢一点")

        # 生成按钮
        self._voice_gen_btn = ctk.CTkButton(tab, text="✨ 生成优化音色描述", height=36,
                                           fg_color=PRIMARY, hover_color=PRIMARY_HOVER,
                                           text_color="white", font=ctk.CTkFont(size=14, weight="bold"),
                                           command=self._generate_voice_design)
        self._voice_gen_btn.pack(anchor="w", padx=16, pady=(0, 10))

        # 输出区
        output_card = self._card(tab, "优化后")
        output_card.pack(fill="both", expand=True, padx=16, pady=(0, 16))
        self._voice_output_text = ctk.CTkTextbox(output_card, height=180, wrap="word",
                                               fg_color=INPUT_BG, border_color=BORDER,
                                               border_width=1, text_color=TEXT,
                                               font=ctk.CTkFont(size=13))
        self._voice_output_text.pack(fill="both", expand=True, padx=14, pady=12)
        ctk.CTkButton(output_card, text="📋 复制到剪贴板", height=32, fg_color=SOFT_BG,
                      hover_color=SOFT_HOVER, text_color=PRIMARY, font=ctk.CTkFont(size=12),
                      command=self._copy_voice_design).pack(anchor="e", padx=14, pady=(0, 12))

    def _generate_voice_design(self):
        raw = self._voice_input_text.get("1.0", "end-1c").strip()
        if not raw:
            messagebox.showwarning("提示", "请先输入你的音色想法")
            return
        self._voice_gen_btn.configure(state="disabled", text="⏳ 正在生成...")
        self._voice_output_text.delete("1.0", "end")
        self._voice_output_text.insert("1.0", "生成中，请稍候...")
        threading.Thread(target=self._voice_design_worker, args=(raw,), daemon=True).start()

    def _voice_design_worker(self, raw):
        import asyncio
        from llm_factory import get_llm_client

        async def _run():
            client = get_llm_client()
            try:
                return await client.chat(
                    [
                        {"role": "system", "content": self.VOICE_DESIGN_SYSTEM},
                        {"role": "user", "content": raw},
                    ],
                    temperature=0.7,
                    max_tokens=300,
                )
            finally:
                await client.aclose()

        try:
            result = asyncio.run(_run())
            self._safe_after(0, lambda: self._on_voice_design_done(result))
        except Exception as e:
            self._safe_after(0, lambda: self._on_voice_design_done("", str(e)))

    def _on_voice_design_done(self, result, error=""):
        self._voice_gen_btn.configure(state="normal", text="✨ 生成优化音色描述")
        if error:
            self._voice_output_text.delete("1.0", "end")
            self._voice_output_text.insert("1.0", "生成失败：" + error)
            return
        self._voice_output_text.delete("1.0", "end")
        self._voice_output_text.insert("1.0", result or "（生成结果为空）")

    def _copy_voice_design(self):
        text = self._voice_output_text.get("1.0", "end-1c").strip()
        if not text:
            return
        self.root.clipboard_clear()
        self.root.clipboard_append(text)
        messagebox.showinfo("已复制", "音色描述已复制到剪贴板，可直接粘贴到语音设置里。")

    def _build_settings_tab(self):
        tab = self._notebook.add("⚙️ 设置")
        sub_nb = ctk.CTkTabview(tab, fg_color=BG, segmented_button_fg_color=BG,
                                segmented_button_selected_color="#ffffff",
                                segmented_button_selected_hover_color="#e8e8e8",
                                segmented_button_unselected_color="#f0f0f0",
                                text_color="#000000",
                                corner_radius=10)
        sub_nb.pack(fill="both", expand=True)
        self._build_params_tab(sub_nb)
        self._build_interact_tab(sub_nb)
        self._build_usage_tab(sub_nb)

    def _build_connection_tab(self, nb):
        tab = nb.add("🧠 模型与连接")
        tab.configure(fg_color=PANEL)
        # 内容较多，放入可滚动区域
        _scroll = ctk.CTkScrollableFrame(tab, fg_color=PANEL, corner_radius=0)
        _scroll.pack(fill="both", expand=True)
        tab = _scroll

        row = 0

        def section_title(text, color, note=""):
            """分区标题（跨三列），note 为补充说明（小字）。"""
            nonlocal row
            ctk.CTkFrame(tab, fg_color=BORDER, height=1).grid(
                row=row, column=0, columnspan=3, sticky="ew", pady=(16, 4))
            row += 1
            ctk.CTkLabel(tab, text=text, text_color=color,
                         font=ctk.CTkFont(size=14, weight="bold"),
                         fg_color="transparent").grid(
                row=row, column=0, columnspan=3, sticky="w", padx=2)
            row += 1
            if note:
                ctk.CTkLabel(tab, text=note, text_color=MUTED,
                             font=ctk.CTkFont(size=12),
                             fg_color="transparent").grid(
                    row=row, column=0, columnspan=3, sticky="w", padx=2)
                row += 1

        def entry_row(label, var, show=None, width=360, service=None):
            """普通输入行：标签 + 输入框（自动占位行号）。

            service 非空时右侧加「测试」按钮（deepseek / dashscope / mimo），
            点击后验证该 API Key 是否有效（读取输入框当前值）。
            """
            nonlocal row
            ctk.CTkLabel(tab, text=label, text_color=TEXT, font=ctk.CTkFont(size=13),
                         fg_color="transparent").grid(row=row, column=0, sticky="w", pady=5)
            ctk.CTkEntry(tab, textvariable=var, width=width, height=34, show=show,
                         fg_color=INPUT_BG, border_color=BORDER, text_color=TEXT).grid(
                row=row, column=1, sticky="w", padx=10, pady=5)
            if service:
                ctk.CTkButton(tab, text="测试", width=70, height=30, fg_color=SOFT_BG,
                              hover_color=SOFT_HOVER, text_color=PRIMARY, font=ctk.CTkFont(size=12),
                              command=lambda s=service: self._test_api_key(s)).grid(
                    row=row, column=2, sticky="w", pady=5)
            row += 1

        # =====================================================================
        # 第一层：模型提供商（Harness 风格卡片区）
        # =====================================================================
        section_title("模型提供商", PRIMARY)

        self._models_list_frame = ctk.CTkFrame(tab, fg_color="transparent")
        self._models_list_frame.grid(row=row, column=0, columnspan=3, sticky="ew", padx=2, pady=(2, 4))
        row += 1

        add_row = ctk.CTkFrame(tab, fg_color="transparent")
        add_row.grid(row=row, column=0, columnspan=3, sticky="w", padx=2, pady=(0, 6))
        ctk.CTkButton(add_row, text="➕ 添加提供商", width=130, height=30, fg_color=PRIMARY,
                      hover_color=PRIMARY_HOVER, text_color="white",
                      font=ctk.CTkFont(size=12, weight="bold"),
                      command=self._open_provider_dialog).pack(side="left")
        row += 1

        # =====================================================================
        # 第二层：模块分派（各模块用各自的模型）
        # =====================================================================
        section_title("模块分派", PRIMARY)

        self._assign_vars = {}
        try:
            from llm_providers import load_providers as _lp_load, get_assignment as _lp_ass
            _providers = _lp_load()
        except Exception:
            _providers = []
        for _cat, _label in (("chat", "对话"), ("task", "文字任务"), ("vision", "看图")):
            _rf = ctk.CTkFrame(tab, fg_color="transparent")
            _rf.grid(row=row, column=0, columnspan=3, sticky="ew", padx=2, pady=4)
            ctk.CTkLabel(_rf, text=f"{_label}：", text_color=TEXT, width=96,
                         font=ctk.CTkFont(size=13), fg_color="transparent").pack(side="left")
            _choices = [("（当前激活）", "")] + [
                ((_p.get("name") or _p.get("id")), _p.get("id")) for _p in _providers]
            _cvar = tk.StringVar()
            ctk.CTkComboBox(_rf, variable=_cvar, width=210, height=30,
                            values=[_c[0] for _c in _choices],
                            fg_color=INPUT_BG, border_color=BORDER, button_color=PRIMARY,
                            button_hover_color=PRIMARY_HOVER).pack(side="left", padx=(0, 8))
            _mvar = tk.StringVar(value="")
            try:
                _mvar = tk.StringVar(value=(_lp_ass(_cat).get("model") or ""))
            except Exception:
                _mvar = tk.StringVar(value="")
            ctk.CTkEntry(_rf, textvariable=_mvar, width=190, height=30,
                         fg_color=INPUT_BG, border_color=BORDER, text_color=TEXT).pack(side="left")

            def _on_assign(*_a, _cat=_cat, _choices=_choices, _cvar=_cvar, _mvar=_mvar):
                sel = _cvar.get()
                pid = next((c[1] for c in _choices if c[0] == sel), "")
                try:
                    from llm_providers import set_assignment
                    set_assignment(_cat, pid, _mvar.get().strip())
                except Exception:
                    pass
            _cvar.trace_add("write", _on_assign)
            _mvar.trace_add("write", _on_assign)
            _cur = ""
            try:
                _cur = _lp_ass(_cat).get("provider") or ""
            except Exception:
                pass
            _cvar.set(next((c[0] for c in _choices if c[1] == _cur), "（当前激活）"))
            self._assign_vars[_cat] = (_cvar, _mvar, _choices)
            row += 1

        # =====================================================================
        # 第三层：连接设置（分层）
        # =====================================================================

        # ---------- QQ / OneBot 连接（需重启） ----------
        section_title("连接设置", WARNING)

        self._ws_url_var = tk.StringVar(value=config.ONEBOT_WS_URL)
        entry_row("OneBot WS 地址：", self._ws_url_var)
        self._ws_token_var = tk.StringVar(value=config.ONEBOT_ACCESS_TOKEN)
        entry_row("Access Token：", self._ws_token_var, show="*")

        # ---------- 图生成（Key / 模型） ----------
        self._dashscope_key_var = tk.StringVar(value=config.DASHSCOPE_API_KEY)
        entry_row("百炼 API Key：", self._dashscope_key_var, show="*", service="dashscope")

        ctk.CTkLabel(tab, text="图片生成模型：", text_color=TEXT, font=ctk.CTkFont(size=13),
                     fg_color="transparent").grid(row=row, column=0, sticky="w", pady=5)
        self._image_model_var = tk.StringVar(value=config.DASHSCOPE_IMAGE_MODEL)
        self._image_model_combobox = ctk.CTkComboBox(
            tab, variable=self._image_model_var, width=320, height=34,
            values=["qwen-image-3.0-pro", "qwen-image-3.0", "wanx2.1-t2i-turbo"],
            fg_color=INPUT_BG, border_color=BORDER, button_color=PRIMARY,
            button_hover_color=PRIMARY_HOVER)
        self._image_model_combobox.grid(row=row, column=1, sticky="w", padx=10, pady=5)
        ctk.CTkButton(tab, text="刷新模型", width=90, height=32, fg_color=SOFT_BG,
                      hover_color=SOFT_HOVER, text_color=PRIMARY, font=ctk.CTkFont(size=12),
                      command=lambda: self._refresh_image_models(silent=False)).grid(row=row, column=2, sticky="w", pady=5)
        row += 1

        # ---------- 图生成（后端 / 尺寸 / 工作流） ----------
        section_title("图生成", SUCCESS)

        self._imagegen_enabled_var = tk.BooleanVar(value=bool(runtime.IMAGE_GEN_ENABLED))
        ctk.CTkCheckBox(tab, text="图生成",
                        variable=self._imagegen_enabled_var, text_color=TEXT,
                        fg_color=PRIMARY, hover_color=PRIMARY_HOVER,
                        font=ctk.CTkFont(size=13)).grid(row=row, column=0, columnspan=3, sticky="w")
        row += 1

        ctk.CTkLabel(tab, text="图片后端：", text_color=TEXT, font=ctk.CTkFont(size=13),
                     fg_color="transparent").grid(row=row, column=0, sticky="w", pady=5)
        self._image_backend_var = tk.StringVar(value=runtime.IMAGE_BACKEND or "dashscope")
        backend_frame = ctk.CTkFrame(tab, fg_color="transparent")
        backend_frame.grid(row=row, column=1, sticky="w", padx=10, pady=5)
        ctk.CTkRadioButton(backend_frame, text="百炼 API", variable=self._image_backend_var,
                           value="dashscope", text_color=TEXT, fg_color=PRIMARY,
                           hover_color=PRIMARY_HOVER, font=ctk.CTkFont(size=13)).pack(side="left")
        ctk.CTkRadioButton(backend_frame, text="本地 ComfyUI", variable=self._image_backend_var,
                           value="comfyui", text_color=TEXT, fg_color=PRIMARY,
                           hover_color=PRIMARY_HOVER, font=ctk.CTkFont(size=13)).pack(side="left", padx=16)
        row += 1

        self._image_prompt_use_llm_var = tk.BooleanVar(value=bool(runtime.IMAGE_PROMPT_USE_LLM))
        ctk.CTkCheckBox(tab, text="图生文提示词走大模型",
                        variable=self._image_prompt_use_llm_var, text_color=TEXT,
                        fg_color=PRIMARY, hover_color=PRIMARY_HOVER,
                        font=ctk.CTkFont(size=13)).grid(row=row, column=0, columnspan=3, sticky="w", pady=(8, 4))
        row += 1

        self._dashscope_base_url_var = tk.StringVar(value=runtime.DASHSCOPE_BASE_URL or "https://dashscope.aliyuncs.com")
        entry_row("百炼 API 地址：", self._dashscope_base_url_var)

        ctk.CTkLabel(tab, text="输出尺寸：", text_color=TEXT, font=ctk.CTkFont(size=13),
                     fg_color="transparent").grid(row=row, column=0, sticky="w", pady=5)
        self._image_size_var = tk.StringVar(value=runtime.IMAGE_GEN_SIZE or "1024*1024")
        ctk.CTkComboBox(tab, variable=self._image_size_var, width=160, height=34,
                        values=["1024*1024", "1080*1080", "720*1280", "1080*1920", "1920*1080",
                                "1440*2560", "2560*1440", "1280*720", "768*1344", "1344*768",
                                "1536*1024", "1024*1536", "2048*2048"],
                        fg_color=INPUT_BG, border_color=BORDER, button_color=PRIMARY,
                        button_hover_color=PRIMARY_HOVER).grid(row=row, column=1, sticky="w", padx=10, pady=5)
        row += 1

        self._comfy_url_var = tk.StringVar(value=runtime.COMFYUI_URL)
        entry_row("ComfyUI 地址：", self._comfy_url_var)
        self._comfy_wf_var = tk.StringVar(value=runtime.COMFYUI_WORKFLOW_FILE)
        entry_row("工作流文件：", self._comfy_wf_var)

        ctk.CTkLabel(tab, text="发图评论概率：", text_color=TEXT, font=ctk.CTkFont(size=13),
                     fg_color="transparent").grid(row=row, column=0, sticky="w", pady=5)
        self._comment_prob_var = tk.DoubleVar(value=float(runtime.IMAGE_COMMENT_PROBABILITY or 0))
        self._comment_prob_label = ctk.CTkLabel(tab, text=f"{self._comment_prob_var.get()*100:.0f}%",
                                                width=70, text_color=PRIMARY,
                                                font=ctk.CTkFont(size=14, weight="bold"),
                                                fg_color="transparent")
        self._comment_prob_label.grid(row=row, column=2, sticky="w", padx=10)
        ctk.CTkSlider(tab, from_=0.0, to=1.0, variable=self._comment_prob_var,
                      number_of_steps=100, fg_color=TRACK, progress_color=PRIMARY,
                      button_color=PRIMARY, button_hover_color=PRIMARY_HOVER,
                      command=lambda v: self._comment_prob_label.configure(text=f"{float(v)*100:.0f}%")
                      ).grid(row=row, column=1, sticky="ew", padx=10, pady=5)
        row += 1

        # ---------- 语音（小米 MiMo：TTS + ASR） ----------
        section_title("语音", SUCCESS)

        self._tts_prob_var = tk.DoubleVar(value=float(runtime.TTS_PROBABILITY or 0))
        self._tts_prob_label = ctk.CTkLabel(tab, text=f"{self._tts_prob_var.get()*100:.0f}%",
                                            width=70, text_color=PRIMARY,
                                            font=ctk.CTkFont(size=14, weight="bold"),
                                            fg_color="transparent")
        ctk.CTkLabel(tab, text="语音回复概率：", text_color=TEXT, font=ctk.CTkFont(size=13),
                     fg_color="transparent").grid(row=row, column=0, sticky="w", pady=5)
        self._tts_prob_label.grid(row=row, column=2, sticky="w", padx=10)
        ctk.CTkSlider(tab, from_=0.0, to=1.0, variable=self._tts_prob_var,
                      number_of_steps=100, fg_color=TRACK, progress_color=PRIMARY,
                      button_color=PRIMARY, button_hover_color=PRIMARY_HOVER,
                      command=lambda v: self._tts_prob_label.configure(text=f"{float(v)*100:.0f}%")
                      ).grid(row=row, column=1, sticky="ew", padx=10, pady=5)
        row += 1

        self._mimo_key_var = tk.StringVar(value=runtime.MIMO_API_KEY or "")
        entry_row("小米语音 API Key：", self._mimo_key_var, show="*", service="mimo")
        self._mimo_base_url_var = tk.StringVar(value=runtime.MIMO_API_BASE_URL or "https://api.xiaomimimo.com/v1")
        entry_row("小米语音 API 地址：", self._mimo_base_url_var)

        ctk.CTkLabel(tab, text="TTS 模型：", text_color=TEXT, font=ctk.CTkFont(size=13),
                     fg_color="transparent").grid(row=row, column=0, sticky="w", pady=5)
        self._tts_model_var = tk.StringVar(value=runtime.TTS_MODEL or "mimo-v2.5-tts-voicedesign")
        self._tts_model_combobox = ctk.CTkComboBox(
            tab, variable=self._tts_model_var, width=320, height=34,
            values=[runtime.TTS_MODEL or "mimo-v2.5-tts-voicedesign"],
            fg_color=INPUT_BG, border_color=BORDER, button_color=PRIMARY,
            button_hover_color=PRIMARY_HOVER)
        self._tts_model_combobox.grid(row=row, column=1, sticky="w", padx=10, pady=5)
        ctk.CTkButton(tab, text="刷新模型", width=90, height=32, fg_color=SOFT_BG,
                      hover_color=SOFT_HOVER, text_color=PRIMARY, font=ctk.CTkFont(size=12),
                      command=lambda: self._refresh_tts_models(silent=False)).grid(row=row, column=2, sticky="w", pady=5)
        row += 1

        # ===== 语音识别（MiMo-V2.5-ASR） =====
        self._asr_enabled_var = tk.BooleanVar(value=bool(runtime.ASR_ENABLED))
        ctk.CTkCheckBox(tab, text="识别用户发来的语音",
                        variable=self._asr_enabled_var, text_color=TEXT,
                        fg_color=PRIMARY, hover_color=PRIMARY_HOVER,
                        font=ctk.CTkFont(size=13)).grid(row=row, column=0, columnspan=3, sticky="w", pady=(4, 6))
        row += 1

        ctk.CTkLabel(tab, text="识别语种：", text_color=TEXT, font=ctk.CTkFont(size=13),
                     fg_color="transparent").grid(row=row, column=0, sticky="w", pady=5)
        self._asr_language_var = tk.StringVar(value=runtime.ASR_LANGUAGE or "auto")
        ctk.CTkComboBox(tab, variable=self._asr_language_var, width=140, height=34,
                        values=["auto", "zh", "en"],
                        fg_color=INPUT_BG, border_color=BORDER, button_color=PRIMARY,
                        button_hover_color=PRIMARY_HOVER).grid(row=row, column=1, sticky="w", padx=10, pady=5)
        row += 1

        ctk.CTkLabel(tab, text="ASR 模型：", text_color=TEXT, font=ctk.CTkFont(size=13),
                     fg_color="transparent").grid(row=row, column=0, sticky="w", pady=5)
        self._asr_model_var = tk.StringVar(value=runtime.ASR_MODEL or "mimo-v2.5-asr")
        self._asr_model_combobox = ctk.CTkComboBox(
            tab, variable=self._asr_model_var, width=320, height=34,
            values=[runtime.ASR_MODEL or "mimo-v2.5-asr"],
            fg_color=INPUT_BG, border_color=BORDER, button_color=PRIMARY,
            button_hover_color=PRIMARY_HOVER)
        self._asr_model_combobox.grid(row=row, column=1, sticky="w", padx=10, pady=5)
        ctk.CTkButton(tab, text="刷新模型", width=90, height=32, fg_color=SOFT_BG,
                      hover_color=SOFT_HOVER, text_color=PRIMARY, font=ctk.CTkFont(size=12),
                      command=lambda: self._refresh_asr_models(silent=False)).grid(row=row, column=2, sticky="w", pady=5)
        row += 1

        # ---------- 大模型兜底（DeepSeek .env，提供商卡片未填时用） ----------
        section_title("大模型兜底", MUTED)

        ctk.CTkLabel(tab, text="DeepSeek 模型：", text_color=TEXT, font=ctk.CTkFont(size=13),
                     fg_color="transparent").grid(row=row, column=0, sticky="w", pady=5)
        self._model_var = tk.StringVar(value=config.DEEPSEEK_MODEL)
        self._model_combobox = ctk.CTkComboBox(
            tab, variable=self._model_var, width=320, height=34,
            values=["deepseek-v4-flash", "deepseek-v4-pro", "deepseek-v4-flash-vision-exp"],
            fg_color=INPUT_BG, border_color=BORDER, button_color=PRIMARY,
            button_hover_color=PRIMARY_HOVER)
        self._model_combobox.grid(row=row, column=1, sticky="w", padx=10, pady=5)
        ctk.CTkButton(tab, text="刷新模型", width=90, height=32, fg_color=SOFT_BG,
                      hover_color=SOFT_HOVER, text_color=PRIMARY, font=ctk.CTkFont(size=12),
                      command=lambda: self._refresh_models(silent=False)).grid(row=row, column=2, sticky="w", pady=5)
        row += 1

        ctk.CTkLabel(tab, text="图片识别模型：", text_color=TEXT, font=ctk.CTkFont(size=13),
                     fg_color="transparent").grid(row=row, column=0, sticky="w", pady=5)
        self._vision_model_var = tk.StringVar(value=config.DEEPSEEK_VISION_MODEL)
        self._vision_model_combobox = ctk.CTkComboBox(
            tab, variable=self._vision_model_var, width=320, height=34,
            values=["deepseek-v4-flash-vision-exp", "deepseek-v4-pro"],
            fg_color=INPUT_BG, border_color=BORDER, button_color=PRIMARY,
            button_hover_color=PRIMARY_HOVER)
        self._vision_model_combobox.grid(row=row, column=1, sticky="w", padx=10, pady=5)
        row += 1

        self._base_url_var = tk.StringVar(value=config.DEEPSEEK_BASE_URL)
        entry_row("DeepSeek API 地址：", self._base_url_var)
        self._api_key_var = tk.StringVar(value=config.DEEPSEEK_API_KEY)
        entry_row("DeepSeek API Key：", self._api_key_var, show="*", service="deepseek")

        tab.grid_columnconfigure(1, weight=1)
        self._refresh_models_tab()

    # ===================== 模型提供商卡片（连接与模型页共用） =====================

    def _refresh_models_tab(self):
        """重建提供商卡片列表（增删改/切换后调用）。"""
        frame = getattr(self, "_models_list_frame", None)
        if frame is None:
            return
        for w in frame.winfo_children():
            w.destroy()
        try:
            from llm_providers import load_providers
            providers = load_providers()
        except Exception as e:
            logging.getLogger("gui").warning("读取提供商失败: %s", e)
            return
        type_labels = {"deepseek": "DeepSeek", "openai": "OpenAI 兼容"}
        for p in providers:
            pid = p.get("id")
            card = ctk.CTkFrame(frame, fg_color="#ffffff", corner_radius=10,
                                border_width=1,
                                border_color=PRIMARY if p.get("active") else BORDER)
            card.pack(fill="x", padx=4, pady=4)
            head = ctk.CTkFrame(card, fg_color="transparent")
            head.pack(fill="x", padx=12, pady=(8, 2))
            # 密钥状态点：deepseek 类型回退 .env key，视为已配置；openai 看是否填了 key
            dot_green = bool(p.get("api_key")) or p.get("type") == "deepseek"
            ctk.CTkLabel(head, text="●", text_color="#2ecc71" if dot_green else "#e74c3c",
                         font=ctk.CTkFont(size=14), fg_color="transparent").pack(side="left", padx=(0, 6))
            ctk.CTkLabel(head, text=p.get("name") or pid or "?",
                         text_color=TEXT, font=ctk.CTkFont(size=13, weight="bold"),
                         fg_color="transparent").pack(side="left")
            ctk.CTkLabel(head, text=type_labels.get(p.get("type"), p.get("type") or "?"),
                         text_color=MUTED, font=ctk.CTkFont(size=11),
                         fg_color="transparent").pack(side="left", padx=8)
            if p.get("active"):
                ctk.CTkLabel(head, text="✓ 当前使用", text_color=PRIMARY,
                             font=ctk.CTkFont(size=12, weight="bold"),
                             fg_color="transparent").pack(side="left", padx=10)
            btn_frame = ctk.CTkFrame(head, fg_color="transparent")
            btn_frame.pack(side="right")
            if not p.get("active"):
                ctk.CTkButton(btn_frame, text="设为当前", width=76, height=28,
                              fg_color=SOFT_BG, hover_color=SOFT_HOVER, text_color=PRIMARY,
                              font=ctk.CTkFont(size=11),
                              command=lambda x=pid: self._provider_action(x, "activate")
                              ).pack(side="left", padx=2)
            ctk.CTkButton(btn_frame, text="获取模型", width=76, height=28,
                          fg_color=SOFT_BG, hover_color=SOFT_HOVER, text_color=PRIMARY,
                          font=ctk.CTkFont(size=11),
                          command=lambda x=pid: self._provider_action(x, "fetch_models")
                          ).pack(side="left", padx=2)
            ctk.CTkButton(btn_frame, text="测试", width=56, height=28,
                          fg_color=SOFT_BG, hover_color=SOFT_HOVER, text_color=PRIMARY,
                          font=ctk.CTkFont(size=11),
                          command=lambda x=pid: self._provider_action(x, "test")
                          ).pack(side="left", padx=2)
            ctk.CTkButton(btn_frame, text="编辑", width=56, height=28,
                          fg_color=SOFT_BG, hover_color=SOFT_HOVER, text_color=PRIMARY,
                          font=ctk.CTkFont(size=11),
                          command=lambda x=pid: self._provider_action(x, "edit")
                          ).pack(side="left", padx=2)
            ctk.CTkButton(btn_frame, text="删除", width=56, height=28,
                          fg_color="#fdecea", hover_color="#f8d7da", text_color="#c0392b",
                          font=ctk.CTkFont(size=11),
                          command=lambda x=pid: self._provider_action(x, "remove")
                          ).pack(side="left", padx=2)
            sub = ctk.CTkFrame(card, fg_color="transparent")
            sub.pack(fill="x", padx=14, pady=(0, 8))
            if p.get("type") == "deepseek":
                info = "DeepSeek 官方"
                if p.get("base_url"):
                    info += "  ·  " + p["base_url"]
                if p.get("model"):
                    info += "  ·  模型 " + p["model"]
            else:
                info = (p.get("base_url") or "未填地址") + (
                    "  ·  模型 " + p.get("model") if p.get("model") else "  ·  未填模型")
            ctk.CTkLabel(sub, text=info, text_color=MUTED, font=ctk.CTkFont(size=11),
                         fg_color="transparent").pack(anchor="w")

    def _provider_action(self, pid, action):
        try:
            from llm_providers import load_providers
            p = next((x for x in load_providers() if x.get("id") == pid), None)
        except Exception:
            p = None
        if p is None:
            return
        if action == "activate":
            from llm_providers import set_active
            set_active(pid)
            self._refresh_models_tab()
            messagebox.showinfo("已切换", f"已切换到「{p.get('name')}」，重启 Bot 后生效。")
        elif action == "remove":
            if not messagebox.askyesno("删除提供商", f"确定删除「{p.get('name')}」吗？"):
                return
            from llm_providers import remove_provider
            remove_provider(pid)
            self._refresh_models_tab()
        elif action == "edit":
            self._open_provider_dialog(p)
        elif action == "fetch_models":
            self._fetch_provider_models(p)
        elif action == "test":
            self._test_provider(p)

    def _fetch_provider_models(self, p):
        """后台拉取该提供商的模型列表，成功后弹窗展示，点击即可设为默认模型。"""
        pid = p.get("id")
        name = p.get("name") or "?"
        ptype = p.get("type")
        api_key = p.get("api_key") or ""
        base_url = p.get("base_url") or ""

        def worker():
            import asyncio
            models, err = None, None
            try:
                if ptype == "deepseek":
                    from deepseek_client import DeepSeekClient
                    client = DeepSeekClient(api_key=api_key or None, base_url=base_url or None)
                else:
                    from openai_compat import OpenAICompatClient
                    client = OpenAICompatClient(base_url=base_url or "",
                                                api_key=api_key or "sk-local")

                async def _run():
                    try:
                        return await client.list_models()
                    finally:
                        await client.aclose()
                models = asyncio.run(_run())
            except Exception as e:
                err = str(e)
            self._safe_after(0, lambda: self._on_fetch_models_done(pid, name, models, err))

        threading.Thread(target=worker, daemon=True).start()

    def _on_fetch_models_done(self, pid, name, models, err):
        """获取完成：失败提示；成功弹窗列出模型，点击选择设为该提供商默认模型。"""
        if err or not models:
            messagebox.showerror("获取模型列表失败", f"{name}：{err or '未返回任何模型'}")
            return
        dlg = ctk.CTkToplevel(self.root)
        dlg.title(f"模型列表 · {name}")
        dlg.geometry("440x500")
        dlg.transient(self.root)
        dlg.grab_set()
        dlg.configure(fg_color=BG)
        dlg.attributes("-topmost", True)

        ctk.CTkLabel(dlg, text=f"共 {len(models)} 个模型，点击设为该提供商默认：",
                     text_color=MUTED, font=ctk.CTkFont(size=12),
                     fg_color="transparent").pack(anchor="w", padx=14, pady=(12, 6))

        scroll = ctk.CTkScrollableFrame(dlg, fg_color=BG, corner_radius=0)
        scroll.pack(fill="both", expand=True, padx=10, pady=(0, 12))

        def _pick(mm):
            try:
                from llm_providers import update_provider
                update_provider(pid, model=mm)
                self._refresh_models_tab()
                dlg.destroy()
                messagebox.showinfo("已设置", f"已把「{mm}」设为该提供商的默认模型。")
            except Exception as e:
                messagebox.showerror("设置失败", str(e))

        for mm in models:
            ctk.CTkButton(scroll, text=mm, height=30,
                          fg_color=LIST_BTN, hover_color=LIST_BTN_HOVER,
                          text_color=TEXT, font=ctk.CTkFont(size=12),
                          anchor="w", command=lambda m=mm: _pick(m)).pack(fill="x", pady=2)

    def _open_provider_dialog(self, provider=None):
        """添加 / 编辑提供商对话框（独立窗口）。"""
        from llm_providers import TEMPLATES
        dlg = ctk.CTkToplevel(self.root)
        dlg.title("编辑提供商" if provider else "添加提供商")
        dlg.geometry("500x430")
        dlg.transient(self.root)
        dlg.grab_set()
        dlg.configure(fg_color=BG)
        dlg.attributes("-topmost", True)

        body = ctk.CTkScrollableFrame(dlg, fg_color=BG, corner_radius=0)
        body.pack(fill="both", expand=True, padx=16, pady=10)

        row = [0]

        def label(text):
            ctk.CTkLabel(body, text=text, text_color=TEXT, font=ctk.CTkFont(size=13),
                         fg_color="transparent").grid(row=row[0], column=0, sticky="w", pady=6)

        def entry(var, width=300, show=None):
            e = ctk.CTkEntry(body, textvariable=var, width=width, height=32, show=show,
                             fg_color=INPUT_BG, border_color=BORDER, text_color=TEXT)
            e.grid(row=row[0], column=1, sticky="w", padx=8, pady=6)
            row[0] += 1

        is_edit = provider is not None
        tpl_var = tk.StringVar()
        name_var = tk.StringVar(value=(provider or {}).get("name") or "")
        url_var = tk.StringVar(value=(provider or {}).get("base_url") or "")
        key_var = tk.StringVar(value=(provider or {}).get("api_key") or "")
        model_var = tk.StringVar(value=(provider or {}).get("model") or "")

        if not is_edit:
            label("模板：")
            combo = ctk.CTkComboBox(body, variable=tpl_var, width=300, height=32,
                                    values=[v["label"] for v in TEMPLATES.values()],
                                    fg_color=INPUT_BG, border_color=BORDER, button_color=PRIMARY,
                                    button_hover_color=PRIMARY_HOVER)
            combo.grid(row=row[0], column=1, sticky="w", padx=8, pady=6)
            row[0] += 1

            def _on_tpl(*_):
                tpl = next((v for v in TEMPLATES.values() if v["label"] == tpl_var.get()), None)
                if tpl:
                    name_var.set(tpl["name"])
                    url_var.set(tpl["base_url"])
                    model_var.set(tpl["model"])
            tpl_var.trace_add("write", _on_tpl)
            tpl_var.set("DeepSeek 官方")

        label("名称：")
        entry(name_var)
        label("API 地址：")
        entry(url_var)
        label("API Key（本地常留空）：")
        entry(key_var, show="*")
        label("模型名：")
        entry(model_var)

        err_lbl = ctk.CTkLabel(body, text="", text_color="#c0392b",
                               font=ctk.CTkFont(size=12), fg_color="transparent")
        err_lbl.grid(row=row[0], column=0, columnspan=2, sticky="w", pady=4)
        row[0] += 1

        def _collect():
            name = name_var.get().strip()
            url = url_var.get().strip()
            key = key_var.get().strip()
            model = model_var.get().strip()
            if not name:
                return None, "请填名称"
            # API 地址允许留空（DeepSeek 官方留空 = 默认官方地址）
            if not model:
                return None, "请填模型名（本地/兼容服务都需要）"
            if is_edit:
                ptype = provider.get("type", "openai")
            else:
                tpl = next((v for v in TEMPLATES.values() if v["label"] == tpl_var.get()), None)
                ptype = (tpl or {}).get("type", "openai")
            return {"name": name, "url": url, "key": key, "model": model, "ptype": ptype}, None

        def _save():
            data, err = _collect()
            if err:
                err_lbl.configure(text=err)
                return
            from llm_providers import add_provider, update_provider
            if is_edit:
                update_provider(provider["id"], name=data["name"], base_url=data["url"],
                                api_key=data["key"], model=data["model"])
            else:
                add_provider(name=data["name"], ptype=data["ptype"], base_url=data["url"],
                             api_key=data["key"], model=data["model"])
            dlg.destroy()
            self._refresh_models_tab()
            messagebox.showinfo("已保存", "提供商已保存。切到「设为当前」并重启 Bot 后生效。")

        def _test():
            data, err = _collect()
            if err:
                err_lbl.configure(text=err)
                return
            self._test_provider({"type": data["ptype"], "base_url": data["url"],
                                 "api_key": data["key"], "model": data["model"],
                                 "name": data["name"]})

        btns = ctk.CTkFrame(dlg, fg_color="transparent")
        btns.pack(fill="x", padx=16, pady=(0, 12))
        ctk.CTkButton(btns, text="测试", width=80, height=34, fg_color=SOFT_BG,
                      hover_color=SOFT_HOVER, text_color=PRIMARY,
                      font=ctk.CTkFont(size=13), command=_test).pack(side="left")
        ctk.CTkButton(btns, text="保存", width=110, height=34, fg_color=PRIMARY,
                      hover_color=PRIMARY_HOVER, text_color="white",
                      font=ctk.CTkFont(size=13, weight="bold"), command=_save).pack(side="right")
        ctk.CTkButton(btns, text="取消", width=80, height=34, fg_color="#e8e8e8",
                      hover_color="#dcdcdc", text_color="#333333",
                      font=ctk.CTkFont(size=13), command=dlg.destroy).pack(side="right", padx=8)

    def _test_provider(self, p):
        """后台线程测试一个提供商（发一句最简单的对话）。"""
        name = p.get("name") or "?"

        def worker():
            import asyncio
            if p.get("type") == "deepseek":
                from deepseek_client import DeepSeekClient
                client = DeepSeekClient(api_key=p.get("api_key") or None,
                                        base_url=p.get("base_url") or None,
                                        model=p.get("model") or None)
            else:
                from openai_compat import OpenAICompatClient
                client = OpenAICompatClient(base_url=p.get("base_url") or "",
                                            api_key=p.get("api_key") or "",
                                            model=p.get("model") or "")
            result, err = None, None
            try:
                async def _run():
                    try:
                        return await client.chat(
                            [{"role": "user", "content": "回复两个字：在线"}],
                            temperature=0.1, max_tokens=10,
                        )
                    finally:
                        await client.aclose()
                result = asyncio.run(_run())
            except Exception as e:
                err = str(e)
            if err or not result:
                self._safe_after(0, lambda: messagebox.showerror(
                    "测试提供商", f"❌ 连接失败（{name}）：\n{err or '无返回'}"))
            else:
                self._safe_after(0, lambda: messagebox.showinfo(
                    "测试提供商", f"✅ 连通正常（{name}）\n模型回复：{result[:40]}"))

        threading.Thread(target=worker, daemon=True).start()

    def _build_interact_tab(self, nb):
        tab = nb.add("互动")
        tab.configure(fg_color=PANEL)
        scroll = ctk.CTkScrollableFrame(tab, fg_color=PANEL, corner_radius=0)
        scroll.pack(fill="both", expand=True)
        self._build_sticker_section(scroll)
        self._build_illustration_section(scroll)
        self._build_qzone_section(scroll)
        self._build_proactive_section(scroll)
        self._build_liveinfo_section(scroll)

    def _build_sticker_section(self, parent):
        tab = self._card(parent, "表情包")
        tab.pack(fill="x", pady=(0, 12))

        ctk.CTkLabel(tab, text="发送概率：", text_color=TEXT, font=ctk.CTkFont(size=13),
                     fg_color="transparent").pack(anchor="w", padx=16)
        prob_frame = ctk.CTkFrame(tab, fg_color="transparent")
        prob_frame.pack(fill="x", padx=16, pady=(2, 10))
        self._sticker_prob_var = tk.DoubleVar(value=float(runtime.STICKER_PROBABILITY or 0))
        self._sticker_prob_label = ctk.CTkLabel(prob_frame, text=f"{self._sticker_prob_var.get()*100:.0f}%",
                                                width=60, text_color=PRIMARY,
                                                font=ctk.CTkFont(size=13, weight="bold"),
                                                fg_color="transparent")
        self._sticker_prob_label.pack(side="right")
        ctk.CTkSlider(prob_frame, from_=0.0, to=1.0, variable=self._sticker_prob_var,
                      number_of_steps=100, fg_color=TRACK, progress_color=PRIMARY,
                      button_color=PRIMARY, button_hover_color=PRIMARY_HOVER,
                      command=lambda v: self._sticker_prob_label.configure(text=f"{float(v)*100:.0f}%")
                      ).pack(side="left", fill="x", expand=True)

        ctk.CTkLabel(tab, text="表情包文件夹：", text_color=TEXT, font=ctk.CTkFont(size=13),
                     fg_color="transparent").pack(anchor="w", padx=16)
        self._sticker_dir_var = tk.StringVar(value=runtime.STICKER_DIR)
        ctk.CTkEntry(tab, textvariable=self._sticker_dir_var, width=420, height=34,
                     fg_color=INPUT_BG, border_color=BORDER, text_color=TEXT).pack(anchor="w", padx=16, pady=(2, 6))

    def _build_illustration_section(self, parent):
        tab = self._card(parent, "自动配图")
        tab.pack(fill="x", pady=(0, 12))

        anni_row = ctk.CTkFrame(tab, fg_color="transparent")
        anni_row.pack(fill="x", padx=16, pady=(0, 12))
        ctk.CTkLabel(anni_row, text="纪念日：",
                     text_color=TEXT, font=ctk.CTkFont(size=13),
                     fg_color="transparent").pack(side="left")
        self._anniversary_var = tk.StringVar(value=runtime.ANNIVERSARY_DATE or "2025-07-20")
        ctk.CTkEntry(anni_row, textvariable=self._anniversary_var, width=110, height=30,
                     fg_color=INPUT_BG, border_color=BORDER, text_color=TEXT).pack(side="left", padx=8)

        ctk.CTkLabel(tab, text="配图概率：", text_color=TEXT, font=ctk.CTkFont(size=13),
                     fg_color="transparent").pack(anchor="w", padx=16)
        prob_frame = ctk.CTkFrame(tab, fg_color="transparent")
        prob_frame.pack(fill="x", padx=16, pady=(2, 12))
        self._illustrate_prob_var = tk.DoubleVar(value=float(runtime.AUTO_ILLUSTRATE_PROBABILITY or 0))
        self._illustrate_prob_label = ctk.CTkLabel(prob_frame, text=f"{self._illustrate_prob_var.get()*100:.0f}%",
                                                   width=60, text_color=PRIMARY,
                                                   font=ctk.CTkFont(size=13, weight="bold"),
                                                   fg_color="transparent")
        self._illustrate_prob_label.pack(side="right")
        ctk.CTkSlider(prob_frame, from_=0.0, to=1.0, variable=self._illustrate_prob_var,
                      number_of_steps=100, fg_color=TRACK, progress_color=PRIMARY,
                      button_color=PRIMARY, button_hover_color=PRIMARY_HOVER,
                      command=lambda v: self._illustrate_prob_label.configure(text=f"{float(v)*100:.0f}%")
                      ).pack(side="left", fill="x", expand=True)

    def _build_qzone_section(self, parent):
        tab = self._card(parent, "QQ 空间")
        tab.pack(fill="x", pady=(0, 12))

        uin_row = ctk.CTkFrame(tab, fg_color="transparent")
        uin_row.pack(fill="x", padx=16, pady=(0, 6))
        ctk.CTkLabel(uin_row, text="自己的 QQ 号：",
                     text_color=TEXT, font=ctk.CTkFont(size=13),
                     fg_color="transparent").pack(side="left")
        self._qzone_self_uin_var = tk.StringVar(value=runtime.QZONE_SELF_UIN or "")
        ctk.CTkEntry(uin_row, textvariable=self._qzone_self_uin_var, width=130, height=30,
                     fg_color=INPUT_BG, border_color=BORDER, text_color=TEXT).pack(side="left", padx=8)

        ctk.CTkLabel(tab, text="好友动态评论概率：",
                     text_color=TEXT, font=ctk.CTkFont(size=13),
                     fg_color="transparent").pack(anchor="w", padx=16)
        prob_frame = ctk.CTkFrame(tab, fg_color="transparent")
        prob_frame.pack(fill="x", padx=16, pady=(2, 12))
        self._qzone_comment_prob_var = tk.DoubleVar(value=float(runtime.QZONE_FEED_COMMENT_PROB or 0.35))
        self._qzone_comment_prob_label = ctk.CTkLabel(prob_frame, text=f"{self._qzone_comment_prob_var.get()*100:.0f}%",
                                                      width=60, text_color=PRIMARY,
                                                      font=ctk.CTkFont(size=13, weight="bold"),
                                                      fg_color="transparent")
        self._qzone_comment_prob_label.pack(side="right")
        ctk.CTkSlider(prob_frame, from_=0.0, to=1.0, variable=self._qzone_comment_prob_var,
                      number_of_steps=100, fg_color=TRACK, progress_color=PRIMARY,
                      button_color=PRIMARY, button_hover_color=PRIMARY_HOVER,
                      command=lambda v: self._qzone_comment_prob_label.configure(text=f"{float(v)*100:.0f}%")
                      ).pack(side="left", fill="x", expand=True)

        posts_row = ctk.CTkFrame(tab, fg_color="transparent")
        posts_row.pack(fill="x", padx=16, pady=(0, 6))
        ctk.CTkLabel(posts_row, text="每天自动发说说：", text_color=TEXT, font=ctk.CTkFont(size=13),
                     fg_color="transparent").pack(side="left")
        self._qzone_posts_var = tk.StringVar(value=str(int(runtime.QZONE_POSTS_PER_DAY or 3)))
        ctk.CTkComboBox(posts_row, variable=self._qzone_posts_var, width=70, height=30,
                        values=["1", "2", "3", "4", "5", "6", "8", "10"],
                        fg_color=INPUT_BG, border_color=BORDER, button_color=PRIMARY,
                        button_hover_color=PRIMARY_HOVER).pack(side="left", padx=8)
        ctk.CTkLabel(posts_row, text="条",
                     text_color=MUTED, font=ctk.CTkFont(size=12),
                     fg_color="transparent").pack(side="left", padx=(4, 0))

        interval_frame = ctk.CTkFrame(tab, fg_color="transparent")
        interval_frame.pack(fill="x", padx=16, pady=(0, 6))
        ctk.CTkLabel(interval_frame, text="条间间隔：", text_color=TEXT, font=ctk.CTkFont(size=13),
                     fg_color="transparent").grid(row=0, column=0, sticky="w")
        # 最短间隔（小时+分钟）
        self._qzone_interval_min_h_var = tk.StringVar(
            value=str(_split_hours(runtime.QZONE_POST_INTERVAL_MIN or 2)[0]))
        self._qzone_interval_min_m_var = tk.StringVar(
            value=str(_split_hours(runtime.QZONE_POST_INTERVAL_MIN or 2)[1]))
        ctk.CTkEntry(interval_frame, textvariable=self._qzone_interval_min_h_var, width=46, height=30,
                     fg_color=INPUT_BG, border_color=BORDER, text_color=TEXT).grid(row=0, column=1, padx=(8, 2))
        ctk.CTkLabel(interval_frame, text="时", text_color=TEXT, font=ctk.CTkFont(size=13),
                     fg_color="transparent").grid(row=0, column=2, sticky="w")
        ctk.CTkEntry(interval_frame, textvariable=self._qzone_interval_min_m_var, width=46, height=30,
                     fg_color=INPUT_BG, border_color=BORDER, text_color=TEXT).grid(row=0, column=3, padx=4)
        ctk.CTkLabel(interval_frame, text="分 ~", text_color=TEXT, font=ctk.CTkFont(size=13),
                     fg_color="transparent").grid(row=0, column=4, sticky="w")
        # 最长间隔（小时+分钟）
        self._qzone_interval_max_h_var = tk.StringVar(
            value=str(_split_hours(runtime.QZONE_POST_INTERVAL_MAX or 4)[0]))
        self._qzone_interval_max_m_var = tk.StringVar(
            value=str(_split_hours(runtime.QZONE_POST_INTERVAL_MAX or 4)[1]))
        ctk.CTkEntry(interval_frame, textvariable=self._qzone_interval_max_h_var, width=46, height=30,
                     fg_color=INPUT_BG, border_color=BORDER, text_color=TEXT).grid(row=0, column=5, padx=(8, 2))
        ctk.CTkLabel(interval_frame, text="时", text_color=TEXT, font=ctk.CTkFont(size=13),
                     fg_color="transparent").grid(row=0, column=6, sticky="w")
        ctk.CTkEntry(interval_frame, textvariable=self._qzone_interval_max_m_var, width=46, height=30,
                     fg_color=INPUT_BG, border_color=BORDER, text_color=TEXT).grid(row=0, column=7, padx=4)
        ctk.CTkLabel(interval_frame, text="分",
                     text_color=MUTED, font=ctk.CTkFont(size=12),
                     fg_color="transparent").grid(row=0, column=8, sticky="w", padx=(4, 0))

        ctk.CTkLabel(tab, text="说说配图概率：",
                     text_color=TEXT, font=ctk.CTkFont(size=13),
                     fg_color="transparent").pack(anchor="w", padx=16)
        img_frame = ctk.CTkFrame(tab, fg_color="transparent")
        img_frame.pack(fill="x", padx=16, pady=(2, 12))
        self._qzone_img_prob_var = tk.DoubleVar(value=float(runtime.QZONE_POST_IMAGE_PROB or 0.6))
        self._qzone_img_prob_label = ctk.CTkLabel(img_frame, text=f"{self._qzone_img_prob_var.get()*100:.0f}%",
                                                  width=60, text_color=PRIMARY,
                                                  font=ctk.CTkFont(size=13, weight="bold"),
                                                  fg_color="transparent")
        self._qzone_img_prob_label.pack(side="right")
        ctk.CTkSlider(img_frame, from_=0.0, to=1.0, variable=self._qzone_img_prob_var,
                      number_of_steps=100, fg_color=TRACK, progress_color=PRIMARY,
                      button_color=PRIMARY, button_hover_color=PRIMARY_HOVER,
                      command=lambda v: self._qzone_img_prob_label.configure(text=f"{float(v)*100:.0f}%")
                      ).pack(side="left", fill="x", expand=True)

        self._qzone_img_check_var = tk.BooleanVar(value=bool(runtime.QZONE_IMAGE_CHECK))
        ctk.CTkCheckBox(tab, text="配图质量检查",
                        variable=self._qzone_img_check_var, text_color=TEXT,
                        fg_color=PRIMARY, hover_color=PRIMARY_HOVER,
                        font=ctk.CTkFont(size=13)).pack(anchor="w", padx=16, pady=(0, 12))

    def _build_proactive_section(self, parent):
        tab = self._card(parent, "主动消息")
        tab.pack(fill="x", pady=(0, 12))

        only_row = ctk.CTkFrame(tab, fg_color="transparent")
        only_row.pack(fill="x", padx=16, pady=(0, 10))
        ctk.CTkLabel(only_row, text="只对指定 QQ 号主动发消息：",
                     text_color=TEXT, font=ctk.CTkFont(size=13),
                     fg_color="transparent").pack(side="left")
        self._proactive_only_var = tk.StringVar(value=runtime.PROACTIVE_ONLY_USER_ID or "")
        ctk.CTkEntry(only_row, textvariable=self._proactive_only_var, width=140, height=30,
                     fg_color=INPUT_BG, border_color=BORDER, text_color=TEXT).pack(side="left", padx=8)

        grid = ctk.CTkFrame(tab, fg_color="transparent")
        grid.pack(fill="x", padx=16, pady=(0, 10))

        self._proactive_interval_var = tk.StringVar(value=str(int(runtime.PROACTIVE_INTERVAL_MIN or 45)))
        ctk.CTkLabel(grid, text="最短间隔（分钟）：", text_color=TEXT, font=ctk.CTkFont(size=13),
                     fg_color="transparent").grid(row=0, column=0, sticky="w", pady=4)
        ctk.CTkEntry(grid, textvariable=self._proactive_interval_var, width=80, height=30,
                     fg_color=INPUT_BG, border_color=BORDER, text_color=TEXT).grid(row=0, column=1, sticky="w", padx=8)

        self._proactive_interval_max_var = tk.StringVar(value=str(int(runtime.PROACTIVE_INTERVAL_MAX or 75)))
        ctk.CTkLabel(grid, text="最长间隔（分钟）：", text_color=TEXT, font=ctk.CTkFont(size=13),
                     fg_color="transparent").grid(row=0, column=2, sticky="w", pady=4, padx=(12, 0))
        ctk.CTkEntry(grid, textvariable=self._proactive_interval_max_var, width=80, height=30,
                     fg_color=INPUT_BG, border_color=BORDER, text_color=TEXT).grid(row=0, column=3, sticky="w", padx=8)

        self._proactive_prob_var = tk.DoubleVar(value=float(runtime.PROACTIVE_PROBABILITY or 0))
        self._proactive_prob_label = ctk.CTkLabel(grid, text=f"{self._proactive_prob_var.get()*100:.0f}%",
                                                  width=50, text_color=PRIMARY,
                                                  font=ctk.CTkFont(size=12, weight="bold"),
                                                  fg_color="transparent")
        ctk.CTkLabel(grid, text="每次概率：", text_color=TEXT, font=ctk.CTkFont(size=13),
                     fg_color="transparent").grid(row=1, column=0, sticky="w", pady=4)
        self._proactive_prob_label.grid(row=1, column=3, sticky="w", padx=4)
        ctk.CTkSlider(grid, from_=0.0, to=1.0, variable=self._proactive_prob_var, number_of_steps=100,
                      width=180, fg_color=TRACK, progress_color=PRIMARY,
                      button_color=PRIMARY, button_hover_color=PRIMARY_HOVER,
                      command=lambda v: self._proactive_prob_label.configure(text=f"{float(v)*100:.0f}%")
                      ).grid(row=1, column=2, sticky="w", padx=(4, 0))

        self._proactive_gap_var = tk.StringVar(value=str(int(runtime.PROACTIVE_GAP_MIN or 10)))
        ctk.CTkLabel(grid, text="防打扰（分钟）：", text_color=TEXT, font=ctk.CTkFont(size=13),
                     fg_color="transparent").grid(row=2, column=0, sticky="w", pady=4)
        ctk.CTkEntry(grid, textvariable=self._proactive_gap_var, width=80, height=30,
                     fg_color=INPUT_BG, border_color=BORDER, text_color=TEXT).grid(row=2, column=1, sticky="w", padx=8)
        grid.grid_columnconfigure(2, weight=1)

        fu_grid = ctk.CTkFrame(tab, fg_color="transparent")
        fu_grid.pack(fill="x", padx=16, pady=(0, 10))
        ctk.CTkLabel(fu_grid, text="多久没回触发：", text_color=TEXT, font=ctk.CTkFont(size=13),
                     fg_color="transparent").grid(row=0, column=0, sticky="w")
        self._proactive_followup_hours_var = tk.StringVar(
            value=f"{float(runtime.PROACTIVE_FOLLOWUP_HOURS or 2):g}")
        ctk.CTkComboBox(fu_grid, variable=self._proactive_followup_hours_var, width=70, height=30,
                        values=["1", "2", "3", "4", "6", "8", "12"],
                        fg_color=INPUT_BG, border_color=BORDER, button_color=PRIMARY,
                        button_hover_color=PRIMARY_HOVER).grid(row=0, column=1, sticky="w", padx=8)
        ctk.CTkLabel(fu_grid, text="小时", text_color=TEXT, font=ctk.CTkFont(size=13),
                     fg_color="transparent").grid(row=0, column=2, sticky="w")

        ctk.CTkLabel(fu_grid, text="最多追问：", text_color=TEXT, font=ctk.CTkFont(size=13),
                     fg_color="transparent").grid(row=0, column=3, sticky="w", padx=(16, 0))
        self._proactive_followup_max_var = tk.StringVar(value=str(int(runtime.PROACTIVE_FOLLOWUP_MAX or 2)))
        ctk.CTkComboBox(fu_grid, variable=self._proactive_followup_max_var, width=60, height=30,
                        values=["1", "2", "3", "4", "5"],
                        fg_color=INPUT_BG, border_color=BORDER, button_color=PRIMARY,
                        button_hover_color=PRIMARY_HOVER).grid(row=0, column=4, sticky="w", padx=8)
        ctk.CTkLabel(fu_grid, text="次/沉默期", text_color=MUTED, font=ctk.CTkFont(size=12),
                     fg_color="transparent").grid(row=0, column=5, sticky="w", padx=(4, 0))

        # ---- 晚安静默 ----
        ctk.CTkFrame(tab, fg_color=BORDER, height=1).pack(fill="x", padx=16, pady=(0, 8))
        silence_grid = ctk.CTkFrame(tab, fg_color="transparent")
        silence_grid.pack(fill="x", padx=16, pady=(0, 12))
        ctk.CTkLabel(silence_grid, text="道晚安后静默：", text_color=TEXT, font=ctk.CTkFont(size=13),
                     fg_color="transparent").grid(row=0, column=0, sticky="w")
        self._night_silence_var = tk.StringVar(value=f"{float(runtime.NIGHT_SILENCE_HOURS or 8):g}")
        ctk.CTkComboBox(silence_grid, variable=self._night_silence_var, width=70, height=30,
                        values=["0", "1", "2", "3", "4", "5", "6", "7", "8", "9",
                                "10", "11", "12", "14", "16", "20", "24"],
                        fg_color=INPUT_BG, border_color=BORDER, button_color=PRIMARY,
                        button_hover_color=PRIMARY_HOVER).grid(row=0, column=1, sticky="w", padx=8)
        ctk.CTkLabel(silence_grid, text="小时",
                     text_color=MUTED, font=ctk.CTkFont(size=12),
                     fg_color="transparent").grid(row=0, column=2, sticky="w", padx=(4, 0))

    def _build_liveinfo_section(self, parent):
        tab = self._card(parent, "实时信息")
        tab.pack(fill="x", pady=(0, 12))

        ctk.CTkLabel(tab, text="联网搜索",
                     text_color=MUTED, font=ctk.CTkFont(size=12, weight="bold"),
                     fg_color="transparent").pack(anchor="w", padx=16, pady=(8, 2))

        ctk.CTkLabel(tab, text="天气城市：", text_color=TEXT, font=ctk.CTkFont(size=13),
                     fg_color="transparent").pack(anchor="w", padx=16)
        self._weather_city_var = tk.StringVar(value=runtime.WEATHER_CITY)
        ctk.CTkEntry(tab, textvariable=self._weather_city_var, width=180, height=34,
                     fg_color=INPUT_BG, border_color=BORDER, text_color=TEXT).pack(anchor="w", padx=16, pady=(2, 8))

    def _build_appearance_content(self, parent):
        """外貌设定内容（放在「人设编辑 → 外貌设定」子页）。"""
        tab = parent

        from appearance_ref import get_ref_dir, load_summary, ensure_ref_dir
        ensure_ref_dir()
        self._appearance_dir_label = ctk.CTkLabel(tab, text="文件夹：" + get_ref_dir(),
                                                  text_color=TEXT, font=ctk.CTkFont(size=12),
                                                  fg_color="transparent")
        self._appearance_dir_label.pack(anchor="w", padx=16, pady=(0, 6))

        self._appearance_summary_text = ctk.CTkTextbox(tab, height=130, wrap="word",
                                                      fg_color=INPUT_BG, border_color=BORDER,
                                                      border_width=1, text_color=TEXT,
                                                      font=ctk.CTkFont(size=12))
        self._appearance_summary_text.pack(fill="x", padx=16, pady=(0, 8))
        self._appearance_summary_text.insert("1.0", load_summary() or "（尚未生成外貌总结）")

        self._appearance_btn = ctk.CTkButton(tab, text="生成外貌总结", height=34,
                                             fg_color=PRIMARY, hover_color=PRIMARY_HOVER,
                                             text_color="white", font=ctk.CTkFont(size=13, weight="bold"),
                                             command=self._generate_appearance_summary)
        self._appearance_btn.pack(anchor="w", padx=16, pady=(0, 14))

    def _generate_appearance_summary(self):
        self._appearance_btn.configure(state="disabled", text="⏳ 正在总结图片...")
        threading.Thread(target=self._appearance_summary_worker, daemon=True).start()

    def _appearance_summary_worker(self):
        import asyncio
        from llm_factory import get_llm_client
        from appearance_ref import summarize_appearance, list_ref_images
        try:
            if not list_ref_images():
                self._safe_after(0, lambda: self._on_appearance_summary_done("", "「外貌设定」文件夹中没有图片，请先放入参考图。"))
                return

            async def _run():
                client = get_llm_client()
                try:
                    return await summarize_appearance(client)
                finally:
                    await client.aclose()

            result = asyncio.run(_run())
            self._safe_after(0, lambda: self._on_appearance_summary_done(result, ""))
        except Exception as e:
            self._safe_after(0, lambda: self._on_appearance_summary_done("", str(e)))

    def _on_appearance_summary_done(self, summary, error):
        self._appearance_btn.configure(state="normal", text="生成外貌总结")
        if error:
            messagebox.showerror("生成失败", error)
            return
        if summary:
            self._appearance_summary_text.delete("1.0", "end")
            self._appearance_summary_text.insert("1.0", summary)
            messagebox.showinfo("生成完成", "外貌总结已生成并保存，后续生成自拍会自动使用该总结锚定外貌。")
        else:
            messagebox.showwarning("提示", "生成结果为空，请检查「外貌设定」文件夹中的图片。")

    # ===================== 图片模型 =====================

    def _refresh_image_models(self):
        if getattr(self, "_refreshing_image_models", False):
            return
        self._refreshing_image_models = True
        api_key = self._dashscope_key_var.get().strip() or config.DASHSCOPE_API_KEY

        def worker():
            import asyncio
            from image_gen import list_image_models
            models = asyncio.run(list_image_models(api_key))
            self._safe_after(0, lambda: self._on_image_models_loaded(models))

        threading.Thread(target=worker, daemon=True).start()

    def _on_image_models_loaded(self, models):
        self._refreshing_image_models = False
        if models:
            self._image_model_combobox.configure(values=models)
            current = self._image_model_var.get()
            if current not in models and models:
                self._image_model_var.set(models[0])

    def _refresh_tts_models(self):
        """后台线程拉取 DashScope TTS 模型列表填充下拉框。"""
        if getattr(self, "_refreshing_tts_models", False):
            return
        self._refreshing_tts_models = True
        api_key = self._mimo_key_var.get().strip() or runtime.MIMO_API_KEY

        def worker():
            import asyncio
            from 语音.tts import list_tts_models
            models = asyncio.run(list_tts_models(api_key))
            self._safe_after(0, lambda: self._on_tts_models_loaded(models))

        threading.Thread(target=worker, daemon=True).start()

    def _on_tts_models_loaded(self, models):
        self._refreshing_tts_models = False
        if models:
            self._tts_model_combobox.configure(values=models)
            current = self._tts_model_var.get()
            if current not in models and models:
                self._tts_model_var.set(models[0])

    def _refresh_asr_models(self):
        """后台线程拉取小米 ASR 模型列表填充下拉框。"""
        if getattr(self, "_refreshing_asr_models", False):
            return
        self._refreshing_asr_models = True
        api_key = self._mimo_key_var.get().strip() or runtime.MIMO_API_KEY

        def worker():
            import asyncio
            from 语音.asr import list_asr_models
            models = asyncio.run(list_asr_models(api_key))
            self._safe_after(0, lambda: self._on_asr_models_loaded(models))

        threading.Thread(target=worker, daemon=True).start()

    def _on_asr_models_loaded(self, models):
        self._refreshing_asr_models = False
        if models:
            self._asr_model_combobox.configure(values=models)
            current = self._asr_model_var.get()
            if current not in models and models:
                self._asr_model_var.set(models[0])

    # ===================== Token 用量 =====================

    def _build_usage_tab(self, nb):
        tab = nb.add("Token 用量")
        tab.configure(fg_color=PANEL)

        summary = ctk.CTkFrame(tab, fg_color=CARD, corner_radius=12,
                               border_width=1, border_color=BORDER)
        summary.pack(fill="x", pady=(0, 12))
        self._usage_vars = {
            "calls": tk.StringVar(value="-"),
            "prompt": tk.StringVar(value="-"),
            "completion": tk.StringVar(value="-"),
            "total": tk.StringVar(value="-"),
            "local": tk.StringVar(value="-"),
        }
        cols = [
            ("累计调用", "calls"), ("输入 tokens", "prompt"),
            ("输出 tokens", "completion"), ("总计 tokens", "total"),
            ("本地模型调用", "local"),
        ]
        inner = ctk.CTkFrame(summary, fg_color="transparent")
        inner.pack(fill="x", padx=16, pady=12)
        for i, (label, key) in enumerate(cols):
            ctk.CTkLabel(inner, text=f"{label}：", text_color=MUTED, font=ctk.CTkFont(size=12),
                         fg_color="transparent").grid(row=0, column=i * 2, sticky="w", pady=2)
            ctk.CTkLabel(inner, textvariable=self._usage_vars[key], text_color=PRIMARY,
                         font=ctk.CTkFont(size=15, weight="bold"),
                         fg_color="transparent").grid(row=0, column=i * 2 + 1, sticky="w", padx=(0, 24), pady=2)

        table_card = ctk.CTkFrame(tab, fg_color=CARD, corner_radius=12,
                                  border_width=1, border_color=BORDER)
        table_card.pack(fill="both", expand=True)
        ctk.CTkLabel(table_card, text="分模型明细", text_color=PRIMARY,
                     font=ctk.CTkFont(size=14, weight="bold"),
                     fg_color="transparent").pack(anchor="w", padx=16, pady=(12, 6))

        self._usage_tree = ttk.Treeview(
            table_card, columns=("model", "calls", "prompt", "completion", "total"),
            show="headings", height=8)
        for col, title, width in (
            ("model", "模型", 260), ("calls", "调用次数", 90),
            ("prompt", "输入 tokens", 110), ("completion", "输出 tokens", 110),
            ("total", "总计 tokens", 110),
        ):
            self._usage_tree.heading(col, text=title)
            self._usage_tree.column(col, width=width, anchor="center" if col != "model" else "w")
        self._usage_tree.pack(fill="both", expand=True, padx=16, pady=(0, 12))

        bar = ctk.CTkFrame(tab, fg_color="transparent")
        bar.pack(fill="x", pady=(10, 0))
        ctk.CTkButton(bar, text="立即刷新", width=100, height=32, fg_color=SOFT_BG,
                      hover_color=SOFT_HOVER, text_color=PRIMARY, font=ctk.CTkFont(size=12),
                      command=self._refresh_usage).pack(side="left")
        ctk.CTkButton(bar, text="清零", width=80, height=32, fg_color=DANGER,
                      hover_color="#c9302c", text_color="white", font=ctk.CTkFont(size=12),
                      command=self._reset_usage).pack(side="left", padx=8)
        self._refresh_usage()

    def _refresh_usage(self):
        from usage import usage_tracker
        s = usage_tracker.snapshot()
        t = s["totals"]
        self._usage_vars["calls"].set(str(s["calls"]))
        self._usage_vars["prompt"].set(str(t["prompt_tokens"]))
        self._usage_vars["completion"].set(str(t["completion_tokens"]))
        self._usage_vars["total"].set(str(t["total_tokens"]))
        self._usage_vars["local"].set(str(s.get("local_calls", 0)))
        for item in self._usage_tree.get_children():
            self._usage_tree.delete(item)
        for model, m in s["per_model"].items():
            self._usage_tree.insert(
                "", "end",
                values=(model, m["calls"], m["prompt_tokens"], m["completion_tokens"], m["total_tokens"]),
            )

    def _reset_usage(self):
        from usage import usage_tracker
        if messagebox.askyesno("清零", "确定要清零 Token 用量统计吗？"):
            usage_tracker.reset()
            self._refresh_usage()

    def _poll_usage(self):
        """每 1 秒刷新一次 Token 用量"""
        if self._usage_vars:
            self._refresh_usage()
        self.root.after(1000, self._poll_usage)

    # ===================== 运行日志 =====================

    def _build_log_tab(self):
        tab = self._notebook.add("📄 运行日志")
        tab.configure(fg_color=PANEL)
        self._log_text = ctk.CTkTextbox(
            tab, wrap="word", fg_color=INPUT_BG, text_color=TEXT,
            border_color=BORDER, border_width=1, corner_radius=10,
            font=ctk.CTkFont(family="Consolas", size=12), state="disabled")
        self._log_text.pack(fill="both", expand=True, padx=12, pady=(10, 6))

        bottom_bar = ctk.CTkFrame(tab, fg_color="transparent")
        bottom_bar.pack(fill="x", pady=(4, 0))
        ctk.CTkButton(bottom_bar, text="清空日志", width=100, height=32, fg_color=SOFT_BG,
                      hover_color=SOFT_HOVER, text_color=PRIMARY, font=ctk.CTkFont(size=12),
                      command=self._clear_log).pack(side="left")
        self._auto_scroll_var = tk.BooleanVar(value=True)
        ctk.CTkCheckBox(bottom_bar, text="自动滚动", variable=self._auto_scroll_var,
                        text_color=TEXT, fg_color=PRIMARY, hover_color=PRIMARY_HOVER,
                        font=ctk.CTkFont(size=12)).pack(side="right")

    def _poll_logs(self):
        """每 100ms 从队列取日志并显示。"""
        while True:
            try:
                msg = self.log_queue.get_nowait()
                self._log_text.configure(state="normal")
                self._log_text.insert("end", msg + "\n")
                self._log_text.configure(state="disabled")
                if self._auto_scroll_var.get():
                    self._log_text.see("end")
            except queue.Empty:
                break
        self.root.after(100, self._poll_logs)

    def _clear_log(self):
        self._log_text.configure(state="normal")
        self._log_text.delete("1.0", "end")
        self._log_text.configure(state="disabled")

    # ===================== QQ 检测 =====================

    def _detect_qq(self):
        self._qq_status_var.set("检测中...")
        self._qq_status_label.configure(text_color=WARNING)
        self._qq_version_var.set("-")
        self._qq_path_var.set("-")
        threading.Thread(target=self._qq_detect_worker, daemon=True).start()

    def _qq_detect_worker(self):
        found, path, version = detect_qq()
        self._safe_after(0, lambda: self._on_qq_detected(found, path, version))

    def _on_qq_detected(self, found, path, version):
        if found:
            self._qq_status_var.set("✅ 已安装")
            self._qq_status_label.configure(text_color=SUCCESS)
            self._qq_version_var.set(version or "未知版本")
            self._qq_path_var.set(path)
        else:
            self._qq_status_var.set("❌ 未检测到 QQ")
            self._qq_status_label.configure(text_color=DANGER)
            self._qq_version_var.set("-")
            self._qq_path_var.set("未检测到 QQ")

    # ===================== SNOWLUMA 控制 =====================

    def _start_snowluma(self):
        if self._snowluma_proc and self._snowluma_proc.poll() is None:
            messagebox.showwarning("提示", "SNOWLUMA 已在运行中")
            return
        if shutil.which("node") is None:
            messagebox.showerror("启动失败", "未检测到 Node.js，请先安装 Node.js 22+")
            return
        # 版本检查放到后台线程，避免 subprocess.run 阻塞主线程最长 15s（GUI 冻结）
        self._sl_btn_start.configure(state="disabled", text="⏳ 检查中...")
        threading.Thread(target=self._snowluma_check_worker, daemon=True).start()

    def _snowluma_check_worker(self):
        """后台检查 Node 版本；通过后回到主线程启动 SNOWLUMA。"""
        try:
            check = subprocess.run(["node", "check-node-version.cjs"], cwd=SNOWLUMA_DIR,
                                   capture_output=True, text=True, timeout=15,
                                   creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
            ok = check.returncode == 0
            err = (check.stderr or "").strip() if not ok else ""
        except Exception as e:
            ok, err = False, str(e)
        self._safe_after(0, lambda: self._start_snowluma_after_check(ok, err))

    def _start_snowluma_after_check(self, ok, err):
        self._sl_btn_start.configure(state="normal", text="▶ 启动 SNOWLUMA")
        if not ok:
            messagebox.showerror("启动失败", "Node.js 版本不满足要求：\n" + (err or "未知错误"))
            return

        try:
            self._snowluma_proc = subprocess.Popen(
                ["node", "index.mjs"],
                cwd=SNOWLUMA_DIR,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
        except Exception as e:
            messagebox.showerror("启动失败", str(e))
            self._snowluma_proc = None
            return

        self._snowluma_status_var.set("● 运行中")
        self._snowluma_status_label.configure(text_color=SUCCESS)
        self._sl_btn_start.configure(state="disabled")
        self._sl_btn_stop.configure(state="normal")
        self._snowluma_output_thread = threading.Thread(
            target=self._read_snowluma_output, daemon=True)
        self._snowluma_output_thread.start()
        logging.getLogger("snowluma").info("SNOWLUMA 已启动 (PID=%s)", self._snowluma_proc.pid)

    def _read_snowluma_output(self):
        proc = self._snowluma_proc
        if not proc or not proc.stdout:
            return
        logger = logging.getLogger("snowluma")
        for line in iter(proc.stdout.readline, ""):
            if line:
                logger.info(line.rstrip())
        if proc.stdout:
            try:
                proc.stdout.close()
            except Exception:
                pass

    def _stop_snowluma(self):
        proc = self._snowluma_proc
        if proc and proc.poll() is None:
            try:
                proc.terminate()
                proc.wait(timeout=5)
            except (subprocess.TimeoutExpired, OSError):
                try:
                    proc.kill()
                except OSError:
                    pass
        self._snowluma_proc = None
        self._snowluma_status_var.set("● 已停止")
        self._snowluma_status_label.configure(text_color=MUTED)
        self._sl_btn_start.configure(state="normal")
        self._sl_btn_stop.configure(state="disabled")
        logging.getLogger("snowluma").info("SNOWLUMA 已停止")

    def _restart_snowluma(self):
        """重启 SNOWLUMA：若在运行则先停止，等进程完全退出后再启动。"""
        proc = self._snowluma_proc
        if proc is None or proc.poll() is not None:
            self._start_snowluma()
            return
        logging.getLogger("snowluma").info("SNOWLUMA 正在重启...")
        self._stop_snowluma()
        self.root.after(300, lambda: self._wait_snowluma_exit(proc))

    def _wait_snowluma_exit(self, proc):
        if proc and proc.poll() is None:
            self.root.after(300, lambda: self._wait_snowluma_exit(proc))
        else:
            self._start_snowluma()

    def _poll_snowluma_status(self):
        if self._snowluma_proc and self._snowluma_proc.poll() is None:
            if self._snowluma_status_var.get() != "● 运行中":
                self._snowluma_status_var.set("● 运行中")
                self._snowluma_status_label.configure(text_color=SUCCESS)
                self._sl_btn_start.configure(state="disabled")
                self._sl_btn_stop.configure(state="normal")
        else:
            if self._snowluma_proc is not None or self._snowluma_status_var.get() != "● 已停止":
                if self._snowluma_proc is not None:
                    self._snowluma_proc = None
                    self._snowluma_status_var.set("● 已停止")
                    self._snowluma_status_label.configure(text_color=MUTED)
                    self._sl_btn_start.configure(state="normal")
                    self._sl_btn_stop.configure(state="disabled")
        self.root.after(1000, self._poll_snowluma_status)

    # ===================== 配置操作 =====================

    def _sync_birthdate(self):
        """把年/月/日三个下拉框合成 YYYY-MM-DD 写入 _entries（供保存使用）。"""
        v = getattr(self, "_birth_vars", None)
        e = self._entries.get("GIRLFRIEND_BIRTHDATE") if hasattr(self, "_entries") else None
        if not v or e is None:
            return
        y, m, d = v["y"].get().strip(), v["m"].get().strip(), v["d"].get().strip()
        if y and m and d:
            e.set(f"{int(y):04d}-{int(m):02d}-{int(d):02d}")
        else:
            e.set("")

    # 配置对比：字段中文名（供"本次改动"展示）
    _CONFIG_LABELS = {
        "GIRLFRIEND_NAME": "名字", "GIRLFRIEND_AGE": "年龄", "GIRLFRIEND_BIRTHDATE": "出生日期",
        "GIRLFRIEND_IDENTITY": "身份", "GIRLFRIEND_CHARACTER": "性格",
        "GIRLFRIEND_APPEARANCE": "外貌设定", "GIRLFRIEND_LANGUAGE_STYLE": "语言风格",
        "GIRLFRIEND_SPECIAL_REACTIONS": "特殊反应", "GIRLFRIEND_CONSTRAINTS": "底层约束",
        "GIRLFRIEND_SCENARIO": "当前场景", "SYSTEM_PROMPT": "自定义提示词",
        "TEMPERATURE": "Temperature", "MAX_TOKENS": "最大 Token 数", "MAX_HISTORY_LENGTH": "对话记忆轮数",
        "REPLY_COOLDOWN_MIN": "最短冷却", "REPLY_COOLDOWN_MAX": "最长冷却",
        "STICKER_PROBABILITY": "表情包概率", "STICKER_DIR": "表情包目录",
        "PROACTIVE_INTERVAL_MIN": "主动间隔(短)", "PROACTIVE_INTERVAL_MAX": "主动间隔(长)",
        "PROACTIVE_PROBABILITY": "主动概率", "PROACTIVE_GAP_MIN": "防打扰(分)",
        "PROACTIVE_FOLLOWUP_HOURS": "追问时长(时)", "PROACTIVE_FOLLOWUP_MAX": "追问上限(次)",
        "NIGHT_SILENCE_HOURS": "晚安静默(时)",
        "WEATHER_CITY": "天气城市",
        "THINKING_MODE": "思考模式",
        "IMAGE_GEN_ENABLED": "图片生成开关", "IMAGE_GEN_SIZE": "图片尺寸", "IMAGE_BACKEND": "图片后端",
        "DASHSCOPE_BASE_URL": "百炼地址", "COMFYUI_URL": "ComfyUI 地址", "COMFYUI_WORKFLOW_FILE": "工作流文件",
        "IMAGE_PROMPT_USE_LLM": "图生文走大模型", "IMAGE_COMMENT_PROBABILITY": "发图评论概率",
        "TTS_PROBABILITY": "语音回复概率", "TTS_VOICE_DESCRIPTION": "音色描述", "TTS_MODEL": "TTS 模型",
        "MIMO_API_BASE_URL": "小米语音地址",
        "ASR_ENABLED": "语音识别开关", "ASR_LANGUAGE": "识别语种", "ASR_MODEL": "ASR 模型",
    }
    # 这些密钥只提示"已修改"，不展示内容
    _SECRET_KEYS = {"MIMO_API_KEY", "DEEPSEEK_API_KEY", "DASHSCOPE_API_KEY", "ONEBOT_ACCESS_TOKEN"}

    @staticmethod
    def _fmt_cfg_value(v):
        s = str(v)
        return s if len(s) <= 50 else s[:50] + "…"

    # ===================== 设置改动检测 / 保存按钮 =====================

    def _hook_dirty_tracking(self):
        """扫描所有配置控件，挂"值变更"监听：任何设置改动 → 显示保存按钮。

        只跟踪配置变量；显示用变量（状态/用量轮询）不跟踪，避免误报。
        """
        if getattr(self, "_dirty_hooked", False):
            return
        self._dirty_hooked = True
        self._dirty = False
        self._suppress_dirty = False
        # 显示用变量：由轮询/状态刷新写，不是配置，排除
        display_vars = frozenset((
            "_qq_status_var", "_qq_version_var", "_qq_path_var",
            "_snowluma_status_var", "_growth_evo_var", "_auto_scroll_var",
        ))
        seen = set()

        def _track(v):
            if id(v) in seen:
                return
            seen.add(id(v))
            try:
                v.trace_add("write", lambda *a, **k: self._on_setting_changed())
            except Exception:
                pass

        for name, v in vars(self).items():
            if isinstance(v, tk.Variable) and name not in display_vars:
                _track(v)
        for _c in ("_sliders", "_birth_vars", "_entries"):
            for v in (getattr(self, _c, None) or {}).values():
                if isinstance(v, tk.Variable):
                    _track(v)
        # 文本控件（人设/约束/音色描述等）无变量，监听按键
        for w in (getattr(self, "_text_widgets", None) or {}).values():
            try:
                w.bind("<KeyRelease>", lambda e: self._on_setting_changed())
            except Exception:
                pass

    def _on_setting_changed(self, *_a):
        """任一配置控件被修改时调用：标记未保存并显示保存按钮。"""
        if getattr(self, "_suppress_dirty", False):
            return
        self._dirty = True
        btn = getattr(self, "_save_btn", None)
        if btn is not None:
            try:
                btn.place(relx=1.0, rely=0.5, anchor="e", x=-140)
            except Exception:
                pass

    def _clear_dirty(self):
        """保存/重载完成后：清除未保存标记并隐藏保存按钮。"""
        self._dirty = False
        btn = getattr(self, "_save_btn", None)
        if btn is not None:
            try:
                btn.place_forget()
            except Exception:
                pass

    def _save_from_bar(self):
        """顶部保存按钮：保存配置后清除未保存标记（期间抑制误触发）。"""
        try:
            self._suppress_dirty = True
            self._save_config()
        finally:
            self._suppress_dirty = False
            self._clear_dirty()

    def _save_config(self):
        """从 GUI 控件读取值，写入 runtime 并持久化。"""
        try:
            self._save_config_inner()
        except (ValueError, TypeError) as e:
            # 数值输入非法（如冷却时间填了字母）时明确提示，而不是静默失败
            messagebox.showerror("保存失败", f"配置项数值格式不正确：{e}")

    def _show_config_diff(self, old_data, new_data):
        """对比上次保存的配置，弹窗列出本次改动（密钥只提示已修改）。"""
        changes = []
        for key, new_val in new_data.items():
            old_val = old_data.get(key, "<空>")
            if str(old_val) == str(new_val):
                continue
            label = self._CONFIG_LABELS.get(key, key)
            if key in self._SECRET_KEYS:
                changes.append(f"· {label}：已修改（内容保密）")
                continue
            if old_val == "<空>":
                changes.append(f"· {label}：{self._fmt_cfg_value(new_val)}（新增）")
            else:
                changes.append(f"· {label}：{self._fmt_cfg_value(old_val)} → {self._fmt_cfg_value(new_val)}")
        if not changes:
            messagebox.showinfo("保存完成", "配置已保存，与上次保存相比没有修改。")
            return
        win = ctk.CTkToplevel(self.root)
        win.title("本次配置改动")
        win.geometry("640x440")
        win.transient(self.root)
        win.grab_set()
        txt = ctk.CTkTextbox(win, wrap="word", fg_color=INPUT_BG, text_color=TEXT,
                             border_color=BORDER, border_width=1, font=ctk.CTkFont(size=13))
        txt.pack(fill="both", expand=True, padx=12, pady=(12, 8))
        txt.insert("1.0", "本次保存相比上次修改了以下配置：\n\n" + "\n".join(changes) +
                   "\n\n（人设与参数保存后立即生效；连接设置中标注「🔁 需重启 Bot」的项，在「首页 → 重启 bot」后生效）")
        txt.configure(state="disabled")
        ctk.CTkButton(win, text="知道了", height=32, fg_color=PRIMARY, hover_color=PRIMARY_HOVER,
                      text_color="white", command=win.destroy).pack(pady=(0, 12))

    def _save_config_inner(self):
        # 读取上次保存的配置（磁盘），保存后用于对比"本次改了哪些"
        old_data = {}
        try:
            with open(RUNTIME_CONFIG_FILE, "r", encoding="utf-8") as f:
                old_data = json.load(f)
        except (OSError, json.JSONDecodeError):
            pass
        str_fields = ["GIRLFRIEND_NAME", "GIRLFRIEND_AGE", "GIRLFRIEND_BIRTHDATE",
                     "GIRLFRIEND_IDENTITY", "GIRLFRIEND_CHARACTER"]
        data = {k: self._entries[k].get() for k in str_fields}
        for key, widget in self._text_widgets.items():
            data[key] = widget.get("1.0", "end-1c")
        prompt_text = getattr(self, "_prompt_text", None)
        data["SYSTEM_PROMPT"] = prompt_text.get("1.0", "end-1c") if prompt_text else runtime.SYSTEM_PROMPT
        data["GIRLFRIEND_SCENARIO"] = ""

        for key, var in self._sliders.items():
            data[key] = var.get()

        data["REPLY_COOLDOWN_MIN"] = float(self._reply_cd_min_var.get() or 0)
        data["REPLY_COOLDOWN_MAX"] = float(self._reply_cd_max_var.get() or 3)

        data["STICKER_PROBABILITY"] = self._sticker_prob_var.get()
        data["STICKER_DIR"] = self._sticker_dir_var.get().strip() or "晚晚/图片/表情包"

        data["PROACTIVE_ONLY_USER_ID"] = self._proactive_only_var.get().strip()
        data["PROACTIVE_INTERVAL_MIN"] = int(self._proactive_interval_var.get() or 45)
        data["PROACTIVE_INTERVAL_MAX"] = int(self._proactive_interval_max_var.get() or 75)
        data["PROACTIVE_PROBABILITY"] = self._proactive_prob_var.get()
        data["PROACTIVE_GAP_MIN"] = int(self._proactive_gap_var.get() or 10)
        data["PROACTIVE_FOLLOWUP_HOURS"] = float(self._proactive_followup_hours_var.get() or 2)
        data["PROACTIVE_FOLLOWUP_MAX"] = int(self._proactive_followup_max_var.get() or 2)
        data["NIGHT_SILENCE_HOURS"] = float(self._night_silence_var.get() or 0)

        data["WEATHER_CITY"] = self._weather_city_var.get().strip() or "南昌"

        data["THINKING_MODE"] = 1 if self._thinking_mode_var.get() else 0

        data["IMAGE_GEN_ENABLED"] = 1 if self._imagegen_enabled_var.get() else 0
        data["IMAGE_GEN_SIZE"] = self._image_size_var.get().strip() or "1024*1024"
        data["IMAGE_BACKEND"] = self._image_backend_var.get() or "dashscope"
        data["DASHSCOPE_BASE_URL"] = self._dashscope_base_url_var.get().strip() or "https://dashscope.aliyuncs.com"
        data["COMFYUI_URL"] = self._comfy_url_var.get().strip() or "http://127.0.0.1:8188"
        data["COMFYUI_WORKFLOW_FILE"] = self._comfy_wf_var.get().strip() or "comfy_workflow.json"
        data["IMAGE_PROMPT_USE_LLM"] = 1 if self._image_prompt_use_llm_var.get() else 0
        data["IMAGE_COMMENT_PROBABILITY"] = self._comment_prob_var.get()
        data["AUTO_ILLUSTRATE_PROBABILITY"] = self._illustrate_prob_var.get()
        data["ANNIVERSARY_DATE"] = self._anniversary_var.get().strip() or "2025-07-20"
        data["QZONE_SELF_UIN"] = self._qzone_self_uin_var.get().strip()
        data["QZONE_FEED_COMMENT_PROB"] = self._qzone_comment_prob_var.get()
        data["QZONE_POSTS_PER_DAY"] = int(self._qzone_posts_var.get() or 3)
        data["QZONE_POST_IMAGE_PROB"] = self._qzone_img_prob_var.get()
        data["QZONE_POST_INTERVAL_MIN"] = (int(self._qzone_interval_min_h_var.get() or 0)
                                           + int(self._qzone_interval_min_m_var.get() or 0) / 60)
        data["QZONE_POST_INTERVAL_MAX"] = (int(self._qzone_interval_max_h_var.get() or 0)
                                           + int(self._qzone_interval_max_m_var.get() or 0) / 60)
        data["QZONE_IMAGE_CHECK"] = 1 if self._qzone_img_check_var.get() else 0

        data["TTS_PROBABILITY"] = self._tts_prob_var.get()
        data["TTS_VOICE_DESCRIPTION"] = self._tts_voice_desc_text.get("1.0", "end-1c").strip()
        data["TTS_MODEL"] = self._tts_model_var.get().strip() or "mimo-v2.5-tts-voicedesign"
        data["MIMO_API_KEY"] = self._mimo_key_var.get().strip()
        data["MIMO_API_BASE_URL"] = self._mimo_base_url_var.get().strip() or "https://api.xiaomimimo.com/v1"
        data["ASR_ENABLED"] = 1 if self._asr_enabled_var.get() else 0
        data["ASR_LANGUAGE"] = self._asr_language_var.get().strip() or "auto"
        data["ASR_MODEL"] = self._asr_model_var.get().strip() or "mimo-v2.5-asr"

        runtime.update(data)
        runtime.save_to_file()

        try:
            os.makedirs(data["STICKER_DIR"], exist_ok=True)
        except OSError:
            pass

        self._save_env()

        # 对比上次保存的配置，展示本次改动
        try:
            self._show_config_diff(old_data, data)
        except Exception as e:
            logging.getLogger("gui").warning("展示配置改动失败: %s", e)
            messagebox.showinfo("保存成功", "配置已保存。人设与参数立即生效；连接设置中标注「🔁 需重启 Bot」的项，重启 bot 后生效。")

    def _test_api_key(self, service):
        """测试指定服务的 API Key 是否有效（读取输入框当前值，后台线程执行）。

        service: deepseek / dashscope / mimo —— 分别请求各自的 /models 接口。
        """
        if service == "deepseek":
            key = self._api_key_var.get().strip()
            base = self._base_url_var.get().strip() or "https://api.deepseek.com"
            url = base.rstrip("/") + "/models"
        elif service == "dashscope":
            key = self._dashscope_key_var.get().strip()
            base = self._dashscope_base_url_var.get().strip() or "https://dashscope.aliyuncs.com"
            url = base.rstrip("/") + "/compatible-mode/v1/models"
        elif service == "mimo":
            key = self._mimo_key_var.get().strip()
            base = self._mimo_base_url_var.get().strip() or "https://api.xiaomimimo.com/v1"
            url = base.rstrip("/") + "/models"
        else:
            return
        if not key:
            messagebox.showwarning("测试 API Key", "请先填写 API Key 再测试。")
            return

        def worker():
            try:
                import httpx
                with httpx.Client(timeout=10, follow_redirects=True) as client:
                    resp = client.get(url, headers={"Authorization": f"Bearer {key}"})
            except Exception as e:
                self._safe_after(0, lambda: messagebox.showerror(
                    "测试 API Key", f"连接失败（网络/地址问题）：\n{e}"))
                return
            if resp.status_code == 200:
                count = "?"
                try:
                    data = resp.json()
                    models = data.get("data") or data.get("models") or []
                    count = len(models) if isinstance(models, list) else "?"
                except Exception:
                    pass
                self._safe_after(0, lambda: messagebox.showinfo(
                    "测试 API Key", f"✅ Key 有效（HTTP 200），可用模型 {count} 个"))
            elif resp.status_code in (401, 403):
                self._safe_after(0, lambda: messagebox.showerror(
                    "测试 API Key", "❌ Key 无效：认证失败（HTTP %d）。\n请检查 Key 是否正确或已过期。" % resp.status_code))
            else:
                body = resp.text[:150].replace("\n", " ")
                self._safe_after(0, lambda: messagebox.showerror(
                    "测试 API Key", "❌ 测试失败（HTTP %d）：\n%s" % (resp.status_code, body)))

        threading.Thread(target=worker, daemon=True).start()

    def _refresh_models(self, silent=False):
        """在后台线程请求 {base_url}/models 获取模型列表，更新下拉框。"""
        if getattr(self, "_refreshing_models", False):
            return
        self._refreshing_models = True
        api_key = self._api_key_var.get().strip() or config.DEEPSEEK_API_KEY
        base_url = self._base_url_var.get().strip() or config.DEEPSEEK_BASE_URL

        def worker():
            import asyncio
            from deepseek_client import DeepSeekClient

            async def _run():
                client = DeepSeekClient()
                try:
                    return await client.list_models(api_key=api_key, base_url=base_url)
                finally:
                    await client.aclose()

            models, err = None, None
            try:
                models = asyncio.run(_run())
            except Exception as e:
                err = str(e)
            self._safe_after(0, lambda: self._on_models_loaded(models, err, silent))

        threading.Thread(target=worker, daemon=True).start()

    def _on_models_loaded(self, models, error, silent):
        self._refreshing_models = False
        if models:
            current = self._model_var.get()
            self._model_combobox.configure(values=models)
            if current not in models:
                self._model_var.set(models[0])

            self._vision_model_combobox.configure(values=models)
            vcurrent = self._vision_model_var.get()
            vision_models = [m for m in models if "vision" in m.lower()]
            if vcurrent not in models:
                self._vision_model_var.set(vision_models[0] if vision_models else models[0])

            if not silent:
                messagebox.showinfo(
                    "模型列表",
                    f"已获取 {len(models)} 个模型：\n" + "、".join(models),
                )
        elif not silent:
            messagebox.showerror("获取模型列表失败", error or "未知错误")

    def _save_env(self):
        """回写连接设置到 .env 文件。"""
        env_path = os.path.join(BASE_DIR, "晚晚", "配置", ".env")
        try:
            with open(env_path, "r", encoding="utf-8") as f:
                lines = f.readlines()
        except FileNotFoundError:
            lines = []

        updates = {
            "DEEPSEEK_API_KEY": self._api_key_var.get().strip(),
            "DEEPSEEK_MODEL": self._model_var.get(),
            "DEEPSEEK_VISION_MODEL": self._vision_model_var.get(),
            "DEEPSEEK_BASE_URL": self._base_url_var.get(),
            "ONEBOT_WS_URL": self._ws_url_var.get(),
            "ONEBOT_ACCESS_TOKEN": self._ws_token_var.get(),
            "DASHSCOPE_API_KEY": self._dashscope_key_var.get().strip(),
            "DASHSCOPE_BASE_URL": self._dashscope_base_url_var.get().strip() or "https://dashscope.aliyuncs.com",
            "DASHSCOPE_IMAGE_MODEL": self._image_model_var.get(),
            "MIMO_API_BASE_URL": self._mimo_base_url_var.get().strip() or "https://api.xiaomimimo.com/v1",
        }

        new_lines = []
        seen = set()
        for line in lines:
            key = line.split("=")[0].strip() if "=" in line else ""
            if key in updates:
                new_lines.append(f"{key}={updates[key]}\n")
                seen.add(key)
            else:
                new_lines.append(line)
        for key, val in updates.items():
            if key not in seen and val:
                new_lines.append(f"{key}={val}\n")

        with open(env_path, "w", encoding="utf-8") as f:
            f.writelines(new_lines)

    def _reload_config(self):
        """重新加载配置到 GUI。"""
        self._suppress_dirty = True  # 重载期间批量写控件，不触发"改动"标记
        runtime.load_from_file()
        for key, var in self._entries.items():
            var.set(str(getattr(runtime, key, "")))
        # 出生日期：重新解析到年/月/日下拉框
        if hasattr(self, "_birth_vars"):
            try:
                _y, _m, _d = str(getattr(runtime, "GIRLFRIEND_BIRTHDATE", "") or "").split("-")
                self._birth_vars["y"].set(_y)
                self._birth_vars["m"].set(str(int(_m)))
                self._birth_vars["d"].set(str(int(_d)))
            except ValueError:
                pass
        for key, widget in self._text_widgets.items():
            widget.delete("1.0", "end")
            widget.insert("1.0", getattr(runtime, key, ""))
        prompt_text = getattr(self, "_prompt_text", None)
        if prompt_text:
            prompt_text.delete("1.0", "end")
            prompt_text.insert("1.0", runtime.SYSTEM_PROMPT)
        for key, var in self._sliders.items():
            var.set(float(getattr(runtime, key, 0)))
        self._reply_cd_min_var.set(str(float(runtime.REPLY_COOLDOWN_MIN or 0)))
        self._reply_cd_max_var.set(str(float(runtime.REPLY_COOLDOWN_MAX or 3)))
        self._sticker_prob_var.set(float(runtime.STICKER_PROBABILITY or 0))
        self._sticker_prob_label.configure(text=f"{float(runtime.STICKER_PROBABILITY or 0)*100:.0f}%")
        self._sticker_dir_var.set(runtime.STICKER_DIR)
        self._illustrate_prob_var.set(float(runtime.AUTO_ILLUSTRATE_PROBABILITY or 0))
        self._anniversary_var.set(runtime.ANNIVERSARY_DATE or "2025-07-20")
        self._illustrate_prob_label.configure(text=f"{float(runtime.AUTO_ILLUSTRATE_PROBABILITY or 0)*100:.0f}%")
        self._qzone_self_uin_var.set(runtime.QZONE_SELF_UIN or "")
        self._qzone_comment_prob_var.set(float(runtime.QZONE_FEED_COMMENT_PROB or 0.35))
        self._qzone_comment_prob_label.configure(text=f"{float(runtime.QZONE_FEED_COMMENT_PROB or 0.35)*100:.0f}%")
        self._qzone_posts_var.set(str(int(runtime.QZONE_POSTS_PER_DAY or 3)))
        self._qzone_img_prob_var.set(float(runtime.QZONE_POST_IMAGE_PROB or 0.6))
        self._qzone_img_prob_label.configure(text=f"{float(runtime.QZONE_POST_IMAGE_PROB or 0.6)*100:.0f}%")
        h, m = _split_hours(runtime.QZONE_POST_INTERVAL_MIN or 2)
        self._qzone_interval_min_h_var.set(str(h))
        self._qzone_interval_min_m_var.set(str(m))
        h, m = _split_hours(runtime.QZONE_POST_INTERVAL_MAX or 4)
        self._qzone_interval_max_h_var.set(str(h))
        self._qzone_interval_max_m_var.set(str(m))
        self._qzone_img_check_var.set(bool(runtime.QZONE_IMAGE_CHECK))
        self._proactive_only_var.set(runtime.PROACTIVE_ONLY_USER_ID or "")
        self._proactive_interval_var.set(str(int(runtime.PROACTIVE_INTERVAL_MIN or 45)))
        self._proactive_interval_max_var.set(str(int(runtime.PROACTIVE_INTERVAL_MAX or 75)))
        self._proactive_prob_var.set(float(runtime.PROACTIVE_PROBABILITY or 0))
        self._proactive_prob_label.configure(text=f"{float(runtime.PROACTIVE_PROBABILITY or 0)*100:.0f}%")
        self._proactive_gap_var.set(str(int(runtime.PROACTIVE_GAP_MIN or 10)))
        self._proactive_followup_hours_var.set(f"{float(runtime.PROACTIVE_FOLLOWUP_HOURS or 2):g}")
        self._proactive_followup_max_var.set(str(int(runtime.PROACTIVE_FOLLOWUP_MAX or 2)))
        self._night_silence_var.set(f"{float(runtime.NIGHT_SILENCE_HOURS or 8):g}")
        self._weather_city_var.set(runtime.WEATHER_CITY)
        self._thinking_mode_var.set(bool(runtime.THINKING_MODE))
        self._imagegen_enabled_var.set(bool(runtime.IMAGE_GEN_ENABLED))
        self._image_size_var.set(runtime.IMAGE_GEN_SIZE or "1024*1024")
        self._image_backend_var.set(runtime.IMAGE_BACKEND or "dashscope")
        self._dashscope_base_url_var.set(runtime.DASHSCOPE_BASE_URL or "https://dashscope.aliyuncs.com")
        self._image_prompt_use_llm_var.set(bool(runtime.IMAGE_PROMPT_USE_LLM))
        self._comfy_url_var.set(runtime.COMFYUI_URL or "http://127.0.0.1:8188")
        self._comfy_wf_var.set(runtime.COMFYUI_WORKFLOW_FILE or "comfy_workflow.json")
        self._comment_prob_var.set(float(runtime.IMAGE_COMMENT_PROBABILITY or 0))
        self._comment_prob_label.configure(text=f"{float(runtime.IMAGE_COMMENT_PROBABILITY or 0)*100:.0f}%")
        self._tts_prob_var.set(float(runtime.TTS_PROBABILITY or 0))
        self._tts_prob_label.configure(text=f"{float(runtime.TTS_PROBABILITY or 0)*100:.0f}%")
        self._tts_voice_desc_text.delete("1.0", "end")
        self._tts_voice_desc_text.insert("1.0", runtime.TTS_VOICE_DESCRIPTION or "")
        self._tts_model_var.set(runtime.TTS_MODEL or "mimo-v2.5-tts-voicedesign")
        self._mimo_key_var.set(runtime.MIMO_API_KEY or "")
        self._mimo_base_url_var.set(runtime.MIMO_API_BASE_URL or "https://api.xiaomimimo.com/v1")
        self._asr_enabled_var.set(bool(runtime.ASR_ENABLED))
        self._asr_language_var.set(runtime.ASR_LANGUAGE or "auto")
        self._asr_model_var.set(runtime.ASR_MODEL or "mimo-v2.5-asr")
        # 连接设置区（config.* 在进程启动时冻结，这里同步显示磁盘/当前值）
        self._ws_url_var.set(config.ONEBOT_WS_URL)
        self._ws_token_var.set(config.ONEBOT_ACCESS_TOKEN)
        self._model_var.set(config.DEEPSEEK_MODEL)
        self._vision_model_var.set(config.DEEPSEEK_VISION_MODEL)
        self._base_url_var.set(config.DEEPSEEK_BASE_URL)
        self._api_key_var.set(config.DEEPSEEK_API_KEY)
        self._dashscope_key_var.set(config.DASHSCOPE_API_KEY)
        self._image_model_var.set(config.DASHSCOPE_IMAGE_MODEL)
        self._suppress_dirty = False
        self._clear_dirty()
        messagebox.showinfo("重载完成", "已从磁盘重新加载配置。")

    # ===================== Bot 控制 =====================

    def _start_bot(self):
        if self._bot_thread and self._bot_thread.is_alive():
            messagebox.showwarning("提示", "Bot 已在运行中")
            return
        import singleton
        if not singleton.acquire_lock():
            messagebox.showerror("启动失败", "已有 bot 实例在运行中。请先关闭旧实例再启动。")
            return
        self._stop_event.clear()
        self._btn_start.configure(state="disabled")
        self._btn_stop.configure(state="normal")
        self._status_label.configure(text="● 启动中...", text_color=WARNING)
        self._bot_thread = threading.Thread(target=self._run_bot, daemon=True)
        self._bot_thread.start()

    def _safe_after(self, delay, fn):
        """安全调度回调：窗口已销毁时静默跳过，避免后台线程 TclError 噪音。"""
        try:
            if self.root.winfo_exists():
                self.root.after(delay, fn)
        except Exception:
            pass

    def _apply_connection_settings(self):
        """把 GUI「连接设置」当前值同步到 config 类属性，使新建的 bot 立即生效。

        连接设置原本只写 .env，但 config.* 在进程启动时已冻结，不重启整个程序
        不会生效；这里在每次启动/重启 bot 前用 GUI 当前值覆盖一次。
        """
        api_key = self._api_key_var.get().strip()
        base_url = self._base_url_var.get().strip()
        model = self._model_var.get().strip()
        vision = self._vision_model_var.get().strip()
        ws_url = self._ws_url_var.get().strip()
        token = self._ws_token_var.get().strip()
        dash_key = self._dashscope_key_var.get().strip()
        dash_base = self._dashscope_base_url_var.get().strip()
        img_model = self._image_model_var.get().strip()
        if api_key:
            config.DEEPSEEK_API_KEY = api_key
        if base_url:
            config.DEEPSEEK_BASE_URL = base_url
        if model:
            config.DEEPSEEK_MODEL = model
        if vision:
            config.DEEPSEEK_VISION_MODEL = vision
        if ws_url:
            config.ONEBOT_WS_URL = ws_url
        if token:
            config.ONEBOT_ACCESS_TOKEN = token
        if dash_key:
            config.DASHSCOPE_API_KEY = dash_key
        if dash_base:
            config.DASHSCOPE_BASE_URL = dash_base
        if img_model:
            config.DASHSCOPE_IMAGE_MODEL = img_model

    def _run_bot(self):
        """在后台线程中运行 asyncio 事件循环。"""
        import asyncio
        from qq_bot import QQGirlfriendBot

        async def runner():
            self._loop = asyncio.get_running_loop()
            # 把 GUI「连接设置」当前值同步到 config，让新建的 bot 立即使用新值
            self._apply_connection_settings()
            bot = QQGirlfriendBot()
            self._bot_task = asyncio.current_task()
            # 启动竞态：若用户在 bot 初始化期间点了停止，直接退出不启动
            if self._stop_event.is_set():
                await bot.stop()
                return
            self._safe_after(0, lambda: self._status_label.configure(
                text="● 运行中", text_color=SUCCESS))
            try:
                await bot.start()
            except asyncio.CancelledError:
                pass
            except Exception:
                logging.getLogger("gui").exception("Bot 运行异常")
            finally:
                # 关闭 WS 与 DeepSeek 客户端，避免每次重启泄漏 httpx 连接池
                try:
                    await bot.stop()
                except Exception:
                    logging.getLogger("gui").exception("Bot 停止异常")

        try:
            asyncio.run(runner())
        finally:
            import singleton
            singleton.release_lock()
            self._safe_after(0, lambda: self._status_label.configure(
                text="● 已停止", text_color=MUTED))
            self._safe_after(0, lambda: self._btn_start.configure(state="normal"))
            self._safe_after(0, lambda: self._btn_stop.configure(state="disabled"))

    def _stop_bot(self):
        if not self._bot_thread or not self._bot_thread.is_alive():
            return
        self._status_label.configure(text="● 停止中...", text_color=WARNING)
        # 先置停止标志（runner 启动时会检查；之前只有 loop 存在才有效，
        # 刚点启动就点停止会因 _loop/_bot_task 还没赋值而失效）
        self._stop_event.set()
        if self._loop and self._bot_task:
            self._loop.call_soon_threadsafe(self._bot_task.cancel)

    def _restart_bot(self):
        """重启 bot（AI Bot）：若在运行则先停止，等线程完全退出后再启动。"""
        thread = self._bot_thread
        if not (thread and thread.is_alive()):
            self._start_bot()
            return
        logging.getLogger("gui").info("bot 正在重启...")
        self._stop_bot()
        self.root.after(300, lambda: self._wait_bot_exit(thread))

    def _wait_bot_exit(self, thread):
        if thread and thread.is_alive():
            self.root.after(300, lambda: self._wait_bot_exit(thread))
        else:
            self._start_bot()

    # ===================== 无边框窗口控制 =====================

    def _start_resize(self, event):
        self._resize_x = event.x_root
        self._resize_y = event.y_root
        self._resize_w = self.root.winfo_width()
        self._resize_h = self.root.winfo_height()

    def _on_resize(self, event):
        w = self._resize_w + (event.x_root - self._resize_x)
        h = self._resize_h + (event.y_root - self._resize_y)
        if w >= 860 and h >= 600:
            self.root.geometry(f"{w}x{h}")

    def _start_move(self, event):
        self._drag_x = event.x_root - self.root.winfo_x()
        self._drag_y = event.y_root - self.root.winfo_y()

    def _on_move(self, event):
        x = event.x_root - self._drag_x
        y = event.y_root - self._drag_y
        self.root.geometry(f"+{x}+{y}")

    # ===================== 系统托盘 =====================

    def _app_icon_path(self):
        ico_path = os.path.join(BASE_DIR, "晚晚", "界面", "icon.ico")
        src = os.path.join(BASE_DIR, "晚晚", "界面", "5.jpg")
        if not os.path.exists(src):
            return None
        if os.path.exists(ico_path):
            return ico_path
        try:
            from PIL import Image
            img = Image.open(src).convert("RGBA")
            w, h = img.size
            s = min(w, h)
            img = img.crop(((w - s) // 2, (h - s) // 2, (w + s) // 2, (h + s) // 2))
            img.save(ico_path, format="ICO", sizes=[(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
            return ico_path
        except Exception as e:
            logging.getLogger("gui").warning("生成应用图标失败: %s", e)
            return None

    def _create_tray(self):
        try:
            ico = self._app_icon_path()
            self._tray = tray.TrayIcon(
                tooltip="控制面板",
                on_left_click=lambda: self._safe_after(0, self._show_from_tray),
                menu_items=[("显示/隐藏", 1), ("退出", 2)],
                on_menu=lambda cmd: self._safe_after(0, lambda: self._on_tray_menu(cmd)),
                icon_path=ico,
            )
        except Exception as e:
            logging.getLogger("gui").warning("创建系统托盘失败: %s", e)
            self._tray = None

    def _on_tray_menu(self, cmd):
        if cmd == 1:
            self._toggle_visible()
        elif cmd == 2:
            self._quit_application()

    def _toggle_visible(self):
        if self.root.state() == "withdrawn" or not self.root.winfo_viewable():
            self._show_from_tray()
        else:
            self._hide_to_tray()

    def _show_from_tray(self):
        self.root.deiconify()
        self.root.lift()
        self.root.focus_force()

    def _hide_to_tray(self):
        self.root.withdraw()

    def _minimize_window(self):
        # 最小化按钮：隐藏到托盘（不关闭）
        self._hide_to_tray()

    def _on_close(self):
        # 关闭时让用户选择：最小化到托盘 或 退出
        if messagebox.askyesno(
            "关闭控制面板",
            "最小化到系统托盘？\n\n[是] 最小化到托盘（程序继续在后台运行）\n[否] 直接退出",
        ):
            self._hide_to_tray()
        else:
            self._quit_application()

    def _quit_application(self):
        self._stop_bot()
        # 等 bot 线程真正退出后再释放 bot.lock，
        # 避免旧实例还活着时锁已被删、另一个实例趁虚双开
        thread = self._bot_thread
        if thread and thread.is_alive():
            thread.join(timeout=5)
        self._stop_snowluma()
        if self._tray:
            try:
                self._tray.remove()
            except Exception:
                pass
            self._tray = None
        import singleton
        singleton.release_lock()
        self.root.destroy()

    def run(self):
        self.root.mainloop()


def main():
    import logging
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    # 检测是否已有管理面板在运行（防止多窗口同时存在）
    import singleton
    if not singleton.acquire_lock(GUI_LOCK_FILE):
        try:
            messagebox.showerror(
                "启动失败",
                "控制面板已有一个实例在运行，请勿重复打开！\n\n"
                "如需重新打开，请先关闭已运行的控制面板窗口。",
            )
        except Exception:
            pass
        return
    try:
        app = GirlfriendGUI()
        app.run()
    finally:
        singleton.release_lock(GUI_LOCK_FILE)


if __name__ == "__main__":
    main()