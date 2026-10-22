# 英语平台试用（WE Learn / 老版 U校园）

入口：导航「英语平台试用」，路径 `/english-learning`。所有接口位于
`/api/v1/english-learning`，沿用当前用户认证，账号会话和任务按管理用户隔离。
本功能为初期接入，尚未通过真实账号验证，不能保证平台接纳时长。

## 当前支持范围

- WE Learn：导入官网请求 Cookie、读取课程及已开放小节、选择单个小节，
  按真实等待时间发心跳并保存累计时长；原进度、分数、完成状态保持平台返回值。
  首次使用小节需先在官网打开以初始化学习数据。
- 老版 U校园：导入 Playwright storage state 和课程目录网址，读取必修的
  预览/文本入口，选择一项后在隔离的无头浏览器上下文中打开并停留。
  检测到输入框时提示人工处理。仅允许平台 HTTPS 资源；依赖其他域名的课程可能无法打开。
- 每次启动前显示课程、内容和时长。默认 60 秒，试用范围 30–1800 秒。
- 停止终止工作进程组并等待其退出；平台已接收的心跳/访问记录不会撤回。
- 任务结束只说明限时会话结束。不会把本地结束解释为平台课程通过。

当前不支持自动答题、作业/考试提交、密码登录、验证码代填、自动恢复/重启，
也不写入学习通账号表、重启配置或 QQ 状态快照。没有移植参考项目中直接写入
100% 分数、伪造完成状态、随机答案、JWT 自签或失败强行提交的代码。

## 登录信息与隔离

WE Learn 登录官网后在开发者工具 Network 中复制对 `welearn.sflep.com` 请求的
Cookie。不要粘贴其他网站凭据。

老版 U校园先在本人本地浏览器完成登录/验证码，使用本人的 Playwright 浏览器
上下文 `context.storage_state()` 导出登录状态。只保留 `unipus.cn` cookies，
以及 `https://u.unipus.cn`、`https://ucontent.unipus.cn` 的 origins/localStorage。
需要课程目录网址：`https://u.unipus.cn/user/student/mycourse/courseCatalog?...`。
本项目不会读取机器上已有浏览器 profile。

登录信息不进入 localStorage、日志、数据库或任务返回值。服务端复用现有 Fernet
辅助工具在内存中保存，连接验证进程短暂使用明文，关掉工作进程即释放该副本。
会话一小时过期（执行中会话延至任务结束清理），可手动断开；终态记录两小时清理。
服务重启会丢失这些试用会话/记录，已有学习通任务机制不受此模块修改。
相同试用会话只允许一项操作；导入同一真实账号的多个会话没有跨会话去重，
请勿同时运行同一账号。长期独立低权限宿主管理用户尚未落实。

## 可选浏览器运行环境与上线

WE Learn 使用现有 requests 依赖。老版 U校园需要在项目独立后端运行环境内安装
`backend/requirements-english.txt` 以及 Playwright Chromium 和相应系统库。
现有本机后端基础镜像不含这些依赖。不得在宿主或 Agent 环境全局安装，不得为
解决 Chromium 启动失败关闭 sandbox。浏览器使用 `chromium_sandbox=True`，
无摄像头/麦克风授权、不使用共享 profile、不挂 Docker socket。

已提供可选 `Dockerfile.english`（尚未构建或验证实际浏览器）。该构建文件固定 Playwright 1.63.0，
将浏览器与系统依赖安装在镜像内，以原 app 用户运行，保留 sandbox。
镜像依赖与 sandbox 注意事项参考 <https://playwright.dev/python/docs/docker>。
发布前需准备独立浏览器镜像/受限运行环境，评估现有 app 的 200 pids 和内存预算，
验证宿主/容器支持 Chromium sandbox；未经资源与上线授权不能提高业务限额或重启 app。
没有浏览器环境时会返回明确错误，不能声称 U校园已上线可用。
该初版代码不自动部署，也不会使用真实 Cookie、真实平台或付费 API 自测。

## 参考来源

- <https://github.com/xuebi0218/welearn-gui>，实际 LICENSE 为 MulanPSL-2.0。
  参考课程列表、单元、小节及心跳/保存协议，重新实现固定域名、有界响应、
  禁止重定向、真实等待和错误脱敏。许可证保留于
  `third_party/english-learning/welearn-gui.LICENSE`。
- <https://github.com/hyhang915/AutoUnipus-plus>，BSD-3-Clause，
  Copyright (c) 2024, PengChenxu。参考目录地址、必修图标及阅读/预览入口定位方式；
  未引入其整个 runner 或答题链。许可证保留于
  `third_party/english-learning/AutoUnipus-plus.LICENSE`。

此处借用内容按各自许可证提供，不更改仓库其他内容的许可证。

参考源码核对版本（2026-10-22）：
`welearn-gui@015c1ac58669c5ca2f20d2015d6e184266ce7f08`；
`AutoUnipus-plus@724f50c11ca9dff16cfc874f492c5abbc4ab6d83`。

构建示例（不代表部署授权）：在源码目录中使用
`docker build -f Dockerfile.english -t university-helper-app-english:trial .`。
构建上下文由 `Dockerfile.english.dockerignore` 限制为后端源码和浏览器依赖清单，
不会包含本机 runtime、secrets、data 或 browser profile。构建后仍需在隔离环境
验证 Chromium sandbox、资源余量和两平台真实课程格式，不能直接宣称可上线。
