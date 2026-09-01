# -*- coding: utf-8 -*-
"""Windows 系统托盘（纯 ctypes 实现，无第三方依赖）。

用法：
    import tray
    t = tray.TrayIcon(tooltip="控制面板",
                      on_left_click=show_fn,      # 左键单击（主线程回调）
                      menu_items=[("显示/隐藏", 1), ("退出", 2)],
                      on_menu=menu_fn)             # 右键菜单回调(command_id)
    ...
    t.remove()   # 程序退出前移除托盘图标
"""
import ctypes
import ctypes.wintypes as wt
import threading

# ---- Win32 常量 ----
WM_APP = 0x8000
WM_TRAYICON = WM_APP + 1
WM_DESTROY = 0x0002
WM_LBUTTONUP = 0x0202
WM_RBUTTONUP = 0x0205
WM_QUIT = 0x0012

NIM_ADD = 0x00000000
NIM_MODIFY = 0x00000001
NIM_DELETE = 0x00000002
NIF_MESSAGE = 0x00000001
NIF_ICON = 0x00000002
NIF_TIP = 0x00000004

MF_STRING = 0x00000000
MF_SEPARATOR = 0x00000800
TPM_RETURNCMD = 0x0100
TPM_NONOTIFY = 0x0080
TPM_LEFTALIGN = 0x0000
TPM_BOTTOMALIGN = 0x0020

IDI_APPLICATION = 32512
IMAGE_ICON = 1
LR_LOADFROMFILE = 0x00000010

user32 = ctypes.windll.user32
shell32 = ctypes.windll.shell32
kernel32 = ctypes.windll.kernel32

# ---- 类型 / 原型定义 ----
HWND = wt.HWND
UINT = wt.UINT
WPARAM = ctypes.c_size_t
LPARAM = ctypes.c_ssize_t
LRESULT = ctypes.c_ssize_t


class POINT(ctypes.Structure):
    _fields_ = [("x", wt.LONG), ("y", wt.LONG)]


class MSG(ctypes.Structure):
    _fields_ = [
        ("hwnd", HWND), ("message", UINT), ("wParam", WPARAM),
        ("lParam", LPARAM), ("time", wt.DWORD), ("pt", POINT),
    ]


class WNDCLASSW(ctypes.Structure):
    _fields_ = [
        ("style", UINT), ("lpfnWndProc", ctypes.c_void_p), ("cbClsExtra", ctypes.c_int),
        ("cbWndExtra", ctypes.c_int), ("hInstance", wt.HINSTANCE), ("hIcon", wt.HICON),
        ("hCursor", wt.HANDLE), ("hbrBackground", wt.HBRUSH),
        ("lpszMenuName", wt.LPCWSTR), ("lpszClassName", wt.LPCWSTR),
    ]


class NOTIFYICONDATAW(ctypes.Structure):
    _fields_ = [
        ("cbSize", wt.DWORD), ("hWnd", HWND), ("uID", UINT),
        ("uFlags", UINT), ("uCallbackMessage", UINT), ("hIcon", wt.HICON),
        ("szTip", ctypes.c_wchar * 128), ("dwState", wt.DWORD), ("dwStateMask", wt.DWORD),
        ("szInfo", ctypes.c_wchar * 256), ("uVersion", UINT),
        ("szInfoTitle", ctypes.c_wchar * 64), ("dwInfoFlags", wt.DWORD),
        ("guidItem", ctypes.c_byte * 16), ("hBalloonIcon", wt.HICON),
    ]


WNDPROC = ctypes.WINFUNCTYPE(LRESULT, HWND, UINT, WPARAM, LPARAM)

# 设置关键函数原型，避免 64 位指针溢出
user32.DefWindowProcW.restype = LRESULT
user32.DefWindowProcW.argtypes = [HWND, UINT, WPARAM, LPARAM]
user32.RegisterClassW.argtypes = [ctypes.POINTER(WNDCLASSW)]
user32.CreateWindowExW.restype = HWND
user32.CreateWindowExW.argtypes = [wt.DWORD, wt.LPCWSTR, wt.LPCWSTR, wt.DWORD,
                                   ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                   HWND, wt.HMENU, wt.HINSTANCE, ctypes.c_void_p]
user32.GetMessageW.argtypes = [ctypes.POINTER(MSG), HWND, UINT, UINT]
user32.GetMessageW.restype = ctypes.c_int
user32.PostThreadMessageW.argtypes = [wt.DWORD, UINT, WPARAM, LPARAM]
shell32.Shell_NotifyIconW.argtypes = [wt.DWORD, ctypes.POINTER(NOTIFYICONDATAW)]
user32.LoadImageW.restype = wt.HANDLE
user32.LoadImageW.argtypes = [wt.HINSTANCE, wt.LPCWSTR, UINT, ctypes.c_int, ctypes.c_int, UINT]
user32.TrackPopupMenu.argtypes = [wt.HMENU, UINT, ctypes.c_int, ctypes.c_int,
                                  ctypes.c_int, HWND, ctypes.c_void_p]
