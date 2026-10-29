**语言：** [English](./README.md) | 简体中文

# University Helper

**个人电脑桌面版：** Windows 优先，macOS / Linux 同步保留。当前工作台的桌面适配、安装步骤与发行状态见 [桌面版使用说明](./docs/DESKTOP.md)。无需 Docker、Python 或 PostgreSQL；安装包以本 fork 的 `desktop-v…` Release 为准。

<p align="center">
  <strong>上游演示网站（非本 fork 实例）：</strong>
  <a href="https://shuake.cornna.xyz">shuake.cornna.xyz</a>
</p>

<p align="center">
  <img src="docs/university-helper-promo.gif" alt="University Helper 产品宣传片：你睡觉时，它在超星学习通和智慧树上替你签到、刷课、答题，天亮前做完" width="640" />
  <br />
  <sub><b>「你睡觉，它上课」</b> 60 秒产品宣传片，用 Remotion 逐帧渲染。</sub>
</p>

## 学习平台与接入状态

本 fork 主要维护学习通 / 泛雅，保留学习通签到和智慧树入口，并提供 **WE Learn、U校园 AI版 5.0 测试接入**。后端为 FastAPI，前端为 React。以下状态以本仓库已提交实现为准，不能替代目标服务器的版本核对或真实课程验收。

| 学习平台 | 当前状态 | 支持范围与边界 |
| --- | --- | --- |
| 学习通 / 泛雅 | 主要维护，部分真实课程已验证 | 多账号、章节范围、视频/文档与已适配练习、普通课程作业/考试、任务历史与重新启动；作业/考试单独勾选，未开放、已截止及验证要求保留人工处理。 |
| 学习通签到 | 沿用上游支持 | 签到页面与接口保留，本 fork 尚未逐项完成真实场景验收。 |
| 智慧树 | 沿用上游支持 | 账号/二维码登录与课程任务入口保留，本 fork 尚未逐项完成真实课程回归。 |
| WE Learn | **测试中** | 登录、课程/课节范围、已适配 ET 文本练习与内部翻页、原生提交及完成回读；部分真实课节已确认完成，加载、阅读完成判定与特殊交互仍待完善，本人录音需人工。 |
| U校园 AI版 5.0 | **测试中** | 5.0 门户登录、书架与已适配课程入口；真实教材的执行兼容性仍需验收，不能宣称全平台可用；旧版 U校园已移除。 |

WE Learn 优先课件答案和缓存；每道新 AI 题（含首题）调用前随机等待 **20–40 秒**，计入答题预算。学习通新任务默认 `LocalCache → AI`，GO / 言溪不默认启用。答案命中、按钮点击和本地执行结束均不等于平台确认完成。

**部署本 fork：先读 [Agent 部署手册](./docs/AGENT_DEPLOYMENT.md)**。手册覆盖固定提交的源码构建、私有配置、状态持久化、可选英语浏览器沙箱、空闲升级、备份、验收与回滚。上游演示站和预构建镜像不代表本 fork 的部署版本。

## 本 fork 与原仓库的区别

