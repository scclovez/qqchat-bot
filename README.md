# AI 电子女友 · 开发版说明

基于 DeepSeek 大模型的 QQ 聊天机器人「bot」。本文件夹为**纯净开发版**（无个人数据），
用于代码开发与测试；正式使用请用带个人数据的版本。

> **开源协议**：本项目自身代码（`晚晚/` 及入口文件）采用 [MIT](./LICENSE) 许可。
> 随附的 SnowLuma（QQ 协议运行时）**不属本项目代码**，按其自身的**源码可见·非商业许可**授权，
> 详见 [第三方组件许可说明](./THIRD_PARTY_NOTICES.md)。

---

## 📁 目录结构

```
开发/
├── main.py                  # 入口：python main.py（命令行）/ --gui（控制面板）
├── gui.py                   # 控制面板入口（指向 PySide6 版 gui_qt）
├── 路径.py                  # 路径引导：把中文功能目录加入 sys.path；支持打包路径
├── 1.vbs                    # 双击启动控制面板（自动定位真实 pythonw）
├── requirements.txt         # 依赖清单
├── build.spec               # PyInstaller 打包配置（onefile + 内置 Node）
├── build_exe.bat            # 一键打包脚本
├── 测试/
│   └── test_all.py          # 综合测试：模块导入 / 配置 / 成长 / 提供商 / GUI 冒烟
├── 晚晚/                    # Python 业务代码（按功能分中文目录）
│   ├── 机器人/qq_bot.py     #   核心：QQ 消息主循环（3279 行）
│   ├── 人设/                #   人设 / 活人感引擎 / 情绪边界
│   ├── 对话/                #   会话上下文 / 长期记忆（SQLite）
│   ├── 成长/                #   日记 / 性格演化 / 亲密度阶段 / 称号
│   ├── 客户端/              #   DeepSeek / OpenAI 兼容 LLM 客户端
│   ├── 配置/                #   config.py + llm_providers.py（.env.example 模板）
│   ├── 图片/ 语音/ 空间/    #   生图 / TTS / ASR / QQ 空间
│   ├── 互动/ 实时/ 用量/    #   自主互动工具 / 天气搜索 / Token 统计
│   ├── 界面/                #   gui_qt.py（PySide6 控制面板）+ tray.py
│   └── 外貌/ 工具/          #   外貌参考图总结 / 单实例锁
└── snowluma/                # QQ 协议运行时（Node.js，OneBot v11）
    ├── index.mjs            #   主程序（node index.mjs）
    ├── native/              #   原生扩展（ffmpeg 转码等）
    └── client/              #   SnowLuma 自带 WebUI 资源
```

---

## 🚀 环境要求与安装

- Windows 10/11
- Python 3.10+（开发推荐 3.14）
- Node.js 22.13+（或 23.4+，SnowLuma 需要）
- 本机已登录 QQ

```bash
pip install -r requirements.txt
```

开发如需打包 exe，再装：

```bash
pip install pyinstaller
```

---

## ▶️ 启动方式

```bash
# 控制面板（图形界面）
python main.py --gui
# 或
python gui.py
# 或双击 1.vbs

# 命令行模式（纯日志，不弹界面）
python main.py
```

启动流程：控制面板 → 首页 → 「启动 SNOWLUMA」→「启动 bot」。

---

## ⚙️ 配置说明

开发版不带任何个人配置。首次运行前，从模板复制：

```bash
copy 晚晚\配置\.env.example 晚晚\配置\.env
```

然后编辑 `.env` 填入：

| 变量 | 说明 |
|---|---|
| `DEEPSEEK_API_KEY` | DeepSeek 官方 Key（必填） |
| `ONEBOT_WS_URL` | OneBot WebSocket 地址（默认 ws://localhost:3001，与 SnowLuma 一致） |
| `ONEBOT_ACCESS_TOKEN` | OneBot Access Token（与 SnowLuma config 里一致） |
| `DASHSCOPE_API_KEY` | 阿里云百炼 Key（图片生成，可选） |
| `MIMO_API_KEY` | 小米语音 Key（TTS/ASR，可选） |

提供商 / 模块分派在 GUI「模型与连接」页配置（`llm_providers.json`，首次运行自动生成）。
人设 / 参数在 GUI 保存后写入 `晚晚/配置/.runtime_config.json`。

> 数据文件（`晚晚/数据/bot_memory.db` 记忆、`用量/usage_stats.json`、图片/语音缓存等）
> 均为运行时自动创建，开发版保持干净。

---

## 🧪 测试

综合测试（一键跑 5 组）：

```bash
python 测试/test_all.py
```