user32.GetCursorPos.argtypes = [ctypes.POINTER(POINT)]


class TrayIcon:
    """在独立线程运行消息循环，通过回调安全地操作主界面。"""

    def __init__(self, tooltip="控制面板", on_left_click=None,
                 menu_items=None, on_menu=None, icon_id=IDI_APPLICATION, icon_path=None):
        self.tooltip = tooltip
        self.icon_path = icon_path
        self.on_left_click = on_left_click
        self.menu_items = menu_items or [("显示/隐藏", 1), ("退出", 2)]
        self.on_menu = on_menu
        self.icon_id = icon_id
        self._proc = None
        self._hwnd = None
        self._nid = None
        self._thread_id = None
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    # ---------- 内部 ----------

    def _run(self):
        def wnd_proc(hwnd, msg, wparam, lparam):
            if msg == WM_TRAYICON:
                code = lparam & 0xFFFF
                if code == WM_LBUTTONUP and self.on_left_click:
                    self.on_left_click()
                elif code == WM_RBUTTONUP:
                    self._show_menu(hwnd)
                return 0
            if msg == WM_DESTROY:
                return 0
            return user32.DefWindowProcW(hwnd, msg, wparam, lparam)

        self._proc = WNDPROC(wnd_proc)  # 保持引用，防止被 GC

        hinst = kernel32.GetModuleHandleW(None)
        class_name = "SnowLumaTrayWindow"
        wc = WNDCLASSW()
        wc.lpfnWndProc = ctypes.cast(self._proc, ctypes.c_void_p)
        wc.hInstance = hinst
        wc.lpszClassName = class_name
        user32.RegisterClassW(ctypes.byref(wc))

        hwnd = user32.CreateWindowExW(
            0, class_name, "Tray", 0, 0, 0, 0, 0,
            None, None, hinst, None)
        if not hwnd:
            return
        self._hwnd = hwnd
        self._thread_id = kernel32.GetCurrentThreadId()

        hicon = None
        if self.icon_path:
            hicon = user32.LoadImageW(None, self.icon_path, IMAGE_ICON, 32, 32, LR_LOADFROMFILE)
        if not hicon:
            hicon = user32.LoadIconW(None, ctypes.c_void_p(self.icon_id))

        nid = NOTIFYICONDATAW()
        nid.cbSize = ctypes.sizeof(NOTIFYICONDATAW)
        nid.hWnd = hwnd
        nid.uID = 1
        nid.uFlags = NIF_MESSAGE | NIF_ICON | NIF_TIP
        nid.uCallbackMessage = WM_TRAYICON
        nid.hIcon = hicon
        nid.szTip = (self.tooltip or "")[:127]
        shell32.Shell_NotifyIconW(NIM_ADD, ctypes.byref(nid))
        self._nid = nid

        # 消息循环
        msg = MSG()
        while not self._stop.is_set():
            r = user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
            if r <= 0:
                break
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))

        # 清理
        if self._nid is not None:
            shell32.Shell_NotifyIconW(NIM_DELETE, ctypes.byref(self._nid))
            self._nid = None
        user32.DestroyWindow(hwnd)
        self._hwnd = None

    def _show_menu(self, hwnd):
        menu = user32.CreatePopupMenu()
        for text, cmd in self.menu_items:
            if text == "-":
                user32.AppendMenuW(menu, MF_SEPARATOR, 0, None)
            else:
                user32.AppendMenuW(menu, MF_STRING, cmd, text)
        pt = POINT()
        user32.GetCursorPos(ctypes.byref(pt))
        chosen = user32.TrackPopupMenu(
            menu,
            TPM_RETURNCMD | TPM_NONOTIFY | TPM_LEFTALIGN | TPM_BOTTOMALIGN,
            pt.x, pt.y, 0, hwnd, None)
        user32.DestroyMenu(menu)
        if chosen and self.on_menu:
            self.on_menu(chosen)

    # ---------- 对外 ----------

    def remove(self):
        """移除托盘图标并结束托盘线程。"""
        self._stop.set()
        if self._thread_id:
            user32.PostThreadMessageW(self._thread_id, WM_QUIT, 0, 0)
        # 等消息循环退出（最多 2s），确保图标已删除、线程不泄漏
        if self._thread.is_alive():
            self._thread.join(timeout=2)
