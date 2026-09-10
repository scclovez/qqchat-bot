# -*- coding: utf-8 -*-
"""PySide6 版控制面板（暮光陪伴 / 深夜模式主题）。

性能更强（Qt C++ 渲染）、QSS 美化、支持 PyInstaller 打包。
业务逻辑复用 晚晚/ 各模块（config / llm_providers / usage / appearance_ref / personality_state 等），
仅 UI 层使用 Qt。直接运行： python 晚晚/界面/gui_qt.py
"""
import ctypes
import json
import logging
import os
import queue
import subprocess
import sys
import threading
import time
from pathlib import Path

# ---------- 路径引导（直接运行本文件时也能找到项目模块） ----------
if os.path.dirname(__file__) not in sys.path:
    _ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
    if _ROOT not in sys.path:
        sys.path.insert(0, _ROOT)
import 路径  # noqa: E402
from 路径 import PROJECT_ROOT, CODE_ROOT, DATA_ROOT, data_path, code_path, is_frozen  # noqa: E402

from PySide6.QtCore import Qt, QTimer, QObject, QEvent, QPoint, QRect, QUrl  # noqa: E402
from PySide6.QtGui import QAction, QIcon, QPainter, QPixmap  # noqa: E402
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer  # noqa: E402
from PySide6.QtWidgets import (  # noqa: E402
    QApplication, QCheckBox, QComboBox, QDialog, QDoubleSpinBox, QFormLayout,
    QFrame, QGridLayout, QHBoxLayout, QHeaderView, QLabel, QLineEdit,
    QListWidget, QListWidgetItem,
    QMainWindow, QMenu, QMessageBox, QPlainTextEdit, QPushButton, QRadioButton,
    QScrollArea, QSizeGrip, QSlider, QSpinBox, QStackedWidget, QStyle, QStyleOptionTab,
    QSystemTrayIcon, QTabBar, QTabWidget, QTableWidget, QTableWidgetItem,
    QTextEdit, QToolTip, QVBoxLayout, QWidget,
)

from config import config, runtime  # noqa: E402
from usage import usage_tracker  # noqa: E402

logger = logging.getLogger("gui_qt")

# =============================================================================
# 暮光陪伴配色（默认）
# =============================================================================
BG = "#f7f4fa"
CARD = "#ffffff"
PANEL = "#eee8f2"
INPUT_BG = "#ffffff"
PRIMARY = "#88689c"
PRIMARY_HOVER = "#705284"
PRIMARY_DARK = "#5d416f"
PRIMARY_LIGHT = "#e7dcef"
TEXT = "#342d3b"
MUTED = "#83788d"
BORDER = "#ded3e5"
SUCCESS = "#719781"
WARNING = "#c28c63"
DANGER = "#c66d7c"
HEADER_BG = "#ffffff"
HEADER_BG2 = "#e7dcef"
SOFT_BG = "#f1eaf5"
SOFT_HOVER = "#e4d8eb"
TRACK = "#ded2e7"
CELL_BG = "#faf7fc"
CELL_BORDER = "#e8dfea"
HINT = "#a887ba"
LIST_BTN = "#f2ebf6"
LIST_BTN_HOVER = "#e6dbee"
TIP_BG = "#392f42"
TIP_FG = "#f8f4fb"

# 成长状态各参数的说明（悬停 ⓘ 显示）
GROWTH_HINTS = {
    "stage": "性格阶段：随相处推进，从礼貌试探 → 热情升温 → 深度绑定，影响她的语气与状态",
    "rel_hot": "关系温度：最近这段关系的冷热感（不是亲密度），由今日活跃与亲密度推导",
    "energy": "能量状态：她今天累不累（结合当前时段与今日聊天量）",
    "mood": "实时情绪：优先显示会自然衰减的持续情绪，不再只看聊天关键词",
    "affection": "亲密度：每聊一句 +1，反映关系的亲密程度；到阈值进入下一阶段",
    "dependency": "依赖度：她有多依赖你；主动聊天、追问、撩人都会增加",
    "jealousy": "醋意倾向：你提到别人、久不回复时会增加，影响她吃醋的表现",
    "lewdness": "淫乱度：亲密互动积累的程度；档位（害羞/主动/放开）影响亲密话题的尺度",
    "nickname": "她最近几轮顺口的称呼；会随关系、语境自然变化，认真话题会收敛，也不会每句话都叫",
    "now_thought": "今天一件值得记的小事（大模型提炼，最生动/最生活感的那一件）",
    "state": "连续生活状态：包含地点、活动、开始与预计结束时间；明确说出的计划会在到点后自动推进",
    "mood_state": "情绪状态：开心 / 生气 / 委屈 / 疲惫的当前强度，以及今天被惹次数",
    "days": "在一起第几天（从纪念日起始日期算起）",
    "memory": "她长期记忆里关于你的事（提炼的事实 + 偏好数量）",
    "chats": "累计聊天的消息条数",
    "mood_delta": "今日亲密度变化：今天通过聊天涨了多少亲密度",
}

# =============================================================================
# 天空蓝 QSS 主题
# =============================================================================
QSS = f"""
* {{ font-family: "Microsoft YaHei UI"; font-size: 13px; color: {TEXT}; }}
QMainWindow, QWidget#Root {{ background: {BG}; }}
QWidget#Header {{ background: {HEADER_BG}; border-bottom: 1px solid {BORDER}; }}
QLabel#AppTitle {{ font-size: 17px; font-weight: bold; color: {PRIMARY}; }}
QLabel#HeaderPill {{ color: {PRIMARY_DARK}; background: {PRIMARY_LIGHT}; border-radius: 10px; padding: 3px 9px; }}
QLabel#SidebarTitle {{ font-size: 16px; font-weight: bold; color: {TEXT}; }}
QLabel#PageTitle {{ font-size: 20px; font-weight: bold; color: {PRIMARY}; }}
QLabel#SectionTitle {{ font-size: 14px; font-weight: bold; color: {PRIMARY}; }}
QLabel#Muted {{ color: {MUTED}; font-size: 11px; }}
QLabel#CellName {{ color: {MUTED}; font-size: 11px; }}
QLabel#CellValue {{ font-size: 15px; font-weight: bold; color: {TEXT}; }}
QLabel#GrowthGroupTitle {{ color: {PRIMARY}; font-size: 13px; font-weight: bold; }}
QLabel#GrowthMetricName {{ color: {MUTED}; font-size: 11px; }}
QLabel#GrowthMetricValue {{ color: {TEXT}; font-size: 12px; font-weight: bold; }}
QLabel#GrowthBody {{ color: {TEXT}; font-size: 12px; line-height: 1.35; }}
QLabel#Hint {{ color: {HINT}; font-size: 10px; font-weight: bold; }}
QLabel#BigValue {{ font-size: 15px; font-weight: bold; color: {PRIMARY}; }}
QLabel#Status {{ font-size: 14px; font-weight: bold; }}
QFrame#Card {{ background: {CARD}; border: 1px solid {BORDER}; border-radius: 12px; }}
QFrame#Cell {{ background: {CELL_BG}; border: 1px solid {CELL_BORDER}; border-radius: 10px; }}
QFrame#GrowthGroup {{ background: {CELL_BG}; border: 1px solid {CELL_BORDER}; border-radius: 11px; }}
QFrame#GrowthDivider {{ background: {CELL_BORDER}; min-height: 1px; max-height: 1px; border: none; }}
QFrame#Divider {{ background: {BORDER}; max-height: 1px; min-height: 1px; }}
QPushButton#Primary {{ background: {PRIMARY}; color: white; border: none; border-radius: 8px;
    padding: 6px 14px; font-weight: bold; }}
QPushButton#Primary:hover {{ background: {PRIMARY_HOVER}; }}
QPushButton#Primary:disabled {{ background: {BORDER}; }}
QPushButton#Soft {{ background: {SOFT_BG}; color: {PRIMARY}; border: none; border-radius: 6px;
    padding: 4px 10px; }}
QPushButton#Soft:hover {{ background: {SOFT_HOVER}; }}
QPushButton#SoftRed {{ background: #f6e3e1; color: #b35a54; border: none; border-radius: 6px;
    padding: 4px 10px; }}
QPushButton#SoftRed:hover {{ background: #efd5d0; }}
QPushButton#Ghost {{ background: transparent; color: {TEXT}; border: none; border-radius: 6px; }}
QPushButton#Ghost:hover {{ background: {PANEL}; }}
QPushButton#Danger {{ background: {DANGER}; color: white; border: none; border-radius: 8px;
    padding: 6px 14px; font-weight: bold; }}
QPushButton#Danger:hover {{ background: #b35a54; }}
QPushButton#Success {{ background: {SUCCESS}; color: white; border: none; border-radius: 8px;
    padding: 6px 14px; font-weight: bold; }}
QPushButton#Success:hover {{ background: #6c8f62; }}
QPushButton#HeaderBtn {{ background: transparent; color: {TEXT}; border: none;
    font-size: 15px; border-radius: 6px; }}
QPushButton#HeaderBtn:hover {{ background: {PANEL}; }}
QPushButton#HeaderClose {{ background: transparent; color: {TEXT}; border: none;
    font-size: 15px; border-radius: 6px; }}
QPushButton#HeaderClose:hover {{ background: {DANGER}; color: white; }}
QLineEdit, QTextEdit, QPlainTextEdit, QComboBox, QSpinBox, QDoubleSpinBox {{
    background: {INPUT_BG}; border: 1px solid {BORDER}; border-radius: 8px;
    padding: 4px 8px; selection-background-color: {PRIMARY_LIGHT};
}}
QLineEdit:focus, QTextEdit:focus, QPlainTextEdit:focus, QComboBox:focus {{
    border: 1px solid {PRIMARY}; }}
QComboBox::drop-down {{ border: none; width: 22px; }}
QComboBox QAbstractItemView {{ background: white; border: 1px solid {BORDER};
    selection-background-color: {SOFT_BG}; selection-color: {PRIMARY}; }}
QCheckBox, QRadioButton {{ spacing: 6px; }}
QCheckBox::indicator {{ width: 16px; height: 16px; border: 1px solid {BORDER};
    border-radius: 4px; background: white; }}
QCheckBox::indicator:checked {{ background: {PRIMARY}; border-color: {PRIMARY}; }}
QRadioButton::indicator {{ width: 16px; height: 16px; border: 1px solid {BORDER};
    border-radius: 8px; background: white; }}
QRadioButton::indicator:checked {{ background: {PRIMARY}; border: 4px solid white; }}
QSlider::groove:horizontal {{ height: 6px; background: {TRACK}; border-radius: 3px; }}
QSlider::handle:horizontal {{ width: 16px; margin: -5px 0; border-radius: 8px;
    background: {PRIMARY}; }}
QSlider::handle:horizontal:hover {{ background: {PRIMARY_HOVER}; }}
QTabWidget::pane {{ border: none; }}
QTabBar::tab {{ background: {PANEL}; color: {MUTED}; padding: 8px 20px;
    border-top-left-radius: 10px; border-top-right-radius: 10px;
    margin-right: 3px; font-weight: bold; }}
QTabBar::tab:selected {{ background: {CARD}; color: {PRIMARY};
    border-bottom: 2px solid {PRIMARY}; }}
QTabBar::tab:hover {{ color: {PRIMARY_HOVER}; }}
QWidget#Sidebar {{ background: {HEADER_BG}; border-right: 1px solid {BORDER}; min-width: 172px; max-width: 172px; }}
QListWidget#SidebarNav {{ background: transparent; border: none; outline: none; color: {MUTED}; }}
QListWidget#SidebarNav::item {{ padding: 10px 12px; margin: 2px 8px; border-radius: 8px; }}
QListWidget#SidebarNav::item:selected {{ background: {PRIMARY_LIGHT}; color: {PRIMARY_DARK}; font-weight: bold; }}
QListWidget#SidebarNav::item:hover {{ background: {SOFT_BG}; color: {PRIMARY}; }}
QScrollArea {{ border: none; background: transparent; }}
QScrollArea > QWidget > QWidget {{ background: transparent; }}
QScrollBar:vertical {{ background: transparent; width: 8px; margin: 0; }}
QScrollBar::handle:vertical {{ background: {BORDER}; border-radius: 4px; min-height: 30px; }}
QScrollBar::handle:vertical:hover {{ background: {PRIMARY_LIGHT}; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; }}
QTableWidget {{ background: white; border: 1px solid {BORDER}; border-radius: 10px;
    gridline-color: {PANEL}; }}
QHeaderView::section {{ background: {SOFT_BG}; color: {MUTED}; border: none;
    padding: 6px; font-weight: bold; }}
QToolTip {{ background: {TIP_BG}; color: {TIP_FG}; border: none;
    padding: 6px 8px; border-radius: 6px; }}
QPlainTextEdit#LogView {{ background: #2f2a24; color: #e8dcc8;
    font-family: Consolas, "Microsoft YaHei UI"; font-size: 12px;
    border: 1px solid {BORDER}; border-radius: 10px; }}
QMenu {{ background: {CARD}; border: 1px solid {BORDER}; border-radius: 8px; padding: 4px; }}
QMenu::item {{ padding: 6px 18px; border-radius: 6px; }}
QMenu::item:selected {{ background: {SOFT_BG}; color: {PRIMARY}; }}
"""


THEME_LABELS = {
    "twilight": "暮光陪伴",
    "night": "深夜模式",
    "system": "跟随系统",
}


def _system_prefers_dark() -> bool:
    """读取 Windows 的应用颜色偏好；读取失败时保留暮光陪伴。"""
    if sys.platform != "win32":
        return False
    try:
        import winreg
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize",
        ) as key:
            return int(winreg.QueryValueEx(key, "AppsUseLightTheme")[0]) == 0
    except (OSError, ValueError):
        return False


def _theme_qss(is_night: bool) -> str:
    """在暮光基础样式后覆盖深夜模式的关键表面与交互色。"""
    if not is_night:
        return QSS
    return QSS + """
QMainWindow, QWidget#Root, QWidget#PageArea { background: #17151d; color: #eee9f2; }
QWidget#Header { background: #211d29; border-bottom: 1px solid #383143; }
QWidget#Sidebar { background: #211d29; border-right: 1px solid #383143; }
QLabel#AppTitle, QLabel#PageTitle, QLabel#SectionTitle, QLabel#BigValue { color: #d6b9ea; }
QLabel#Muted, QLabel#CellName { color: #aaa1b3; }
QLabel#CellValue, QLabel#Status, QLabel#GrowthMetricValue, QLabel#GrowthBody { color: #eee9f2; }
QLabel#GrowthGroupTitle { color: #d6b9ea; }
QLabel#GrowthMetricName { color: #aaa1b3; }
QLabel#SidebarTitle { color: #f2edf5; font-size: 16px; font-weight: bold; }
QLabel#HeaderPill { color: #d8c0e9; background: #332b40; border-radius: 10px; padding: 3px 9px; }
QFrame#Card { background: #24202c; border: 1px solid #383143; }
QFrame#Cell { background: #2b2633; border: 1px solid #403849; }
QFrame#GrowthGroup { background: #2b2633; border: 1px solid #403849; }
QFrame#GrowthDivider { background: #403849; }
QPushButton#Primary { background: #9a75b4; color: #18131e; }
QPushButton#Primary:hover { background: #b18bca; }
QPushButton#Soft { background: #302a39; color: #dbc6ea; }
QPushButton#Soft:hover, QPushButton#Ghost:hover, QPushButton#HeaderBtn:hover { background: #3b3346; }
QPushButton#HeaderBtn, QPushButton#HeaderClose, QPushButton#Ghost { color: #eee9f2; }
QLineEdit, QTextEdit, QPlainTextEdit, QComboBox, QSpinBox, QDoubleSpinBox {
    background: #201c27; color: #eee9f2; border-color: #4a4055; }
QComboBox QAbstractItemView, QMenu { background: #272230; color: #eee9f2; border-color: #4a4055; }
QListWidget#SidebarNav { background: transparent; border: none; outline: none; color: #c8becf; }
QListWidget#SidebarNav::item { padding: 10px 12px; margin: 2px 8px; border-radius: 8px; }
QListWidget#SidebarNav::item:selected { background: #443750; color: #f5eff8; font-weight: bold; }
QListWidget#SidebarNav::item:hover { background: #342d3e; }
QScrollBar::handle:vertical { background: #52475e; }
QTableWidget { background: #24202c; color: #eee9f2; border-color: #403849; gridline-color: #383143; }
QHeaderView::section { background: #302a39; color: #cfc4d7; }
"""


# =============================================================================
# 通用小工具
# =============================================================================
def _card(parent, title=None):
    f = QFrame(parent)
    f.setObjectName("Card")
    lay = QVBoxLayout(f)
    lay.setContentsMargins(16, 14, 16, 14)
    lay.setSpacing(8)
    if title:
        t = QLabel(title)
        t.setObjectName("SectionTitle")
        lay.addWidget(t)
    return f, lay


def _label(text, obj=""):
    l = QLabel(text)
    if obj:
        l.setObjectName(obj)
    return l


def _btn(text, obj="Soft", on=None):
    b = QPushButton(text)
    b.setObjectName(obj)
    b.setCursor(Qt.CursorShape.PointingHandCursor)
    if on:
        b.clicked.connect(on)
    return b


IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp"}
AUDIO_EXTS = {".mp3", ".wav", ".ogg", ".m4a", ".aac", ".flac"}


def _recent_media_files(folder, extensions, limit=60):
    """按最近修改时间列出素材；目录不存在时返回空列表，不主动创建。"""
    try:
        items = [p for p in Path(folder).iterdir()
                 if p.is_file() and p.suffix.lower() in extensions]
        return sorted(items, key=lambda p: p.stat().st_mtime, reverse=True)[:limit]
    except OSError:
        return []


def _human_size(size: int) -> str:
    if size < 1024 * 1024:
        return f"{max(1, round(size / 1024))} KB"
    return f"{size / (1024 * 1024):.1f} MB"


