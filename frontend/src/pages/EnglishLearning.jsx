import { useCallback, useEffect, useRef, useState } from 'react'
import { api } from '../utils/api'
import EnglishLogin from './EnglishLogin'

const field = 'w-full rounded-lg border border-border bg-surface p-3 text-text'
const button = 'rounded-lg border border-border bg-surface px-4 py-2 disabled:opacity-50'
const statuses = { running: '执行中', stopping: '停止中', cancelled: '已停止', failed: '失败', finished: '执行结束', needs_manual: '需人工处理' }

export default function EnglishLearning() {
  const [session, setSession] = useState(null)
  const [course, setCourse] = useState('')
  const [items, setItems] = useState([])
  const [item, setItem] = useState('')
  const [endItem, setEndItem] = useState('')
  const firstIndex = items.findIndex(i => i.id === item)
  const lastIndex = items.findIndex(i => i.id === endItem)
  const selectedItems = firstIndex >= 0 && lastIndex >= firstIndex ? items.slice(firstIndex, lastIndex + 1) : []
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
    setMessage(result.courses.length ? '登录成功。请选择课程并读取内容。' : '登录成功，但平台未返回可用课程。请在官网确认已开通教材或加入班级后重新登录。')
  }, [])

  const readItems = () => perform(async () => {
    const version = ++revision.current
    setItems([])
    setItem(''); setEndItem('')
    const result = await api(`/english-learning/sessions/${session.session_id}/items`, {
      method: 'POST', timeoutMs: 125000, body: JSON.stringify({ course }),
    })
    if (!alive.current || version !== revision.current) return
    setItems(result.items)
    if (!result.items.length) setMessage('未发现可试用内容。U校园当前识别已开放的必修阅读、练习／作业入口。')
  })

  const start = () => perform(async () => {
    const task = await api(`/english-learning/sessions/${session.session_id}/tasks`, {
      method: 'POST', body: JSON.stringify({ course, item, end_item: endItem, seconds: Number(seconds), mode, submit: mode === 'answer' && submit, allow_ai: mode === 'answer' && allowAi }),
    })
    if (alive.current) setTasks((old) => [...old, task])
  })

  return (
    <div className="mx-auto max-w-3xl space-y-6">
      <div>
        <h1 className="text-2xl font-bold">英语平台试用</h1>
        <p className="mt-2 text-text-muted">WE Learn · U校园 5.0（AI版）</p>
      </div>
      <div className="rounded-xl border border-border bg-surface p-4 text-sm leading-7">
        <p>支持所选范围内各内容当前页的自动答题/作业保存，也可切换为限时学习。WE Learn 优先读取课件自带答案，其次查缓存，最后才按选项使用 AI；遇到缺失答案或不支持的题型会停止提交。</p>
        <p>自动提交须单独勾选；平台回执未确认时不会重试提交。验证码、监考、口语录音和无文本的听力需要人工处理，暂不自动考试。</p>
        <p>使用平台账号密码登录，验证码在本页完成。登录会话短期保留，退出或服务重启后需要重新登录。</p>
      </div>
      {!session ? (
        <EnglishLogin onConnected={connected} />
      ) : (
        <div className="space-y-4 rounded-xl border border-border p-5">
          <label className="block">课程
            <select className={field} disabled={busy} value={course} onChange={(e) => { revision.current++; setCourse(e.target.value); setItems([]); setItem(''); setEndItem('') }}>
              <option value="">请选择课程</option>
              {session.courses.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
            </select>
          </label>
          <button className={button} disabled={busy || !course} onClick={readItems}>读取课程内容</button>
          <label className="block">范围起点
            <select className={field} value={item} disabled={busy} onChange={(e) => { setItem(e.target.value); if (!endItem || items.findIndex(i => i.id === endItem) < items.findIndex(i => i.id === e.target.value)) setEndItem(e.target.value) }}>
              <option value="">请选择起始内容</option>
              {items.map((i) => <option key={i.id} value={i.id}>{i.name} {i.progress}</option>)}
            </select>
          </label>
          <label className="block">范围终点
            <select className={field} value={endItem} disabled={busy || !item} onChange={(e) => setEndItem(e.target.value)}>
              <option value="">请选择结束内容</option>
              {items.map((i, index) => <option key={i.id} value={i.id} disabled={index < firstIndex}>{i.name} {i.progress}</option>)}
            </select>
          </label>
          <p className="text-sm">已选 {selectedItems.length} 节，将按目录顺序执行；起点和终点相同表示仅一节。</p>
          <label className="block">执行模式
            <select className={field} value={mode} disabled={busy} onChange={(e) => setMode(e.target.value)}>
              <option value="answer">自动答题／作业（所选范围）</option>
              <option value="study">限时学习</option>
            </select>
          </label>
          {mode === 'answer' && <div className="space-y-2 text-sm">
            <p>默认尝试保存，最终状态以平台回执为准。仅有 Submit 的课件需勾选自动提交；没有保存按钮时，仅填写不会保留或计为完成。</p>
            <label className="flex gap-2"><input type="checkbox" checked={allowAi} disabled={busy} onChange={(e) => setAllowAi(e.target.checked)} />课件与缓存均未命中时使用服务器 AI（可能产生费用）</label>
            <label className="flex gap-2"><input type="checkbox" checked={submit} disabled={busy} onChange={(e) => setSubmit(e.target.checked)} />整页答案完整并复核通过后，自动提交所选练习／作业</label>
          </div>}
          <label className="block">{mode === 'answer' ? '每节答题预算' : '每节会话时长'}（秒，30–1800）
            <input className={field} type="number" min="30" max="1800" step="1" value={seconds} onChange={(e) => setSeconds(e.target.value)} disabled={busy} />
          </label>
          <p className="text-sm">将对上述所选课程的 {selectedItems.length} 节内容，逐节执行 {seconds || '—'} 秒{mode === 'answer' ? `答题（${submit ? '允许完整提交' : '仅尝试保存'}）` : '学习会话'}，请核对后启动。</p>
          <div className="flex gap-3">
            <button className={button} disabled={busy || !course || !selectedItems.length || !Number.isInteger(Number(seconds)) || seconds < 30 || seconds > 1800} onClick={start}>确认并启动</button>
            <button className={button} disabled={busy} onClick={() => perform(async () => {
              await api(`/english-learning/sessions/${session.session_id}`, { method: 'DELETE' })
              if (alive.current) { setSession(null); setCourse(''); setItems([]); setItem(''); setEndItem('') }
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
            <p>{task.platform === 'welearn' ? 'WE Learn' : 'U校园 5.0（AI版）'} · {task.course_name}</p>
            <p>{task.item_name} · {task.mode === 'answer' ? '自动答题' : '限时学习'} · {task.total_items > 1 ? '每节 ' : ''}{task.seconds} 秒 · {statuses[task.status] || task.status}</p>
            <p className="mt-2 text-sm text-text-muted">{task.message}</p>
            {task.total_items > 1 && <p className="text-sm">进度 {task.completed_items}/{task.total_items} · 当前：{task.current_item}</p>}
            {task.total_items > 1 && task.results?.length > 0 && <details className="mt-2 text-sm"><summary>逐节结果</summary>{task.results.map((r, index) => <p key={index}>{r.name} · {statuses[r.status] || r.status} · {r.message}</p>)}</details>}
            {task.mode === 'answer' && <p className="text-sm">已访问 {task.counts?.visited || 0} · 平台确认完成 {task.counts?.platform_completed || 0} · 题目 {task.counts?.questions || 0} · 有答案 {task.counts?.answered || 0} · 课件答案 {task.counts?.native_answers || 0} · 缓存 {task.counts?.cache_hits || 0} · AI作答 {task.counts?.ai_answers || 0} · 已填 {task.counts?.filled || 0} · 缺失 {task.counts?.missing || 0} · 不支持 {task.counts?.unsupported || 0} · 已保存 {task.counts?.saved || 0} · 已提交 {task.counts?.submitted || 0} · 平台拒绝提交 {task.counts?.submission_rejected || 0} · 回执未确认 {task.counts?.submission_unknown || 0}</p>}
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
