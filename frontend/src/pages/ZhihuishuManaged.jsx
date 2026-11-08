import { useCallback, useEffect, useRef, useState } from 'react'
import { api } from '../utils/api'
import { groupVideosByChapter } from '../utils/zhihuishuTasks'

const BASE = '/course/zhihuishu/managed'
const CARD = 'rounded-2xl border border-border/30 bg-surface p-5 space-y-4'
const BUTTON = 'rounded-xl bg-primary px-4 py-2 text-white disabled:opacity-50'
const FIELD = 'rounded-xl border border-border/40 bg-surface p-2 text-text'
const ACTIVE = new Set(['queued', 'running', 'paused', 'cancelling'])
const STATUS = { queued: '排队中', waiting: '等待', starting: '启动中', running: '学习中', paused: '已暂停',
  cancelling: '正在停止', cancelled: '已停止', completed: '已完成', failed: '失败', interrupted: '已中断' }
const request = async (path, method = 'GET', body) => (await api(`${BASE}${path}`, {
  method, ...(body === undefined ? {} : { body: JSON.stringify(body) }), timeoutMs: 120000,
})).data

export default function ZhihuishuManaged() {
  const [accounts, setAccounts] = useState([])
  const [accountId, setAccountId] = useState('')
  const [courses, setCourses] = useState([])
  const [selected, setSelected] = useState([])
  const [chapters, setChapters] = useState({})
  const [ends, setEnds] = useState({})
  const [queues, setQueues] = useState([])
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [busy, setBusy] = useState(false)
  const [loadingCourses, setLoadingCourses] = useState(false)
  const [label, setLabel] = useState('')
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [loginTarget, setLoginTarget] = useState('')
  const [qr, setQr] = useState(null)
  const [speed, setSpeed] = useState('1')
  const [aiMode, setAiMode] = useState(false)
  const [autoAnswer, setAutoAnswer] = useState(true)
  const accountRef = useRef('')
  const generation = useRef(0)
  const mounted = useRef(true)
  const qrGeneration = useRef(0)
  const selectedAccount = accounts.find((a) => a.account_id === accountId)
  const activeQueue = queues.some((q) => q.account_id === accountId && ACTIVE.has(q.status))

  const reloadAccounts = useCallback(async () => {
    const rows = await request('/accounts')
    if (!mounted.current) return []
    setAccounts(rows)
    return rows
  }, [])

  const reloadQueues = useCallback(async () => {
    const rows = await request('/queues')
    if (mounted.current) setQueues(rows)
  }, [])

  const chooseAccount = useCallback((id) => {
    generation.current += 1
    accountRef.current = id
    setAccountId(id)
    setCourses([])
    setSelected([])
    setChapters({})
    setEnds({})
    setError('')
    setNotice('')
  }, [])

  useEffect(() => {
    mounted.current = true
    void (async () => {
      try {
        const rows = await reloadAccounts()
        if (mounted.current && !accountRef.current && rows[0]) chooseAccount(rows[0].account_id)
        await reloadQueues()
      } catch (err) {
        if (mounted.current) setError(err.message)
      }
    })()
    let inFlight = false
    const timer = setInterval(async () => {
      if (inFlight) return
      inFlight = true
      try { await reloadQueues() } catch (err) {
        if (mounted.current) setError(err.message)
      } finally { inFlight = false }
    }, 5000)
    return () => {
      mounted.current = false
      generation.current += 1
      qrGeneration.current += 1
      clearInterval(timer)
    }
  }, [chooseAccount, reloadAccounts, reloadQueues])

  const loadCourses = useCallback(async (id) => {
    const intent = ++generation.current
    setLoadingCourses(true)
    try {
      const rows = await request(`/accounts/${encodeURIComponent(id)}/courses`)
      if (!mounted.current || generation.current !== intent || accountRef.current !== id) return
      setCourses(rows)
      setSelected([])
      setError('')
    } catch (err) {
      if (mounted.current && generation.current === intent) setError(err.message)
    } finally {
      if (mounted.current && generation.current === intent) setLoadingCourses(false)
    }
  }, [])

  useEffect(() => {
    if (accountId) void loadCourses(accountId)
    else setLoadingCourses(false)
  }, [accountId, loadCourses])

  const loginSucceeded = useCallback(async (account) => {
    await reloadAccounts()
    if (!mounted.current) return
    const sameAccount = accountRef.current === account.account_id
    chooseAccount(account.account_id)
    // Updating the selected account does not change accountId, so refresh explicitly.
    if (sameAccount) void loadCourses(account.account_id)
    setPassword('')
    setUsername('')
    setQr(null)
    setLoginTarget('')
    setLabel('')
    setNotice('登录状态已加密保存。')
  }, [chooseAccount, loadCourses, reloadAccounts])

  const run = async (action) => {
    if (busy) return
    setBusy(true)
    setError('')
    try { await action() } catch (err) {
      if (mounted.current) setError(err.message)
    } finally { if (mounted.current) setBusy(false) }
  }

  const startQr = () => run(async () => {
    const intent = ++qrGeneration.current
    setQr(null)
    const value = await request('/login/qr', 'POST', { label, account_id: loginTarget || null })
    if (mounted.current && qrGeneration.current === intent) {
      if (value.status === 'failed') throw new Error(value.message || '获取二维码失败。')
      setQr(value)
    }
  })

  useEffect(() => {
    if (!qr || qr.status !== 'pending') return undefined
    const intent = qrGeneration.current
    let inFlight = false
    let stopped = false
    const timer = setInterval(async () => {
      if (inFlight || stopped) return
      inFlight = true
      try {
        const value = await request(`/login/qr/${encodeURIComponent(qr.session_id)}`)
        if (!mounted.current || qrGeneration.current !== intent || stopped) return
        if (value.status === 'success') {
          stopped = true
          clearInterval(timer)
          await loginSucceeded(value.account)
        } else if (value.status === 'failed') {
          stopped = true
          clearInterval(timer)
          setQr(value)
          setError(value.message || '扫码失败，请重新获取二维码。')
        } else setQr((current) => ({ ...current, qr_code: value.qr_code || current.qr_code }))
      } catch (err) {
        if (mounted.current && qrGeneration.current === intent) {
          stopped = true
          clearInterval(timer)
          setError(err.message)
        }
      } finally { inFlight = false }
    }, 2000)
    return () => { stopped = true; clearInterval(timer) }
  }, [qr?.session_id, qr?.status, loginSucceeded]) // eslint-disable-line react-hooks/exhaustive-deps

  const startQueue = () => run(async () => {
    const id = accountRef.current
    const intent = generation.current
    const chosen = courses.filter((c) => selected.includes(c.course_id))
    await request(`/accounts/${encodeURIComponent(id)}/queue`, 'POST', {
      courses: chosen.map((c) => ({ course_id: c.course_id, end_chapter_id: ends[c.course_id] || null })),
      speed: Number(speed), task_type: aiMode ? 'ai-course' : 'course', auto_answer: autoAnswer,
    })
    if (accountRef.current === id && generation.current === intent) {
      setSelected([])
      setNotice('所选课程已加入队列，按列表顺序依次执行。')
    }
    await reloadQueues()
  })

  const loadChapters = (courseId) => run(async () => {
    const id = accountRef.current
    const intent = generation.current
    const videos = await request(`/accounts/${encodeURIComponent(id)}/videos/${encodeURIComponent(courseId)}`)
    if (accountRef.current === id && generation.current === intent) {
      setChapters((prev) => ({ ...prev, [courseId]: groupVideosByChapter(videos) }))
    }
  })

  return <div className="space-y-6 text-text">
    <header><h1 className="text-2xl font-bold">智慧树学习助手</h1>
      <p className="mt-2 text-text/70">保存多个账号，勾选课程后按顺序学习。同一账号一次执行一个队列。</p></header>
    {error && <p role="alert" className="rounded-xl bg-red-500/10 p-3 text-danger">{error}</p>}
    {notice && <p role="status" className="rounded-xl bg-primary/10 p-3">{notice}</p>}
    <section className={CARD} aria-label="账号管理">
      <h2 className="text-lg font-semibold">已保存账号</h2>
      {accounts.length === 0 && <p>还没有保存的智慧树账号，请先登录一次。</p>}
      <div className="flex flex-wrap gap-2">{accounts.map((account) => <button key={account.account_id}
        type="button" aria-pressed={accountId === account.account_id} className={FIELD}
        disabled={busy} onClick={() => chooseAccount(account.account_id)}>
        {account.label}{account.status === 'needs_login' ? '（需更新登录）' : ''}
      </button>)}</div>
      {selectedAccount && <div className="flex flex-wrap gap-3">
        <button type="button" className={FIELD} disabled={busy || activeQueue} onClick={() => {
          qrGeneration.current += 1
          setQr(null)
          setLoginTarget(accountId)
          setLabel(selectedAccount.label)
        }}>更新所选账号登录</button>
        <button type="button" className={FIELD} disabled={busy} onClick={() => {
          const value = window.prompt('请输入账号备注', selectedAccount.label)
          if (!value?.trim()) return
          void run(async () => {
            await request(`/accounts/${accountId}`, 'PATCH', { label: value.trim() })
            await reloadAccounts()
          })
        }}>修改备注</button>
        <button type="button" className={FIELD} disabled={busy || activeQueue} onClick={() => {
          if (!window.confirm(`删除“${selectedAccount.label}”的登录状态？课程历史会保留。`)) return
          const id = accountId
          void run(async () => {
            await request(`/accounts/${id}`, 'DELETE')
            const rows = await reloadAccounts()
            if (accountRef.current === id) chooseAccount(rows[0]?.account_id || '')
          })
        }}>删除所选账号</button>
      </div>}
    </section>
    <details className={CARD} open={accounts.length === 0 || Boolean(loginTarget)}>
      <summary className="cursor-pointer font-semibold">{loginTarget ? '更新账号登录' : '新增智慧树账号'}</summary>
      <div className="flex flex-wrap items-center gap-3">
        <label>账号备注 <input className={FIELD} value={label} maxLength={64}
          onChange={(e) => setLabel(e.target.value)} /></label>
        {loginTarget && <button className={FIELD} type="button" onClick={() => {
          qrGeneration.current += 1; setQr(null); setLoginTarget(''); setLabel('')
        }}>改为新增账号</button>}
        <button className={BUTTON} type="button" disabled={busy} onClick={startQr}>获取登录二维码</button>
      </div>
      {qr?.qr_code && <img className="h-56 w-56" alt="智慧树登录二维码" src={`data:image/png;base64,${qr.qr_code}`} />}
      {qr?.status === 'pending' && <p>请使用智慧树 App 扫码。二维码刷新后以最新图片为准。</p>}
      <form className="flex flex-wrap items-end gap-3" onSubmit={(event) => {
        event.preventDefault()
        qrGeneration.current += 1
        setQr(null)
        void run(async () => {
          const account = await request('/login/password', 'POST', { username, password, label,
            account_id: loginTarget || null })
          await loginSucceeded(account)
        })
      }}>
        <label>智慧树账号 <input className={FIELD} autoComplete="username" value={username}
          onChange={(e) => setUsername(e.target.value)} required /></label>
        <label>密码 <input className={FIELD} type="password" autoComplete="current-password" value={password}
          onChange={(e) => setPassword(e.target.value)} required /></label>
        <button className={BUTTON} disabled={busy} type="submit">登录并保存</button>
      </form>
      <p className="text-sm text-text/70">登录状态加密保存在服务器，平台会话失效时仍需更新登录。旧版会话在本次升级后需重新登录一次。</p>
    </details>
    <section className={CARD} aria-label="课程选择">
      <div className="flex flex-wrap items-center gap-3"><h2 className="text-lg font-semibold">{selectedAccount?.label || '所选账号'}的课程</h2>
        <button className={FIELD} type="button" disabled={!accountId || busy || loadingCourses}
          onClick={() => void loadCourses(accountId)}>{loadingCourses ? '读取中…' : '刷新课程'}</button>
        <button className={FIELD} type="button" disabled={!courses.length || busy || activeQueue}
          onClick={() => setSelected(courses.map((c) => c.course_id))}>全选课程</button>
        <button className={FIELD} type="button" disabled={busy || activeQueue} onClick={() => setSelected([])}>取消全选</button>
      </div>
      {courses.map((course) => <div key={course.course_id} className="flex flex-wrap items-center gap-3 border-b border-border/20 py-2">
        <label className="flex flex-1 items-center gap-3"><input type="checkbox" checked={selected.includes(course.course_id)}
          disabled={busy || activeQueue} onChange={(event) => setSelected((prev) => event.target.checked
            ? [...prev, course.course_id] : prev.filter((id) => id !== course.course_id))} />
          {course.course_name} <span className="text-sm text-text/60">{course.progress || ''}</span></label>
        <button type="button" className={FIELD} disabled={busy || activeQueue} onClick={() => loadChapters(course.course_id)}>读取章节范围</button>
        <label>截止章节：{course.course_name}<select className={FIELD} aria-label={`${course.course_name}截止章节`}
          value={ends[course.course_id] || ''} disabled={busy || activeQueue}
          onChange={(e) => setEnds((prev) => ({ ...prev, [course.course_id]: e.target.value }))}>
          <option value="">整门课程</option>{(chapters[course.course_id] || []).map((chapter) =>
            <option key={chapter.id} value={chapter.id}>{chapter.title}</option>)}
        </select></label>
      </div>)}
      <div className="flex flex-wrap items-center gap-4">
        <label>倍速 <select className={FIELD} value={speed} disabled={busy} onChange={(e) => setSpeed(e.target.value)}>
          {[0.5, 1, 1.5, 2].map((v) => <option key={v} value={v}>{v} 倍</option>)}</select></label>
        <label><input type="checkbox" checked={aiMode} disabled={busy} onChange={(e) => setAiMode(e.target.checked)} /> AI模式</label>
        <label><input type="checkbox" checked={autoAnswer} disabled={busy || aiMode}
          onChange={(e) => setAutoAnswer(e.target.checked)} /> 自动生成答案</label>
        <button className={BUTTON} type="button" disabled={busy || !selected.length || activeQueue || loadingCourses}
          onClick={startQueue}>按顺序学习所选 {selected.length} 门课程</button>
      </div>
      <p className="text-sm text-text/70">每门课结束后才启动下一门。失败或取消会停止后续课程。当前AI仅能生成答案，尚不支持提交测验，不进入考试。</p>
    </section>
    <section className={CARD} aria-label="课程队列">
      <h2 className="text-lg font-semibold">全部账号的课程队列</h2>
      {!queues.length && <p>暂无课程队列。</p>}
      {queues.map((queue) => <article key={queue.task_id} className="rounded-xl border border-border/30 p-4 space-y-2">
        <h3 className="font-semibold">{queue.account_label} · {STATUS[queue.status] || queue.status}</h3>
        <p>{queue.message}</p>
        <ol className="list-decimal pl-5">{queue.courses.map((course) => <li key={course.course_id}>
          {course.course_name}：{STATUS[course.status] || course.status}
          {course.progress && <span> · {course.progress.completed}/{course.progress.total}，失败 {course.progress.failed}
            {course.progress.current_video ? ` · ${course.progress.current_video}` : ''}</span>}
        </li>)}</ol>
        {ACTIVE.has(queue.status) && <div className="flex gap-3">
          <button className={FIELD} type="button" disabled={busy || queue.status === 'cancelling'} onClick={() => void run(async () => {
            await request(`/queues/${queue.task_id}/${queue.status === 'paused' ? 'resume' : 'pause'}`, 'POST')
            await reloadQueues()
          })}>{queue.status === 'paused' ? '继续队列' : '暂停队列'}</button>
          <button className={FIELD} type="button" disabled={busy || queue.status === 'cancelling'} onClick={() => {
            if (!window.confirm('停止整个队列？后续课程不会启动。')) return
            void run(async () => { await request(`/queues/${queue.task_id}/cancel`, 'POST'); await reloadQueues() })
          }}>停止队列</button>
        </div>}
      </article>)}
      <p className="text-sm text-text/70">关闭网页不影响队列。服务重启后保留记录并标记中断，请重新勾选未完成课程；启动时会读取平台已有视频进度。</p>
    </section>
  </div>
}
