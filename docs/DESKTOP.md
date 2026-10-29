# 学道桌面版（本 fork）

面向个人电脑，Windows 10/11 x64 优先，同时保留 macOS Apple Silicon / Intel 和 Linux x64。
桌面程序包含当前工作台、Python 后端、SQLite 和英语试用需要的 Chromium，无需另外部署服务。
初次启动可能需要较长时间解包；界面就绪前请等候。课程平台与 AI 仍需要联网。

## 下载和开始使用

从 [本仓库 Releases](https://github.com/bzsjmz/university-helper/releases) 下载，
预览版本使用 `desktop-v…` 标签。只有构建通过并经维护者发布的版本才对外提供安装包；
源码已有桌面实现不等于已经有可下载的包。

| 电脑 | 安装包文件名结尾 |
| --- | --- |
| Windows x64 | `x86_64-pc-windows-msvc.exe`（推荐）或 `.msi` |
| macOS Apple Silicon | `aarch64-apple-darwin.dmg` |
| macOS Intel | `x86_64-apple-darwin.dmg` |
| Linux x64 | `x86_64-unknown-linux-gnu.AppImage` 或 `.deb` |

文件名统一为 `xuedao_<版本>_<目标系统>.<扩展名>`；同一 Release 附有 `SHA256SUMS`。
Windows 可用 PowerShell 的 `Get-FileHash 安装包路径 -Algorithm SHA256` 对照。
预览版未购买 Windows 代码签名、未做 macOS 公证；只在确认来源与校验值后按系统提示打开。
Windows `.exe` 使用当前用户安装；WebView2 缺失时安装器可能需要联网下载。
Linux 还需要系统的 WebKitGTK 4.1 / 桌面运行库及 Chromium 系统库；
AppImage 不能保证在所有发行版运行。Linux 不使用 root 账号启动浏览器，也不关闭沙箱。

1. 安装并打开学道，无需注册本地管理账号。
2. 在“学习通泛雅”添加自己的学习通账号并连接读取课程。
3. 选择课程及章节终点，预览确认后启动；批量模式同样需要逐账号明确范围。
4. 在工作台查看进度、暂停、停止和历史，终态任务可重新启动或删除记录。
5. 如需 AI，进入“桌面设置”，填写自己的公网接口、模型和优先 Key，备用 Key 可选。
   不知道模型名称时查询所选服务商，不默认承诺某个模型可用或费用。保存配置不会调用付费 API。

默认答题来源保持 `LocalCache → AI`。不需要 AI 时将任务来源改为仅本地缓存，
未命中的题目可能无法完成。私有题库不随安装包发布，不能假设初次安装具有服务器命中率。
更换接口地址会清除旧 Key，须同时填写新接口的 Key；空 Key 输入默认保留，勾选“清除”才删除。
新设置影响新建任务，已经初始化的请求可能继续使用原配置。

学习通签到、智慧树沿用已有实现；WE Learn / U校园 AI版 5.0 继续标为测试中，
不承诺全部真实教材兼容。验证码、本人录音或特殊平台要求保留人工处理。
课程作业/考试的范围、保存与提交语义同服务器版；默认仍只选章节学习。

## 本地数据和更新

数据存放在当前系统用户的专用目录，保留此前上游桌面版的数据路径：

| 系统 | 数据目录 |
| --- | --- |
| Windows | `%LOCALAPPDATA%\cornna\UniversityHelper\` |
| macOS | `~/Library/Application Support/UniversityHelper/` |
| Linux | `~/.local/share/UniversityHelper/`（遵循 XDG 配置） |

目录内包含 `local.db`（账号和任务）、`credential.key`（解密凭据必需）、
`secret_key`、缓存、AI 用量记录与桌面设置。AI Key 文件使用当前用户私有文件权限，
不应把整个目录发给别人。备份时先退出应用并复制整个目录；只复制数据库无法保证解密。
安装程序和业务数据分开保存，更新时不要删除数据目录或重新生成密钥。
本 fork 的桌面应用标识为 `io.github.bzsjmz.university-helper`，与上游分开；
避免同时打开上游和 fork 操作同一份本地数据。

本预览未配置自己的自动更新签名密钥，采用手动下载新包更新。
不会从上游安装更新，也不会迁入服务器上的账号或 API Key。
退出、休眠及更新会影响任务；线程不能跨进程恢复。
运行过的任务在重开后可按原范围重新启动，读取平台已完成进度，不能称为自动断点恢复。
不需要连接维护者服务器，也不包含被动 QQ 状态插件的宿主部署。

遇到启动失败，可查看 `desktop.log`：

- Windows：`%LOCALAPPDATA%\io.github.bzsjmz.university-helper\logs\`
- macOS：`~/Library/Logs/io.github.bzsjmz.university-helper/`
- Linux：`~/.local/share/io.github.bzsjmz.university-helper/logs/`

反馈时提供系统、安装包版本及脱敏错误信息，不附账号、Key 或整个数据目录。

## 维护者构建与发行

使用 `.github/workflows/desktop.yml`，手动指定版本或推送 `desktop-vX.Y.Z-beta.N` 标签。
工作流先验证共享工作台、SQLite、多账号、设置和 worker，再分别在四种原生 runner 上构建。
Windows 还执行真实管道轮询验证。PyInstaller 包含英语 JS 资源，浏览器独立随安装器分发。
打包冒烟检查仅使用临时数据、模拟账号和本地 HTML，不访问真实课程或调用付费 AI。
最终产出四个平台的安装包、校验值和 **GitHub Release 草稿**；不会自动公开发布。
确认安装体验后再发布草稿。失败的构建不得当作可下载版本交付。

此流程与服务器部署分开，不重建服务器容器、不更新数据库，
也不触发上游站点的前端自动发布。原 `release.yml` 的 `v*` 流程保留作上游参考；
本 fork 桌面预览必须使用 `desktop.yml`。正式自动更新要另行配置本 fork 自己的签名密钥与公钥，
不能借用上游公钥或宣称未签名预览包具备自动更新能力。
