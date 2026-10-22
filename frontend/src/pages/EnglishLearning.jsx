import { useEffect, useRef, useState } from 'react'
import { api } from '../utils/api'

const field = 'w-full rounded-lg border border-border bg-surface p-3 text-text'
const button = 'rounded-lg border border-border bg-surface px-4 py-2 disabled:opacity-50'
const statuses = { running: '执行中', stopping: '停止中', cancelled: '已停止', failed: '失败', finished: '会话结束', needs_manual: '需人工处理' }

export default function EnglishLearning() {
  const [platform, setPlatform] = useState('welearn')
  const [credential, setCredential] = useState('')
  const [url, setUrl] = useState('')
  const [session, setSession] = useState(null)
  const [course, setCourse] = useState('')
  const [items, setItems] = useState([])
  const [item, setItem] = useState('')
  const [seconds, setSeconds] = useState(60)
  const [tasks, setTasks] = useState([])
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState('')
  const alive = useRef(true)
  const revision = useRef(0)

  useEffect(() => {
    alive.current = true
    let timer
    let active = true
    const poll = async () => {
      try {
        const result = await api('/english-learning/tasks')
        if (active) setTasks(result.tasks)
      } catch (error) {
        if (active) setMessage(error.message)
      } finally {
        if (active) timer = setTimeout(poll, 3000)
      }
    }
    poll()
    return () => { active = false; alive.current = false; clearTimeout(timer) }
  }, [])

  const perform = async (operation) => {
    setBusy(true)
    setMessage('')
    try { await operation() } catch (error) { if (alive.current) setMessage(error.message) }
    finally { if (alive.current) setBusy(false) }
  }

  const connect = (event) => {
    event.preventDefault()
    perform(async () => {
      const result = await api('/english-learning/sessions', {
        method: 'POST', timeoutMs: 125000, body: JSON.stringify({ platform, credential, course_url: url }),
      })
      if (!alive.current) return
      setSession(result)
      setCredential('')
      setCourse('')
      setMessage('连接成功。请选择课程并读取内容；会话最多保留一小时，重启后需要重新连接。')
    })
  }

  const readItems = () => perform(async () => {
    const version = ++revision.current
    setItems([])
    setItem('')
    const result = await api(`/english-learning/sessions/${session.session_id}/items`, {
      method: 'POST', timeoutMs: 125000, body: JSON.stringify({ course }),
    })
    if (!alive.current || version !== revision.current) return
    setItems(result.items)
    if (!result.items.length) setMessage('未发现可试用内容。U校园当前仅识别已开放的必修预览／阅读入口。')
  })

  const start = () => perform(async () => {
    const task = await api(`/english-learning/sessions/${session.session_id}/tasks`, {
      method: 'POST', body: JSON.stringify({ course, item, seconds: Number(seconds) }),
    })
    if (alive.current) setTasks((old) => [...old, task])
  })

  return (
    <div className="mx-auto max-w-3xl space-y-6">
      <div>
        <h1 className="text-2xl font-bold">英语平台试用</h1>
        <p className="mt-2 text-text-muted">WE Learn · 老版 U校园</p>
      </div>
      <div className="rounded-xl border border-border bg-surface p-4 text-sm leading-7">
        <p>当前支持限时学习会话。WE Learn 累计时长并保留原分数和完成状态；U校园打开所选阅读页面，遇到交互题目提示人工处理。</p>
        <p>本页暂不自动答题、提交作业或考试。会话结束表示本地流程结束，平台时长、进度和成绩请在官网核对。</p>
        <p>登录信息只在服务端短期会话中使用，不写入浏览器存储。请只导入目标平台的登录信息。</p>
      </div>
      {!session ? (
        <form onSubmit={connect} className="space-y-4 rounded-xl border border-border p-5">
          <label className="block">平台
            <select className={field} value={platform} disabled={busy} onChange={(e) => { setPlatform(e.target.value); setCredential(''); setUrl('') }}>
              <option value="welearn">WE Learn</option><option value="unipus">老版 U校园</option>
            </select>
          </label>
          {platform === 'unipus' && <>
            <p className="text-sm text-text-muted">需要后端安装 Playwright／Chromium。请先在本机浏览器完成登录和验证码，再导出仅含 unipus.cn 信息的 Playwright storage state（cookies 和 origins），粘贴到下方。普通 Cookie 字符串不足以恢复老版的浏览器登录状态。</p>
            <label className="block">课程目录网址
              <input className={field} value={url} onChange={(e) => setUrl(e.target.value)} required type="url" disabled={busy} placeholder="https://u.unipus.cn/user/student/mycourse/courseCatalog?..." />
            </label>
          </>}
          {platform === 'welearn' && <p className="text-sm text-text-muted">在官网登录后，从开发者工具 Network 中复制发往 welearn.sflep.com 的请求 Cookie。首次使用的小节需先在官网打开一次。</p>}
          <label className="block">{platform === 'welearn' ? 'Cookie' : 'Storage state JSON'}
            <textarea className={field} rows={5} value={credential} onChange={(e) => setCredential(e.target.value)} required disabled={busy} autoComplete="off" spellCheck={false} maxLength={100000} />
          </label>
          <button className={button} disabled={busy || !credential.trim()}>{busy ? '连接中…' : '连接并读取课程'}</button>
        </form>
      ) : (
        <div className="space-y-4 rounded-xl border border-border p-5">
          <label className="block">课程
            <select className={field} disabled={busy} value={course} onChange={(e) => { revision.current++; setCourse(e.target.value); setItems([]); setItem('') }}>
              <option value="">请选择课程</option>
              {session.courses.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
            </select>
          </label>
          <button className={button} disabled={busy || !course} onClick={readItems}>读取课程内容</button>
          <label className="block">本次内容
            <select className={field} value={item} disabled={busy} onChange={(e) => setItem(e.target.value)}>
              <option value="">请选择一项内容</option>
              {items.map((i) => <option key={i.id} value={i.id}>{i.name} {i.progress}</option>)}
            </select>
          </label>
          <label className="block">会话时长（秒，30–1800）
            <input className={field} type="number" min="30" max="1800" step="1" value={seconds} onChange={(e) => setSeconds(e.target.value)} disabled={busy} />
          </label>
          <p className="text-sm">将对上述所选课程的一项内容执行 {seconds || '—'} 秒会话，请核对后启动。</p>
          <div className="flex gap-3">
            <button className={button} disabled={busy || !course || !item || !Number.isInteger(Number(seconds)) || seconds < 30 || seconds > 1800} onClick={start}>确认并启动</button>
            <button className={button} disabled={busy} onClick={() => perform(async () => {
              await api(`/english-learning/sessions/${session.session_id}`, { method: 'DELETE' })
              if (alive.current) { setSession(null); setCourse(''); setItems([]); setItem('') }
            })}>断开会话</button>
          </div>
        </div>
      )}
      {message && <p role="status" className="rounded-lg border border-border p-3">{message}</p>}
      <section className="space-y-3" aria-label="英语平台任务">
        <h2 className="text-lg font-semibold">试用任务</h2>
        <p className="text-sm text-text-muted">本页任务与学习通控制台独立；记录保留两小时，不支持跨进程恢复或自动重启。</p>
        {tasks.length === 0 && <p>暂无任务</p>}
        {tasks.map((task) => (
          <article key={task.id} className="rounded-xl border border-border p-4">
            <p>{task.platform === 'welearn' ? 'WE Learn' : '老版 U校园'} · {task.course_name}</p>
            <p>{task.item_name} · {task.seconds} 秒 · {statuses[task.status] || task.status}</p>
            <p className="mt-2 text-sm text-text-muted">{task.message}</p>
            {['running', 'stopping'].includes(task.status) && <button className={`${button} mt-3`} disabled={busy || task.status === 'stopping'} onClick={() => perform(async () => {
              const result = await api(`/english-learning/tasks/${task.id}/stop`, { method: 'POST' })
              if (alive.current) setTasks((old) => old.map((t) => t.id === task.id ? result.task : t))
            })}>停止</button>}
          </article>
        ))}
      </section>
    </div>
  )
}
