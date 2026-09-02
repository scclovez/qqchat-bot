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

## 🚀 从零部署：拿到别人给的开发版怎么跑起来

> 你拿到的是一个**纯净开发版**（不含任何人的 API Key / QQ 账号 / 运行数据）。
> 下面让使用者用**自己的 QQ 账号**和**自己的 Key**跑起来。

### 0️⃣ 先认清这文件夹里有什么

| 内容 | 说明 |
|---|---|
| `晚晚/` | 机器人业务代码（Python） |
| `snowluma/` | QQ 协议运行时（Node.js，负责连 QQ） |
| `晚晚/配置/.env` | 各平台 API Key 配置（**需自己填**；模板为 `.env.example`） |
| `晚晚/数据/`、`晚晚/配置/*.json`、`snowluma/config`、`snowluma/data`、`snowluma/logs` | 运行数据（全新拷贝里没有，**运行后自动生成**） |

### 1️⃣ 装运行环境（一次性）

1. **Python 3.10+**（推荐 3.14）：安装时勾选 **Add Python to PATH**。
   验证：`python --version`；要能用 `1.vbs` 双击启动，还需要 `pythonw`（一般随 Python 一起装）。
2. **Node.js 22.13+**（SnowLuma 必需）：验证 `node --version`。
   版本不够时，GUI 点「启动 SNOWLUMA」会提示 *请先安装 Node.js 22+*。
3. （可选）**Git**，以后方便拉取更新。
4. （可选）若要用 ComfyUI 本地生图，最好有 **NVIDIA 显卡 + CUDA**。

### 2️⃣ 安装 Python 依赖

在含 `requirements.txt` 的项目根目录执行：

```bash
pip install -r requirements.txt
```

> 若提示 `pip` 找不到，改用：`py -m pip install -r requirements.txt`。

### 3️⃣ 创建你自己的 `.env`（填自己的 Key）

把 `晚晚/配置/.env.example` **复制一份** 为 `晚晚/配置/.env`，至少填：

```ini
DEEPSEEK_API_KEY=sk-你的DeepSeek密钥     # 必填
```

其余按需（缺了就少对应功能）：
```ini
DASHSCOPE_API_KEY=你的百炼Key            # 图片生成（可选）
MIMO_API_KEY=你的小米MiMoKey             # 语音 TTS/ASR（可选）
ONEBOT_WS_URL=ws://localhost:3001       # 第 5 步再对齐
ONEBOT_ACCESS_TOKEN=
```

> 不知道去哪申请这些 Key？见下文「申请 API / 工具详细指南」。**务必用自己的账号申请**，别用别人给的 Key。

### 4️⃣ 启动控制面板

```bash
python main.py --gui
```
或双击 `1.vbs`。看到主界面即成功；否则先看命令行报错。

### 5️⃣ 配置并启动 SNOWLUMA（连 QQ）

在 GUI **首页**点「**启动 SNOWLUMA**」：

- 若提示缺 Node.js → 回第 1 步装 Node 22+；
- 首次运行会弹出 **SnowLuma 自己的配置 / 登录界面**，请：
  1. 用**你的 QQ** 登录（机器人要挂的号）；
  2. 把 **OneBot（正向 WebSocket）** 服务端口设为 **3001**（默认）；
  3. 设一个 **Access Token** 并记下来。
- 回 GUI「模型与连接」，把 `OneBot WS 地址 = ws://localhost:3001`、`Access Token = 上面记的` 填成一致（也应同步进 `.env`）。

### 6️⃣ 启动 bot（开始聊天）

GUI 首页点「**启动 bot**」。成功时按钮变“运行中”，日志页能看到进程信息；此时**用你的 QQ 给机器人发消息**就有反应了。

### 7️⃣ 人设与参数（可选）

GUI 里把「人设」「API 参数」「互动」「用量」各页按喜好配好（保存到 `晚晚/配置/.runtime_config.json`）。

### 8️⃣ 高级能力（可选，按需）

- **图片生成**：用阿里云百炼（填 `DASHSCOPE_API_KEY`）或本地 ComfyUI（GUI「模型与连接 → 图生成」）；
- **语音回复 / 听懂语音**：用小米 MiMo（填 `MIMO_API_KEY`）；
- 对应申请步骤见下文「申请 API / 工具详细指南」。

