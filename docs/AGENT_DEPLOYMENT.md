# University Helper fork：Agent 部署手册

适用仓库：[`bzsjmz/university-helper`](https://github.com/bzsjmz/university-helper)。
核对日期：2026-10-28。本文面向执行部署的 Agent，描述可复用的服务器部署流程，
不包含维护者的主机地址、凭据、运行配置、题库文件或备份。

## 1. 先确认目标和版本

先确认用户要求的是新服务器安装、现有实例升级，还是只修改文档。
部署授权不包含创建真实课程任务、付费 AI 验证、考试提交或操作其他项目。

读取目标主机适用的 `AGENTS.md` 和本机运行记录，再核对：

- 实际源码目录、Compose 文件及项目名；已有容器、端口、数据库和状态卷归属。
- `git remote -v`、分支、工作区差异，以及用户批准部署的提交 SHA。
- 当前 app/web 镜像 ID、启动时间；源码版本不能代替运行版本。
- 系统负载、可用内存、磁盘和端口余量；保留其他服务的资源余量。
- 所有管理用户的活动、暂停任务、定时重试，以及英语登录/执行 worker。
  学习通控制台和 QQ 快照不包含全部英语操作，单看这两个入口不足以确认空闲。

有活动任务时先完成构建和可审查的部署方案；没有中断授权就等待空闲。
暂停不会回收执行线程。旧的重启授权不能作为中断新任务的依据。

本 fork 默认远端应为 `git@github.com:bzsjmz/university-helper.git`
或同仓库 HTTPS 地址；上游为 `sweetcornna/university-helper`。
上游 Release、镜像、更新提醒不代表已经包含本 fork 的改动。
已有实例沿用它自己的运行入口，不能直接套用本文的新装路径或服务名。
不要运行带有上游固定主机默认值的 `scripts/hotfix_publish.sh`。

## 2. 平台能力与可选依赖

| 平台 | 定位 | 部署时需要说明 |
| --- | --- | --- |
| 学习通 / 泛雅 | 本 fork 主要维护平台，部分真实课程已验证 | 多账号、章节、普通课程作业/考试；验证码、监考、截止项目及未知题型仍可能需要人工。 |
| 学习通签到 | 沿用上游功能 | 有页面与接口，不代表本 fork 已逐项验收；不要发起真实签到作为健康检查。 |
| 智慧树 | 沿用上游功能 | 登录与课程任务入口保留，本 fork 未逐项完成真实课程回归。 |
| WE Learn | 测试中 | 现有提交版本使用隔离浏览器执行已适配练习；部分真实课节有完成回读，加载、特殊交互及本人录音仍有边界。 |
| U校园 AI版 5.0 | 测试中 | 5.0 登录和书架接入，课程执行兼容性仍需真实教材验收；旧版 U校园已移除。 |

普通后端构建不安装 Playwright。启用英语登录/答题试用前，须完成第 6 节的
浏览器依赖与沙箱检查。不要把未提交或未验收的 HTTP 适配草案当作已发布能力。
WE Learn 新 AI 题目调用前会随机等待 20–40 秒；课件/缓存命中不等待，等待计入预算。

WE Learn 独立工作台支持加密保存登录、多账号并发和课程／课节全选；登录过期仍需重新验证，执行线程不跨重启恢复。部署时备份新增的 `welearn_accounts`、`welearn_tasks` 表及既有加密密钥，并将工作台活动任务纳入空闲检查。操作和存储细节见 [WE Learn 工作台说明](welearn-workbench.md)。

## 3. 新服务器：目录与源码

以下仅是新装示例。目录、项目名、端口和配额必须先与目标主机核对。
宿主机需要 Git、Docker Engine/Compose v2、Python 3 和 OpenSSL；
业务依赖装在镜像内，不共用 Agent 的 Python 环境，也不全局安装项目依赖。

```bash
docker info >/dev/null
docker compose version
git --version
openssl version
df -h .
free -m
ss -ltn 'sport = :8000 or sport = :8080'
```

在用户指定的新项目目录执行下面的克隆。将 `APPROVED_COMMIT_SHA` 替换为
本次批准的真实提交；缺少该值时先取得目标版本，不直接跟随浮动 `main` 上线。

```bash
mkdir -p university-helper-deploy
cd university-helper-deploy
git clone https://github.com/bzsjmz/university-helper.git repo
git -C repo checkout --detach APPROVED_COMMIT_SHA
git -C repo remote -v
git -C repo status --short
git -C repo rev-parse HEAD
install -d -m 700 secrets data backups logs
install -d -m 755 runtime
install -d -m 700 -o 10001 -g 10001 data/app
```

记录实际绝对路径与 SHA。后文工作目录均为此部署根目录。
构建前确保源码目录没有 Cookie、私有字体映射、账号数据或其他敏感附件；
`.dockerignore` 不是收集运行目录后再筛选的替代方案。

## 4. 首次初始化配置

仅在全新实例、没有旧数据库/状态时生成密钥。已有实例必须恢复原配置，
不能为解决启动错误重新生成数据库密码、JWT 密钥或 Fernet 密钥。
以下命令使用标准库生成 Fernet 格式的 32 字节随机密钥，不打印密钥。
需要由有权管理此部署目录的用户执行文件所有权设置。

```bash
python3 - <<'PY'
from pathlib import Path
import base64
import os
import secrets

os.umask(0o077)
config = Path('secrets/deploy.env')
values = {
    'UH_PROJECT': 'university-helper-fork',
    'UH_APP_IMAGE': 'university-helper-fork-app:initial',
    'UH_WEB_IMAGE': 'university-helper-fork-web:initial',
    'POSTGRES_PASSWORD': secrets.token_hex(32),
    'SECRET_KEY': secrets.token_hex(32),
    'SHUAKE_COMPAT_SECRET': secrets.token_hex(32),
    'CREDENTIAL_ENCRYPTION_KEY': base64.urlsafe_b64encode(secrets.token_bytes(32)).decode(),
    'CORS_ORIGINS': '["http://localhost:8080","http://127.0.0.1:8080"]',
    'ALLOWED_HOSTS': 'localhost,127.0.0.1',
    'ADMIN_EMAILS': '',
    'ENV': 'dev',
    'APP_PORT': '8000',
    'HTTP_PORT': '8080',
    'CHAOXING_AI_ENDPOINT': 'https://api.deepseek.com/chat/completions',
    'CHAOXING_AI_MODEL': '',
}
with config.open('x') as stream:
    stream.write(''.join(f'{key}={value}\n' for key, value in values.items()))
for name in ('ai-primary-key', 'ai-fallback-key'):
    path = Path('secrets') / name
    with path.open('x'):
        pass
    path.chmod(0o600)
    os.chown(path, 10001, 10001)
print('Created private configuration and empty AI key files.')
PY
```

用安全编辑器填写管理员邮箱；如需服务器 AI，再填写实际支持的模型和两个单文件
Key。空 Key 表示未配置，不能宣称 AI 已可用。设置了 Key 也不等于已验证余额。
不把 Key 写入命令参数、报告、Git、前端或共享目录。更新已挂载 Key 时保留 inode，
新任务读取新值，已初始化的任务继续使用原值。

生产域名部署前，将 `ENV` 改为 `production`，配置精确的 HTTPS `CORS_ORIGINS`
与 `ALLOWED_HOSTS`，通过既有反向代理提供 TLS。不要设 `*`；不要因为单个项目
改动全局防火墙/代理。示例端口保持 loopback，不自行暴露管理 API 或数据库。

将以下内容保存为 `runtime/fork.override.yml`。它在仓库的 release Compose
上覆盖为本地构建的 fork 镜像，并添加持久状态和单文件 Key 挂载。
**Compose 的相对路径按第一个文件 `repo/docker-compose.release.yml` 所在目录解析**，
所以 `../data`、`../secrets` 指向部署根目录。数据库沿用该 Compose 的专用命名卷，
不与其他项目共享。

```yaml
services:
  app:
    image: ${UH_APP_IMAGE:?Set UH_APP_IMAGE}
    container_name: ${UH_PROJECT:?Set UH_PROJECT}-app
    pids_limit: 200
    environment:
      UPDATE_CHECK_ENABLED: "false"
      CHAOXING_COOKIES_FILE: /state/cookies.json
      CHAOXING_CACHE_FILE: /state/answer-cache.json
      CHAOXING_API_USAGE_FILE: /state/api-usage.jsonl
      CHAOXING_LOCAL_BANK_FILE: /state/imported-question-bank.sqlite
      CHAOXING_AI_ENDPOINT: ${CHAOXING_AI_ENDPOINT}
      CHAOXING_AI_MODEL: ${CHAOXING_AI_MODEL:-}
      CHAOXING_AI_PRIMARY_KEY_FILE: /run/secrets/ai_primary
      CHAOXING_AI_KEY_FILE: /run/secrets/ai_fallback
      CHAOXING_AI_TIMEOUT_SECONDS: "120"
      CHAOXING_QUESTION_TIMEOUT_SECONDS: "120"
      CHAOXING_ANSWER_JOB_TIMEOUT_SECONDS: "300"
      CHAOXING_REQUEST_TIMEOUT_SECONDS: "30"
    volumes:
      - ../data/app:/state
      - ../secrets/ai-primary-key:/run/secrets/ai_primary:ro
      - ../secrets/ai-fallback-key:/run/secrets/ai_fallback:ro
    deploy:
      resources:
        limits:
          memory: 1G
          cpus: '0.75'
          pids: 200
  web:
    image: ${UH_WEB_IMAGE:?Set UH_WEB_IMAGE}
    container_name: ${UH_PROJECT:?Set UH_PROJECT}-web
    deploy:
      resources:
        limits:
          memory: 256M
          cpus: '0.25'
  postgres:
    container_name: ${UH_PROJECT:?Set UH_PROJECT}-db
```

配额只用于此新实例的评估起点，不是已经证明的并发容量。
不要借部署手册修改现有业务容器或整台主机的配额。
如要取消学习通应用任务数量上限，须按用户的明确决定配置
`CHAOXING_MAX_ACTIVE_TASKS=0`；不要将它误解为取消容器资源限制。

定义本次 shell 的项目入口；后文的 `uh` 均使用这组文件和项目名。
在新 shell 中重新定义，不能误用另一个项目的默认 Compose。

```bash
uh() {
  docker compose --env-file secrets/deploy.env -p university-helper-fork \
    -f repo/docker-compose.release.yml -f runtime/fork.override.yml "$@"
}
uh config --quiet
uh config --images
```

若调整 `UH_PROJECT`，同步调整 `-p` 参数。不要将展开后的完整 Compose 输出到日志，
其中包含数据库密码和认证密钥。确认最终 app/web 指向本 fork 的镜像，
再继续；不要对整个栈执行 `pull` 后把上游 app/web 当作本 fork 使用。

## 5. 构建同一提交的 app 与 web

给镜像设置可追溯且唯一的标签（例如提交 SHA），同时更新私有 env 中的
`UH_APP_IMAGE`、`UH_WEB_IMAGE`。下面 `initial` 仅对应首次配置示例。

```bash
docker build -f repo/Dockerfile.server \
  -t university-helper-fork-app:initial repo
docker build -f repo/Dockerfile.web \
  -t university-helper-fork-web:initial repo
uh config --quiet
uh config --images
```

`Dockerfile.server` 构建后端，`Dockerfile.web` 构建前端并内置 nginx；
只构建后端不会更新网页。普通镜像没有浏览器，学习通和智慧树的现有入口与英语
试用的依赖不同。不要把服务重启当作安装 Playwright 的方法。

源码不附带维护者的本地题库和字体数据：

- 可选本地题库默认路径为 `/state/imported-question-bank.sqlite`，须从拥有授权的
  数据自行构建并验证，不能复制维护者私有运行目录。缺少它不等于内置了同样题库。
- 加密字体题需要合法来源的映射 JSON，运行路径为
  `/srv/backend/resource/font_map_table.json`。缺少映射时会停止处理加密题，
  不能绕过检查把乱码交给 AI。若单文件只读挂载，先在镜像中创建父目录，
  并核对格式、权限和来源；不把私有附件直接纳入源码构建上下文。
- QQ 状态插件为可选集成，需要单独部署 AstrBot 插件及指定 owner 的脱敏导出。
  不共享数据库/JWT/QQ Token，不因安装主应用自动修改 Bot 或发送测试消息。

## 6. 可选：英语试用的浏览器运行环境

当前已提交的英语实现需要 Playwright 和 Chromium，依赖固定在
`backend/requirements-english.txt`。仓库提供的可选镜像可这样构建：

课件十秒停留、ET 控件适配及 App 录音边界见
[WE Learn 修复与录音调查](welearn-et-controls.md)。

```bash
docker build -f repo/Dockerfile.english \
  -t university-helper-fork-app:initial-browser repo
```

此 Dockerfile 以固定的上游 `1.5.0` app 镜像提供基础依赖，再复制当前 fork 的
`backend/app` 并安装浏览器；与 `Dockerfile.server` 的完整源码构建不同。
核对基础镜像架构/版本与该提交依赖，再在私有 env 中将 `UH_APP_IMAGE`
切换为实际构建的 browser 标签。不能假设别的主机拥有维护者的本地镜像。

**浏览器镜像构建成功仍不足以上线英语试用。** 代码使用
`chromium_sandbox=True`、非 root 用户、cap-drop 和 no-new-privileges。
部分宿主机的默认 Docker seccomp 会阻止所需的用户/PID namespace 或 chroot。
本仓库没有随附维护者本机专用的 seccomp 配置；部署者须另行评审最小权限规则，
通过目标主机上同等约束的隔离浏览器检查后，才添加项目专属只读配置。
不能使用 `--no-sandbox`、`privileged`、`seccomp=unconfined`、Docker socket 挂载
或扩大宿主能力来让检查通过。无法满足时，报告英语试用尚未具备运行条件，
不要声称整个英语功能已部署完成。

验证使用合成页面和模拟 AI，不登录真实课程、不批准微信测试授权、不提交录音。
参考测试入口为 `backend/tests/browser/english_password_login.py` 和
`backend/tests/browser/english_native_answers.py`；先阅读其参数与依赖。
测试必须遵守目标主机的测试限制；在装有 `bounded-test` 的本机，所有测试入口为：

```bash
/usr/local/bin/bounded-test -- /absolute/project/runtime/test-script
```

通过 Docker 执行时，测试容器还须显式设置共享测试 slice、512MiB 内存/内存加交换、
0.75 CPU、192 进程上限，并有墙钟超时和退出清理。仅包住 Docker 客户端不限制
daemon 创建的子进程。未配置这些设施的其他主机，应先落实其等效测试隔离方案，
不要把容器测试指向生产数据。限制配置不得顺便收紧现有业务服务。

## 7. 首次启动与验收

仅在确认这是全新部署、配置完成且没有同名项目后启动整个新栈：

```bash
uh up -d postgres app web
uh ps
curl --fail --silent --show-error http://127.0.0.1:8080/health
```

等待数据库和应用就绪；短暂 502 先等待，不能连续重启。
`/health` 应返回 `status`、`db`、`schema` 均为 `ok`。
未就绪时只看目标服务的有界日志，例如 `uh logs --tail 80 app postgres`，
日志仅在私有环境检查并脱敏；不要附上完整配置或账号响应。

还须核对：

- 未登录访问 `/api/v1/learning-accounts`、`/api/v1/english-learning/tasks`
  应返回 401/403；使用已部署脚本或隔离 HTTP 检查，不创建真实课程任务。
- web 的 `index.html`、Service Worker 和主要页面资源与本次构建产物一致。
- 实际 app 用户为非 root，根文件系统只读；Key 只读挂载，状态目录可写。
  数据库无宿主发布端口，app/web 仅按已确认的地址监听，资源限制确实生效。
- 首次管理员注册由用户通过 HTTPS 或本地安全访问完成，不使用手册示例密码，
  不把 access token 写进验收报告。没有单独授权，不代用户创建业务账号。
- `LocalCache → AI` 为本 fork 新任务默认链；不启用 GO 或言溪作为“安装验证”。
  UI 中保留适配器不代表用户已经授权启用或购买该题库。

源码/配置静态检查、镜像构建、隔离测试、HTTP 健康、真实课程验收分别报告。
不能以 HTTP 200 或本地执行结束代替平台确认完成。

## 8. 现有实例升级与回滚

现有实例优先使用已经核实的本机运行入口；以下是使用本文新装示例时的流程。
先在隔离的源码快照构建新镜像并完成相关检查，再等所有任务空闲。
不要覆盖有未提交改动的工作区，也不要自动合并上游。

备份必须包含主库、所有租户库、原私有 env、Fernet 密钥、状态目录和原 Compose。
以下命令适用于空闲的示例实例，备份目录仅供管理员读取：

```bash
umask 077
backup_dir="backups/$(date -u +%Y%m%dT%H%M%SZ)"
mkdir "$backup_dir"
uh exec -T postgres pg_dumpall -U easylearning > "$backup_dir/database.sql"
cp -a secrets runtime/fork.override.yml "$backup_dir/"
cp repo/docker-compose.release.yml "$backup_dir/base-compose.yml"
tar -czf "$backup_dir/app-state.tar.gz" -C data app
```

备份后再次核对空闲、当前配置及镜像 ID。保留旧镜像，记录新的 SHA/镜像标签，
只修改需要更新的镜像配置。例如后端单独升级：

```bash
uh up -d --no-deps app
# 等待 app healthy 后刷新既有 web 的后端地址解析
uh exec -T web nginx -s reload
```

只有前端也变更时才执行 `uh up -d --no-deps web`。
不要整栈 `down`，不要重建数据库、无关 Bot 或共享设施。
app 重建后原执行线程及英语短期登录会话不会恢复；需要重新登录或按原范围重启任务。
定时重试也要纳入升级前检查，不能只看当前 running 数。

失败时先保留日志并确认故障；回滚到记录的旧 app/web 镜像，复用原配置、密钥和数据。
有数据库结构变更时须先判断向后兼容性，不能盲目降级，也不能为回滚自动覆盖数据库。
数据库恢复、迁移、Fernet 更换、公开端口调整均需相应的明确授权。
任何情况下都不要执行 `down -v`、全局 prune 或删除现有状态。

## 9. Agent 交付记录

完成时记录：目标项目、提交 SHA、app/web 实际镜像 ID 与启动时间、备份和回滚位置、
实际变更的服务、检查结果、未验证的平台/教材、是否调用付费接口、Git 提交与推送状态。
说明未落实的隔离，例如是否仍由宿主 root 管理。不得附上私有配置、认证材料或题库数据。

本手册是部署步骤，不是向任何既有服务器执行命令的授权。
旧的 [上游部署文档](DEPLOYMENT.md) 保留作历史参考；其中固定主机、脚本默认值及
上游自动更新命令不适用于本 fork 的所有部署。