本仓库是 [bzsjmz/university-helper](https://github.com/bzsjmz/university-helper)，基于 [sweetcornna/university-helper](https://github.com/sweetcornna/university-helper) 的 **v1.5.0**（`6c10c064`）维护服务器扩展。下表描述本 fork 的源码改动；不表示某个服务器已部署全部功能，也不自动同步上游后续版本。

| 方面 | 本 fork 的改动 |
| --- | --- |
| 多账号工作台 | 同一管理用户保存多个学习通账号，凭据加密；账号任务独立运行，阻止同一真实账号重复启动。支持显示名称修改、账号删除，以及逐账号选课、章节范围预览和批量启动。 |
| 课程作业与考试 | 章节学习、课程作业、课程考试分别勾选；支持只做作业/考试以及无章节课程。默认只勾选章节学习，沿用题库及保存/提交模式，独立统计处理结果。 |
| 任务控制与历史 | 聚合控制台、详情、暂停/停止、终态记录删除；保存加密启动配置，重新启动沿用原范围并读取平台进度，新任务启动成功后替换对应旧记录。历史列表从数据库补读，避免终态记录超过两小时后消失。首次失败后等待一小时自动按原配置重启一次，再次失败保留记录等待人工处理；只应用于此策略启用后发生的失败。 |
| 失败与线程处理 | 失败章节先让后续章节执行，最多三次尝试后保留失败并跳过；视频连续 403 有界刷新重试。答题预算、协作取消与 worker 回收减少长时间等待。 |
| AI 与答题费用 | 后端配置 DeepSeek 默认参数和优先/回退双 Key；同题并发合并、题型/选项顺序安全缓存，避免 HTTP 200 空白或解析失败后再次付费生成；记录不含题目、答案或 Key 的请求尝试/token usage。 |
| 题库与并发 | 新任务默认 1.5 倍速、2 章节并发、LocalCache→AI；GO / 言溪不默认启用。可配置关闭应用任务数量上限，章节并发无应用最大值，容器资源限制仍需由部署者设置。 |
| 被动 QQ 状态 | 独立 AstrBot 插件响应管理员 `/任务状态`，读取指定管理用户的脱敏快照并拒绝过期数据；不主动推送，不共享数据库或认证凭据。 |

课程作业和考试覆盖所选课程当前可处理的未提交项目，**不受章节终点限制**。新功能支持普通作业试卷和逐题考试页面；平台布局或协议变化可能需要适配。已提交、未开放、已截止及重考项目不自动进入；验证码、人脸、口令、监考或指定客户端要求保留人工处理。作业/考试答案未全部匹配时仅保存并提示未完成，不随机补答案，也不在再次执行时降低提交条件。仅保存模式不交卷；考试进入后平台限时仍可能继续，暂停和服务重启不会暂停平台计时。模拟回归不等于已验证每个真实课程的兼容性。

任务重新启动会创建新的执行线程，不是跨进程断点恢复；跳过失败章节不代表学习通已认可完成。保留 AI 兜底时，缓存未命中仍可能产生费用。源码、测试和插件可公开；本机凭据、运行数据、私有运维配置与备份不包含在本仓库中。下方部署/桌面端说明继承上游，本 fork 的预构建镜像与安装包应以实际发布内容为准。

## 快速开始

### 桌面端

个人电脑优先使用本 fork 的 [桌面版使用说明](./docs/DESKTOP.md)。Windows 10/11 x64 优先，同时保留 macOS（Apple Silicon / Intel）和 Linux x64。
安装包包含当前多账号工作台、后端、SQLite 与英语试用浏览器，无需安装 Docker、PostgreSQL 或 Python，也不用注册管理账号。

到 [本 fork 的 Releases](https://github.com/bzsjmz/university-helper/releases) 查找 `desktop-v…` 版本；草稿和失败构建不代表安装包已公开可用。
Windows 推荐下载文件名以 `x86_64-pc-windows-msvc.exe` 结尾的安装器，其他平台文件名及校验方法见使用说明。

安装后在“学习通泛雅”添加账号、读取课程并选择范围；任务控制、批量预览、作业/考试和历史重启与当前服务器工作台共用实现。
“桌面设置”可填写自己的 AI 接口、模型和双 Key；默认 `LocalCache → AI`，不包含维护者的凭据或私有题库。AI 可能产生费用。

当前预览未配置本 fork 自动更新签名，需手动下载新包更新。请先停止任务再退出或升级，电脑休眠/进程重启后的执行线程不会自动恢复。
Windows 安装包未做商业代码签名，macOS 未公证；安装来源、系统依赖、数据备份与日志路径均见 [详细说明](./docs/DESKTOP.md)。

### 上游 Release 安装（参考）

> 本 fork 的服务器安装请优先使用 [Agent 部署手册](./docs/AGENT_DEPLOYMENT.md)，从批准的提交构建 app/web。下面保留的安装器默认下载上游产物，不保证包含本 fork 扩展。

新服务器装好 Docker 后，运行部署脚本即可。脚本会生成带随机密钥的 `.env`，从 GHCR 拉取预构建镜像，启动 Postgres、后端和 web 容器，并等 `/health` 通过。不需要先 git clone：脚本单独运行时，会先把对应版本的源码下载到 `./university-helper`（可用 `UH_INSTALL_DIR` 改位置）。

```bash
# Linux / macOS / WSL2，本机访问 http://localhost:8080
curl -fsSL https://github.com/sweetcornna/university-helper/releases/latest/download/deploy_server.sh | bash -s -- -y --admin-email you@example.com

# 公网 IP，纯 http
curl -fsSL https://github.com/sweetcornna/university-helper/releases/latest/download/deploy_server.sh | bash -s -- -y --host 203.0.113.10 --admin-email you@example.com

# 域名（ENV=production、https CORS，并生成配置 TLS 用的宿主机 nginx 模板）
curl -fsSL https://github.com/sweetcornna/university-helper/releases/latest/download/deploy_server.sh | bash -s -- -y --domain your.domain --admin-email you@example.com
```

Windows（Docker Desktop）：

```powershell
Invoke-WebRequest -UseBasicParsing https://github.com/sweetcornna/university-helper/releases/latest/download/deploy_server.ps1 -OutFile deploy_server.ps1
powershell -ExecutionPolicy Bypass -File deploy_server.ps1 -Yes -AdminEmail you@example.com
```

已经 clone 了仓库的话，直接运行仓库里的脚本：

```bash
git clone https://github.com/sweetcornna/university-helper.git
cd university-helper

# Linux / macOS / WSL2
bash scripts/deploy_server.sh --tag 1.4.7 -y                 # 本机：http://localhost:8080
bash scripts/deploy_server.sh --tag 1.4.7 --host 203.0.113.10 -y
bash scripts/deploy_server.sh --tag 1.4.7 --domain your.domain -y

# Windows（PowerShell + Docker Desktop）
pwsh scripts/deploy_server.ps1 -Tag 1.4.7 -Port 8080 -Yes
```

常用参数（括号里是 PowerShell 的写法）：

- `--tag`（`-Tag`）：要安装的版本，`1.4.7` 和 `v1.4.7` 都行，默认是最新版。
- `--port`（`-Port`）：web 容器在宿主机上的端口，默认 8080。
- `--admin-email`（`-AdminEmail`）：管理员邮箱，多个用逗号分隔。
- `--allowed-hosts`（`-AllowedHosts`）：还会用哪些域名或 IP 打开网站，比如局域网 IP 或第二个域名。
- `--build`（`-Build`）：从源码构建镜像，不拉取预构建镜像。

脚本可以重复运行。`.env` 里的密钥和数据库都会保留，这次传入的参数会生效。升级时在安装目录里带上新版本号再跑一次：`bash scripts/deploy_server.sh --tag <新版本> -y`。安装目录如果是脚本下载的，它会先把 compose 文件和脚本换成新版本的。

服务端不会自己升级。GitHub 上有新版本时，管理员打开网页会看到提示，里面有新版本号、更新说明和可以直接复制的升级命令。管理员是 `ADMIN_EMAILS`（即 `--admin-email`）里的邮箱；没配置时，服务器上第一个注册的账号就是管理员。服务端每 6 小时检查一次；服务器不能上网的话，在 `.env` 里设 `UPDATE_CHECK_ENABLED=false`，再运行一次部署脚本。

### 本地开发

```bash
bash scripts/setup.sh        # 创建 .env，安装 Python/Node 依赖
make start                   # 启动 docker-compose 栈（app + postgres）
make test                    # 运行后端 + 前端测试
```

Windows 用户请在 WSL2 里运行这些命令，并打开 Docker Desktop 的 WSL 集成。

## 给 AI Agent 的部署入口

请完整阅读 [Agent 部署手册](./docs/AGENT_DEPLOYMENT.md)，按目标主机规则核对提交、镜像、任务与数据归属后执行。新装和既有实例升级分别处理；已有任务未获中断授权时等待空闲。文档不包含任何维护者私有配置，也不授权操作上游服务器。

## 功能概览

- 基于 JWT 的用户注册与登录
- 超星签到接口与任务轮询
- 超星刷课任务管理
- 智慧树二维码或账号密码登录，以及课程任务编排
- 基于 PostgreSQL 的多租户数据隔离，每个用户一个独立数据库
- 认证、仪表盘、超星、智慧树流程的 React 前端页面
- 服务端管理员的新版本提醒，桌面端更新前先询问

## 仓库结构

```text
backend/        FastAPI 应用、服务、数据模型、测试
frontend/       React + Vite 前端
database/       SQL schema 与租户初始化脚本
nginx/          反向代理配置
scripts/        安装、测试、备份、部署脚本
```

## 技术栈

- 后端：Python 3.11、FastAPI 0.115、Pydantic v2、psycopg2、PyJWT、bcrypt、Fernet 凭据加密
- 前端：React 18、Vite 5、React Router 6、Tailwind CSS
- 桌面端：Tauri 2 外壳，内置 PyInstaller 打包的后端
- 数据层：PostgreSQL 15
- 部署：Docker / Docker Compose + nginx

## 操作系统与访问方式

桌面端可以在 Windows、macOS、Linux 上原生运行。服务端是 Web 应用，生产环境以 Linux 为主，任何装了 Docker 的机器都能用下文的多架构镜像一条命令跑起来。包括安卓在内的任何设备，都可以通过浏览器或安装好的 PWA 访问服务端。

| 平台 | 支持范围 | 推荐方式 |
|---|---|---|
| Linux | 本地开发 + 生产部署 | Docker Engine + Compose、Python 3.11、Node 20 |
| macOS | 本地开发 + 部署客户端 | Docker Desktop 或 Colima、Python 3.11、Node 20 |
| Windows | 服务端（Docker Desktop）+ 部署客户端 | `scripts/deploy_server.ps1`（PowerShell）或 WSL2 + `deploy_server.sh` |
| Android | 终端用户访问 | 在 Chrome/Edge 中安装 PWA；目前没有原生 APK |

详细说明见 [平台支持](./docs/PLATFORMS.md)。

## 主要 API

- `POST /api/v1/auth/register`
- `POST /api/v1/auth/login`
- `GET /api/v1/auth/shuake-token`
- `POST /api/v1/chaoxing/login`
- `GET /api/v1/chaoxing/courses`
- `POST /api/v1/chaoxing/sign`
- `POST /api/v1/course/start`
- `GET /api/v1/course/status/{task_id}`
- `POST /api/v1/course/zhihuishu/qr-login`
- `POST /api/v1/course/zhihuishu/password-login`
- `POST /api/v1/course/zhihuishu/tasks/course`
- `GET /api/v1/system/update`（仅服务端，仅管理员）
- `GET /health`

## 题库

自动答题时，每道题都会去可配置的题库（`tiku`）里查。查到的答案会按题型校验并写入缓存，所以一份 N 道题的试卷只需要 O(1) 次缓存读取。题库来源在 `tiku_config.provider` 里配置，泛雅页面上对应「题库来源」。

| 题库 | `provider` 取值 | Token | 说明 |
|------|----------------|-------|------|
| 言溪题库 | `TikuYanxi` | 需要 | 通用题库。 |
| GO 题库 | `TikuGo` | 可选 | 免费搜题源（网课小工具，`q.icodef.com`），有节流。 |
| Like 题库 | `TikuLike` | 需要 | 备用题库（datam.site）。 |
| 题库适配器 | `TikuAdapter` | 不需要 | 指向自建的 [tikuAdapter](https://github.com/DokiDoki1103/tikuAdapter)（`url`）。 |
| AI 智能答题 | `AI` | 不需要 | OpenAI 兼容大模型（`endpoint`/`key`/`model`）。 |
| 硅基流动 | `SiliconFlow` | 需要 | 硅基流动大模型。 |
| 本地缓存 | `LocalCache` | 不需要 | 只用已缓存的答案，不调用外部 API。 |

### 多题库回退

`provider` 也可以写成逗号分隔的有序列表。前一个题库没查到，或者返回的答案和题型对不上，就换下一个。无法初始化的题库（比如缺 Token 的题库、缺 key 的大模型）会被自动移出回退链，所以链上只要还有一个能用，整条链就能用。

```jsonc
// 本 fork 默认链：先查本地，未命中才使用已配置的服务器 AI
{ "provider": "LocalCache,AI" }
```

> 共享缓存和已安装的本地题库优先查询。`LocalCache` 可单独用于不调用外部 API 的模式，也保留在默认链首位；AI 未命中兜底可能收费。上表列的是可选适配器，不是启用或购买 GO / 言溪等服务的建议。

## 开发细节

### 后端单独启动

```bash
cd backend
install -m 600 .env.example .env  # 然后编辑 SECRET_KEY / CORS_ORIGINS / 数据库配置
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt -r requirements-dev.txt
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

### 前端单独启动

```bash
cd frontend
npm install
npm run dev                  # http://localhost:3000，/api 代理到 :8000
```

## 部署细节

本 fork 的新装、升级和回滚统一见 [Agent 部署手册](./docs/AGENT_DEPLOYMENT.md)。使用本仓库固定提交构建后端与前端，分别记录源码和实际镜像版本。英语试用额外需要浏览器及经过验证的沙箱环境，普通后端镜像并不包含它们。

[旧部署文档](./docs/DEPLOYMENT.md) 描述上游环境，仅作历史参考；不要将其中固定主机、热修复脚本或镜像地址直接用于本 fork。数据库、Fernet 密钥、状态目录和私有配置必须一起备份；更新 app 前检查所有活动/暂停任务、定时重试和英语 worker，按授权范围更新目标服务。

## 测试

```bash
cd backend && pytest -q
cd frontend && npm run test
cd frontend && npm run lint
```

## 说明

- 仓库里是当前可运行的应用代码，另有一些历史部署辅助脚本。
- `node_modules`、`dist`、`__pycache__`、`%TEMP%` 等生成目录默认已忽略。
- 手动部署并对外提供服务时，首次启动前要换掉数据库密码和 JWT 密钥。部署脚本会自动生成随机值。

## 致谢

本项目超星学习通和智慧树的自动化功能，参考了下面这些开源项目的协议研究，部分实现也借鉴了它们。感谢各位作者。

**超星学习通 · 签到**
- [cxOrz/chaoxing-signin](https://github.com/cxOrz/chaoxing-signin)：普通、拍照、手势、位置、二维码签到的协议参考。

**超星学习通 · 刷课**
- [Samueli924/chaoxing](https://github.com/Samueli924/chaoxing)：超星、尔雅、泛雅任务点全自动完成。本项目超星刷课模块的整体思路来自它。

**智慧树 / 知到 · 刷课**
- [luoyily/zhihuishu-tool](https://github.com/luoyily/zhihuishu-tool)：知到、智慧树的 API 与工具参考。

**题库、字体解密、验证码（直接使用）**
- [SocialSisterYi/xuexiaoyi-to-xuexitong-tampermonkey-proxy](https://github.com/SocialSisterYi/xuexiaoyi-to-xuexitong-tampermonkey-proxy)：超星加密字体反混淆与题库代理（见 `backend/app/services/course/chaoxing/cxsecret_font.py`、`answer_cache.py`）。
- [DokiDoki1103/tikuAdapter](https://github.com/DokiDoki1103/tikuAdapter)：可插拔题库适配器（见 `answer_providers/adapter.py`）。
- [sml2h3/ddddocr](https://github.com/sml2h3/ddddocr)：验证码 OCR 识别（见 `captcha.py`）。

这些上游项目各有自己的开源许可证，使用时请遵守。如果你的项目列在这里，想调整或移除署名，请提 issue。

## 合规提示

请只在符合学校规定、平台规则和当地法律的前提下使用本项目。对第三方平台启用自动化之前，先想清楚相应的风险和合规要求。

### 英语平台试用

WE Learn 和 U校园 AI版 5.0 仍处于测试中。当前提交使用隔离浏览器执行所选范围内已适配课节；WE Learn 支持已声明的 ET 内部题页切换、原生提交确认和平台完成回读，不能等同于全教材兼容。录音、未知控件、加载失败及完成未确认会分别提示人工处理。
WE Learn 的新 AI 题目调用前随机等待 20–40 秒，课件答案与缓存命中不等待。普通后端镜像不含 Playwright/Chromium，部署要求见 [Agent 手册](docs/AGENT_DEPLOYMENT.md)，接入细节与许可见 [接入说明](docs/english-learning.md)。