覆盖：
1. **模块导入** —— 28 个业务模块全部可导入
2. **配置加载** —— config / runtime 正常（无 .env 时用默认值，不报错）
3. **成长系统** —— 性格阶段 / 淫乱度档位 / 称号 / 注入文本计算正常
4. **提供商配置** —— llm_providers.json 读写正常
5. **GUI 冒烟** —— PySide6 构建 5 个顶级页 + 成长网格 12 格（窗口 1.5 秒自动关闭）

输出：`结果: 5 通过 / 0 失败`，全部通过返回 0。

---

## 📦 打包为 exe（详细说明）

### 一键打包

双击 `build_exe.bat`，或在项目根执行：

```bash
python -m PyInstaller build.spec --noconfirm --clean
```

产物：`dist/bot控制面板.exe`（约 101MB，onefile 单文件）。

### 打包内容

| 内容 | 说明 |
|---|---|
| Python 业务代码 | PyInstaller 自动收集（含全部中文模块，spec 已显式列出） |
| PySide6 GUI | 自动收集 Qt 插件 |
| **node.exe（内置）** | SnowLuma 运行时用它启动，目标机器**无需安装 Node** |
| snowluma/ 程序 | index.mjs / native / client 等（排除 data/logs 运行数据） |
| 配置文件 | `.env` / `llm_providers.json` **存在才打包**（开发版无 .env 则跳过，exe 读 exe 旁的 .env） |

### 运行行为

- exe 放在 `dist/` 子目录下运行时，**自动共用上一级源码项目的个人数据**（.env / 数据库 / snowluma），与源码版数据一致
- exe 单独分发到别的机器时，数据根回退到 exe 所在目录，需自备配置（`晚晚/配置/.env` 等）
- exe 内不包含任何个人数据（开发版打包天然干净）

### 常见问题

| 问题 | 解决 |
|---|---|
| 打包报错 `Unable to find ...` | spec 里资源文件路径/文件名写错，检查 DATAS 列表 |
| 打包被沙盒/杀软拦截 | PyInstaller 需创建命名管道，加白名单或管理员运行 |
| exe 双击无反应 | windowed 模式无控制台——先源码版跑通，exe 报错看 exe 旁日志 |
| node.exe 路径不同 | 修改 `build.spec` 里 `BINARIES` 的路径 |

### 自定义

- **改 exe 名**：`build.spec` 里 `name="bot控制面板"`
- **改图标**：`icon="晚晚/界面/icon.ico"`
- **onedir（目录）模式**：把 `EXE()` 的 `a.binaries/a.datas` 移到 `COLLECT()`（onedir 启动更快、更好调试）

---

## 📌 注意事项

- 本开发版不包含任何个人数据（Key / 数据库 / 图片 / 语音缓存 / SnowLuma 配置均已清除）
- 涉及敏感信息时，用 `.env.example` 模板 + 本地 `.env`（不提交）
- QQ 空间 / 语音 / 图片等依赖外部 API 的功能，未配置对应 Key 时会优雅降级（日志提示）

---

## ⚖️ 开源协议与免责声明

- **许可证**：本项目自身代码（`晚晚/` 及入口文件）采用 [MIT](./LICENSE) 许可。
  你可以自由使用、修改、再分发（含商用），但需保留版权声明。
- **第三方组件**：仓库内的 SnowLuma（QQ 协议运行时）及其原生模块为第三方作品，
  按 **SnowLuma 源码可见·非商业许可** 授权，**不**受本项目 MIT 许可约束。
  使用前请阅读 [SnowLuma 最终用户许可协议](./snowluma/EULA.md) 与 [隐私说明](./snowluma/PRIVACY.md)，
  并遵守其关于非商业使用、以及原生组件的再分发限制。详见 [第三方组件许可说明](./THIRD_PARTY_NOTICES.md)。
- **非官方 / 免责声明**：本项目依赖的非官方 QQ 协议工具（SnowLuma）与腾讯 / QQ 无任何隶属、
  合作、授权或背书关系。使用此类工具接入 QQ **可能违反《QQ 用户协议》及平台条款，
  存在账号风控风险**，请自行评估并承担相应风险。作者不对因此产生的任何直接或间接损失负责。
- **数据合规**：运行本程序会处理你 QQ 联系人 / 群成员的**个人信息**，你作为数据控制者，
  须自行确保处理行为符合相关法律法规（取得必要同意、保障安全、响应数据主体请求等）。
- 本开发版不含任何真实 API Key、个人数据；请在本地通过 `晚晚/配置/.env`（依 `.env.example` 模板）配置，
  **切勿将真实密钥或个人信息提交到仓库**。