---

### 🔍 常见问题排查

| 现象 | 原因 / 解决 |
|---|---|
| 双击 `1.vbs` 没反应 | 没装 `pythonw`，或 PATH 里缺 python。直接 `python main.py --gui` 也能开 |
| 点「启动 SNOWLUMA」提示 Node 版本不够 | 安装 Node.js 22.13+ 后重试 |
| SNOWLUMA 起不来 / 连不上 QQ | QQ 未登录 / 端口被占 / OneBot 端口或 Token 与 `.env` 不一致 |
| 启动 bot 提示缺 `DEEPSEEK_API_KEY` | 没建 `.env` 或没填 Key |
| 机器人不回复 | OneBot WS 地址 / Token 两边不一致，或 SNOWLUMA 没跑起来 |
| 图片 / 语音 / QQ 空间没反应 | 没配对应 API Key，功能自动降级（看日志） |

### ⚠️ 给使用者的提醒（授权 & 隐私）

- 这份代码（`晚晚/` 及入口）是 **MIT** 许可，可自由修改、使用、再分发。
- 随附的 **SnowLuma**（连 QQ 的运行时）是第三方**非商业许可**，使用前请读 [`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md)；若想商用或再分发其二进制，需先取得 SnowLuma 作者书面授权。
- 你的 **API Key / QQ 账号属于隐私**，只留在各自本机 `.env` 里，**不要提交或外传**（`.gitignore` 已屏蔽）。

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

## 🔑 申请 API / 工具详细指南

本机器人要"全能"跑起来，需要用到以下几家的能力。**按需申请**：缺哪个就少哪个功能——未配置对应 Key 时会**自动降级，只在日志提示、不会崩溃**。下面一步步教你怎么申请、以及把 Key 填到哪。

| 能力 | 用在哪 | 是否必需 | 官方申请入口 |
|---|---|---|---|
| **DeepSeek 大模型** | 对话 / 主动消息 / 成长日记 等所有"她说的话" | **必填** | [DeepSeek 开放平台](https://platform.deepseek.com/) |
| **阿里云百炼（通义/千问）** | 图片生成、外貌总结、视觉评论 | 可选 | [阿里云百炼控制台](https://bailian.console.aliyun.com/) |
| **小米 MiMo** | 语音回复（TTS）+ 语音识别（ASR） | 可选 | [小米 MiMo 开放平台](https://mimo.mi.com/) |
| **ComfyUI（本地图生）** | 本地、离线、免费的图片生成 | 可选 | [ComfyUI 官网](https://www.comfy.org/) |

> 习惯命令行 / 不想开 GUI？所有 Key 也可直接写进开发板根目录的 `晚晚/配置/.env`（模板见 [`.env.example`](晚晚/配置/.env.example)）。填写变量见上文「配置说明」表。

<br>

### 🥇 1️⃣ DeepSeek API（必填，聊天核心）

1. 打开 [DeepSeek 开放平台](https://platform.deepseek.com/)（标题即 *DeepSeek Platform*）。
2. 用**手机号 / 邮箱注册并登录**（国内手机号即可）。
3. 新账户通常赠送**体验额度**，用于先测试；正式用再按需充值。
4. 进「**API Keys**」页面 → 点「**创建 API Key**」→ 起个名字 → 生成以 `sk-` 开头的 Key。
5. **立即复制保存**（该值通常只完整显示一次）。
6. 回本项目把 Key 填入以下任一处：
   - 源码版：`晚晚/配置/.env` 的 `DEEPSEEK_API_KEY`；
   - 或 GUI「模型与连接 → 大模型兜底（DeepSeek .env）」的 API Key 框。
   - 模型名 `DEEPSEEK_MODEL` 用 DeepSeek 官方模型名；API 地址默认 `https://api.deepseek.com`，一般无需改动。

<br>

### 🥈 2️⃣ 阿里云百炼 / 通义千问（可选，图片生成）

用于"**画一张图 / 发自拍 / 说说配图 / 外貌总结**"等视觉能力：

1. 打开 [阿里云百炼控制台](https://bailian.console.aliyun.com/)（大模型服务平台·百炼）。
2. 用**阿里云账号**登录；没有则先注册阿里云账号并完成**实名认证**。
3. 首次进入会引导**开通百炼服务**，通常有**新用户免费额度**。
4. 在控制台「**API-KEY 管理**」页面创建 **API Key**。
5. 复制后填入：
   - `晚晚/配置/.env` 的 `DASHSCOPE_API_KEY`；
   - 或 GUI「模型与连接 → 图生成」的「百炼 API Key」框。
6. 图片生成模型 `DASHSCOPE_IMAGE_MODEL` 填 `qwen-image-3.0-pro`（或 `wanx2.1-t2i-turbo` 等，已在 GUI 下拉中）。
   API 地址默认 `https://dashscope.aliyuncs.com`。

> 说明：阿里云已把大模型统一收进「百炼（Bailian）」，旧版 DashScope 入口同样兼容；**申请 Key 以百炼控制台为准**。

<br>

### 🥉 3️⃣ 小米 MiMo（可选，语音 TTS + ASR）

用于**语音回复**（她把话读成语音条）与**听懂你发的语音**（ASR）。走的是**小米 MiMo 大模型**：

1. 打开 [小米 MiMo API 开放平台](https://mimo.mi.com/)（页面标题即 *Xiaomi MiMo API 开放平台*）。
2. 用**小米账号**登录 / 注册。
3. 新用户有**免费额度**（TTS 模型全档位套餐**限时免费**，以官网活动为准）。
4. 在控制台「**创建 / 获取 API Key**」生成一个 Key。
5. 复制后填入：
   - `晚晚/配置/.env` 的 `MIMO_API_KEY`；
   - 或 GUI「模型与连接 → 语音（小米 MiMo）」的「小米语音 API Key」框。
6. 模型名（一般不动）：TTS = `mimo-v2.5-tts-voicedesign`（`TTS_MODEL`）、ASR = `mimo-v2.5-asr`（`ASR_MODEL`）。
   API 地址默认 `https://api.xiaomimimo.com/v1`（`MIMO_API_BASE_URL`）。
7. 想让对方 **每次回复都带语音**，把「语音回复概率」调高（GUI「设置 → API 参数」的 `TTS_PROBABILITY`，或写 `TTS_PROBABILITY=0.6` 等）。

<br>

### 🔧 4️⃣ ComfyUI（可选，本地免费图生）

不依赖云端、**完全本地跑**（免费、可离线、数据不出本机），适合不想购买百炼额度或更看重隐私的情况：

1. 到 [ComfyUI 官网](https://www.comfy.org/)（官方文档 [docs.comfy.org](https://docs.comfy.org/) ｜ 源码 [github.com/comfyanonymous/ComfyUI](https://github.com/comfyanonymous/ComfyUI)）。
2. 下载**桌面版 / 便携版**并解压运行。
3. **硬件建议**：有 NVIDIA 显卡 + CUDA 生成快；只有 CPU 也能跑，但一张图要等很久。
4. 启动 ComfyUI 后，它的网页服务默认地址是 **http://127.0.0.1:8188**。
5. 本项目 GUI「模型与连接 → 图生成」里设置：
   - 「图片后端」选 **本地 ComfyUI**；
   - 「ComfyUI 地址」填 `http://127.0.0.1:8188`；
   - 「工作流文件」填 `comfy_workflow.json`（随项目自带，位于 `晚晚/图片/comfy_workflow.json`）。
6. ⚠️ ComfyUI 需**提前就绪**：工作流里引用的大模型 / 采样器 / LoRA 等都要已下载，否则生图会失败。
7. 配好后在 GUI「模型与连接」的「图片后端」切到 ComfyUI，重启 bot 生效。

> ComfyUI 是本地程序，**不联网、不采集、不上传**任何数据，适合注重隐私与成本可控的用户。

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
5. **GUI 冒烟** —— PySide6 构建 5 个顶级页 + 成长网格 13 格（窗口 1.5 秒自动关闭）

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
