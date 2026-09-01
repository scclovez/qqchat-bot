"""
单实例锁 —— 通过 PID 文件防止重复启动。

用法：
    import singleton
    if not singleton.acquire_lock():        # 使用默认 bot.lock
        ...
    singleton.release_lock()

    if not singleton.acquire_lock("gui.lock"):   # 自定义锁文件
        ...
    singleton.release_lock("gui.lock")
"""
import os
import ctypes
import ctypes.wintypes

from 路径 import PROJECT_ROOT, DATA_ROOT
BASE_DIR = DATA_ROOT
LOCK_FILE = os.path.join(BASE_DIR, "bot.lock")


def _resolve(lock_file):
    """解析锁文件路径：传入相对文件名时放到项目根目录。"""
    if lock_file is None:
        return LOCK_FILE
    if os.path.isabs(lock_file):
        return lock_file
    return os.path.join(BASE_DIR, lock_file)


def _pid_is_running(pid: int) -> bool:
    """检查指定 PID 的进程是否仍在运行。"""
    kernel32 = ctypes.windll.kernel32
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return False
    exit_code = ctypes.wintypes.DWORD()
    kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code))
    kernel32.CloseHandle(handle)
    return exit_code.value == 259  # STILL_ACTIVE


def acquire_lock(lock_file=None) -> bool:
    """尝试获取单实例锁。成功返回 True，已有实例运行返回 False。

    用 O_CREAT|O_EXCL 原子创建锁文件，避免 check-then-write 竞态
    （两个进程同时启动时不会双双拿到锁）。
    """
    path = _resolve(lock_file)
    for _ in range(2):  # 第一轮可能清掉已死进程的残留锁，第二轮重试
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            try:
                with open(path, "r") as f:
                    old_pid = int(f.read().strip())
                if _pid_is_running(old_pid):
                    return False
            except (ValueError, OSError):
                pass
            # 持有者已退出或锁文件损坏：清掉残留再试一轮
            try:
                os.remove(path)
            except OSError:
                return False
            continue
        except OSError:
            return False
        try:
            os.write(fd, str(os.getpid()).encode("ascii"))
        finally:
            os.close(fd)
        return True
    return False


def release_lock(lock_file=None):
    """释放单实例锁。

    只删除自己持有的锁（校验 PID），防止其他进程已接管该锁时被误删导致双开。
    """
    path = _resolve(lock_file)
    try:
        if os.path.exists(path):
            with open(path, "r") as f:
                pid = int(f.read().strip())
            if pid != os.getpid():
                return
            os.remove(path)
    except (ValueError, OSError):
        pass
