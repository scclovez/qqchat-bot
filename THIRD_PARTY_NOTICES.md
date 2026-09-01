# 第三方组件许可说明 / Third-Party Notices

本项目自身的代码（`晚晚/` 目录及入口文件）依 [LICENSE](./LICENSE)（MIT）授权。
本仓库随附的第三方组件**不**属于上述 MIT 许可范围，各自按其自身条款授权，特此说明如下。

---

## SnowLuma（QQ 协议运行时）

- **名称 / 版本**：SnowLuma `@snowluma/runtime` v1.14.13
- **用途**：OneBot v11 协议运行时，负责本机 QQ 登录、消息收发与音视频转码。
- **目录**：`snowluma/`
- **版权方 / 联系方式**：SnowLumaDevs（`motricseven@foxmail.com`）
- **授权**：**SnowLuma 源码可见非商业许可（SnowLuma Source-Available Non-Commercial License）**。详见 `snowluma/EULA.md`（最终用户许可协议）与 `snowluma/PRIVACY.md`（隐私与数据处理说明）。

### 使用限制（依其 EULA，请务必遵守）

1. 允许：查看、学习、个人或**非商业**使用、非商业自托管。
2. **任何商业用途，以及公开发布其修改版 / 衍生版，均须事先取得著作权人的书面授权。**
3. **原生组件为专有**（`snowluma/native/*.node`、`snowluma/native/*.dll`、`snowluma/native/ffmpeg/*`）：
   不得复制、修改、单独再分发或再许可；将其并入第三方安装包 / Docker 镜像、通过自动化脚本部署，或用于任何商业用途，均须事先取得书面授权。
4. 使用非官方协议工具接入第三方平台**可能违反该平台（如 QQ）服务条款并带来账号风险**，请自行评估并承担相应风险。
5. SnowLuma 与腾讯 / QQ **无任何隶属、合作、授权或背书关系**。

> 提醒：若本仓库在未来转为**公开**并包含上述 SnowLuma 运行时与原生组件，请先依其 EULA 取得著作权人的书面授权，或改为指引用户从 SnowLuma 官方渠道自行获取，以免违反其许可条款。

---

## 其他依赖（通过 pip / npm 安装，不随附源码）

- OpenAI SDK（`openai`）、WebSockets、HTTPX、python-dotenv、PySide6：均按各自开源许可（如 MIT / Apache-2.0）授权。
- SnowLuma 原生模块可能捆绑第三方组件（如 FFmpeg），用于本地音视频处理，数据不出本机。
