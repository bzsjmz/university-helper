# WE Learn 语音准备与上传状态

核对日期：2026-10-29。

## 模型选择

建议使用豆包语音合成 2.0 的系统音色，资源 ID `seed-tts-2.0`。
默认音色为美式英语 Alberto：`en_male_alberto_uranus_bigtts`。
官方将其列为通用、教学场景音色；可更换为同版本、已开通的英语音色。
无需为了生成普通口语参考音频购买声音复刻或实时语音对话服务。

这条实现调用豆包语音服务的 HTTP 单向流式接口，鉴权使用该服务的
`X-Api-Key`，不复用项目文本答题 Key，也不假定方舟文本模型 Key 能通用。
它不是方舟文本模型的 `chat/completions` 请求。

官方来源：

- [语音合成接口、鉴权与结束事件](https://docs.volcengine.com/docs/doubao-voice/http-chunked-sse-unidirectional-streaming-v3?lang=zh)
- [2.0 音色列表](https://docs.volcengine.com/docs/DoubaoVoice/Tonelist-1?lang=zh)

## 已实现的范围

- 固定官方 HTTPS 地址、禁止重定向、不继承系统代理，不向任意自定义地址发送服务端 Key。
- 生成 24 kHz MP3，按 JSON 行解析音频片段；必须收到 `code=20000000` 结束事件，
  且得到非空、符合 MP3 文件头的字节，才采用结果。文件头校验不等于播放或时长验收。
- 单次合成预算最多 60 秒，保留协作停止检查；网络等待单阶段最多 10 秒。
  限制文本、单条响应、响应总量和音频大小；失败不自动重试，避免不确定情况下重复收费。
- 录音文件按账号范围、课程、课节、录音题原生 ID、文本哈希、题型、模型与音色绑定，
  不把一段音频当作所有课节的同一答案。绑定不代替平台授权或上传确认。
- 普通口语题需要明确提供对应回答；朗读评分题必须使用该控件的实际朗读文本。
  `et-talk` 对话、逐句跟读等其他控件不由此准备器推断处理。
- 浏览器只读区分普通录音和朗读评分，不把录音次数、分数或完成字段写成通过。
  未完成录音的题页不会因存在“下一页”按钮而被提前跳过。
  平台已经恢复有效录音的纯录音页可尝试原生提交，仍需原生回执。

## 尚未接通的部分

此教材 `etRecorder` 调用 App 原生 SCORM 扩展：
`cci.service.record.files.<index>.filename`、`cci.service.record.init`、
`cci.service.record.action`，评分题另取 `cci.service.record.result`。
公开课件没有给出接收 MP3 的 HTTP 上传路径、授权字段或上传回执结构。
非移动端录音按钮会要求转至 App。

普通录音前端在 `recordingCount > 0` 时标记交互完成，结果为 `neutral`；
带 `script` 的题目还读取评分。这个前端条件无法证明服务端接受任意音频，
也无法证明只说一句话就能完成所有课程。

**自动音频上传、App 录音文件绑定和真实账号平台完成验收尚未实现。**
正常课节任务不会因为配置了 TTS 就自动收费合成一批无法上传的音频。
目前操作入口用于显式生成参考试听文件；输出状态始终为 `prepared`，
`platform_completed` 始终为 `false`，不会显示为课节已完成。

要补齐上传，需要在获授权的官方 App 会话中核对一次成功录音的真实请求：
如何取得上传目标、文件编码、课节/录音题字段、成功回执，以及上传后平台如何读取录音。
不能猜测接口或仅改本地录音次数来代替这段过程。

## Agent 配置与操作

使用项目自己的 Python 环境，依赖仅为已有的 `requests`；不新增系统包。
将独立语音服务 API Key 放在本项目专用 Secret 文件中，权限 0600、仅运行用户可读。
容器部署时单文件只读挂载，不把整个凭据目录挂入容器。

设置：

```text
VOLCENGINE_TTS_API_KEY_FILE=/run/secrets/volcengine-tts-api-key
VOLCENGINE_TTS_SPEAKER=en_male_alberto_uranus_bigtts
```

不要把 Key 填进任务 JSON、前端、命令行参数或 Git。
这个 CLI 不会把 Key 传给浏览器，也没有新增公开音频生成 API。

准备一份项目私有任务文件（ID 必须取自目标账号/课程/课节和实际录音控件）：

```json
{
  "owner": "operator-scope",
  "course": "selected-course-id",
  "item": "selected-lesson-id",
  "detail": {
    "id": "actual-recorder-element-id",
    "kind": "recording",
    "scored": false,
    "script": "",
    "app_required": true
  },
  "answer": "A response prepared specifically for this speaking question."
}
```

从 backend 目录，使用该项目环境的 Python 执行。先不加 `--preview`，只核对任务结构，
不读取 Key、不创建音频目录、不调用服务：

```bash
python -m app.services.course.english.prepare_recording \
  --job /private/project/job.json --output-dir /private/project/audio
```

明确需要调用计费服务生成试听时，再加 `--preview`。必须先确认服务和音色已开通。
音频目录要求 0700，MP3 和绑定清单为 0600；清单不保存原文或 Key。
同一绑定的输出已存在时拒绝覆盖或重复合成，需先人工核对已有文件。
失败会清理本次预留文件。进程被强制终止可能留下预留空文件，应核对后清理，
不要把它们当作合成成功。

此命令会调用真实计费服务；离线测试使用模拟响应，不执行这条付费调用。
测试仍须使用部署手册规定的资源限制入口。
