import { useCallback, useEffect, useRef, useState } from 'react'
import { api } from '../utils/api'
import EnglishLogin from './EnglishLogin'

const field = 'w-full rounded-lg border border-border bg-surface p-3 text-text'
const button = 'rounded-lg border border-border bg-surface px-4 py-2 disabled:opacity-50'
const statuses = { running: '执行中', stopping: '停止中', cancelled: '已停止', failed: '失败', finished: '会话结束', needs_manual: '需人工处理' }

export default function EnglishLearning() {
  const [session, setSession] = useState(null)
  const [course, setCourse] = useState('')
  const [items, setItems] = useState([])
  const [item, setItem] = useState('')
  const [seconds, setSeconds] = useState(300)
  const [mode, setMode] = useState('answer')
  const [submit, setSubmit] = useState(false)
  const [allowAi, setAllowAi] = useState(true)
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

  const connected = useCallback((result) => {
    setSession(result)
    setCourse('')
    setMessage('登录成功。请选择课程并读取内容。')
  }, [])

  const readItems = () => perform(async () => {
    const version = ++revision.current
    setItems([])
    setItem('')
    const result = await api(`/english-learning/sessions/${session.session_id}/items`, {
      method: 'POST', timeoutMs: 125000, body: JSON.stringify({ course }),
    })
    if (!alive.current || version !== revision.current) return
    setItems(result.items)
    if (!result.items.length) setMessage('未发现可试用内容。U校园当前识别已开放的必修阅读、练习／作业入口。')
  })

  const start = () => perform(async () => {
    const task = await api(`/english-learning/sessions/${session.session_id}/tasks`, {
      method: 'POST', body: JSON.stringify({ course, item, seconds: Number(seconds), mode, submit: mode === 'answer' && submit, allow_ai: mode === 'answer' && allowAi }),
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
        <p>支持所选内容当前页的自动答题/作业保存，也可切换为限时学习。WE Learn 优先读取课件自带答案，其次查缓存，最后才按选项使用 AI；遇到缺失答案或不支持的题型会停止提交。</p>
        <p>自动提交须单独勾选；平台回执未确认时不会重试提交。验证码、监考、口语录音和无文本的听力需要人工处理，暂不自动考试或跨页遍历。</p>
        <p>使用平台账号密码登录，验证码在本页完成。登录会话短期保留，退出或服务重启后需要重新登录。</p>
      </div>
      {!session ? (
        <EnglishLogin onConnected={connected} />
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
          <label className="block">执行模式
            <select className={field} value={mode} disabled={busy} onChange={(e) => setMode(e.target.value)}>
              <option value="answer">自动答题／作业（当前页）</option>
              <option value="study">限时学习</option>
            </select>
          </label>
          {mode === 'answer' && <div className="space-y-2 text-sm">
            <p>默认尝试保存，最终状态以平台回执为准。</p>
            <label className="flex gap-2"><input type="checkbox" checked={allowAi} disabled={busy} onChange={(e) => setAllowAi(e.target.checked)} />课件与缓存均未命中时使用服务器 AI（可能产生费用）</label>
            <label className="flex gap-2"><input type="checkbox" checked={submit} disabled={busy} onChange={(e) => setSubmit(e.target.checked)} />整页答案完整并复核通过后，自动提交所选练习／作业</label>
          </div>}
          <label className="block">{mode === 'answer' ? '答题总预算' : '会话时长'}（秒，30–1800）
            <input className={field} type="number" min="30" max="1800" step="1" value={seconds} onChange={(e) => setSeconds(e.target.value)} disabled={busy} />
          </label>
          <p className="text-sm">将对上述所选课程的一项内容执行 {seconds || '—'} 秒{mode === 'answer' ? `答题（${submit ? '允许完整提交' : '仅尝试保存'}）` : '学习会话'}，请核对后启动。</p>
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
            <p>{task.item_name} · {task.mode === 'answer' ? '自动答题' : '限时学习'} · {task.seconds} 秒 · {statuses[task.status] || task.status}</p>
            <p className="mt-2 text-sm text-text-muted">{task.message}</p>
            {task.mode === 'answer' && <p className="text-sm">题目 {task.counts?.questions || 0} · 有答案 {task.counts?.answered || 0} · 课件答案 {task.counts?.native_answers || 0} · 缓存 {task.counts?.cache_hits || 0} · AI作答 {task.counts?.ai_answers || 0} · 已填 {task.counts?.filled || 0} · 缺失 {task.counts?.missing || 0} · 不支持 {task.counts?.unsupported || 0} · 已保存 {task.counts?.saved || 0} · 已提交 {task.counts?.submitted || 0} · 回执未确认 {task.counts?.submission_unknown || 0}</p>}
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
