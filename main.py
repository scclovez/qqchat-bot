# -*- coding: utf-8 -*-
"""AI 电子女友 — 主入口。

用法:
  python main.py          命令行模式（纯日志输出）
  python main.py --gui     GUI 管理面板模式
  python gui.py            GUI 管理面板模式（直接启动）
"""
import sys
import 路径
路径.ensure_runtime_data()  # 打包模式：先准备 exe 旁可写数据，再加载业务模块


def main_cli():
    import singleton
    if not singleton.acquire_lock():
        print("错误：已有 bot 实例在运行中。请先关闭旧实例再启动。")
        print(f"锁文件位置: {singleton.LOCK_FILE}")
        sys.exit(1)
    import atexit
    atexit.register(singleton.release_lock)
    import asyncio
    import logging
    import signal
    from config import config
    from qq_bot import QQGirlfriendBot

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    logger = logging.getLogger("main")

    missing = config.validate()
    if missing:
        logger.error("缺少必要配置: %s", ", ".join(missing))
        logger.error("请将 配置/.env.example 复制为 配置/.env 并填入正确的值")
        sys.exit(1)

    logger.info("=" * 40)
    logger.info("   AI 电子女友 — 小晚 启动中...")
    logger.info("=" * 40)
    logger.info("DeepSeek 模型: %s", config.DEEPSEEK_MODEL)
    logger.info("OneBot 地址: %s", config.ONEBOT_WS_URL)

    bot = QQGirlfriendBot()

    async def run():
        loop = asyncio.get_running_loop()
        def shutdown():
            logger.info("收到退出信号，正在关闭...")
            asyncio.create_task(bot.stop())
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, shutdown)
            except NotImplementedError:
                signal.signal(sig, lambda s, f: shutdown())
        try:
            await bot.start()
        except KeyboardInterrupt:
            pass
        finally:
            await bot.stop()
            logger.info("小晚已下线，晚安~")

    asyncio.run(run())


if __name__ == "__main__":
    if "--gui" in sys.argv:
        from gui_qt import main as gui_main
        gui_main()
    else:
        main_cli()
