# WE Learn 录音提交研究

核对日期：2026-10-29。本文记录公开源码与离线验证，不代表真实账号验收。

## 当前产品状态

已撤回火山 TTS 客户端、课节音频绑定和试听 CLI；没有保留语音服务配置入口。
保留十秒课件停留、AI 新题 20–40 秒随机间隔，以及录音分类、阻止跳过未完成录音、
已有录音的原生提交与目录完成回读修复。当前产品不自动填写虚构录音、分数或完成记录。

## 社区方案的实际行为

[WELearn-College-English-Complete](https://github.com/That-is-good/WELearn-College-English-Complete)
的 `solveRecordingTasksPersistent` 针对 `et-recorder`、`et-follow-me` 和 `et-talk`：
构造录音次数、虚构文件名和结果，调用 `etItem.handleStatusChange`，最后调用原生
`submit`。它尝试保存交互数据，不只是改网页样式；没有生成、上传或验证真实音频。

核对版本：`dacf16c04ac58ffc8ec7abacb1f563f1446c7e34`。
[相关源码](https://github.com/That-is-good/WELearn-College-English-Complete/blob/dacf16c04ac58ffc8ec7abacb1f563f1446c7e34/WELearnHelper.user.js#L2423-L2493)。
未复制该脚本到生产代码，也没有在账号会话中执行。

其他直接提交进度的项目，例如
[Welearn_helper](https://github.com/YZBRH/Welearn_helper/blob/main/welearn_accuracy.py)
和 [Auto_WeLearn](https://github.com/jhl337/Auto_WeLearn/blob/master/core/api.py)，
使用完成数据与空录音文件列表。它们不提供真实音频上传协议，也不能作为
“逐个录音控件已经适配”的证据。

## 与当前教材的对照

对照教材公开的
[主脚本](https://centercourseware.sflep.com/New%20Target%20College%20English_V2_Video%20Course%201/res/script/main.js)，
本轮使用已有匿名下载副本进行静态分析与断网验证。结论只覆盖该副本。

| 环节 | 教材原生实现 | 对社区方案的影响 |
| --- | --- | --- |
| 交互 ID | `etItem` 编译时建立 ID 与状态表，部分 ID 来自 `uuid` | 必须使用编译后的实际 ID；未知 ID 会在状态处理时失败 |
| 完成计算 | 根据计入进度的交互状态计算 `progressMeasure` | 改交互状态可改变客户端完成计算，但不能证明音频存在 |
| 保存队列 | 只有 `isDirty` 的交互进入待保存队列 | 只改完成状态会出现客户端进度改变、交互未入队的情况 |
| 普通录音 | 无朗读脚本时原生结果为 `neutral`；次数和文件名另存 | 社区脚本统一写入分数，与普通录音原生语义不同 |
| 跟读 | 原生结果是逐句二维分数数组的 JSON，另有逐句文件列表 | 社区脚本使用分隔的分数字符串；多句时无法按原格式解析，单句数字也不是预期数组 |
| 对话 | 原生结果是含 `attempts`、`allScores`、`htmls` 的 JSON 对象，文件列表带角色 | 社区脚本统一分数字符串缺少恢复所需字段，不能保证重开课节后正常 |
| 提交 | 原生提交写 SCORM 进度、排空交互队列并调用 `Commit` | 已调用提交不等于服务器持久化成功 |
| 界面回执 | 本教材提交处理未按 `commit()` 返回值阻止 `isSubmitted=true` | 按钮变化和本地“已提交”不能充当服务端验收 |

真实录音与回放另外调用 App 的 `cci.service.record` 扩展。完成状态链路与音频服务
不是同一条链路；这里尚未获得实际音频上传地址、授权参数或上传回执。

## 离线验证与结论范围

仅从公开教材脚本提取五个原生函数，在断网、无凭据的浏览器中注入模拟 SCORM，
核对以下五项：未完成状态、已知 ID 的完成计算、dirty 保存队列、未知 ID 拒绝，
以及 Commit 返回失败时仍出现已提交界面。五项均通过。
该验证没有模拟平台服务端接受规则，不是在线绕过成功证明。

因此，社区方法能影响这本教材的客户端完成检查，有继续研究的依据；
当前脚本不能直接作为稳定的全录音题实现。仍需区分提交回执、重新进入后的交互恢复、
目录/学习统计持久化，以及音频是否实际可回放。不得只观察当前页面就声称通过。

后续研究应限于用户指定的账号、课程和单个未完成录音课节，先记录原进度与交互，
保留已有文本答案，不套用全课程满分/批量完成数据。本文不提供默认启用的绕过模式，
本轮没有提交、重置或修改任何真实课节。
