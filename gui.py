# -*- coding: utf-8 -*-
"""控制面板入口（根目录启动器）。

实际界面代码：界面/gui_qt.py（PySide6 版，天空蓝主题）。
打包（exe）模式下先准备 exe 旁的可写数据，再加载业务模块（config 等读 .env 依赖它）。
"""
import 路径
路径.ensure_runtime_data()  # 打包模式：把 .env / snowluma 等复制到 exe 旁（未打包时无操作）
import gui_qt as _gui

MainWindow = _gui.MainWindow
main = _gui.main

if __name__ == "__main__":
    main()
