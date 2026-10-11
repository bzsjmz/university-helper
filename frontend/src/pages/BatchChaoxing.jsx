import { useEffect, useRef, useState } from 'react'
import { api } from '../utils/api'
import { CARD, getChapterId, getChapterTitle, getCourseId, getCourseName } from './chaoxing-fanya/utils'
import { managedTaskPayload } from './chaoxing-fanya/managedTaskPayload'
import CourseListSection from './chaoxing-fanya/components/CourseListSection'

const ACTIVE = new Set(['pending', 'started', 'running', 'paused', 'cancelling', 'stopping'])
const PROVIDERS = { LocalCache: '本地缓存', AI: 'AI 智能答题', TikuGo: 'GO 题库', TikuYanxi: '言溪题库', TikuLike: 'Like 题库', TikuAdapter: '题库适配器', SiliconFlow: '硅基流动' }
const emptyRow = () => ({ courses: [], selected: [], limits: {}, chapters: {}, expanded: [], phase: '', error: '' })

export default function BatchChaoxing({ accounts, tasks, config, refreshTasks, onBusyChange }) {
  const [selected, setSelected] = useState([])
  const [rows, setRows] = useState({})
  const [busy, setBusy] = useState(false)
  const [review, setReview] = useState(null)
  const [notice, setNotice] = useState('')
  const working = useRef(false)
  const mounted = useRef(true)
  const tasksRef = useRef(tasks)
  tasksRef.current = tasks
  const settingsKey = JSON.stringify(managedTaskPayload(config, [], {}))

  useEffect(() => { setReview(null) }, [settingsKey])
  useEffect(() => { onBusyChange?.(busy) }, [busy, onBusyChange])
  useEffect(() => {
    mounted.current = true
    return () => { mounted.current = false }
  }, [])

  const hasActive = (id, list = tasksRef.current) => list.some(task => task.account_id === id && ACTIVE.has(task.status))
  const locked = id => hasActive(id) || ['started', 'uncertain'].includes(rows[id]?.phase)
  const patch = (id, change) => {
    if (mounted.current) setRows(previous => {
      const row = previous[id] || emptyRow()
      return { ...previous, [id]: { ...row, ...(typeof change === 'function' ? change(row) : change) } }
    })
  }
  const selectedAccounts = accounts.filter(account => selected.includes(account.id))
  const readyAccounts = selectedAccounts.filter(account => !locked(account.id) && rows[account.id]?.phase === 'ready'
    && rows[account.id].selected.length > 0 && rows[account.id].selected.length <= 100)

  const loadAccount = async id => {
    patch(id, { phase: 'loading', error: '' })
    try {
      const response = await api(`/learning-accounts/${id}/courses`, { timeoutMs: 60000 })
      const courses = response.courses || []
      const ids = new Set(courses.map(getCourseId).filter(Boolean))
      patch(id, previous => ({
        phase: 'ready', courses, selected: previous.selected.filter(value => ids.has(value)),
        limits: Object.fromEntries(Object.entries(previous.limits).filter(([id]) => ids.has(id))),
        chapters: Object.fromEntries(Object.entries(previous.chapters).filter(([id]) => ids.has(id))),
        expanded: [], error: '',
      }))
    } catch (error) { patch(id, { phase: 'error', error: error.message || '课程读取失败' }) }
  }

  const loadSelected = async () => {
    if (working.current) return
    working.current = true; setBusy(true); setReview(null); setNotice('')
    try {
      for (const account of selectedAccounts) {
        if (!mounted.current) break
        if (!locked(account.id)) await loadAccount(account.id)
      }
    } finally { working.current = false; if (mounted.current) setBusy(false) }
  }

  const refreshAccount = async id => {
    if (working.current) return
    working.current = true; setBusy(true); setReview(null)
    try { await loadAccount(id) }
    finally { working.current = false; if (mounted.current) setBusy(false) }
  }

  const expand = async (id, course) => {
    if (working.current) return
    const selector = getCourseId(course)
    if (rows[id]?.expanded.includes(selector)) {
      patch(id, row => ({ expanded: row.expanded.filter(value => value !== selector) }))
      return
    }
    working.current = true; setBusy(true)
    try {
      const response = await api(`/learning-accounts/${id}/chapters/${encodeURIComponent(selector)}`, { timeoutMs: 60000 })
      patch(id, row => ({ chapters: { ...row.chapters, [selector]: response.chapters || [] }, expanded: [...row.expanded, selector], error: '' }))
    } catch (error) { patch(id, { error: error.message || '章节读取失败' }) }
    finally { working.current = false; if (mounted.current) setBusy(false) }
  }

  const preview = () => {
    setNotice('')
    setReview(readyAccounts.map(account => {
      const row = rows[account.id]
      const valid = new Set(row.courses.map(getCourseId).filter(Boolean))
      const ids = row.selected.filter(id => valid.has(id))
      const limits = Object.fromEntries(Object.entries(row.limits).filter(([id, value]) => ids.includes(id) && value))
      return {
        account, payload: managedTaskPayload(config, ids, limits),
        courses: row.courses.filter(course => ids.includes(getCourseId(course))).map(course => {
          const id = getCourseId(course)
          const chapter = (row.chapters[id] || []).find(item => getChapterId(item) === limits[id])
          return { name: getCourseName(course), limit: limits[id] ? `学到 ${chapter ? getChapterTitle(chapter) : limits[id]}（含）` : '全部章节' }
        }),
      }
    }).filter(item => item.payload.course_ids.length > 0))
  }

  const startReviewed = async () => {
    if (working.current || !review?.length) return
    working.current = true; setBusy(true); setNotice('')
    const plan = review
    let started = 0, skipped = 0, failed = 0
    try {
      // Read current activity again after the user has reviewed the course scope.
      const response = await api('/course/tasks')
      const freshTasks = response.data || response.tasks || []
      for (const item of plan) {
        if (!mounted.current) break
        const id = item.account.id
        if (hasActive(id, freshTasks) || hasActive(id)) {
          skipped += 1; patch(id, { error: '此账号已有活动任务，已跳过' }); continue
        }
        patch(id, { phase: 'starting', error: '' })
        try {
          const result = await api(`/learning-accounts/${id}/tasks`, { method: 'POST', body: JSON.stringify(item.payload), timeoutMs: 30000 })
          if (!result.task_id) throw new Error('启动结果不明确')
          started += 1; patch(id, { phase: 'started', taskId: result.task_id, error: '' })
        } catch (error) {
          failed += 1
          const uncertain = !error.status || error.status >= 500
          patch(id, { phase: uncertain ? 'uncertain' : 'ready', error: uncertain
            ? '启动结果未确认。请在任务列表核对后再操作，避免重复启动。' : error.message })
        }
      }
      setReview(null)
      setNotice(`启动请求成功 ${started} 个，跳过 ${skipped} 个，失败或待核对 ${failed} 个。执行进度请看任务列表。`)
      try { await refreshTasks() } catch { setNotice(previous => `${previous} 任务列表同步失败，请稍后刷新。`) }
    } catch (error) { if (mounted.current) setNotice(`任务状态核对失败，未发送启动请求：${error.message}`) }
    finally { working.current = false; if (mounted.current) setBusy(false) }
  }

  return <section className={CARD} aria-label="批量课程预览和启动">
    <h2 className="text-xl font-semibold">批量课程预览和启动</h2>
    <p className="mt-2 text-sm text-text-muted">勾选账号 → 一次读取课程 → 逐人选择课程和章节终点 → 预览清单并确认启动。课程不会自动全选。</p>
    <p className="mt-1 text-xs text-text-muted">本次选择保留在当前页面；离开页面后需重新读取。活动账号会跳过，暂停也算活动任务。</p>
    {notice && <p role="status" className="my-3">{notice}</p>}
    <fieldset disabled={busy || Boolean(review)} className="mt-4 space-y-4 disabled:opacity-70">
      <div className="flex flex-wrap gap-3">
        {accounts.map(account => <label key={account.id} className="flex items-center gap-2 rounded-xl border border-border px-3 py-2">
          <input type="checkbox" checked={selected.includes(account.id)} disabled={locked(account.id)}
            onChange={() => setSelected(previous => previous.includes(account.id) ? previous.filter(id => id !== account.id) : [...previous, account.id])} />
          {account.label} · {account.account_label}{locked(account.id) ? '（已有任务或待核对）' : ''}
        </label>)}
      </div>
      <div className="flex flex-wrap gap-3">
        <button type="button" className="rounded-xl border border-border px-4 py-2" onClick={() => setSelected(accounts.filter(account => !locked(account.id)).map(account => account.id))}>勾选全部可用账号</button>
        <button type="button" className="rounded-xl border border-border px-4 py-2" onClick={() => setSelected([])}>清空账号选择</button>
        <button type="button" disabled={!selectedAccounts.length} className="rounded-xl bg-primary px-4 py-2 text-white disabled:opacity-40" onClick={() => void loadSelected()}>连接并读取所选账号课程</button>
      </div>
      {selectedAccounts.map(account => {
        const row = rows[account.id] || emptyRow()
        return <article key={account.id} aria-label={`${account.label}的批量课程`} className="rounded-xl border border-border p-3">
          <h3 className="text-lg font-semibold">{account.label} · {account.account_label}</h3>
          {row.phase === 'loading' && <p role="status">连接中…</p>}
          {row.error && <p role="alert" className="mt-2 text-danger">{row.error}</p>}
          {row.phase === 'started' && <p className="text-success">启动请求成功，请查看任务进度。</p>}
          {row.phase === 'ready' && !row.courses.length && <p>暂无课程</p>}
          {row.selected.length > 100 && <p role="alert">每个账号最多选择 100 门课程</p>}
          {row.courses.length > 0 && <fieldset disabled={locked(account.id)}>
            <CourseListSection scope={account.id} courses={row.courses} selectedCourses={row.selected}
              setSelectedCourses={value => patch(account.id, current => ({ selected: typeof value === 'function' ? value(current.selected) : value }))}
              chapters={row.chapters} expanded={new Set(row.expanded)} toggleExpand={course => expand(account.id, course)}
              loadCourses={() => refreshAccount(account.id)} chapterLimits={row.limits}
              setChapterLimit={(id, value) => patch(account.id, current => ({
                limits: { ...current.limits, [id]: value },
                selected: value && !current.selected.includes(id) ? [...current.selected, id] : current.selected,
              }))} />
          </fieldset>}
        </article>
      })}
      <button type="button" disabled={!readyAccounts.length} onClick={preview}
        className="rounded-xl bg-primary px-5 py-3 text-white disabled:opacity-40">预览启动清单（{readyAccounts.length} 个账号）</button>
    </fieldset>
    {busy && <p role="status" className="mt-3">正在逐个处理，请等待当前操作完成…</p>}
    {review && <div className="mt-5 rounded-xl border border-primary p-4" aria-label="启动确认清单">
      <h3 className="font-semibold">确认本次课程范围</h3>
      <p className="my-2 text-sm">{review[0]?.payload.speed} 倍速 · 每账号 {review[0]?.payload.concurrency} 章节并发 · {review[0]?.payload.tiku_config.provider.split(',').filter(Boolean).map(name => PROVIDERS[name] || name).join(' → ') || '未启用题库'}</p>
      <p className="mb-3 text-xs text-text-muted">本地缓存始终优先查询。缓存未命中时，AI 作答可能产生费用。</p>
      <ul className="space-y-3">{review.map(item => <li key={item.account.id}>
        <strong>{item.account.label} · {item.account.account_label}</strong>
        <ul className="ml-4 list-disc">{item.courses.map((course, index) => <li key={index}>{course.name} · {course.limit}</li>)}</ul>
      </li>)}</ul>
      <div className="mt-4 flex gap-3">
        <button type="button" disabled={busy} onClick={() => setReview(null)} className="rounded-xl border border-border px-4 py-2">返回修改</button>
        <button type="button" disabled={busy} onClick={() => void startReviewed()} className="rounded-xl bg-primary px-4 py-2 text-white disabled:opacity-40">确认启动 {review.length} 个账号</button>
      </div>
    </div>}
  </section>
}