class CenterTabBar(QTabBar):
    """居中铺满的页签栏：setExpanding 让页签均匀铺满整个宽度并居中（QTabWidget 默认左对齐）。

    页签文本默认水平居中；不再自绘（自绘在页签内容超出栏宽时会裁切不可见）。
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setExpanding(True)
        self.setDrawBase(False)

    def sizeHint(self):
        # 返回一个很大的宽度，让 QTabWidget 布局把页签栏拉伸到全宽（默认只到内容宽，
        # 导致页签挤在左侧、内容多时被裁切）。配合 setExpanding(True) 页签均匀铺满。
        s = super().sizeHint()
        s.setWidth(max(s.width(), 100000))
        return s


class SidebarPages(QStackedWidget):
    """保留 addTab 调用习惯的页面容器，把主导航交给左侧列表。"""

    def __init__(self, navigation, parent=None):
        super().__init__(parent)
        self._navigation = navigation

    def addTab(self, widget, label):
        index = self.addWidget(widget)
        self._navigation.addItem(QListWidgetItem(label))
        return index


class _CellTooltip(QObject):
    """整格悬停立即显示说明（不走 Qt 原生 tooltip 的 ~700ms 延迟）。

    鼠标进入格子任意位置立即弹出，移开立即消失；比只挂在 ⓘ 上更好对准。
    """

    def __init__(self, text, parent=None):
        super().__init__(parent)
        self.text = text

    def eventFilter(self, obj, event):
        if not self.text:
            return False
        if event.type() == QEvent.Type.Enter:
            QToolTip.showText(event.globalPosition().toPoint() + QPoint(14, 20),
                              self.text, obj)
        elif event.type() == QEvent.Type.Leave:
            QToolTip.hideText()
        return False


# =============================================================================
# 主窗口（无边框 + 自绘标题栏）
# =============================================================================
class HeaderBar(QWidget):
    def __init__(self, win):
        super().__init__(win)
        self._win = win
        self._drag_pos = None
        self.setObjectName("Header")
        self.setFixedHeight(54)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(16, 0, 8, 0)
        lay.setSpacing(4)
        lay.addWidget(_label("小晚 · 控制台", "AppTitle"))
        self.theme_badge = _label("暮光陪伴", "HeaderPill")
        lay.addWidget(self.theme_badge)
        lay.addStretch(1)
        self.save_btn = _btn("保存", "Primary", self._win._save_from_bar)
        self.save_btn.setFixedSize(80, 30)
        self.save_btn.hide()
        lay.addWidget(self.save_btn)
        bm = _btn("—", "HeaderBtn", self._win.showMinimized)
        bm.setFixedSize(36, 30)
        lay.addWidget(bm)
        bc = _btn("✕", "HeaderClose", self._win.close)
        bc.setFixedSize(36, 30)
        lay.addWidget(bc)

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self._drag_pos = e.globalPosition().toPoint() - self._win.frameGeometry().topLeft()
            e.accept()

    def mouseMoveEvent(self, e):
        if self._drag_pos is not None and e.buttons() & Qt.MouseButton.LeftButton:
            self._win.move(e.globalPosition().toPoint() - self._drag_pos)
            e.accept()

    def mouseReleaseEvent(self, e):
        self._drag_pos = None


class MainWindow(QMainWindow):
    """PySide6 主窗口。"""

    def __init__(self):
        super().__init__()
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint)
        self.setWindowTitle("小晚 · 控制台")
        self.resize(1080, 780)
        self.setMinimumSize(900, 640)
        self._theme_mode = str(getattr(runtime, "GUI_THEME_MODE", "twilight") or "twilight")
        if self._theme_mode not in THEME_LABELS:
            self._theme_mode = "twilight"
        self._is_night_theme = False
        self.setStyleSheet(_theme_qss(False))

        self._entries = {}
        self._text_widgets = {}
        self._sliders = {}
        self._slider_labels = {}
        self._assign_vars = {}
        # 大模型提炼的"今日状态"缓存（约每 10 分钟后台刷新一次，GUI 5 秒刷新只读缓存）
        self._today_digest = None
        self._today_digest_ts = 0.0
        self._digest_building = False
        self._growth_labels = {}
        self._growth_cell_kw = {}
        self._growth_tips = {}
        self._dirty = False
        self._suppress_dirty = False
        self._dirty_hooked = False
        self._bot_thread = None
        self._bot_stop = threading.Event()
        self._snowluma_proc = None
        self._snowluma_output_thread = None
        self._log_queue = queue.Queue()
        self._log_timer = None

        central = QWidget()
        central.setObjectName("Root")
        v = QVBoxLayout(central)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)
        self.header = HeaderBar(self)
        v.addWidget(self.header)
        page_area = QWidget()
        page_area.setObjectName("PageArea")
        page_lay = QHBoxLayout(page_area)
        page_lay.setContentsMargins(0, 0, 0, 0)
        page_lay.setSpacing(0)
        sidebar = QFrame(page_area)
        sidebar.setObjectName("Sidebar")
        side_lay = QVBoxLayout(sidebar)
        side_lay.setContentsMargins(10, 18, 10, 14)
        side_lay.setSpacing(8)
        side_lay.addWidget(_label("导航", "SidebarTitle"))
        side_lay.addWidget(_label("陪伴与管理", "Muted"))
        self._navigation = QListWidget(sidebar)
        self._navigation.setObjectName("SidebarNav")
        self._navigation.setSpacing(2)
        side_lay.addWidget(self._navigation, 1)
        side_lay.addWidget(_label("外观可在「设置」调整", "Muted"))
        self.tabs = SidebarPages(self._navigation, page_area)
        self._navigation.currentRowChanged.connect(self.tabs.setCurrentIndex)
        self.tabs.currentChanged.connect(self._navigation.setCurrentRow)
        page_lay.addWidget(sidebar)
        page_lay.addWidget(self.tabs, 1)
        v.addWidget(page_area, 1)
        self.setCentralWidget(central)

        self._grip = QSizeGrip(self)
        self._grip.setFixedSize(18, 18)

        self._build_pages()
        self._navigation.setCurrentRow(0)
        self._apply_theme(self._theme_mode, persist=False)
        self._hook_dirty_tracking()
        self._start_timers()

    def _apply_theme(self, mode: str, persist: bool = True):
        """立即应用手选主题；“跟随系统”读取 Windows 的应用深浅色偏好。"""
        mode = mode if mode in THEME_LABELS else "twilight"
        is_night = mode == "night" or (mode == "system" and _system_prefers_dark())
        self._theme_mode = mode
        self._is_night_theme = is_night
        self.setStyleSheet(_theme_qss(is_night))
        shown = "深夜模式" if is_night else "暮光陪伴"
        if hasattr(self, "header"):
            suffix = " · 跟随系统" if mode == "system" else ""
            self.header.theme_badge.setText(shown + suffix)
        combo = getattr(self, "_theme_mode_var", None)
        if combo is not None and combo.currentData() != mode:
            combo.blockSignals(True)
            combo.setCurrentIndex(max(0, combo.findData(mode)))
            combo.blockSignals(False)
        if persist:
            runtime.update({"GUI_THEME_MODE": mode})
            runtime.save_to_file()

    def _refresh_system_theme(self):
        if self._theme_mode == "system":
            is_night = _system_prefers_dark()
            if is_night != self._is_night_theme:
                self._apply_theme("system", persist=False)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._grip.move(self.width() - 20, self.height() - 20)

    def _build_pages(self):
        self._build_dashboard_tab()
        self._build_personality_tab()
        self._build_companion_tab()
        self._build_media_tab()
        self._build_connection_tab()
        self._build_settings_tab()
        self._build_log_tab()

    # ===================== 首页 =====================
    def _build_dashboard_tab(self):
        tab = QWidget()
        self.tabs.addTab(tab, "首页")
        outer = QVBoxLayout(tab)
        outer.setContentsMargins(16, 16, 16, 16)
        outer.setSpacing(14)
        outer.addWidget(_label("今天也在陪着你", "PageTitle"))
        outer.addWidget(_label("在这里管理运行状态，并快速前往常用功能。", "Muted"))

        cards = QHBoxLayout()
        cards.setSpacing(12)
        outer.addLayout(cards)

        quick, quick_lay = _card(tab, "快捷入口")
        quick_lay.addWidget(_label("常用素材与外貌设定都可以从这里直接打开。", "Muted"))
        quick_row = QHBoxLayout()
        quick_row.addWidget(_btn("查看最新图片", "Soft", lambda: self._open_media_page(0)))
        quick_row.addWidget(_btn("播放最近语音", "Soft", lambda: self._open_media_page(2)))
        quick_row.addWidget(_btn("编辑外貌总结", "Soft", self._open_appearance_editor))
        quick_row.addStretch(1)
        quick_lay.addLayout(quick_row)
        outer.addWidget(quick)

        qq, qq_lay = _card(tab, "QQ")
        qq.setMinimumHeight(190)
        self._qq_status_var = _label("未检测", "Status")
        qq_lay.addWidget(self._qq_status_var)
        self._qq_version_var = _label("版本：-", "Muted")
        qq_lay.addWidget(self._qq_version_var)
        self._qq_path_var = _label("路径：-", "Muted")
        self._qq_path_var.setWordWrap(True)
        qq_lay.addWidget(self._qq_path_var)
        qq_lay.addStretch(1)
        qq_lay.addWidget(_btn("重新检测", "Soft", self._detect_qq))
        cards.addWidget(qq, 1)

        sl, sl_lay = _card(tab, "SNOWLUMA")
        sl.setMinimumHeight(190)
        self._snowluma_status_var = _label("已停止", "Status")
        sl_lay.addWidget(self._snowluma_status_var)
        sl_lay.addStretch(1)
        self._sl_btn_start = _btn("启动", "Success", self._start_snowluma)
        self._sl_btn_start.setFixedHeight(38)
        sl_lay.addWidget(self._sl_btn_start)
        self._sl_btn_stop = _btn("停止", "Danger", self._stop_snowluma)
        self._sl_btn_stop.setFixedHeight(38)
        self._sl_btn_stop.setEnabled(False)
        sl_lay.addWidget(self._sl_btn_stop)
        self._sl_btn_restart = _btn("重启", "Soft", self._restart_snowluma)
        self._sl_btn_restart.setFixedHeight(34)
        sl_lay.addWidget(self._sl_btn_restart)
        cards.addWidget(sl, 1)

        bot, bot_lay = _card(tab, "bot")
        bot.setMinimumHeight(190)
        self._status_var = _label("已停止", "Status")
        bot_lay.addWidget(self._status_var)
        bot_lay.addStretch(1)
        self._btn_start = _btn("启动", "Success", self._start_bot)
        self._btn_start.setFixedHeight(38)
        bot_lay.addWidget(self._btn_start)
        self._btn_stop = _btn("停止", "Danger", self._stop_bot)
        self._btn_stop.setFixedHeight(38)
        self._btn_stop.setEnabled(False)
        bot_lay.addWidget(self._btn_stop)
        self._btn_restart = _btn("重启", "Soft", self._restart_bot)
        self._btn_restart.setFixedHeight(34)
        bot_lay.addWidget(self._btn_restart)
        cards.addWidget(bot, 1)

    # ===================== 陪伴状态 =====================
    def _build_companion_tab(self):
        tab = QWidget()
        self.tabs.addTab(tab, "陪伴状态")
        outer = QVBoxLayout(tab)
        outer.setContentsMargins(16, 16, 16, 16)
        outer.setSpacing(10)
        outer.addWidget(_label("今日陪伴状态", "PageTitle"))
        outer.addWidget(_label("从此刻状态到长期成长，集中查看小晚今天与你相处的变化。", "Muted"))

        growth, growth_lay = _card(tab, "状态总览")
        grid = QGridLayout()
        grid.setContentsMargins(4, 4, 4, 4)
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(8)

        # 将原先平铺的 16 个指标按阅读语义收拢：先看此刻，再看关系与长期成长。
        # 每个指标仍保留独立值和悬停说明，信息量不减少。
        group_specs = [
            ("此刻的她", [
                ("state", "正在做"), ("energy", "能量"),
                ("mood", "实时情绪"), ("mood_state", "情绪状态"),
            ], 0, 0, 1, 1),
            ("关系进度", [
                ("stage", "性格阶段"), ("rel_hot", "关系温度"),
                ("affection", "亲密度"), ("mood_delta", "今日变化"),
            ], 0, 1, 1, 1),
            ("相处倾向", [
                ("dependency", "依赖度"), ("jealousy", "醋意倾向"),
                ("lewdness", "亲密倾向"),
            ], 0, 2, 1, 1),
            ("今天留下的片段", [
                ("now_thought", "今天的小事"),
            ], 1, 0, 1, 2),
            ("陪伴档案", [
                ("nickname", "最近称呼"), ("days", "在一起"),
                ("memory", "记得你"), ("chats", "聊天记录"),
            ], 1, 2, 1, 1),
        ]

        def add_metric_group(title, items, row, column, row_span=1, column_span=1):
            panel = QFrame()
            panel.setObjectName("GrowthGroup")
            panel_lay = QVBoxLayout(panel)
            panel_lay.setContentsMargins(12, 9, 12, 9)
            panel_lay.setSpacing(5)
            panel_lay.addWidget(_label(title, "GrowthGroupTitle"))
            for key, name in items:
                metric = QWidget(panel)
                metric_lay = QHBoxLayout(metric)
                metric_lay.setContentsMargins(0, 0, 0, 0)
                metric_lay.setSpacing(8)
                name_label = _label(name, "GrowthMetricName")
                value_label = _label("—", "GrowthMetricValue")
                value_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                value_label.setWordWrap(True)
                metric_lay.addWidget(name_label)
                metric_lay.addStretch(1)
                metric_lay.addWidget(value_label, 2)
                panel_lay.addWidget(metric)
                self._growth_labels[key] = value_label
                tip = _CellTooltip(GROWTH_HINTS.get(key, ""), metric)
                metric.installEventFilter(tip)
                self._growth_tips[key] = tip
            panel_lay.addStretch(1)
            grid.addWidget(panel, row, column, row_span, column_span)

        for spec in group_specs:
            add_metric_group(*spec)

        life_panel = QFrame()
        life_panel.setObjectName("GrowthGroup")
        life_lay = QVBoxLayout(life_panel)
        life_lay.setContentsMargins(12, 9, 12, 9)
        life_lay.setSpacing(6)
        life_lay.addWidget(_label("生活轨迹", "GrowthGroupTitle"))
        self._life_state_var = _label("生活线读取中...", "GrowthBody")
        self._life_state_var.setWordWrap(True)
        life_lay.addWidget(self._life_state_var)
        life_lay.addStretch(1)
        grid.addWidget(life_panel, 2, 0)

        growth_panel = QFrame()
        growth_panel.setObjectName("GrowthGroup")
        evo_lay = QVBoxLayout(growth_panel)
        evo_lay.setContentsMargins(12, 9, 12, 9)
        evo_lay.setSpacing(6)
        evo_lay.addWidget(_label("性格与成长", "GrowthGroupTitle"))
        self._personality_axes_var = _label("性格轮廓读取中...", "GrowthBody")
        self._personality_axes_var.setWordWrap(True)
        evo_lay.addWidget(self._personality_axes_var)
        divider = QFrame()
        divider.setObjectName("GrowthDivider")
        evo_lay.addWidget(divider)
        self._growth_evo_var = _label("最近演化读取中...", "GrowthBody")
        self._growth_evo_var.setWordWrap(True)
        evo_lay.addWidget(self._growth_evo_var)
        evo_lay.addStretch(1)
        grid.addWidget(growth_panel, 2, 1, 1, 2)

        for c in range(3):
            grid.setColumnStretch(c, 1)
        growth_lay.addLayout(grid)
        outer.addWidget(growth, 1)

        self._refresh_growth_stats()

    # ===================== 素材库 =====================
    def _build_media_tab(self):
        tab = QWidget()
        self.tabs.addTab(tab, "素材库")
        lay = QVBoxLayout(tab)
        lay.setContentsMargins(16, 16, 16, 16)
        lay.setSpacing(10)
        lay.addWidget(_label("素材库", "PageTitle"))
        lay.addWidget(_label("在这里查看小晚生成的图片、外貌参考图，以及语音缓存。素材始终保留在数据目录中。", "Muted"))

        self._media_tabs = QTabWidget(tab)
        self._media_tabs.setTabBar(CenterTabBar(self._media_tabs))
        lay.addWidget(self._media_tabs, 1)
        self._media_gallery_layouts = {}
        self._media_path_labels = {}
        self._build_image_gallery_page("generated", "生成图片", "小晚生成并发送过的图片，可预览或删除。")
        self._build_image_gallery_page("appearance", "人设图片", "用于外貌总结与人物一致性的参考图，仅供查看，不提供删除。")
        self._build_audio_library_page()
        self._media_tabs.currentChanged.connect(self._refresh_current_media_page)
        self._refresh_media_library()

    def _build_image_gallery_page(self, kind: str, title: str, hint: str):
        page = QWidget()
        self._media_tabs.addTab(page, title)
        lay = QVBoxLayout(page)
        lay.setContentsMargins(0, 10, 0, 0)
        lay.setSpacing(8)
        top, top_lay = _card(page, title)
        top_lay.addWidget(_label(hint, "Muted"))
        path_label = _label("", "Muted")
        path_label.setWordWrap(True)
        top_lay.addWidget(path_label)
        actions = QHBoxLayout()
        actions.addWidget(_btn("刷新", "Soft", lambda _=False, k=kind: self._refresh_image_gallery(k)))
        actions.addWidget(_btn("打开文件夹", "Soft", lambda _=False, k=kind: self._open_media_folder(k)))
        actions.addStretch(1)
        top_lay.addLayout(actions)
        lay.addWidget(top)

        scroll = QScrollArea(page)
        scroll.setWidgetResizable(True)
        body = QWidget(scroll)
        grid = QGridLayout(body)
        grid.setContentsMargins(4, 4, 4, 4)
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(10)
        for col in range(3):
            grid.setColumnStretch(col, 1)
        scroll.setWidget(body)
        lay.addWidget(scroll, 1)
        self._media_gallery_layouts[kind] = grid
        self._media_path_labels[kind] = path_label

    def _build_audio_library_page(self):
        page = QWidget()
        self._media_tabs.addTab(page, "语音缓存")
        lay = QVBoxLayout(page)
        lay.setContentsMargins(0, 10, 0, 0)
        lay.setSpacing(8)
        card, cl = _card(page, "语音缓存")
        cl.addWidget(_label("小晚发送过的语音会保存在这里，可直接播放或删除。", "Muted"))
        self._media_audio_path = _label("", "Muted")
        self._media_audio_path.setWordWrap(True)
        cl.addWidget(self._media_audio_path)
        audio_actions = QHBoxLayout()
        audio_actions.addWidget(_btn("刷新", "Soft", self._refresh_audio_library))
        audio_actions.addWidget(_btn("停止播放", "Soft", self._stop_audio))
        audio_actions.addWidget(_btn("打开文件夹", "Soft", lambda: self._open_media_folder("audio")))
        audio_actions.addStretch(1)
        cl.addLayout(audio_actions)
        self._media_audio_state = _label("未播放", "Muted")
        cl.addWidget(self._media_audio_state)
        lay.addWidget(card)

        self._media_audio_table = QTableWidget(0, 4, page)
        self._media_audio_table.setHorizontalHeaderLabels(["文件", "生成时间", "大小", "操作"])
        self._media_audio_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for col in range(1, 4):
            self._media_audio_table.horizontalHeader().setSectionResizeMode(
                col, QHeaderView.ResizeMode.ResizeToContents)
        self._media_audio_table.verticalHeader().setVisible(False)
        self._media_audio_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._media_audio_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        lay.addWidget(self._media_audio_table, 1)
        self._audio_output = QAudioOutput(self)
        self._audio_player = QMediaPlayer(self)
        self._audio_player.setAudioOutput(self._audio_output)
        self._audio_player.playbackStateChanged.connect(self._on_audio_playback_state)
        self._playing_audio_path = None

    def _media_folder(self, kind: str) -> str:
        if kind == "generated":
            return data_path("晚晚", "图片", "生成图片")
        if kind == "audio":
            return data_path("晚晚", "语音", "语音缓存")
        try:
            from appearance_ref import get_ref_dir
            return get_ref_dir()
        except Exception:
            return data_path("晚晚", "外貌", "外貌设定")

    def _refresh_media_library(self):
        self._refresh_image_gallery("generated")
        self._refresh_image_gallery("appearance")
        self._refresh_audio_library()

    def _refresh_current_media_page(self, index):
        if index == 0:
            self._refresh_image_gallery("generated")
        elif index == 1:
            self._refresh_image_gallery("appearance")
        elif index == 2:
            self._refresh_audio_library()

    def _refresh_image_gallery(self, kind: str):
        grid = self._media_gallery_layouts.get(kind)
        if grid is None:
            return
        while grid.count():
            item = grid.takeAt(0)
            if item.widget() is not None:
                item.widget().deleteLater()
        folder = self._media_folder(kind)
        self._media_path_labels[kind].setText(f"位置：{folder}")
        files = _recent_media_files(folder, IMAGE_EXTS)
        if not files:
            empty = _label("这里还没有图片。生成图片或放入人设参考图后，会自动显示在这里。", "Muted")
            empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
            grid.addWidget(empty, 0, 0, 1, 3)
            return
        for index, path in enumerate(files):
            card, card_lay = _card(None)
            card.setMinimumHeight(190)
            preview = QLabel(card)
            preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
            preview.setFixedHeight(126)
            pixmap = QPixmap(str(path))
            if pixmap.isNull():
                preview.setText("无法预览此图片")
            else:
                preview.setPixmap(pixmap.scaled(200, 126, Qt.AspectRatioMode.KeepAspectRatio,
                                                Qt.TransformationMode.SmoothTransformation))
            card_lay.addWidget(preview)
            name = _label(path.name)
            name.setWordWrap(True)
            card_lay.addWidget(name)
            meta = _label(time.strftime("%Y-%m-%d %H:%M", time.localtime(path.stat().st_mtime)), "Muted")
            card_lay.addWidget(meta)
            row = QHBoxLayout()
            row.addWidget(_btn("预览", "Soft", lambda _=False, p=path: self._open_image_preview(p)))
            row.addWidget(_btn("定位", "Ghost", lambda _=False, p=path: self._open_in_folder(p)))
            if kind == "generated":
                row.addWidget(_btn(
                    "删除", "SoftRed",
                    lambda _=False, p=path: self._delete_media_file("generated", p),
                ))
            row.addStretch(1)
            card_lay.addLayout(row)
            grid.addWidget(card, index // 3, index % 3)

    def _refresh_audio_library(self):
        if not hasattr(self, "_media_audio_table"):
            return
        folder = self._media_folder("audio")
        self._media_audio_path.setText(f"位置：{folder}")
        files = _recent_media_files(folder, AUDIO_EXTS)
        table = self._media_audio_table
        table.setRowCount(len(files))
        for row, path in enumerate(files):
            table.setItem(row, 0, QTableWidgetItem(path.name))
            table.setItem(row, 1, QTableWidgetItem(
                time.strftime("%Y-%m-%d %H:%M", time.localtime(path.stat().st_mtime))))
            table.setItem(row, 2, QTableWidgetItem(_human_size(path.stat().st_size)))
            actions = QWidget(table)
            actions_lay = QHBoxLayout(actions)
            actions_lay.setContentsMargins(2, 1, 2, 1)
            actions_lay.setSpacing(5)
            actions_lay.addWidget(_btn("播放", "Soft", lambda _=False, p=path: self._play_audio(p)))
            actions_lay.addWidget(_btn(
                "删除", "SoftRed",
                lambda _=False, p=path: self._delete_media_file("audio", p),
            ))
            table.setCellWidget(row, 3, actions)

    def _open_media_page(self, index: int):
        self._select_main_page("素材库")
        self._media_tabs.setCurrentIndex(index)
        self._refresh_current_media_page(index)

    def _open_appearance_editor(self):
        self._select_main_page("小晚")
        if hasattr(self, "_personality_tabs"):
            self._personality_tabs.setCurrentIndex(0)

    def _select_main_page(self, label: str):
        for row in range(self._navigation.count()):
            if self._navigation.item(row).text() == label:
                self._navigation.setCurrentRow(row)
                return

    def _open_image_preview(self, path: Path):
        pixmap = QPixmap(str(path))
        if pixmap.isNull():
            QMessageBox.warning(self, "图片预览", "无法读取这张图片。")
            return
        dialog = QDialog(self)
        dialog.setWindowTitle(path.name)
        dialog.resize(820, 640)
        lay = QVBoxLayout(dialog)
        image = QLabel(dialog)
        image.setAlignment(Qt.AlignmentFlag.AlignCenter)
        image.setPixmap(pixmap.scaled(780, 560, Qt.AspectRatioMode.KeepAspectRatio,
                                      Qt.TransformationMode.SmoothTransformation))
        lay.addWidget(image, 1)
        lay.addWidget(_label(str(path), "Muted"))
        actions = QHBoxLayout()
        actions.addStretch(1)
        actions.addWidget(_btn("定位文件", "Soft", lambda: self._open_in_folder(path)))
        actions.addWidget(_btn("关闭", "Primary", dialog.accept))
        lay.addLayout(actions)
        dialog.exec()

    def _open_media_folder(self, kind: str):
        folder = self._media_folder(kind)
        if not os.path.isdir(folder):
            QMessageBox.information(self, "素材库", "该素材文件夹尚未创建；有素材生成后会自动出现。")
            return
        self._open_in_folder(Path(folder))

    def _open_in_folder(self, path: Path):
        try:
            target = path if path.is_dir() else path.parent
            if sys.platform == "win32":
                os.startfile(str(target))
            else:
                QMessageBox.information(self, "素材位置", str(target))
        except OSError as e:
            QMessageBox.warning(self, "无法打开文件夹", str(e))

    def _delete_media_file(self, kind: str, path: Path) -> bool:
        """仅允许从生成图片/语音缓存的直属目录删除单个媒体文件。"""
        if kind not in {"generated", "audio"}:
            return False
        allowed_exts = IMAGE_EXTS if kind == "generated" else AUDIO_EXTS
        try:
            folder = Path(self._media_folder(kind)).resolve()
            target = Path(path).resolve(strict=True)
        except (OSError, RuntimeError):
            if kind == "generated":
                self._refresh_image_gallery("generated")
            else:
                self._refresh_audio_library()
            return False
        # 不接受子目录、软链接逃逸或伪装扩展名，避免界面误删缓存目录外的文件。
        if target.parent != folder or target.suffix.lower() not in allowed_exts:
            QMessageBox.warning(self, "无法删除", "该文件不在允许删除的素材缓存目录中。")
            return False
        media_name = "生成图片" if kind == "generated" else "语音缓存"
        answer = QMessageBox.question(
            self,
            f"删除{media_name}",
            f"确定永久删除「{target.name}」吗？\n此操作无法撤销。",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return False
        try:
            if kind == "audio" and self._playing_audio_path == target:
                self._audio_player.stop()
                self._audio_player.setSource(QUrl())
                self._playing_audio_path = None
            target.unlink()
        except OSError as e:
            QMessageBox.warning(self, "删除失败", str(e))
            return False
        if kind == "generated":
            self._refresh_image_gallery("generated")
        else:
            self._refresh_audio_library()
            self._media_audio_state.setText("已删除语音缓存")
        return True

    def _play_audio(self, path: Path):
        self._playing_audio_path = Path(path).resolve()
        self._audio_player.setSource(QUrl.fromLocalFile(str(self._playing_audio_path)))
        self._audio_player.play()
        self._media_audio_state.setText(f"正在播放：{path.name}")

    def _stop_audio(self):
        self._audio_player.stop()
        self._playing_audio_path = None
        self._media_audio_state.setText("未播放")

    def _on_audio_playback_state(self, state):
        if state == QMediaPlayer.PlaybackState.StoppedState:
            self._playing_audio_path = None
            self._media_audio_state.setText("未播放")

    # ===================== 人设编辑 =====================
    def _build_personality_tab(self):
        tab = QWidget()
        self.tabs.addTab(tab, "小晚")
        sub = QTabWidget(tab)
        sub.setTabBar(CenterTabBar(sub))
        self._personality_tabs = sub
        lay = QVBoxLayout(tab)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(sub)

        # ---- 角色设定 ----
        p1 = QWidget()
        sub.addTab(p1, "角色设定")
        sc = QScrollArea()
        sc.setWidgetResizable(True)
        lay1 = QVBoxLayout(p1)
        lay1.setContentsMargins(0, 0, 0, 0)
        lay1.addWidget(sc)
        body = QWidget()
        sc.setWidget(body)
        form = QFormLayout(body)
        form.setContentsMargins(24, 20, 24, 20)
        form.setSpacing(12)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft)

        def add_line(label, key, width=320):
            e = QLineEdit(str(getattr(runtime, key, "")))
            e.setMinimumWidth(width)
            form.addRow(_label(label + "："), e)
            self._entries[key] = e
            return e

        def add_text(label, key, height=120):
            t = QTextEdit(str(getattr(runtime, key, "") or ""))
            t.setMinimumHeight(height)
            form.addRow(_label(label + "："), t)
            self._text_widgets[key] = t
            return t

        add_line("名字", "GIRLFRIEND_NAME")
        add_line("年龄", "GIRLFRIEND_AGE")
        # 出生日期：年/月/日 下拉
        birth_row = QHBoxLayout()
        import datetime as _dt
        self._birth_vars = {"y": None, "m": None, "d": None}
        cur = str(getattr(runtime, "GIRLFRIEND_BIRTHDATE", "") or "")
        y0, m0, d0 = "", "", ""
        try:
            y0, m0, d0 = cur.split("-")
            m0, d0 = str(int(m0)), str(int(d0))
        except ValueError:
            pass
        now_y = _dt.date.today().year
        for tag, values, w, init in (
            ("y", [str(x) for x in range(now_y, 1969, -1)], 100, y0),
            ("m", [f"{x:02d}" for x in range(1, 13)], 80, m0),
            ("d", [f"{x:02d}" for x in range(1, 32)], 80, d0),
        ):
            cb = QComboBox()
            cb.addItems(values)
            if init:
                cb.setCurrentText(init)
            cb.setFixedWidth(w)
            cb.currentTextChanged.connect(self._sync_birthdate)
            birth_row.addWidget(cb)
            self._birth_vars[tag] = cb
        birth_row.addWidget(_label("年 / 月 / 日", "Muted"))
        form.addRow(_label("出生日期："), birth_row)
        self._entries["GIRLFRIEND_BIRTHDATE"] = cur

        add_line("身份", "GIRLFRIEND_IDENTITY")
        add_line("性格", "GIRLFRIEND_CHARACTER", 480)
        add_text("语言风格", "GIRLFRIEND_LANGUAGE_STYLE", 150)
        add_text("外貌设定", "GIRLFRIEND_APPEARANCE", 130)
        tts_desc = add_text("音色描述", "TTS_VOICE_DESCRIPTION", 110)
        self._tts_voice_desc_text = tts_desc

        # 外貌参考图总结：与“外貌设定”分开保存，但可见、可编辑。
        div = QFrame()
        div.setObjectName("Divider")
        div.setFixedHeight(1)
        form.addRow(div)
        form.addRow(_label("参考图外貌总结：", "SectionTitle"))
        hint = _label("由参考图生成，会自动用于人物生图；你可以在这里补充或修正稳定外貌特征。", "Muted")
        hint.setWordWrap(True)
        form.addRow(hint)
        self._appearance_summary_text = QTextEdit()
        self._appearance_summary_text.setMinimumHeight(150)
        try:
            from appearance_ref import load_summary
            self._appearance_summary_text.setPlainText(load_summary())
        except Exception as e:
            logger.warning("读取外貌总结失败: %s", e)
        form.addRow(self._appearance_summary_text)
        app_row = QHBoxLayout()
        self._appearance_btn = _btn("根据参考图重新生成", "Primary", self._generate_appearance_summary)
        app_row.addWidget(self._appearance_btn)
        app_row.addWidget(_btn("保存手动修改", "Soft", self._save_appearance_summary))
        app_row.addWidget(_btn("复制到外貌设定", "Soft", self._copy_appearance_summary_to_profile))
        app_row.addStretch(1)
        form.addRow(app_row)

        # ---- 特殊反应 ----
        p2 = QWidget()
        sub.addTab(p2, "特殊反应")
        lay2 = QVBoxLayout(p2)
        lay2.setContentsMargins(12, 12, 12, 12)
        sr = QTextEdit(runtime.GIRLFRIEND_SPECIAL_REACTIONS)
        lay2.addWidget(sr)
        self._text_widgets["GIRLFRIEND_SPECIAL_REACTIONS"] = sr

        # ---- 底层约束 ----
        p3 = QWidget()
        sub.addTab(p3, "底层约束")
        lay3 = QVBoxLayout(p3)
        lay3.setContentsMargins(12, 12, 12, 12)
        lc = QTextEdit(runtime.GIRLFRIEND_CONSTRAINTS)
        lay3.addWidget(lc)
        self._text_widgets["GIRLFRIEND_CONSTRAINTS"] = lc

        # ---- 音色助手 ----
        self._build_voice_assistant_tab(sub)

    def _build_voice_assistant_tab(self, sub):
        p = QWidget()
        sub.addTab(p, "音色助手")
        lay = QVBoxLayout(p)
        lay.setContentsMargins(16, 16, 16, 16)
        lay.setSpacing(12)
        lay.addWidget(_label("音色描述助手", "SectionTitle"))
        inp_card, inp_lay = _card(p, "你的音色想法")
        self._voice_input_text = QTextEdit()
        self._voice_input_text.setMinimumHeight(110)
        self._voice_input_text.setPlaceholderText("例如：温柔软糯的年轻女声，带一点撒娇，语速慢一点")
        inp_lay.addWidget(self._voice_input_text)
        lay.addWidget(inp_card)
        self._voice_gen_btn = _btn("生成优化音色描述", "Primary", self._generate_voice_design)
        self._voice_gen_btn.setFixedWidth(200)
        lay.addWidget(self._voice_gen_btn, alignment=Qt.AlignmentFlag.AlignLeft)
        out_card, out_lay = _card(p, "优化后")
        self._voice_output_text = QTextEdit()
        self._voice_output_text.setMinimumHeight(160)
        self._voice_output_text.setReadOnly(True)
        out_lay.addWidget(self._voice_output_text)
        out_lay.addWidget(_btn("复制到剪贴板", "Soft", self._copy_voice_design),
                          alignment=Qt.AlignmentFlag.AlignRight)
        lay.addWidget(out_card, 1)

    # ===================== 运行日志 =====================
    def _build_log_tab(self):
        tab = QWidget()
        self.tabs.addTab(tab, "运行日志")
        lay = QVBoxLayout(tab)
        lay.setContentsMargins(12, 12, 12, 12)
        self._log_text = QPlainTextEdit()
        self._log_text.setObjectName("LogView")
        self._log_text.setReadOnly(True)
        self._log_text.setMaximumBlockCount(5000)
        lay.addWidget(self._log_text)
        bar = QHBoxLayout()
        bar.addWidget(_btn("清空", "Soft", self._clear_log))
        self._auto_scroll_var = QCheckBox("自动滚动")
        self._auto_scroll_var.setChecked(True)
        bar.addWidget(self._auto_scroll_var)
        bar.addStretch(1)
        lay.addLayout(bar)

    # ===================== 模型与连接 =====================
    def _build_connection_tab(self):
        tab = QWidget()
        self.tabs.addTab(tab, "模型与连接")
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        lay0 = QVBoxLayout(tab)
        lay0.setContentsMargins(0, 0, 0, 0)
        lay0.addWidget(scroll)
        body = QWidget()
        scroll.setWidget(body)
        outer = QVBoxLayout(body)
        outer.setContentsMargins(16, 16, 16, 16)
        outer.setSpacing(10)

        outer.addWidget(_label("模型提供商", "SectionTitle"))
        self._models_list_frame = QWidget()
        self._models_list_frame.setObjectName("Root")
        self._ml_lay = QVBoxLayout(self._models_list_frame)
        self._ml_lay.setContentsMargins(0, 0, 0, 0)
        self._ml_lay.setSpacing(6)
        outer.addWidget(self._models_list_frame)
        outer.addWidget(_btn("添加提供商", "Primary", lambda _c=False: self._open_provider_dialog()),
                        alignment=Qt.AlignmentFlag.AlignLeft)

        # ---- 模块分派 ----
        outer.addSpacing(8)
        outer.addWidget(_label("模块分派", "SectionTitle"))
        self._assign_vars = {}
        try:
            from llm_providers import load_providers as lp_load, get_assignment as lp_ass
            providers = lp_load()
        except Exception:
            providers = []
        for cat, label in (("chat", "对话"), ("task", "文字任务"), ("vision", "看图")):
            r = QHBoxLayout()
            r.addWidget(_label("⚡ " + label + "："))
            choices = [("（当前激活）", "")] + [
                ((p.get("name") or p.get("id")), p.get("id")) for p in providers]
            cb = QComboBox()
            for name, pid in choices:
                cb.addItem(name, pid)
            cb.setFixedWidth(220)
            r.addWidget(cb)
            # 模型：可从"获取模型"列表直接下拉选择，也可手输（留空 = 用提供商默认）
            mvar = QComboBox()
            mvar.setEditable(True)
            mvar.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
            mvar.setFixedWidth(220)
            mvar.addItem("（提供商默认）", "")
            try:
                saved = lp_ass(cat).get("model") or ""
                if saved:
                    mvar.setCurrentText(saved)
            except Exception:
                pass
            r.addWidget(mvar)
            r.addWidget(_btn("获取模型", "Soft",
                             lambda _c=False, c=cat, cbs=cb, mv=mvar: self._fill_assign_models(c, cbs, mv)))
            r.addStretch(1)
            outer.addLayout(r)
            cur = ""
            try:
                cur = lp_ass(cat).get("provider") or ""
            except Exception:
                pass
            idx = next((i for i, (n, pid) in enumerate(choices) if pid == cur), 0)
            cb.setCurrentIndex(idx)
            # 更换提供商 → 清空该模块的模型列表（重新获取）
            cb.currentIndexChanged.connect(
                lambda _i, c=cat, cbs=cb, mv=mvar: self._on_assign_provider(c, cbs, mv))
            mvar.currentTextChanged.connect(
                lambda _t, c=cat, cbs=cb, mv=mvar: self._on_assign(c, cbs, mv))
            self._assign_vars[cat] = (cb, mvar)

        outer.addSpacing(8)
        self._conn_form = QFormLayout()
        self._conn_form.setSpacing(10)
        outer.addLayout(self._conn_form)
        self._build_conn_sections()

        outer.addStretch(1)
        self._refresh_models_tab()

    def _on_assign(self, cat, cb, mv):
        try:
            from llm_providers import set_assignment
            # 可编辑下拉：选中具体项/手输 = 模型名；"（提供商默认）"或空 = 留空（用提供商默认）
            model = mv.currentText().strip()
            if not model or model == "（提供商默认）":
                model = ""
            set_assignment(cat, cb.currentData() or "", model)
        except Exception:
            pass

    def _on_assign_provider(self, cat, cb, mv):
        """更换提供商：清空该模块的模型列表，回到"（提供商默认）"，并自动拉取新供应商的模型。"""
        mv.blockSignals(True)
        try:
            mv.clear()
            mv.addItem("（提供商默认）", "")
        finally:
            mv.blockSignals(False)
        self._on_assign(cat, cb, mv)
        # 自动填充该供应商的模型下拉（失败静默，按钮可手动刷新）
        self._fill_assign_models(cat, cb, mv, silent=True)

    def _fill_assign_models(self, cat, cb, mv, silent=False):
        """获取所选提供商 的模型列表，填充到该模块的模型下拉框。

        silent=True（自动触发）失败时静默，不给用户弹错；按钮点击时为 False，弹正确/错误提示。
        """
        from llm_providers import load_providers, get_active_provider
        pid = cb.currentData() or ""
        try:
            providers = load_providers()
            p = next((x for x in providers if x.get("id") == pid), None) if pid else None
        except Exception:
            p = None
        if p is None:
            try:
                p = get_active_provider()
            except Exception:
                p = None
        if p is None:
            QMessageBox.warning(self, "获取模型", "请先添加/选择一个提供商")
            return
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
                    client = OpenAICompatClient(base_url=base_url or "", api_key=api_key or "sk-local")
                async def _run():
                    try:
                        return await client.list_models()
                    finally:
                        await client.aclose()
                models = asyncio.run(_run())
            except Exception as e:
                err = str(e)
            self._safe_after(0, lambda: self._on_assign_models_loaded(mv, models, err, name, silent))

        threading.Thread(target=worker, daemon=True).start()

    def _on_assign_models_loaded(self, mv, models, err, name, silent=False):
        if not models:
            if not silent:
                QMessageBox.warning(self, "获取模型", f"{name}：{err or '未返回任何模型'}")
            return
        cur = mv.currentText() or ""
        mv.blockSignals(True)
        try:
            mv.clear()
            mv.addItem("（提供商默认）", "")
            for m in models:
                mv.addItem(m, m)
            if cur and cur in models:
                mv.setCurrentText(cur)
        finally:
            mv.blockSignals(False)
        if not silent:
            QMessageBox.information(self, "模型列表", f"{name}：已获取 {len(models)} 个模型，可直接下拉选择")

    def _conn_row(self, label):
        """连接设置区一行：返回 (标签, 容器布局)。"""
        row = QHBoxLayout()
        row.setSpacing(8)
        row.addWidget(_label(label))
        return row

    def _build_conn_sections(self):
        """连接设置：QQ/OneBot、图生成、语音、大模型兜底。"""
        self._conn_form.addRow(_label("QQ / OneBot 连接", "SectionTitle"))

        self._ws_url_var = QLineEdit(config.ONEBOT_WS_URL)
        r = self._conn_row("OneBot WS 地址：")
        r.addWidget(self._ws_url_var)
        self._conn_form.addRow(r)

        self._ws_token_var = QLineEdit(config.ONEBOT_ACCESS_TOKEN)
        self._ws_token_var.setEchoMode(QLineEdit.EchoMode.Password)
        r = self._conn_row("Access Token：")
        r.addWidget(self._ws_token_var)
        self._conn_form.addRow(r)

        # 好友申请白名单（逗号分隔的 QQ 号；空 = 拒绝所有加好友申请，隐私安全默认）
        friend_var = QLineEdit(str(getattr(runtime, "FRIEND_APPROVE_UIDS", "") or ""))
        friend_var.setPlaceholderText("如 10001,10002（留空则自动拒绝好友申请）")
        r = self._conn_row("允许加好友 QQ：")
        r.addWidget(friend_var)
        self._conn_form.addRow(r)
        self._entries["FRIEND_APPROVE_UIDS"] = friend_var

        self._conn_form.addRow(_label("图生成", "SectionTitle"))
        self._dashscope_key_var = QLineEdit(config.DASHSCOPE_API_KEY)
        self._dashscope_key_var.setEchoMode(QLineEdit.EchoMode.Password)
        r = self._conn_row("百炼 API Key：")
        r.addWidget(self._dashscope_key_var)
        r.addWidget(_btn("测试", "Soft", lambda: self._test_api_key("dashscope")))
        self._conn_form.addRow(r)

        r = self._conn_row("图片生成模型：")
        self._image_model_var = QComboBox()
        self._image_model_var.addItems(["qwen-image-3.0-pro", "qwen-image-3.0", "wanx2.1-t2i-turbo"])
        self._image_model_var.setCurrentText(config.DASHSCOPE_IMAGE_MODEL)
        r.addWidget(self._image_model_var)
        r.addWidget(_btn("刷新模型", "Soft", self._refresh_image_models))
        self._conn_form.addRow(r)

        self._imagegen_enabled_var = QCheckBox("图生成")
        self._imagegen_enabled_var.setChecked(bool(runtime.IMAGE_GEN_ENABLED))
        self._conn_form.addRow(self._imagegen_enabled_var)

        r = self._conn_row("图片后端：")
        self._image_backend_var = QComboBox()
        self._image_backend_var.addItem("百炼 API", "dashscope")
        self._image_backend_var.addItem("本地 ComfyUI", "comfyui")
        idx = 0 if runtime.IMAGE_BACKEND == "comfyui" else 0
        self._image_backend_var.setCurrentIndex(
            1 if (runtime.IMAGE_BACKEND or "dashscope") == "comfyui" else 0)
        r.addWidget(self._image_backend_var)
        self._conn_form.addRow(r)

        self._image_prompt_use_llm_var = QCheckBox("图生文提示词走大模型")
        self._image_prompt_use_llm_var.setChecked(bool(runtime.IMAGE_PROMPT_USE_LLM))
        self._conn_form.addRow(self._image_prompt_use_llm_var)

        self._dashscope_base_url_var = QLineEdit(runtime.DASHSCOPE_BASE_URL or "https://dashscope.aliyuncs.com")
        r = self._conn_row("百炼 API 地址：")
        r.addWidget(self._dashscope_base_url_var)
        self._conn_form.addRow(r)

        self._image_size_var = QComboBox()
        self._image_size_var.addItems(["1024*1024", "1080*1080", "720*1280", "1080*1920",
                                       "1920*1080", "768*1344", "1344*768", "1536*1024",
                                       "1024*1536", "2048*2048"])
        self._image_size_var.setCurrentText(runtime.IMAGE_GEN_SIZE or "1024*1024")
        r = self._conn_row("输出尺寸：")
        r.addWidget(self._image_size_var)
        self._conn_form.addRow(r)

        self._comfy_url_var = QLineEdit(runtime.COMFYUI_URL)
        r = self._conn_row("ComfyUI 地址：")
        r.addWidget(self._comfy_url_var)
        self._conn_form.addRow(r)

        self._comfy_wf_var = QLineEdit(runtime.COMFYUI_WORKFLOW_FILE)
        r = self._conn_row("工作流文件：")
        r.addWidget(self._comfy_wf_var)
        self._conn_form.addRow(r)

        self._comment_prob_var = QDoubleSpinBox()
        self._comment_prob_var.setRange(0.0, 1.0)
        self._comment_prob_var.setSingleStep(0.05)
        self._comment_prob_var.setValue(float(runtime.IMAGE_COMMENT_PROBABILITY or 0))
        r = self._conn_row("发图评论概率：")
        r.addWidget(self._comment_prob_var)
        self._conn_form.addRow(r)

        self._conn_form.addRow(_label("语音（小米 MiMo：TTS + ASR）", "SectionTitle"))
        self._tts_prob_var = QDoubleSpinBox()
        self._tts_prob_var.setRange(0.0, 1.0)
        self._tts_prob_var.setSingleStep(0.05)
        self._tts_prob_var.setValue(float(runtime.TTS_PROBABILITY or 0))
        r = self._conn_row("语音回复概率：")
        r.addWidget(self._tts_prob_var)
        self._conn_form.addRow(r)

        self._mimo_key_var = QLineEdit(runtime.MIMO_API_KEY or "")
        self._mimo_key_var.setEchoMode(QLineEdit.EchoMode.Password)
        r = self._conn_row("小米语音 API Key：")
        r.addWidget(self._mimo_key_var)
        r.addWidget(_btn("测试", "Soft", lambda: self._test_api_key("mimo")))
        self._conn_form.addRow(r)

        self._mimo_base_url_var = QLineEdit(runtime.MIMO_API_BASE_URL or "https://api.xiaomimimo.com/v1")
        r = self._conn_row("小米语音 API 地址：")
        r.addWidget(self._mimo_base_url_var)
        self._conn_form.addRow(r)

        r = self._conn_row("TTS 模型：")
        self._tts_model_var = QComboBox()
        self._tts_model_var.addItems([runtime.TTS_MODEL or "mimo-v2.5-tts-voicedesign"])
        self._tts_model_var.setCurrentText(runtime.TTS_MODEL or "mimo-v2.5-tts-voicedesign")
        r.addWidget(self._tts_model_var)
        r.addWidget(_btn("刷新模型", "Soft", self._refresh_tts_models))
        self._conn_form.addRow(r)

        self._asr_enabled_var = QCheckBox("识别用户发来的语音")
        self._asr_enabled_var.setChecked(bool(runtime.ASR_ENABLED))
        self._conn_form.addRow(self._asr_enabled_var)

        r = self._conn_row("识别语种：")
        self._asr_language_var = QComboBox()
        self._asr_language_var.addItems(["auto", "zh", "en"])
        self._asr_language_var.setCurrentText(runtime.ASR_LANGUAGE or "auto")
        r.addWidget(self._asr_language_var)
        self._conn_form.addRow(r)

        r = self._conn_row("ASR 模型：")
        self._asr_model_var = QComboBox()
        self._asr_model_var.addItems([runtime.ASR_MODEL or "mimo-v2.5-asr"])
        self._asr_model_var.setCurrentText(runtime.ASR_MODEL or "mimo-v2.5-asr")
        r.addWidget(self._asr_model_var)
        r.addWidget(_btn("刷新模型", "Soft", self._refresh_asr_models))
        self._conn_form.addRow(r)

        self._conn_form.addRow(_label("大模型兜底（DeepSeek .env）", "SectionTitle"))
        r = self._conn_row("DeepSeek 模型：")
        self._model_var = QComboBox()
        self._model_var.addItems(["deepseek-v4-flash", "deepseek-v4-pro", "deepseek-v4-flash-vision-exp"])
        self._model_var.setCurrentText(config.DEEPSEEK_MODEL)
        r.addWidget(self._model_var)
        r.addWidget(_btn("刷新模型", "Soft", self._refresh_models))
        self._conn_form.addRow(r)

        r = self._conn_row("图片识别模型：")
        self._vision_model_var = QComboBox()
        self._vision_model_var.addItems(["deepseek-v4-flash-vision-exp", "deepseek-v4-pro"])
        self._vision_model_var.setCurrentText(config.DEEPSEEK_VISION_MODEL)
        r.addWidget(self._vision_model_var)
        self._conn_form.addRow(r)

        self._base_url_var = QLineEdit(config.DEEPSEEK_BASE_URL)
        r = self._conn_row("DeepSeek API 地址：")
        r.addWidget(self._base_url_var)
        self._conn_form.addRow(r)

        self._api_key_var = QLineEdit(config.DEEPSEEK_API_KEY)
        self._api_key_var.setEchoMode(QLineEdit.EchoMode.Password)
        r = self._conn_row("DeepSeek API Key：")
        r.addWidget(self._api_key_var)
        r.addWidget(_btn("测试", "Soft", lambda: self._test_api_key("deepseek")))
        self._conn_form.addRow(r)

        save_row = QHBoxLayout()
        save_row.addStretch(1)
        save_row.addWidget(_btn("保存", "Primary", self._save_config))
        self._conn_form.addRow(save_row)

    # ---- 提供商卡片 ----
    def _refresh_models_tab(self):
        frame = getattr(self, "_models_list_frame", None)
        if frame is None:
            return
        while self._ml_lay.count():
            item = self._ml_lay.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()
        try:
            from llm_providers import load_providers
            providers = load_providers()
        except Exception as e:
            logger.warning("读取提供商失败: %s", e)
            return
        for p in providers:
            self._ml_lay.addWidget(self._provider_card(p))
        self._ml_lay.addStretch(1)

    def _provider_card(self, p):
        card = QFrame()
        card.setObjectName("Card")
        lay = QVBoxLayout(card)
        lay.setContentsMargins(14, 10, 14, 10)
        lay.setSpacing(4)
        head = QHBoxLayout()
        head.setSpacing(8)
        dot = "●"
        dot_color = "#2ecc71" if (p.get("api_key") or p.get("type") == "deepseek") else "#e74c3c"
        d = _label(dot)
        d.setStyleSheet(f"color: {dot_color}; font-size: 14px;")
        head.addWidget(d)
        head.addWidget(_label(p.get("name") or p.get("id") or "?", ""))
        t = _label(p.get("type") == "deepseek" and "DeepSeek" or "OpenAI 兼容", "Muted")
        head.addWidget(t)
        if p.get("active"):
            head.addWidget(_label("✓ 当前使用", "SectionTitle"))
        head.addStretch(1)
        pid = p.get("id")
        if not p.get("active"):
            head.addWidget(_btn("设为当前", "Soft", lambda _c=False, x=pid: self._provider_action(x, "activate")))
        head.addWidget(_btn("获取模型", "Soft", lambda _c=False, x=pid: self._provider_action(x, "fetch_models")))
        head.addWidget(_btn("测试", "Soft", lambda _c=False, x=pid: self._provider_action(x, "test")))
        head.addWidget(_btn("编辑", "Soft", lambda _c=False, x=pid: self._provider_action(x, "edit")))
        head.addWidget(_btn("删除", "SoftRed", lambda _c=False, x=pid: self._provider_action(x, "remove")))
        lay.addLayout(head)
        info = ""
        if p.get("type") == "deepseek":
            info = "DeepSeek 官方"
            if p.get("base_url"):
                info += "  ·  " + p["base_url"]
            if p.get("model"):
                info += "  ·  模型 " + p["model"]
        else:
            info = (p.get("base_url") or "未填地址")
            if p.get("model"):
                info += "  ·  模型 " + p["model"]
        lay.addWidget(_label(info, "Muted"))
        return card

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
            QMessageBox.information(self, "已切换", f"已切换到「{p.get('name')}」，重启 Bot 后生效。")
        elif action == "remove":
            if QMessageBox.question(self, "删除提供商", f"确定删除「{p.get('name')}」吗？") != QMessageBox.StandardButton.Yes:
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

    # ===================== 设置（API 参数 / 互动 / Token 用量） =====================
    def _build_settings_tab(self):
        tab = QWidget()
        self.tabs.addTab(tab, "设置")
        sub = QTabWidget(tab)
        sub.setTabBar(CenterTabBar(sub))
        lay = QVBoxLayout(tab)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(sub)
        self._build_appearance_tab(sub)
        self._build_params_tab(sub)
        self._build_interact_tab(sub)
        self._build_usage_tab(sub)

    def _build_appearance_tab(self, sub):
        p = QWidget()
        sub.addTab(p, "外观")
        lay = QVBoxLayout(p)
        lay.setContentsMargins(24, 20, 24, 20)
        lay.setSpacing(14)
        card, cl = _card(p, "界面主题")
        cl.addWidget(_label("选择一种陪伴氛围，切换会立即生效并自动记住。", "Muted"))
        row = QHBoxLayout()
        row.addWidget(_label("主题模式："))
        self._theme_mode_var = QComboBox()
        self._theme_mode_var.addItem("暮光陪伴", "twilight")
        self._theme_mode_var.addItem("深夜模式", "night")
        self._theme_mode_var.addItem("跟随系统", "system")
        self._theme_mode_var.setCurrentIndex(max(0, self._theme_mode_var.findData(self._theme_mode)))
        self._theme_mode_var.currentIndexChanged.connect(
            lambda _index: self._apply_theme(self._theme_mode_var.currentData()))
        row.addWidget(self._theme_mode_var)
        row.addStretch(1)
        cl.addLayout(row)
        self._theme_hint = _label(
            "暮光陪伴：温柔浅紫与留白；深夜模式：低亮度深紫；跟随系统：跟随 Windows 应用外观。",
            "Muted",
        )
        self._theme_hint.setWordWrap(True)
        cl.addWidget(self._theme_hint)
        lay.addWidget(card)
        layout_card, layout_lay = _card(p, "布局说明")
        layout_lay.addWidget(_label("左侧导航聚焦主要页面，首页优先展示运行与成长状态；详细参数集中在设置中。", "Muted"))
        lay.addWidget(layout_card)
        lay.addStretch(1)

    def _build_params_tab(self, sub):
        p = QWidget()
        sub.addTab(p, "API 参数")
        form = QFormLayout(p)
        form.setContentsMargins(24, 20, 24, 20)
        form.setSpacing(14)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft)

        def add_slider(label, key, lo, hi, step=1):
            row = QHBoxLayout()
            slider = QSlider(Qt.Orientation.Horizontal)
            slider.setRange(int(lo * step), int(hi * step))
            slider.setSingleStep(1)
            val = float(getattr(runtime, key, lo))
            slider.setValue(int(val * step))
            val_lbl = _label(str(int(val)) if step == 1 else f"{val:.2f}", "BigValue")
            val_lbl.setFixedWidth(70)
            slider.valueChanged.connect(
                lambda v, k=key, vl=val_lbl, st=step: self._on_slider(v, k, vl, st))
            row.addWidget(slider, 1)
            row.addWidget(val_lbl)
            form.addRow(_label(label + "："), row)
            self._sliders[key] = slider
            self._slider_labels[key] = val_lbl

        add_slider("Temperature (0-2)", "TEMPERATURE", 0.0, 2.0, 100)
        add_slider("最大 Token 数", "MAX_TOKENS", 64, 8192)
        add_slider("对话记忆轮数", "MAX_HISTORY_LENGTH", 2, 500)

        row = QHBoxLayout()
        self._reply_cd_min_var = QDoubleSpinBox()
        self._reply_cd_min_var.setRange(0, 60)
        self._reply_cd_min_var.setValue(float(runtime.REPLY_COOLDOWN_MIN or 0))
        row.addWidget(self._reply_cd_min_var)
        form.addRow(_label("最短冷却（秒）："), row)

        row = QHBoxLayout()
        self._reply_cd_max_var = QDoubleSpinBox()
        self._reply_cd_max_var.setRange(0, 60)
        self._reply_cd_max_var.setValue(float(runtime.REPLY_COOLDOWN_MAX or 3))
        row.addWidget(self._reply_cd_max_var)
        form.addRow(_label("最长冷却（秒）："), row)

        self._thinking_mode_var = QCheckBox("开启思考模式")
        self._thinking_mode_var.setChecked(bool(runtime.THINKING_MODE))
        form.addRow(self._thinking_mode_var)
        form.addRow(QWidget())

    @staticmethod
    def _on_slider(v, key, label, step):
        if step == 1:
            label.setText(str(int(v)))
        else:
            label.setText(f"{v / step:.2f}")

    def _build_interact_tab(self, sub):
        p = QWidget()
        sub.addTab(p, "互动")
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        lay0 = QVBoxLayout(p)
        lay0.setContentsMargins(0, 0, 0, 0)
        lay0.addWidget(scroll)
        body = QWidget()
        scroll.setWidget(body)
        outer = QVBoxLayout(body)
        outer.setContentsMargins(16, 16, 16, 16)
        outer.setSpacing(10)

        # 表情包
        card, cl = _card(body, "表情包")
        r = QHBoxLayout()
        r.addWidget(_label("发送概率："))
        self._sticker_prob_var = QDoubleSpinBox()
        self._sticker_prob_var.setRange(0, 1)
        self._sticker_prob_var.setSingleStep(0.05)
        self._sticker_prob_var.setValue(float(runtime.STICKER_PROBABILITY or 0))
        r.addWidget(self._sticker_prob_var)
        r.addStretch(1)
        cl.addLayout(r)
        self._sticker_dir_var = QLineEdit(runtime.STICKER_DIR)
        cl.addRow = None
        r2 = QHBoxLayout()
        r2.addWidget(_label("表情包文件夹："))
        r2.addWidget(self._sticker_dir_var, 1)
        cl.addLayout(r2)
        outer.addWidget(card)

        # 自动配图
        card, cl = _card(body, "自动配图")
        self._anniversary_var = QLineEdit(runtime.ANNIVERSARY_DATE or "2025-07-20")
        self._anniversary_var.setFixedWidth(120)
        r = QHBoxLayout()
        r.addWidget(_label("纪念日："))
        r.addWidget(self._anniversary_var)
        r.addWidget(_label("YYYY-MM-DD", "Muted"))
        r.addStretch(1)
        cl.addLayout(r)
        r = QHBoxLayout()
        r.addWidget(_label("配图概率："))
        self._illustrate_prob_var = QDoubleSpinBox()
        self._illustrate_prob_var.setRange(0, 1)
        self._illustrate_prob_var.setSingleStep(0.05)
        self._illustrate_prob_var.setValue(float(runtime.AUTO_ILLUSTRATE_PROBABILITY or 0))
        r.addWidget(self._illustrate_prob_var)
        r.addStretch(1)
        cl.addLayout(r)
        outer.addWidget(card)

        # QQ 空间
        card, cl = _card(body, "QQ 空间")
        self._qzone_self_uin_var = QLineEdit(runtime.QZONE_SELF_UIN or "")
        self._qzone_self_uin_var.setFixedWidth(130)
        r = QHBoxLayout()
        r.addWidget(_label("自己的 QQ 号："))
        r.addWidget(self._qzone_self_uin_var)
        r.addStretch(1)
        cl.addLayout(r)
        self._qzone_comment_prob_var = QDoubleSpinBox()
        self._qzone_comment_prob_var.setRange(0, 1)
        self._qzone_comment_prob_var.setSingleStep(0.05)
        self._qzone_comment_prob_var.setValue(float(runtime.QZONE_FEED_COMMENT_PROB or 0.35))
        r = QHBoxLayout()
        r.addWidget(_label("好友动态评论概率："))
        r.addWidget(self._qzone_comment_prob_var)
        r.addStretch(1)
        cl.addLayout(r)
        self._qzone_posts_var = QSpinBox()
        self._qzone_posts_var.setRange(0, 20)
        self._qzone_posts_var.setValue(int(runtime.QZONE_POSTS_PER_DAY or 3))
        r = QHBoxLayout()
        r.addWidget(_label("每天发说说："))
        r.addWidget(self._qzone_posts_var)
        r.addStretch(1)
        cl.addLayout(r)
        self._qzone_interval_min_h_var = QSpinBox(); self._qzone_interval_min_h_var.setRange(0, 12)
        self._qzone_interval_min_m_var = QSpinBox(); self._qzone_interval_min_m_var.setRange(0, 59)
        self._qzone_interval_max_h_var = QSpinBox(); self._qzone_interval_max_h_var.setRange(0, 12)
        self._qzone_interval_max_m_var = QSpinBox(); self._qzone_interval_max_m_var.setRange(0, 59)
        h, m = _split_hours(runtime.QZONE_POST_INTERVAL_MIN or 2)
        self._qzone_interval_min_h_var.setValue(h); self._qzone_interval_min_m_var.setValue(m)
        h, m = _split_hours(runtime.QZONE_POST_INTERVAL_MAX or 4)
        self._qzone_interval_max_h_var.setValue(h); self._qzone_interval_max_m_var.setValue(m)
        r = QHBoxLayout()
        r.addWidget(_label("条间间隔（时:分）："))
        r.addWidget(self._qzone_interval_min_h_var); r.addWidget(_label(":", "Muted"))
        r.addWidget(self._qzone_interval_min_m_var)
        r.addWidget(_label("—", "Muted"))
        r.addWidget(self._qzone_interval_max_h_var); r.addWidget(_label(":", "Muted"))
        r.addWidget(self._qzone_interval_max_m_var)
        r.addStretch(1)
        cl.addLayout(r)
        self._qzone_img_prob_var = QDoubleSpinBox()
        self._qzone_img_prob_var.setRange(0, 1)
        self._qzone_img_prob_var.setSingleStep(0.05)
        self._qzone_img_prob_var.setValue(float(runtime.QZONE_POST_IMAGE_PROB or 0.6))
        r = QHBoxLayout()
        r.addWidget(_label("说说配图概率："))
        r.addWidget(self._qzone_img_prob_var)
        r.addStretch(1)
        cl.addLayout(r)
        self._qzone_img_check_var = QCheckBox("说说配图质量检查")
        self._qzone_img_check_var.setChecked(bool(runtime.QZONE_IMAGE_CHECK))
        cl.addWidget(self._qzone_img_check_var)
        outer.addWidget(card)

        # 主动消息
        card, cl = _card(body, "主动消息")
        self._proactive_only_var = QLineEdit(runtime.PROACTIVE_ONLY_USER_ID or "")
        self._proactive_only_var.setFixedWidth(140)
        r = QHBoxLayout()
        r.addWidget(_label("只对指定 QQ 号主动发消息："))
        r.addWidget(self._proactive_only_var)
        r.addStretch(1)
        cl.addLayout(r)
        self._proactive_interval_var = QSpinBox(); self._proactive_interval_var.setRange(5, 300)
        self._proactive_interval_var.setValue(int(runtime.PROACTIVE_INTERVAL_MIN or 45))
        self._proactive_interval_max_var = QSpinBox(); self._proactive_interval_max_var.setRange(5, 300)
        self._proactive_interval_max_var.setValue(int(runtime.PROACTIVE_INTERVAL_MAX or 75))
        r = QHBoxLayout()
        r.addWidget(_label("检查间隔（分钟）："))
        r.addWidget(self._proactive_interval_var)
        r.addWidget(_label("—", "Muted"))
        r.addWidget(self._proactive_interval_max_var)
        r.addStretch(1)
        cl.addLayout(r)
        self._proactive_prob_var = QDoubleSpinBox()
        self._proactive_prob_var.setRange(0, 1)
        self._proactive_prob_var.setSingleStep(0.05)
        self._proactive_prob_var.setValue(float(runtime.PROACTIVE_PROBABILITY or 0))
        r = QHBoxLayout()
        r.addWidget(_label("主动概率："))
        r.addWidget(self._proactive_prob_var)
        r.addStretch(1)
        cl.addLayout(r)
        self._proactive_gap_var = QSpinBox(); self._proactive_gap_var.setRange(1, 120)
        self._proactive_gap_var.setValue(int(runtime.PROACTIVE_GAP_MIN or 10))
        r = QHBoxLayout()
        r.addWidget(_label("防打扰（分钟）："))
        r.addWidget(self._proactive_gap_var)
        r.addStretch(1)
        cl.addLayout(r)
        self._proactive_followup_hours_var = QDoubleSpinBox()
        self._proactive_followup_hours_var.setRange(0, 24)
        self._proactive_followup_hours_var.setValue(float(runtime.PROACTIVE_FOLLOWUP_HOURS or 2))
        r = QHBoxLayout()
        r.addWidget(_label("追问时长（小时）："))
        r.addWidget(self._proactive_followup_hours_var)
        r.addStretch(1)
        cl.addLayout(r)
        self._night_silence_var = QDoubleSpinBox()
        self._night_silence_var.setRange(0, 24)
        self._night_silence_var.setValue(float(runtime.NIGHT_SILENCE_HOURS or 8))
        r = QHBoxLayout()
        r.addWidget(_label("晚安静默（小时）："))
        r.addWidget(self._night_silence_var)
        r.addStretch(1)
        cl.addLayout(r)
        outer.addWidget(card)

        # 实时信息
        card, cl = _card(body, "实时信息")
        cl.addWidget(_label("联网搜索", "SectionTitle"))
        self._weather_city_var = QLineEdit(runtime.WEATHER_CITY)
        self._weather_city_var.setFixedWidth(180)
        r = QHBoxLayout()
        r.addWidget(_label("天气城市："))
        r.addWidget(self._weather_city_var)
        r.addStretch(1)
        cl.addLayout(r)
        outer.addWidget(card)
        outer.addStretch(1)

    def _build_usage_tab(self, sub):
        p = QWidget()
        sub.addTab(p, "Token 用量")
        lay = QVBoxLayout(p)
        lay.setContentsMargins(16, 16, 16, 16)
        lay.setSpacing(12)
        card, cl = _card(p)
        self._usage_vars = {}
        items = [("累计调用", "calls"), ("输入 tokens", "prompt"),
                 ("输出 tokens", "completion"), ("总计 tokens", "total"),
                 ("本地模型调用", "local")]
        grid = QGridLayout()
        for i, (label, key) in enumerate(items):
            grid.addWidget(_label(label + "：", "Muted"), 0, i * 2)
            v = _label("-", "BigValue")
            grid.addWidget(v, 0, i * 2 + 1)
            self._usage_vars[key] = v
        cl.addLayout(grid)
        lay.addWidget(card)

        table_card, tcl = _card(p, "分模型明细")
        self._usage_tree = QTableWidget(0, 5)
        self._usage_tree.setHorizontalHeaderLabels(["模型", "调用次数", "输入 tokens", "输出 tokens", "总计 tokens"])
        self._usage_tree.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for c in range(1, 5):
            self._usage_tree.horizontalHeader().setSectionResizeMode(c, QHeaderView.ResizeMode.ResizeToContents)
        self._usage_tree.verticalHeader().setVisible(False)
        self._usage_tree.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        tcl.addWidget(self._usage_tree)
        bar = QHBoxLayout()
        bar.addWidget(_btn("立即刷新", "Soft", self._refresh_usage))
        bar.addWidget(_btn("清零", "Danger", self._reset_usage))
        bar.addStretch(1)
        tcl.addLayout(bar)
        lay.addWidget(table_card, 1)

        self._refresh_usage()

    # ===================== 保存 / 设置改动检测 =====================
    def _hook_dirty_tracking(self):
        if getattr(self, "_dirty_hooked", False):
            return
        self._dirty_hooked = True
        self._dirty = False
        self._suppress_dirty = False
        exclude = {"_log_text", "_auto_scroll_var", "_voice_output_text", "_usage_tree", "_theme_mode_var"}
        for name, w in vars(self).items():
            if name in exclude or not isinstance(w, QWidget):
                continue
            self._connect_dirty(w)
        # 字典容器里的配置控件
        for w in (list(self._sliders.values()) + list(self._entries.values())
                  + list(self._text_widgets.values()) + list(self._birth_vars.values())):
            if isinstance(w, QWidget):
                self._connect_dirty(w)

    def _connect_dirty(self, w):
        try:
            if isinstance(w, QLineEdit):
                w.textChanged.connect(lambda *a: self._on_setting_changed())
            elif isinstance(w, (QTextEdit, QPlainTextEdit)):
                w.textChanged.connect(lambda *a: self._on_setting_changed())
            elif isinstance(w, QComboBox):
                w.currentIndexChanged.connect(lambda *a: self._on_setting_changed())
            elif isinstance(w, QCheckBox):
                w.toggled.connect(lambda *a: self._on_setting_changed())
            elif isinstance(w, (QSlider, QSpinBox, QDoubleSpinBox)):
                w.valueChanged.connect(lambda *a: self._on_setting_changed())
        except Exception:
            pass

    def _on_setting_changed(self, *_a):
        if self._suppress_dirty:
            return
        self._dirty = True
        btn = getattr(self, "header", None) and self.header.save_btn
        if btn is not None:
            btn.show()

    def _clear_dirty(self):
        self._dirty = False
        btn = getattr(self, "header", None) and self.header.save_btn
        if btn is not None:
            btn.hide()

    def _save_from_bar(self):
        try:
            self._suppress_dirty = True
            self._save_config()
        finally:
            self._suppress_dirty = False
            self._clear_dirty()

    def _collect_config(self) -> dict:
        data = {}
        for k, w in self._entries.items():
            if isinstance(w, QLineEdit):
                data[k] = w.text()
            elif isinstance(w, QComboBox):
                data[k] = w.currentText()
            else:
                data[k] = w or ""
        # 出生日期合并
        try:
            y = self._birth_vars["y"].currentText()
            m = self._birth_vars["m"].currentText()
            d = self._birth_vars["d"].currentText()
            if y and m and d:
                data["GIRLFRIEND_BIRTHDATE"] = f"{y}-{m}-{d}"
        except Exception:
            pass
        for k, w in self._text_widgets.items():
            data[k] = w.toPlainText() if hasattr(w, "toPlainText") else ""
        data["SYSTEM_PROMPT"] = runtime.SYSTEM_PROMPT
        # GIRLFRIEND_SCENARIO：GUI 人设页无此编辑框，保存时不得清空——
        # 保留 .runtime_config.json 中已有的手工配置值（曾强制置空导致手填场景被覆盖丢失）
        data["GIRLFRIEND_SCENARIO"] = runtime.GIRLFRIEND_SCENARIO
        for k, slider in self._sliders.items():
            data[k] = slider.value() / 100 if k == "TEMPERATURE" else slider.value()
        data["REPLY_COOLDOWN_MIN"] = self._reply_cd_min_var.value()
        data["REPLY_COOLDOWN_MAX"] = self._reply_cd_max_var.value()
        data["STICKER_PROBABILITY"] = self._sticker_prob_var.value()
        data["STICKER_DIR"] = self._sticker_dir_var.text().strip() or "晚晚/图片/表情包"
        data["ANNIVERSARY_DATE"] = self._anniversary_var.text().strip() or "2025-07-20"
        data["AUTO_ILLUSTRATE_PROBABILITY"] = self._illustrate_prob_var.value()
        data["QZONE_SELF_UIN"] = self._qzone_self_uin_var.text().strip()
        data["QZONE_FEED_COMMENT_PROB"] = self._qzone_comment_prob_var.value()
        data["QZONE_POSTS_PER_DAY"] = self._qzone_posts_var.value()
        data["QZONE_POST_INTERVAL_MIN"] = (self._qzone_interval_min_h_var.value()
                                           + self._qzone_interval_min_m_var.value() / 60)
        data["QZONE_POST_INTERVAL_MAX"] = (self._qzone_interval_max_h_var.value()
                                           + self._qzone_interval_max_m_var.value() / 60)
        data["QZONE_POST_IMAGE_PROB"] = self._qzone_img_prob_var.value()
        data["QZONE_IMAGE_CHECK"] = 1 if self._qzone_img_check_var.isChecked() else 0
        data["PROACTIVE_ONLY_USER_ID"] = self._proactive_only_var.text().strip()
        data["PROACTIVE_INTERVAL_MIN"] = self._proactive_interval_var.value()
        data["PROACTIVE_INTERVAL_MAX"] = self._proactive_interval_max_var.value()
        data["PROACTIVE_PROBABILITY"] = self._proactive_prob_var.value()
        data["PROACTIVE_GAP_MIN"] = self._proactive_gap_var.value()
        data["PROACTIVE_FOLLOWUP_HOURS"] = self._proactive_followup_hours_var.value()
        data["NIGHT_SILENCE_HOURS"] = self._night_silence_var.value()
        data["WEATHER_CITY"] = self._weather_city_var.text().strip() or "南昌"
        data["THINKING_MODE"] = 1 if self._thinking_mode_var.isChecked() else 0
        data["IMAGE_GEN_ENABLED"] = 1 if self._imagegen_enabled_var.isChecked() else 0
        data["IMAGE_GEN_SIZE"] = self._image_size_var.currentText().strip() or "1024*1024"
        data["IMAGE_BACKEND"] = self._image_backend_var.currentData() or "dashscope"
        data["DASHSCOPE_BASE_URL"] = self._dashscope_base_url_var.text().strip() or "https://dashscope.aliyuncs.com"
        data["COMFYUI_URL"] = self._comfy_url_var.text().strip() or "http://127.0.0.1:8188"
        data["COMFYUI_WORKFLOW_FILE"] = self._comfy_wf_var.text().strip() or "comfy_workflow.json"
        data["IMAGE_PROMPT_USE_LLM"] = 1 if self._image_prompt_use_llm_var.isChecked() else 0
        data["IMAGE_COMMENT_PROBABILITY"] = self._comment_prob_var.value()
        data["TTS_PROBABILITY"] = self._tts_prob_var.value()
        data["TTS_VOICE_DESCRIPTION"] = self._tts_voice_desc_text.toPlainText().strip()
        data["TTS_MODEL"] = self._tts_model_var.currentText().strip() or "mimo-v2.5-tts-voicedesign"
        data["MIMO_API_KEY"] = self._mimo_key_var.text().strip()
        data["MIMO_API_BASE_URL"] = self._mimo_base_url_var.text().strip() or "https://api.xiaomimimo.com/v1"
        data["ASR_ENABLED"] = 1 if self._asr_enabled_var.isChecked() else 0
        data["ASR_LANGUAGE"] = self._asr_language_var.currentText().strip() or "auto"
        data["ASR_MODEL"] = self._asr_model_var.currentText().strip() or "mimo-v2.5-asr"
        data["GUI_THEME_MODE"] = self._theme_mode
        return data

    def _save_config(self):
        try:
            data = self._collect_config()
            runtime.update(data)
            runtime.save_to_file()
            # 出生日期同步
            try:
                y = self._birth_vars["y"].currentText()
                m = self._birth_vars["m"].currentText()
                d = self._birth_vars["d"].currentText()
                if y and m and d:
                    self._entries["GIRLFRIEND_BIRTHDATE"] = f"{y}-{m}-{d}"
            except Exception:
                pass
            # 连接设置（Key/URL/Token/模型名）持久化到 .env：
            # 只改内存会导致重启面板后丢失（曾"假保存"），这里一并写回；
            # 未填的项跳过（不清空 .env 已有 Key）。
            from config import save_env_overrides
            conn = {}
            for env_key, var in (
                ("DEEPSEEK_API_KEY", "_api_key_var"),
                ("DEEPSEEK_BASE_URL", "_base_url_var"),
                ("DEEPSEEK_MODEL", "_model_var"),
                ("DEEPSEEK_VISION_MODEL", "_vision_model_var"),
                ("ONEBOT_WS_URL", "_ws_url_var"),
                ("ONEBOT_ACCESS_TOKEN", "_ws_token_var"),
                ("DASHSCOPE_API_KEY", "_dashscope_key_var"),
                ("DASHSCOPE_BASE_URL", "_dashscope_base_url_var"),
                ("DASHSCOPE_IMAGE_MODEL", "_image_model_var"),
            ):
                w = getattr(self, var, None)
                if w is not None:
                    val = (w.currentText() if isinstance(w, QComboBox) else w.text()).strip()
                    if val:
                        conn[env_key] = val
            saved_env = save_env_overrides(conn)
            msg = "人设与参数已保存并立即生效。"
            if conn:
                if saved_env:
                    msg += "\n连接设置（API Key 等）已写入 .env，重启后依然有效。"
                else:
                    msg += "\n未找到 .env，连接设置仅本次会话生效——请将 晚晚/配置/.env.example 复制为 .env 后重填。"
            QMessageBox.information(self, "保存完成", msg)
        except (ValueError, TypeError) as e:
            QMessageBox.critical(self, "保存失败", f"配置项数值格式不正确：{e}")

    def _reload_config(self):
        self._suppress_dirty = True
        runtime.load_from_file()
        self._apply_theme(str(getattr(runtime, "GUI_THEME_MODE", "twilight") or "twilight"),
                          persist=False)
        for k, w in self._entries.items():
            if isinstance(w, QLineEdit):
                w.setText(str(getattr(runtime, k, "")))
        try:
            cur = str(getattr(runtime, "GIRLFRIEND_BIRTHDATE", "") or "")
            y0, m0, d0 = cur.split("-")
            self._birth_vars["y"].setCurrentText(y0)
            self._birth_vars["m"].setCurrentText(str(int(m0)))
            self._birth_vars["d"].setCurrentText(str(int(d0)))
        except ValueError:
            pass
        for k, w in self._text_widgets.items():
            w.setPlainText(str(getattr(runtime, k, "") or ""))
        for k, slider in self._sliders.items():
            v = float(getattr(runtime, k, 0))
            slider.setValue(int(v * 100) if k == "TEMPERATURE" else int(v))
        self._reply_cd_min_var.setValue(float(runtime.REPLY_COOLDOWN_MIN or 0))
        self._reply_cd_max_var.setValue(float(runtime.REPLY_COOLDOWN_MAX or 3))
        self._sticker_prob_var.setValue(float(runtime.STICKER_PROBABILITY or 0))
        self._sticker_dir_var.setText(runtime.STICKER_DIR)
        self._anniversary_var.setText(runtime.ANNIVERSARY_DATE or "2025-07-20")
        self._illustrate_prob_var.setValue(float(runtime.AUTO_ILLUSTRATE_PROBABILITY or 0))
        self._qzone_self_uin_var.setText(runtime.QZONE_SELF_UIN or "")
        self._qzone_comment_prob_var.setValue(float(runtime.QZONE_FEED_COMMENT_PROB or 0.35))
        self._qzone_posts_var.setValue(int(runtime.QZONE_POSTS_PER_DAY or 3))
        h, m = _split_hours(runtime.QZONE_POST_INTERVAL_MIN or 2)
        self._qzone_interval_min_h_var.setValue(h); self._qzone_interval_min_m_var.setValue(m)
        h, m = _split_hours(runtime.QZONE_POST_INTERVAL_MAX or 4)
        self._qzone_interval_max_h_var.setValue(h); self._qzone_interval_max_m_var.setValue(m)
        self._qzone_img_prob_var.setValue(float(runtime.QZONE_POST_IMAGE_PROB or 0.6))
        self._qzone_img_check_var.setChecked(bool(runtime.QZONE_IMAGE_CHECK))
        self._proactive_only_var.setText(runtime.PROACTIVE_ONLY_USER_ID or "")
        self._proactive_interval_var.setValue(int(runtime.PROACTIVE_INTERVAL_MIN or 45))
        self._proactive_interval_max_var.setValue(int(runtime.PROACTIVE_INTERVAL_MAX or 75))
        self._proactive_prob_var.setValue(float(runtime.PROACTIVE_PROBABILITY or 0))
        self._proactive_gap_var.setValue(int(runtime.PROACTIVE_GAP_MIN or 10))
        self._proactive_followup_hours_var.setValue(float(runtime.PROACTIVE_FOLLOWUP_HOURS or 2))
        self._night_silence_var.setValue(float(runtime.NIGHT_SILENCE_HOURS or 8))
        self._weather_city_var.setText(runtime.WEATHER_CITY)
        self._thinking_mode_var.setChecked(bool(runtime.THINKING_MODE))
        self._imagegen_enabled_var.setChecked(bool(runtime.IMAGE_GEN_ENABLED))
        self._image_size_var.setCurrentText(runtime.IMAGE_GEN_SIZE or "1024*1024")
        self._image_backend_var.setCurrentIndex(
            1 if (runtime.IMAGE_BACKEND or "dashscope") == "comfyui" else 0)
        self._dashscope_base_url_var.setText(runtime.DASHSCOPE_BASE_URL or "https://dashscope.aliyuncs.com")
        self._image_prompt_use_llm_var.setChecked(bool(runtime.IMAGE_PROMPT_USE_LLM))
        self._comfy_url_var.setText(runtime.COMFYUI_URL or "http://127.0.0.1:8188")
        self._comfy_wf_var.setText(runtime.COMFYUI_WORKFLOW_FILE or "comfy_workflow.json")
        self._comment_prob_var.setValue(float(runtime.IMAGE_COMMENT_PROBABILITY or 0))
        self._tts_prob_var.setValue(float(runtime.TTS_PROBABILITY or 0))
        self._tts_voice_desc_text.setPlainText(runtime.TTS_VOICE_DESCRIPTION or "")
        self._tts_model_var.setCurrentText(runtime.TTS_MODEL or "mimo-v2.5-tts-voicedesign")
        self._mimo_key_var.setText(runtime.MIMO_API_KEY or "")
        self._mimo_base_url_var.setText(runtime.MIMO_API_BASE_URL or "https://api.xiaomimimo.com/v1")
        self._asr_enabled_var.setChecked(bool(runtime.ASR_ENABLED))
        self._asr_language_var.setCurrentText(runtime.ASR_LANGUAGE or "auto")
        self._asr_model_var.setCurrentText(runtime.ASR_MODEL or "mimo-v2.5-asr")
        self._ws_url_var.setText(config.ONEBOT_WS_URL)
        self._ws_token_var.setText(config.ONEBOT_ACCESS_TOKEN)
        self._model_var.setCurrentText(config.DEEPSEEK_MODEL)
        self._vision_model_var.setCurrentText(config.DEEPSEEK_VISION_MODEL)
        self._base_url_var.setText(config.DEEPSEEK_BASE_URL)
        self._api_key_var.setText(config.DEEPSEEK_API_KEY)
        self._dashscope_key_var.setText(config.DASHSCOPE_API_KEY)
        self._image_model_var.setCurrentText(config.DASHSCOPE_IMAGE_MODEL)
        self._suppress_dirty = False
        self._clear_dirty()
        QMessageBox.information(self, "重载完成", "已从磁盘重新加载配置。")

    def _sync_birthdate(self, *_a):
        pass  # 值已即时反映在 _collect_config

    # ===================== 定时器 / 刷新 =====================
    def _start_timers(self):
        self._setup_logging()
        QTimer.singleShot(300, lambda: self._refresh_models(silent=True))
        QTimer.singleShot(500, self._detect_qq)
        self._usage_timer = QTimer(self)
        self._usage_timer.timeout.connect(self._refresh_usage)
        self._usage_timer.start(1000)
        self._sl_timer = QTimer(self)
        self._sl_timer.timeout.connect(self._poll_snowluma_status)
        self._sl_timer.start(1000)
        self._log_timer = QTimer(self)
        self._log_timer.timeout.connect(self._poll_logs)
        self._log_timer.start(200)
        self._growth_timer = QTimer(self)
        self._growth_timer.timeout.connect(self._growth_tick)
        self._growth_timer.start(5000)
        self._theme_timer = QTimer(self)
        self._theme_timer.timeout.connect(self._refresh_system_theme)
        self._theme_timer.start(60000)

    def _safe_after(self, delay, fn):
        """安全调度回调到主线程（可在线程中调用）。

        必须传 self 作为 context：否则从子线程调用 QTimer.singleShot 时
        定时器挂在子线程（无事件循环），回调永不执行（QQ 检测等后台线程
        的结果就回不到界面）。
        """
        try:
            QTimer.singleShot(delay, self, fn)
        except Exception:
            pass

    # ---- 日志 ----
    def _setup_logging(self):
        # GUI 模式不走 main.py 的 basicConfig，root 默认 WARNING 会把 INFO 全过滤，
        # 导致日志页空白——这里显式设 INFO 并挂队列 handler（去重，防重复挂载）
        root = logging.getLogger()
        root.setLevel(logging.INFO)
        if not any(isinstance(h, LogQueueHandler) for h in root.handlers):
            handler = LogQueueHandler(self._log_queue)
            handler.setFormatter(logging.Formatter(
                "%(asctime)s [%(levelname)s] %(name)s: %(message)s", "%H:%M:%S"))
            root.addHandler(handler)
        logging.getLogger("gui_qt").info("控制面板已启动（PySide6）")
        logging.getLogger("gui_qt").info("使用流程：首页 → 启动 SNOWLUMA → 启动 bot")

    def _poll_logs(self):
        try:
            while True:
                line = self._log_queue.get_nowait()
                self._log_text.appendPlainText(line)
        except queue.Empty:
            pass
        if self._auto_scroll_var.isChecked():
            sb = self._log_text.verticalScrollBar()
            sb.setValue(sb.maximum())

    def _clear_log(self):
        self._log_text.clear()

    # ---- 用量 ----
    def _refresh_usage(self):
        if not getattr(self, "_usage_vars", None):
            return
        try:
            s = usage_tracker.snapshot()
        except Exception:
            return
        t = s["totals"]
        self._usage_vars["calls"].setText(str(s["calls"]))
        self._usage_vars["prompt"].setText(str(t.get("prompt_tokens", 0)))
        self._usage_vars["completion"].setText(str(t.get("completion_tokens", 0)))
        self._usage_vars["total"].setText(str(t.get("total_tokens", 0)))
        self._usage_vars["local"].setText(str(s.get("local_calls", 0)))
        tree = getattr(self, "_usage_tree", None)
        if tree is None:
            return
        tree.setRowCount(0)
        for model, m in s.get("per_model", {}).items():
            row = tree.rowCount()
            tree.insertRow(row)
            for c, v in enumerate((model, m.get("calls", 0), m.get("prompt_tokens", 0),
                                   m.get("completion_tokens", 0), m.get("total_tokens", 0))):
                tree.setItem(row, c, QTableWidgetItem(str(v)))

    def _reset_usage(self):
        if QMessageBox.question(self, "清零", "确定要清零 Token 用量统计吗？") != QMessageBox.StandardButton.Yes:
            return
        usage_tracker.reset()
        self._refresh_usage()

    # ---- 成长状态 ----
    def _refresh_growth_stats(self):
        try:
            import personality_state as pstate
            import liveness
            import live_info
            import life_state
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
            # 状态类格子：优先用大模型提炼的今日状态，未就绪/失败回退关键词
            self._ensure_today_digest()
            dig = self._today_digest or {}
            vals["state"] = life_state.current_activity_text()
            vals["rel_hot"] = dig.get("rel") or pstate.relationship_temperature()
            vals["energy"] = dig.get("energy") or pstate.energy_state()
            vals["now_thought"] = dig.get("moment") or pstate.current_thought(boyfriend)
            live_mood = pstate.current_mood(boyfriend) if boyfriend else "平静"
            vals["mood"] = live_mood if live_mood != "平静" else (dig.get("mood") or live_mood)
            delta = growth_diary.get_today_affection_delta()
            vals["mood_delta"] = f"{'+' if delta > 0 else ''}{delta}" if delta else ""
            moods = []
            if liveness.today_mood_low():
                moods.append("心情低落")
            if boyfriend:
                try:
                    import emotion_state
                    emotion_values = liveness.emotion_snapshot(boyfriend)
                    active_emotions = sorted(
                        ((name, value) for name, value in emotion_values.items()
                         if value >= emotion_state.ACTIVE_THRESHOLD),
                        key=lambda item: item[1], reverse=True,
                    )
                    moods.extend(
                        f"{emotion_state.EMOTION_LABELS[name]} {round(value):d}%"
                        for name, value in active_emotions[:2]
                    )
                    gr = liveness.grudge_count_today(boyfriend)
                    if gr > 0:
                        moods.append(f"被惹 {gr} 次")
                except Exception:
                    pass
            vals["mood_state"] = " / ".join(moods) if moods else "正常"
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
                    total = conn.execute("SELECT COUNT(*) AS cnt FROM chat_history").fetchone()["cnt"]
                vals["chats"] = f"{total} 条"
            except Exception:
                vals["chats"] = "—"
            for key, lbl in self._growth_labels.items():
                v = vals.get(key, "")
                lbl.setText(v if v else "—")
            # 称号：并入「性格阶段」格子的悬停说明（不占格子）
            try:
                title = pstate.get_title()
                tip = self._growth_tips["stage"]
                tip.text = GROWTH_HINTS.get("stage", "") + (f"　当前称号：{title}" if title else "")
            except Exception:
                pass
            evo = []
            try:
                notes = edb.get_recent_evolution_notes(2)
                evo = [f"{n['note_date']}：{n['note']}" for n in notes]
            except Exception:
                pass
            axes = pstate.get_axes()
            axis_text = " / ".join(
                f"{pstate.PERSONALITY_AXES[key]} {value}"
                for key, value in axes.items()
            )
            evolution_text = "　|　".join(evo) if evo else "暂无性格演化记录"
            self._life_state_var.setText(life_state.status_line())
            self._personality_axes_var.setText("性格轮廓　" + axis_text)
            self._growth_evo_var.setText("最近演化　" + evolution_text)
        except Exception as e:
            self._life_state_var.setText("生活线读取失败")
            self._personality_axes_var.setText("性格轮廓读取失败")
            self._growth_evo_var.setText(f"成长状态读取失败：{e}")


    # ===================== 今日状态（大模型提炼，缓存） =====================
    _DIGEST_TTL = 600  # 秒：大模型结果缓存时长（约 10 分钟）

    def _today_digest_stale(self):
        """大模型结果是否过期（无结果/超时）。"""
        return (not self._today_digest) or (time.time() - self._today_digest_ts > self._DIGEST_TTL)

    def _ensure_today_digest(self):
        """过期且未在构建时，后台线程重新调用大模型。GUI 每 5 秒刷新只读缓存，不阻塞。"""
        if self._digest_building or not self._today_digest_stale():
            return
        self._digest_building = True
        threading.Thread(target=self._digest_worker, daemon=True).start()

    def _digest_worker(self):
        data = self._build_today_digest()
        if data:
            self._today_digest = data
            self._today_digest_ts = time.time()
        self._digest_building = False
        self._safe_after(0, self._refresh_growth_stats)

    def _build_today_digest(self) -> dict:
        """用大模型提炼今天的状态（实时情绪/状态/关系温度/能量/今天的小事）；失败返回空 dict。"""
        import asyncio, json
        import memory as longterm_memory
        from deepseek_client import DeepSeekClient
        try:
            conn = longterm_memory._get_conn()
            with longterm_memory._lock:
                rows = conn.execute(
                    "SELECT role, content FROM chat_history ORDER BY id DESC LIMIT 40"
                ).fetchall()
            rows = list(reversed(rows))
        except Exception:
            rows = []
        if not rows:
            return {}
        hist = "\n".join(f"{'我' if r['role'] == 'user' else '她'}: {r['content']}" for r in rows)
        prompt = (
            f"你是{runtime.GIRLFRIEND_NAME}的成长记录助手。下面是今天她和男朋友最近的聊天记录（节选）。\n"
            "请提炼她今天的状态，只返回一段 JSON，字段如下：\n"
            '{"mood":"她此刻的情绪(2-4字，如 开心/撒娇/疲惫/委屈，没明确则给 平静)",'
            '"state":"她此刻在做什么(如 在休息/在床上/刚忙完，基于最近对话，不要虚构时间与地点)",'
            '"rel":"她对这段关系最近的冷热感(从 滚烫/温热/常温/转凉/冷淡 选一个)",'
            '"energy":"她今天累不累(从 精神/还可以/有点累/很累/困了 选一个)",'
            '"moment":"今天最值得记的一件小事(一句话，≤30字，最生动、最有生活感的那一件)"}\n'
            "只返回 JSON，不要任何其它文字。\n\n聊天记录：\n" + hist
        )
        async def _run():
            client = DeepSeekClient()
            try:
                return await client.chat(
                    [{"role": "system", "content": f"你是{runtime.GIRLFRIEND_NAME}的成长记录助理，只回 JSON。"},
                     {"role": "user", "content": prompt}],
                    temperature=0.3, max_tokens=240, disable_thinking=True,
                )
            finally:
                await client.aclose()
        try:
            text = (asyncio.run(_run()) or "").strip()
        except Exception as ex:
            logger.warning("大模型提炼今日状态失败: %s", ex)
            return {}
        s = text.find("{")
        e = text.rfind("}")
        try:
            return json.loads(text[s:e + 1]) if (s >= 0 and e > s) else {}
        except Exception:
            return {}

    def _growth_tick(self):
        self._refresh_growth_stats()

    # ===================== QQ 检测 =====================
    def _detect_qq(self):
        threading.Thread(target=self._qq_detect_worker, daemon=True).start()

    def _qq_detect_worker(self):
        try:
            found, path, ver = detect_qq()
        except Exception as e:
            found, path, ver = False, "", ""
            logger.warning("QQ 检测异常: %s", e)
        self._safe_after(0, lambda: self._on_qq_detected(found, path, ver))

    def _on_qq_detected(self, found, path, ver):
        if found:
            self._qq_status_var.setText("已安装")
            self._qq_version_var.setText("版本：" + (ver or "未知版本"))
            self._qq_path_var.setText("路径：" + (path or "-"))
        else:
            self._qq_status_var.setText("未检测到 QQ")
            self._qq_version_var.setText("版本：-")
            self._qq_path_var.setText("路径：未检测到 QQ")

    # ===================== SnowLuma 启停 =====================
    def _start_snowluma(self):
        if self._snowluma_proc and self._snowluma_proc.poll() is None:
            QMessageBox.warning(self, "提示", "SNOWLUMA 已在运行中")
            return
        import shutil
        # 打包模式：优先用内置 node.exe（随 exe 分发）；否则找系统 Node
        if is_frozen():
            node_exe = code_path("node.exe")
            if not os.path.isfile(node_exe):
                QMessageBox.critical(self, "启动失败", "打包内未找到 node.exe")
                return
            self._node_exe = node_exe
        else:
            node_exe = shutil.which("node")
            if node_exe is None:
                QMessageBox.critical(self, "启动失败", "未检测到 Node.js，请先安装 Node.js 22+")
                return
            self._node_exe = node_exe
        self._sl_btn_start.setEnabled(False)
        self._sl_btn_start.setText("检查中...")
        threading.Thread(target=self._snowluma_check_worker, daemon=True).start()

    def _snowluma_check_worker(self):
        try:
            check = subprocess.run([self._node_exe, "check-node-version.cjs"], cwd=SNOWLUMA_DIR,
                                   capture_output=True, text=True, timeout=15,
                                   creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
            ok = check.returncode == 0
            err = (check.stderr or "").strip() if not ok else ""
        except Exception as e:
            ok, err = False, str(e)
        self._safe_after(0, lambda: self._start_snowluma_after_check(ok, err))

    def _start_snowluma_after_check(self, ok, err):
        self._sl_btn_start.setEnabled(True)
        self._sl_btn_start.setText("启动")
        if not ok:
            QMessageBox.critical(self, "启动失败", "Node.js 版本不满足要求：\n" + (err or "未知错误"))
            return
        try:
            self._snowluma_proc = subprocess.Popen(
                [self._node_exe, "index.mjs"], cwd=SNOWLUMA_DIR,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, encoding="utf-8", errors="replace",
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        except Exception as e:
            QMessageBox.critical(self, "启动失败", str(e))
            self._snowluma_proc = None
            return
        self._snowluma_status_var.setText("运行中")
        self._sl_btn_start.setEnabled(False)
        self._sl_btn_stop.setEnabled(True)
        self._snowluma_output_thread = threading.Thread(target=self._read_snowluma_output, daemon=True)
        self._snowluma_output_thread.start()
        logging.getLogger("snowluma").info("SNOWLUMA 已启动 (PID=%s)", self._snowluma_proc.pid)

    def _read_snowluma_output(self):
        proc = self._snowluma_proc
        if not proc or not proc.stdout:
            return
        lg = logging.getLogger("snowluma")
        for line in iter(proc.stdout.readline, ""):
            if line:
                lg.info(line.rstrip())
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
        self._snowluma_status_var.setText("已停止")
        self._sl_btn_start.setEnabled(True)
        self._sl_btn_stop.setEnabled(False)
        logging.getLogger("snowluma").info("SNOWLUMA 已停止")

    def _restart_snowluma(self):
        proc = self._snowluma_proc
        if proc is None or proc.poll() is not None:
            self._start_snowluma()
            return
        logging.getLogger("snowluma").info("SNOWLUMA 正在重启...")
        self._stop_snowluma()
        QTimer.singleShot(300, lambda: self._wait_snowluma_exit(proc))

    def _wait_snowluma_exit(self, proc):
        if proc and proc.poll() is None:
            QTimer.singleShot(300, lambda: self._wait_snowluma_exit(proc))
        else:
            self._start_snowluma()

    def _poll_snowluma_status(self):
        if self._snowluma_proc and self._snowluma_proc.poll() is None:
            if self._snowluma_status_var.text() != "运行中":
                self._snowluma_status_var.setText("运行中")
                self._sl_btn_start.setEnabled(False)
                self._sl_btn_stop.setEnabled(True)
        elif self._snowluma_status_var.text() != "已停止":
            self._snowluma_status_var.setText("已停止")
            self._sl_btn_start.setEnabled(True)
            self._sl_btn_stop.setEnabled(False)

    # ===================== bot 启停 =====================
    def _start_bot(self):
        if self._bot_thread and self._bot_thread.is_alive():
            QMessageBox.warning(self, "提示", "Bot 已在运行中")
            return
        import singleton
        if not singleton.acquire_lock():
            QMessageBox.critical(self, "启动失败", "已有 bot 实例在运行中。请先关闭旧实例再启动。")
            return
        self._bot_stop.clear()
        self._btn_start.setEnabled(False)
        self._btn_stop.setEnabled(True)
        self._status_var.setText("启动中...")
        self._bot_thread = threading.Thread(target=self._run_bot, daemon=True)
        self._bot_thread.start()

    def _apply_connection_settings(self):
        api_key = self._api_key_var.text().strip()
        base_url = self._base_url_var.text().strip()
        model = self._model_var.currentText().strip()
        vision = self._vision_model_var.currentText().strip()
        ws_url = self._ws_url_var.text().strip()
        token = self._ws_token_var.text().strip()
        dash_key = self._dashscope_key_var.text().strip()
        dash_base = self._dashscope_base_url_var.text().strip()
        img_model = self._image_model_var.currentText().strip()
        if api_key: config.DEEPSEEK_API_KEY = api_key
        if base_url: config.DEEPSEEK_BASE_URL = base_url
        if model: config.DEEPSEEK_MODEL = model
        if vision: config.DEEPSEEK_VISION_MODEL = vision
        if ws_url: config.ONEBOT_WS_URL = ws_url
        if token: config.ONEBOT_ACCESS_TOKEN = token
        if dash_key: config.DASHSCOPE_API_KEY = dash_key
        if dash_base: config.DASHSCOPE_BASE_URL = dash_base
        if img_model: config.DASHSCOPE_IMAGE_MODEL = img_model

    def _run_bot(self):
        import asyncio
        from qq_bot import QQGirlfriendBot

        async def runner():
            self._loop = asyncio.get_running_loop()
            self._apply_connection_settings()
            bot = QQGirlfriendBot()
            self._bot_task = asyncio.current_task()
            if self._bot_stop.is_set():
                await bot.stop()
                return
            self._safe_after(0, lambda: self._status_var.setText("运行中"))
            try:
                await bot.start()
            except asyncio.CancelledError:
                pass
            except Exception:
                logging.getLogger("gui_qt").exception("Bot 运行异常")
            finally:
                try:
                    await bot.stop()
                except Exception:
                    logging.getLogger("gui_qt").exception("Bot 停止异常")

        try:
            asyncio.run(runner())
        finally:
            import singleton
            singleton.release_lock()
            self._safe_after(0, lambda: self._status_var.setText("已停止"))
            self._safe_after(0, lambda: self._btn_start.setEnabled(True))
            self._safe_after(0, lambda: self._btn_stop.setEnabled(False))

    def _stop_bot(self):
        if not self._bot_thread or not self._bot_thread.is_alive():
            return
        self._status_var.setText("停止中...")
        self._bot_stop.set()
        loop = getattr(self, "_loop", None)
        task = getattr(self, "_bot_task", None)
        if loop and task:
            loop.call_soon_threadsafe(task.cancel)

    def _stop_bot_and_wait(self, timeout: float = 10.0) -> bool:
        """停止 bot 并等待线程真正退出（收尾 bot.stop/release_lock）。

        _stop_bot 只发取消信号：daemon 线程被 QApplication.quit 强杀时，
        _run_bot 的 finally（bot.stop / singleton.release_lock）来不及执行，
        可能留下 bot.lock 残留、对话上下文未全量落盘。这里 join 等待，
        超时仍放行（不阻塞 UI 退出），超时后残留锁会被下次启动清理。
        返回是否在超时内退出。
        """
        thread = self._bot_thread
        if not thread or not thread.is_alive():
            return True
        self._stop_bot()
        thread.join(timeout=timeout)
        return not thread.is_alive()

    def _restart_bot(self):
        thread = self._bot_thread
        if not (thread and thread.is_alive()):
            self._start_bot()
            return
        logging.getLogger("gui_qt").info("bot 正在重启...")
        self._stop_bot()
        QTimer.singleShot(300, lambda: self._wait_bot_exit(thread))

    def _wait_bot_exit(self, thread):
        if thread and thread.is_alive():
            QTimer.singleShot(300, lambda: self._wait_bot_exit(thread))
        else:
            self._start_bot()

    # ===================== API 测试 / 模型刷新 =====================
    def _test_api_key(self, service):
        if service == "deepseek":
            key, base = self._api_key_var.text().strip(), self._base_url_var.text().strip()
        elif service == "dashscope":
            key, base = self._dashscope_key_var.text().strip(), self._dashscope_base_url_var.text().strip()
        elif service == "mimo":
            key, base = self._mimo_key_var.text().strip(), self._mimo_base_url_var.text().strip()
        else:
            return
        if not key:
            QMessageBox.warning(self, "测试", "请先填写 API Key")
            return
        QMessageBox.information(self, "测试", "正在测试，请稍候（结果请看日志）...")
        threading.Thread(target=self._test_key_worker, args=(service, key, base), daemon=True).start()

    def _test_key_worker(self, service, key, base):
        import asyncio
        result, err = None, None
        try:
            async def _run():
                import httpx
                url = (base or {"deepseek": "https://api.deepseek.com",
                                "dashscope": "https://dashscope.aliyuncs.com",
                                "mimo": "https://api.xiaomimimo.com/v1"}[service]).rstrip("/") + "/models"
                async with httpx.AsyncClient(timeout=15, follow_redirects=True) as c:
                    r = await c.get(url, headers={"Authorization": f"Bearer {key}"})
                    return r.status_code, r.text[:200]
            result = asyncio.run(_run())
        except Exception as e:
            err = str(e)
        if err:
            self._safe_after(0, lambda: QMessageBox.critical(self, "测试", f"连接失败：\n{err}"))
        else:
            code, body = result
            if code == 200:
                self._safe_after(0, lambda: QMessageBox.information(self, "测试", "连通正常"))
            else:
                self._safe_after(0, lambda: QMessageBox.critical(self, "测试", f"HTTP {code}：{body[:120]}"))

    def _refresh_models(self, silent=False):
        if getattr(self, "_refreshing_models", False):
            return
        self._refreshing_models = True
        api_key = self._api_key_var.text().strip() or config.DEEPSEEK_API_KEY
        base_url = self._base_url_var.text().strip() or config.DEEPSEEK_BASE_URL

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

    def _on_models_loaded(self, models, err, silent):
        self._refreshing_models = False
        if models:
            cur = self._model_var.currentText()
            self._model_var.clear()
            self._model_var.addItems(models)
            if cur in models:
                self._model_var.setCurrentText(cur)
            vcur = self._vision_model_var.currentText()
            self._vision_model_var.clear()
            vision = [m for m in models if "vision" in m.lower()]
            self._vision_model_var.addItems(vision or models)
            if vcur in (vision or models):
                self._vision_model_var.setCurrentText(vcur)
            if not silent:
                QMessageBox.information(self, "模型列表", f"已获取 {len(models)} 个模型")
        elif not silent:
            QMessageBox.critical(self, "获取模型列表失败", err or "未知错误")

    def _refresh_image_models(self, *_a):
        threading.Thread(target=self._image_models_worker, daemon=True).start()

    def _image_models_worker(self):
        import asyncio
        from 图片.image_gen import list_image_models
        api_key = self._dashscope_key_var.text().strip() or config.DASHSCOPE_API_KEY
        models = asyncio.run(list_image_models(api_key))
        self._safe_after(0, lambda: self._fill_combo(self._image_model_var, models, "qwen-image-3.0-pro"))

    def _refresh_tts_models(self, *_a):
        threading.Thread(target=self._tts_models_worker, daemon=True).start()

    def _tts_models_worker(self):
        import asyncio
        from 语音.tts import list_tts_models
        api_key = self._mimo_key_var.text().strip() or runtime.MIMO_API_KEY
        models = asyncio.run(list_tts_models(api_key))
        self._safe_after(0, lambda: self._fill_combo(self._tts_model_var, models, "mimo-v2.5-tts-voicedesign"))

    def _refresh_asr_models(self, *_a):
        threading.Thread(target=self._asr_models_worker, daemon=True).start()

    def _asr_models_worker(self):
        import asyncio
        from 语音.asr import list_asr_models
        api_key = self._mimo_key_var.text().strip() or runtime.MIMO_API_KEY
        models = asyncio.run(list_asr_models(api_key))
        self._safe_after(0, lambda: self._fill_combo(self._asr_model_var, models, "mimo-v2.5-asr"))

    def _fill_combo(self, combo, models, default):
        if not models:
            return
        cur = combo.currentText()
        combo.clear()
        combo.addItems(models)
        if cur in models:
            combo.setCurrentText(cur)
        else:
            combo.setCurrentText(default if default in models else models[0])

    def _test_provider(self, p):
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
                            temperature=0.1, max_tokens=10)
                    finally:
                        await client.aclose()
                result = asyncio.run(_run())
            except Exception as e:
                err = str(e)
            if err or not result:
                self._safe_after(0, lambda: QMessageBox.critical(self, "测试提供商", f"连接失败（{name}）：\n{err or '无返回'}"))
            else:
                self._safe_after(0, lambda: QMessageBox.information(self, "测试提供商", f"连通正常（{name}）\n模型回复：{result[:40]}"))

        threading.Thread(target=worker, daemon=True).start()

    def _fetch_provider_models(self, p):
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
                    client = OpenAICompatClient(base_url=base_url or "", api_key=api_key or "sk-local")
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
        if err or not models:
            QMessageBox.critical(self, "获取模型列表失败", f"{name}：{err or '未返回任何模型'}")
            return
        dlg = QDialog(self)
        dlg.setWindowTitle(f"模型列表 · {name}")
        dlg.resize(420, 480)
        lay = QVBoxLayout(dlg)
        lay.addWidget(_label(f"共 {len(models)} 个模型，点击设为该提供商默认：", "Muted"))
        from PySide6.QtWidgets import QListWidget
        lst = QListWidget()
        lst.addItems(models)
        lay.addWidget(lst)
        btns = QHBoxLayout()
        btns.addStretch(1)
        btns.addWidget(_btn("取消", "Soft", dlg.reject))
        lay.addLayout(btns)

        def _pick(item):
            try:
                from llm_providers import update_provider
                update_provider(pid, model=item.text())
                self._refresh_models_tab()
                dlg.accept()
                QMessageBox.information(self, "已设置", f"已把「{item.text()}」设为该提供商的默认模型。")
            except Exception as e:
                QMessageBox.critical(self, "设置失败", str(e))
        lst.itemClicked.connect(_pick)
        dlg.exec()

    def _open_provider_dialog(self, provider=None):
        from llm_providers import TEMPLATES
        dlg = QDialog(self)
        dlg.setWindowTitle("编辑提供商" if provider else "添加提供商")
        dlg.resize(460, 400)
        form = QFormLayout(dlg)
        form.setContentsMargins(20, 20, 20, 20)
        form.setSpacing(10)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft)
        is_edit = provider is not None
        name_e = QLineEdit((provider or {}).get("name") or "")
        url_e = QLineEdit((provider or {}).get("base_url") or "")
        key_e = QLineEdit((provider or {}).get("api_key") or "")
        key_e.setEchoMode(QLineEdit.EchoMode.Password)
        model_e = QLineEdit((provider or {}).get("model") or "")
        err_l = _label("", "Muted")
        err_l.setStyleSheet(f"color: {DANGER};")
        tpl = None
        if not is_edit:
            tpl_cb = QComboBox()
            tpl_cb.addItems([v["label"] for v in TEMPLATES.values()])
            form.addRow(_label("模板："), tpl_cb)
            def _tpl(i):
                v = list(TEMPLATES.values())[i]
                name_e.setText(v["name"])
                url_e.setText(v["base_url"])
                model_e.setText(v["model"])
            tpl_cb.currentIndexChanged.connect(_tpl)
            tpl_cb.setCurrentIndex(0)
        form.addRow(_label("名称："), name_e)
        form.addRow(_label("API 地址："), url_e)
        form.addRow(_label("API Key："), key_e)
        form.addRow(_label("模型名："), model_e)
        form.addRow(err_l)

        btns = QHBoxLayout()
        btns.addStretch(1)
        btns.addWidget(_btn("保存", "Primary"))

        def _save():
            name = name_e.text().strip()
            url = url_e.text().strip()
            key = key_e.text().strip()
            model = model_e.text().strip()
            if not name:
                err_l.setText("请填名称"); return
            # API 地址允许留空（DeepSeek 官方留空 = 默认官方地址）
            if not model:
                err_l.setText("请填模型名"); return
            if is_edit:
                ptype = provider.get("type", "openai")
            else:
                ptype = list(TEMPLATES.values())[tpl_cb.currentIndex()].get("type", "openai")
            from llm_providers import add_provider, update_provider
            if is_edit:
                update_provider(provider["id"], name=name, base_url=url, api_key=key, model=model)
            else:
                add_provider(name=name, ptype=ptype, base_url=url, api_key=key, model=model)
            dlg.accept()
            self._refresh_models_tab()
            QMessageBox.information(self, "已保存", "提供商已保存。切到「设为当前」并重启 Bot 后生效。")

        save_btn = btns.itemAt(btns.count() - 1).widget()
        save_btn.clicked.connect(_save)
        form.addRow(btns)
        dlg.exec()

    # ===================== 音色助手 =====================
    def _generate_voice_design(self):
        raw = self._voice_input_text.toPlainText().strip()
        if not raw:
            QMessageBox.warning(self, "提示", "请先输入你的音色想法")
            return
        self._voice_gen_btn.setEnabled(False)
        self._voice_gen_btn.setText("正在生成...")
        self._voice_output_text.setPlainText("生成中，请稍候...")
        threading.Thread(target=self._voice_design_worker, args=(raw,), daemon=True).start()

    def _voice_design_worker(self, raw):
        import asyncio
        from llm_factory import get_llm_client

        async def _run():
            client = get_llm_client("task")
            try:
                system = (
                    "你是音色设计（voice design prompt）专家。用户会给你一个粗略的音色想法，"
                    "请优化成 1~4 句可直接用于 mimo-v2.5-tts-voicedesign 的音色描述："
                    "包含性别年龄、音色质感、情绪语气、语速节奏；避免混响/回声等后期词，"
                    "避免普通/正常/外国等模糊词。直接输出优化结果，不要解释。"
                )
                return await client.chat(
                    [{"role": "system", "content": system},
                     {"role": "user", "content": raw}],
                    max_tokens=300, disable_thinking=True)
            finally:
                await client.aclose()

        result, err = None, None
        try:
            result = asyncio.run(_run())
        except Exception as e:
            err = str(e)
        self._safe_after(0, lambda: self._on_voice_design_done(result, err))

    def _on_voice_design_done(self, result, err=""):
        self._voice_gen_btn.setEnabled(True)
        self._voice_gen_btn.setText("生成优化音色描述")
        if err or not result:
            self._voice_output_text.setPlainText("生成失败：" + (err or "无返回"))
        else:
            self._voice_output_text.setPlainText(result.strip())

    def _copy_voice_design(self):
        txt = self._voice_output_text.toPlainText().strip()
        if not txt:
            return
        QApplication.clipboard().setText(txt)
        QMessageBox.information(self, "已复制", "音色描述已复制到剪贴板。")

    # ===================== 外貌总结 =====================
    def _generate_appearance_summary(self):
        self._appearance_btn.setEnabled(False)
        self._appearance_btn.setText("正在总结图片...")
        threading.Thread(target=self._appearance_summary_worker, daemon=True).start()

    def _appearance_summary_worker(self):
        import asyncio
        from appearance_ref import summarize_appearance
        from deepseek_client import DeepSeekClient

        async def _run():
            client = DeepSeekClient()
            try:
                return await summarize_appearance(client)
            finally:
                await client.aclose()

        summary, err = None, None
        try:
            summary = asyncio.run(_run())
        except Exception as e:
            err = str(e)
        self._safe_after(0, lambda: self._on_appearance_summary_done(summary, err))

    def _on_appearance_summary_done(self, summary, err=""):
        self._appearance_btn.setEnabled(True)
        self._appearance_btn.setText("根据参考图重新生成")
        if err:
            QMessageBox.critical(self, "外貌总结失败", str(err))
        elif not summary:
            QMessageBox.warning(self, "外貌总结", "没有成功生成总结（网络失败或文件夹无有效图片）。请检查网络后重试。")
        else:
            self._appearance_summary_text.setPlainText(summary)
            QMessageBox.information(self, "外貌总结", "已生成并保存，可在总结框中继续修改。")

    def _save_appearance_summary(self):
        """保存可编辑的参考图总结；空文本等同于清空该生图锚点。"""
        try:
            from appearance_ref import save_summary
            save_summary(self._appearance_summary_text.toPlainText())
            QMessageBox.information(self, "外貌总结", "外貌总结已保存，后续人物生图会使用这份内容。")
        except Exception as e:
            QMessageBox.critical(self, "保存失败", f"无法保存外貌总结：{e}")

    def _copy_appearance_summary_to_profile(self):
        """把参考图总结复制进手动外貌设定，方便把两类描述合并管理。"""
        summary = self._appearance_summary_text.toPlainText().strip()
        if not summary:
            QMessageBox.information(self, "外貌总结", "总结框为空，暂无内容可复制。")
            return
        target = self._text_widgets.get("GIRLFRIEND_APPEARANCE")
        if target is None:
            return
        current = target.toPlainText().strip()
        if summary == current or summary in current:
            QMessageBox.information(self, "外貌设定", "这份总结已经在外貌设定中了。")
            return
        target.setPlainText(f"{current}\n\n{summary}".strip())
        self._on_setting_changed()
        QMessageBox.information(self, "外貌设定", "已复制到外貌设定，点击顶部“保存”即可持久化。")

    # ===================== 托盘 / 关闭 =====================
    def _create_tray(self):
        try:
            self._tray = QSystemTrayIcon(self)
            ico = self._app_icon_path()
            if ico and os.path.isfile(ico):
                self._tray.setIcon(QIcon(ico))
            self._tray.setToolTip("控制面板")
            menu = QMenu()
            menu.addAction("显示/隐藏", self._toggle_visible)
            menu.addAction("退出", self._really_quit)
            self._tray.setContextMenu(menu)
            self._tray.activated.connect(self._on_tray_activated)
            self._tray.show()
        except Exception as e:
            logger.warning("托盘创建失败: %s", e)

    def _on_tray_activated(self, reason):
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            self._toggle_visible()

    def _toggle_visible(self):
        if self.isVisible():
            self.hide()
        else:
            self.show()
            self.raise_()
            self.activateWindow()

    def _app_icon_path(self):
        p = code_path("晚晚", "界面", "icon.ico")
        return p if os.path.isfile(p) else (code_path("晚晚", "界面", "icon.png")
                                            if os.path.isfile(code_path("晚晚", "界面", "icon.png")) else "")

    def closeEvent(self, e):
        """点 ✕ 时确认退出；选择「否」则最小化到系统托盘。"""
        ret = QMessageBox.question(
            self, "退出", "确定要退出吗？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No)
        if ret == QMessageBox.StandardButton.Yes:
            self._really_quit()
            e.accept()
        else:
            # 最小化到托盘（不退出，后台照常运行）
            e.ignore()
            self.hide()
            if getattr(self, "_tray", None) and self._tray.isVisible():
                try:
                    self._tray.showMessage(
                        "控制面板", "已最小化到托盘，点击图标可恢复。",
                        QSystemTrayIcon.MessageIcon.Information, 2000)
                except Exception:
                    pass

    def _really_quit(self):
        """真正退出：停掉后台进程与托盘，结束程序。"""
        try:
            # 先等 bot 线程收尾（bot.stop / release_lock 落盘对话上下文、释放单实例锁），
            # 避免 daemon 线程被强杀导致 bot.lock 残留、上下文未保存
            if not self._stop_bot_and_wait(timeout=8.0):
                logging.getLogger("gui_qt").warning("退出超时：bot 线程仍在运行，直接退出（残留锁下次启动会自动清理）")
        except Exception:
            pass
        try:
            self._stop_snowluma()
        except Exception:
            pass
        try:
            if getattr(self, "_tray", None):
                self._tray.hide()
        except Exception:
            pass
        QApplication.quit()


# =============================================================================
# 模块级工具（QQ 检测 / 日志 handler）
# =============================================================================
class LogQueueHandler(logging.Handler):
    def __init__(self, q):
        super().__init__()
        self.q = q

    def emit(self, record):
        try:
            self.q.put(self.format(record))
        except Exception:
            pass


def _split_hours(v) -> tuple:
    v = float(v or 0)
    h = int(v)
    m = int(round((v - h) * 60))
    if m == 60:
        h += 1
        m = 0
    return h, m


def _get_file_version(path):
    try:
        import ctypes.wintypes as wt
        size = ctypes.windll.version.GetFileVersionInfoSizeW(path, None)
        if not size:
            return ""
        data = ctypes.create_string_buffer(size)
        ctypes.windll.version.GetFileVersionInfoW(path, 0, size, data)
        ver_ptr = ctypes.c_void_p()
        ver_len = ctypes.c_uint()
        ctypes.windll.version.VerQueryValueW(
            data, "\\", ctypes.byref(ver_ptr), ctypes.byref(ver_len))
        import struct
        buf = ctypes.string_at(ver_ptr, ver_len.value)
        if len(buf) < 52:
            return ""
        major, minor, build, rev = struct.unpack_from("<4H", buf, 48)
        return f"{major}.{minor}.{build}.{rev}"
    except Exception:
        return ""


def _registry_qq_paths():
    """从注册表收集 QQ 安装路径候选。"""
    try:
        import winreg
    except ImportError:
        return []
    candidates = []

    def add(raw):
        if not raw:
            return
        raw = str(raw).strip().strip('"')
        # 卸载信息里 DisplayIcon 形如 "D:\apps\qq\QQ.exe,0"，剥离 ,N 后缀
        if raw.lower().endswith(".exe") and "," in raw:
            raw = raw.rsplit(",", 1)[0].strip()
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
                add(winreg.QueryValue(k, None))
            except OSError:
                pass
        finally:
            winreg.CloseKey(k)

    # 卸载信息扫描（InstallLocation / DisplayIcon / UninstallString 可定位 QQ.exe）
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
    return list(dict.fromkeys(candidates))


def detect_qq():
    """检测本机是否安装 QQ，返回 (是否安装, 路径, 版本号)。"""
    seen = set()
    for path in _registry_qq_paths():
        key = str(path).lower()
        if key in seen:
            continue
        seen.add(key)
        if os.path.exists(path):
            return True, path, _get_file_version(path)
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
            p = base / pat
            if os.path.exists(p):
                return True, str(p), _get_file_version(str(p))
    return False, "", ""


# =============================================================================
# 入口
# =============================================================================
SNOWLUMA_DIR = data_path("snowluma")


def _ensure_runtime_data():
    """打包（frozen）模式：把可写数据准备到 exe 旁。

    - PyInstaller 解压目录 _MEIPASS 只读，.env / snowluma（运行时要写日志和数据）等
      必须放到 exe 所在目录；首次启动从打包资源复制，之后直接用副本。
    - 未打包时不做任何事（路径本来就指向项目目录）。
    """
    if not is_frozen():
        return
    import shutil
    # 1) 可写目录
    for d in (
        data_path("晚晚", "数据"), data_path("晚晚", "配置"),
        data_path("晚晚", "图片", "生成图片"), data_path("晚晚", "图片", "表情包"),
        data_path("晚晚", "语音", "语音缓存"), data_path("晚晚", "用量"),
        data_path("晚晚", "外貌", "外貌设定"), data_path("晚晚", "日志"),
    ):
        try:
            os.makedirs(d, exist_ok=True)
        except OSError:
            pass
    # 2) 配置文件（.env 含 Key 等），首次复制
    for f in (".env", ".runtime_config.json", "llm_providers.json"):
        src = code_path("晚晚", "配置", f)
        dst = data_path("晚晚", "配置", f)
        if os.path.isfile(src) and not os.path.isfile(dst):
            try:
                shutil.copy2(src, dst)
            except OSError:
                pass
    # 3) snowluma 目录（运行时要写 config/logs/data），首次整体复制
    if not os.path.isdir(SNOWLUMA_DIR):
        src = code_path("snowluma")
        if os.path.isdir(src):
            try:
                shutil.copytree(src, SNOWLUMA_DIR)
            except OSError as e:
                logging.getLogger("gui_qt").warning("snowluma 复制失败: %s", e)
    logging.getLogger("gui_qt").info("数据目录已就绪: %s", DATA_ROOT)


def main():
    _ensure_runtime_data()
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    win = MainWindow()
    win._create_tray()
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
