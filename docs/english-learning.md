# 英语平台试用（WE Learn / 老版 U校园）

入口：导航「英语平台试用」，路径 `/english-learning`。接口位于
`/api/v1/english-learning`，沿用当前管理用户认证并隔离会话/任务。
已通过离线合成页面、模拟请求和实际 Chromium 沙箱/表单保存验证；尚未通过真实课程验证。

## 自动答题与作业

1. 导入登录会话，读取课程并明确选择一项内容。
2. 选择「自动答题／作业（当前页）」。默认总预算 300 秒，可设 30–1800 秒。
3. 默认使用「缓存未命中时使用服务器 AI」；可取消以仅查缓存。启用 AI 后的
   真实任务可能收费，测试不会调用真实付费接口。
4. 默认尝试保存。只有单独勾选自动提交，且全页题目都受支持、答案完整、
   填写后复核一致，才点击所选练习/作业页面的原生提交按钮。
5. 仅有新的平台成功提示时记录已保存/已提交；点击后结果不明会提示官网核对，
   不自动重试，不自动重做。部分平台自动保存没有可识别回执时也不会冒称保存成功。

支持标准 radio 单选、checkbox 多选、文本填空、单个 select 的下拉选词及
textarea 简答/翻译。题目按 DOM 顺序处理，选项顺序严格保留；相同选项文本、
一个容器中混有多个选择组、特殊编辑器、多个混合控件无法明确对应时转人工。
图片题、缺少文本的听力、录音/口语题、验证码、人脸、监考、未开放或过期内容
不自动处理。当前只处理所选内容的当前页，不自动遍历下一页、考试或重考。
独立的班级作业列表/特殊作业页面没有伪造适配；只有符合支持表单的普通课程
练习/作业能够处理，其他页面须提供脱敏结构样例后再适配。

WE Learn 在选中小节后通过原生 StudyCourse 页面读取和填写表单，不构造 100% 分数、
正确率或 completed 数据。老版 U校园目录增加必修 `.icon-lianxi` 练习/作业入口，
与已支持的阅读/预览入口一起展示，启动时重新核对目录与名称，防止错选。
不移植参考项目自签 JWT、随机答案、盲目点击最后按钮、空答案强行提交或自动重做。

任务显示题目数、有答案数、已填数、缺失数、不支持控件数、保存/提交回执数和未确认数。
AI作答数是使用 AI 结果的题目数，不是 HTTP 尝试数或计费请求数。实际 API 用量继续
由既有 api_usage 遥测记录，缺失 usage 不推算为零。

## 与之前答题逻辑的复用

- 使用现有 CacheDAO 的进程共享缓存、同题请求锁和当前等待者失败复用。
- 英语缓存使用独立命名空间，包含平台、课程、题干、上下文、题型、空数、完整
  有序选项和服务器模型/endpoint；不会用旧选项字母绕过换序校验。
- 使用现有 AI provider 的服务器配置、DeepSeek 双 Key、有界重试、HTTP 200
  空/坏响应不再生成、token 限制、超时和 usage 统计。
- 每个新任务初始化自己的 provider，读取当前服务器 Key；英语任务共享请求信号量，
  默认最多 3 个在途请求。没有恢复学习通账号或章节并发上限。
- 使用现有 question/job 控制作用域；单题默认 120 秒，整体另受所选答题预算限制。
  同步 HTTP 无法精确强杀，取消会阻止后续取题和提交；在途网络最多等其有界 timeout。
- AI 在后端父进程执行，浏览器子进程仅通过私有管道传题/接收答案，不接收服务器 Key、
  数据库或 JWT 环境变量。未走会输出原始题目/答案日志的 Tiku.query 包装层。
- 缓存只接受通过形状和选项校验的答案；这不是正确率保证，AI 可能答错。

## 原限时学习模式

WE Learn 仍可读取课程/开放小节、按实际等待心跳累计时长；原进度、分数和完成状态
保留平台返回值。首次使用小节须先在官网打开以初始化学习数据。
老版 U校园限时学习打开所选阅读页，遇到交互题目提示人工处理。
本地会话结束不等于平台课程通过，实际时长/进度/成绩须在官网核对。

## 登录信息与隔离

WE Learn 官网登录后，从开发者工具 Network 复制发往 `welearn.sflep.com` 请求的
Cookie；勿粘贴其他站点信息。

老版 U校园先在本人本地浏览器完成登录/验证码，使用本人的 Playwright 上下文
`context.storage_state()` 导出状态。只保留 `unipus.cn` cookies 及
`https://u.unipus.cn`、`https://ucontent.unipus.cn` 的 origins/localStorage。
课程目录地址必须是 `https://u.unipus.cn/user/student/mycourse/courseCatalog?...`。
不会读取本机已有浏览器 profile。

登录信息不写 localStorage、日志、数据库或任务返回值。服务端使用既有 Fernet
辅助工具在短期内存会话中保存，子进程通过 stdin 短暂使用明文，退出即释放副本。
会话一小时过期（执行中延至结束清理），可手动断开；终态任务保留两小时。
任务/登录状态不跨重启恢复，不自动重新启动，不加入学习通账号表或 QQ 快照。
同一试用会话只允许一项操作；重复导入同一真实账号没有跨会话去重，请勿同时运行。
独立低权限宿主管理用户尚未落实。

## 浏览器运行环境与上线

两平台自动答题及老版 U校园目录/阅读均需要 Playwright 和 Chromium。WE Learn 的
原课程读取/时长模式只需要 requests。原始上游 app 基础镜像不含浏览器。

可选 `Dockerfile.english` 固定 Playwright 1.63.0，在镜像内安装官方 Chromium
headless shell 和系统依赖，以原 app 用户运行，保留 `chromium_sandbox=True`。
已在禁网、只读根文件系统、UID 10001、cap-drop ALL、no-new-privileges、
512MiB/0.75CPU/192 pids 的容器内验证真实浏览器填写及原生保存回执。
默认 Docker seccomp 在移除全部能力后会拦截沙箱初始化；运行环境需要审核
用户命名空间创建、沙箱内 chroot 及 PID 命名空间 clone 的受限规则。
不得关闭 sandbox、添加宿主能力、使用 seccomp unconfined 或自行提高资源限额。
构建/namespace 与 sandbox 要求参考 <https://playwright.dev/python/docs/docker>。

构建示例（不代表部署授权）：源码目录执行
`docker build -f Dockerfile.english -t university-helper-app-english:trial .`。
专用 dockerignore 只开放后端源码和浏览器依赖清单，排除 runtime/secrets/data/profile。
每个部署环境仍须验证 sandbox 和现有资源预算，核对活动任务并取得上线授权。
合成页面通过不保证真实课程结构兼容；源码与镜像构建成功不代表已部署。
浏览器只允许平台域名的 HTTPS 资源，不授权摄像头/麦克风、不共享 profile 或 Docker
socket；依赖其他 CDN/第三方域名的课程可能无法打开，须明确核对后另做受限适配。

## 来源与许可证

- <https://github.com/xuebi0218/welearn-gui>，实际 LICENSE=MulanPSL-2.0，版本
  `015c1ac58669c5ca2f20d2015d6e184266ce7f08`：参考课程、小节及心跳协议。
- <https://github.com/hyhang915/AutoUnipus-plus>，BSD-3-Clause，
  Copyright (c) 2024, PengChenxu，版本 `724f50c11ca9dff16cfc874f492c5abbc4ab6d83`：
  参考目录地址、必修/练习图标及旧版提交控件定位；未引入整个 runner/答题链。

许可证原文保留于 `third_party/english-learning/`，借用内容各自按其许可证提供。
