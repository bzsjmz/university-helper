import { useCallback, useEffect, useRef, useState } from 'react'
import { api } from '../utils/api'
import { StatusBadge } from '../components'
import { CARD, getCourseId } from './chaoxing-fanya/utils'
import useTaskConfig from './chaoxing-fanya/hooks/useTaskConfig'
import ConfigSection from './chaoxing-fanya/components/ConfigSection'
import CourseListSection from './chaoxing-fanya/components/CourseListSection'
import TaskControlSection from './chaoxing-fanya/components/TaskControlSection'
import LogSection from './chaoxing-fanya/components/LogSection'

const ACTIVE = new Set(['pending', 'started', 'running', 'paused', 'cancelling', 'stopping'])

export default function ManagedChaoxing() {
  const [accounts, setAccounts] = useState([])
  const [accountId, setAccountId] = useState('')
  const [draft, setDraft] = useState({ label: '', username: '', password: '' })
  const [tasks, setTasks] = useState([])
  const [taskId, setTaskId] = useState('')
  const [courses, setCourses] = useState([])
  const [selectedCourses, setSelectedCourses] = useState([])
  const [chapters, setChapters] = useState({})
  const [expanded, setExpanded] = useState(new Set())
  const [chapterLimits, setChapterLimits] = useState({})
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [loading, setLoading] = useState(false)
  const [courseLoading, setCourseLoading] = useState(false)
  const [adding, setAdding] = useState(false)
  const [updatedAt, setUpdatedAt] = useState(null)
  const accountRef = useRef(accountId)
  const mounted = useRef(true)
  const config = useTaskConfig()
  const { setConcurrency } = config

  useEffect(() => { setConcurrency(1) }, [setConcurrency])
  useEffect(() => {
    mounted.current = true
    return () => { mounted.current = false }
  }, [])

  const refreshAccounts = useCallback(async () => {
    const response = await api('/learning-accounts')
    if (!mounted.current) return
    setAccounts(response.accounts || [])
    setAccountId(previous => previous || response.accounts?.[0]?.id || '')
  }, [])

  const refreshTasks = useCallback(async () => {
    const response = await api('/course/tasks')
    if (!mounted.current) return
    setTasks(response.data || response.tasks || [])
    setUpdatedAt(new Date())
  }, [])

  useEffect(() => {
    let active = true
    let busy = false
    const poll = async () => {
      if (busy) return
      busy = true
      try { await refreshTasks() } catch (err) {
        if (active) setError(err.message || '任务更新失败，保留上次进度')
      } finally { busy = false }
    }
    refreshAccounts().catch(err => { if (active) setError(err.message) })
    void poll()
    const timer = setInterval(() => { void poll() }, 5000)
    return () => { active = false; clearInterval(timer) }
  }, [refreshAccounts, refreshTasks])

  useEffect(() => {
    accountRef.current = accountId
    setCourses([])
    setSelectedCourses([])
    setChapters({})
    setExpanded(new Set())
    setChapterLimits({})
    setTaskId('')
    setError('')
  }, [accountId])

  const selectedAccount = accounts.find(account => account.id === accountId)
  const accountTasks = tasks.filter(task => task.account_id === accountId)
  const activeTask = accountTasks.find(task => ACTIVE.has(task.status))
  const selectedTask = tasks.find(task => task.task_id === taskId) || activeTask || accountTasks[0]
  const status = selectedTask?.status || ''

  const loadCourses = async () => {
    if (!accountId || courseLoading) return
    const requestedAccount = accountId
    setCourseLoading(true)
    setError('')
    try {
      const response = await api(`/learning-accounts/${requestedAccount}/courses`, { timeoutMs: 60000 })
      if (mounted.current && accountRef.current === requestedAccount) {
        const next = response.courses || []
        setCourses(next)
        const ids = new Set(next.map(getCourseId))
        setSelectedCourses(previous => previous.filter(id => ids.has(id)))
        setNotice(`已读取 ${next.length} 门课程`)
      }
    } catch (err) {
      if (mounted.current && accountRef.current === requestedAccount) setError(err.message)
    } finally { if (mounted.current) setCourseLoading(false) }
  }

  const addAccount = async event => {
    event.preventDefault()
    if (adding) return
    setAdding(true)
    setError('')
    try {
      const response = await api('/learning-accounts', { method: 'POST', body: JSON.stringify(draft) })
      setDraft({ label: '', username: '', password: '' })
      await refreshAccounts()
      setAccountId(response.account.id)
      setNotice('账号已保存，点击“连接并读取课程”验证登录')
    } catch (err) { setError(err.message) } finally { setAdding(false) }
  }

  const deleteAccount = async () => {
    if (!selectedAccount || activeTask || !window.confirm(`删除 ${selectedAccount.label}？历史任务记录会保留。`)) return
    try {
      await api(`/learning-accounts/${accountId}`, { method: 'DELETE' })
      setAccountId('')
      await refreshAccounts()
    } catch (err) { setError(err.message) }
  }

  const updatePassword = async event => {
    event.preventDefault()
    const password = new FormData(event.currentTarget).get('password')
    try {
      await api(`/learning-accounts/${accountId}`, { method: 'PATCH', body: JSON.stringify({ password }) })
      event.target.reset()
      setNotice('密码已更新，下次读取课程会重新登录')
    } catch (err) { setError(err.message) }
  }

  const toggleExpand = async course => {
    const id = getCourseId(course)
    if (expanded.has(id)) {
      setExpanded(previous => new Set([...previous].filter(value => value !== id)))
      return
    }
    const requestedAccount = accountId
    try {
      const response = await api(`/learning-accounts/${accountId}/chapters/${encodeURIComponent(id)}`, { timeoutMs: 60000 })
      if (accountRef.current !== requestedAccount || !mounted.current) return
      setChapters(previous => ({ ...previous, [id]: response.chapters || [] }))
      setExpanded(previous => new Set([...previous, id]))
    } catch (err) { if (accountRef.current === requestedAccount) setError(err.message) }
  }

  const startTask = async () => {
    if (!accountId || activeTask || !selectedCourses.length || loading) return
    setLoading(true)
    setError('')
    try {
      const response = await api(`/learning-accounts/${accountId}/tasks`, { method: 'POST', body: JSON.stringify({
        course_ids: selectedCourses, chapter_limits: chapterLimits,
        speed: config.speed, concurrency: config.concurrency, unopened_strategy: config.unopenedStrategy,
        tiku_config: { provider: config.tikuProvider.join(','), token: config.tikuToken,
          coverage_threshold: config.coverageThreshold, submit_mode: config.submitMode,
          judge_mapping: { correct: config.correctOptions.split(','), wrong: config.wrongOptions.split(',') } },
        notify_config: config.notifyService && config.notifyUrl ? { service: config.notifyService, url: config.notifyUrl } : {},
      }) })
      setTaskId(response.task_id)
      await refreshTasks()
      setNotice('任务已启动，可以切换到其他账号继续操作')
    } catch (err) { setError(err.message) } finally { setLoading(false) }
  }

  const controlTask = async action => {
    if (!selectedTask || loading) return
    setLoading(true)
    try {
      await api(`/course/task/${selectedTask.task_id}/${action}`, { method: 'POST' })
      await refreshTasks()
    } catch (err) { setError(err.message) } finally { setLoading(false) }
  }

  return <div className="space-y-6">
    <section className={CARD}>
      <h1 className="text-3xl font-bold text-text">学习通账号管理</h1>
      <p className="mt-2 text-sm text-text-muted">选择账号启动独立任务；切换账号不会停止其他任务。每个账号只运行一个任务。</p>
      {error && <p role="alert" className="mt-3 text-danger">{error}</p>}
      {notice && <p role="status" className="mt-3 text-success">{notice}</p>}
    </section>
    <section className={CARD}>
      <h2 className="mb-3 text-xl font-semibold">我的学习通账号</h2>
      <div className="flex flex-wrap gap-3">
        {accounts.map(account => <button key={account.id} type="button" aria-pressed={account.id === accountId}
          className={`rounded-xl border px-4 py-3 ${account.id === accountId ? 'border-primary bg-primary/10' : 'border-border'}`}
          onClick={() => setAccountId(account.id)}>{account.label} · {account.account_label}</button>)}
        {!accounts.length && <p className="text-text-muted">先添加一个学习通账号</p>}
      </div>
      <details className="mt-4"><summary className="cursor-pointer">添加学习通账号</summary>
        <form onSubmit={addAccount} className="mt-3 grid gap-3 sm:grid-cols-3">
          {[['label', '备注（可选）'], ['username', '学习通账号'], ['password', '学习通密码']].map(([key, label]) =>
            <label key={key} className="text-sm">{label}<input name={key} type={key === 'password' ? 'password' : 'text'}
              required={key !== 'label'} autoComplete={key === 'password' ? 'new-password' : 'off'}
              className="mt-1 w-full rounded-xl border border-border bg-surface px-3 py-2" value={draft[key]}
              onChange={event => setDraft(previous => ({ ...previous, [key]: event.target.value }))} /></label>)}
          <button disabled={adding} className="rounded-xl bg-primary px-4 py-2 text-white">{adding ? '保存中…' : '保存账号'}</button>
        </form>
      </details>
      {selectedAccount && <div className="mt-4 flex flex-wrap items-start gap-3">
        <button type="button" disabled={courseLoading} onClick={() => { void loadCourses() }} className="rounded-xl bg-primary px-4 py-2 text-white">{courseLoading ? '连接中…' : '连接并读取课程'}</button>
        <button type="button" disabled={Boolean(activeTask)} onClick={() => { void deleteAccount() }} className="rounded-xl border border-border px-4 py-2 disabled:opacity-40">删除账号</button>
        {!activeTask && <details><summary className="cursor-pointer py-2">更新密码</summary>
          <form onSubmit={updatePassword} className="flex gap-2"><input aria-label="新学习通密码" name="password" type="password" required autoComplete="new-password" className="rounded-xl border border-border bg-surface p-2" />
            <button className="rounded-xl bg-primary px-3 text-white">保存新密码</button></form>
        </details>}
      </div>}
    </section>
    <section className={CARD}>
      <h2 className="text-xl font-semibold">所有账号的任务</h2>
      <p className="my-2 text-xs text-text-muted">每 5 秒刷新 · {updatedAt ? `最近同步 ${updatedAt.toLocaleTimeString()}` : '正在读取'} · 数量为程序执行进度，平台任务点未核验</p>
      {!tasks.length && <p>暂无任务</p>}
      <div className="space-y-3">{tasks.map(task => <button key={task.task_id} type="button" onClick={() => setTaskId(task.task_id)}
        aria-pressed={selectedTask?.task_id === task.task_id} className="w-full rounded-xl border border-border p-3 text-left">
        <div className="flex justify-between gap-3"><strong>{task.account_name || '旧任务（账号未记录）'} {task.account_label || ''}</strong><StatusBadge status={task.status} /></div>
        <p className="mt-2 text-sm">{task.progress?.current_course || '等待课程信息'} · {task.progress?.current_chapter || task.current_task || '准备中'}</p>
        <p className="text-sm text-text-muted">课程 {task.progress?.completed || 0}/{task.progress?.total || 0} · 章节 {task.progress?.completed_chapters || 0}/{task.progress?.total_chapters || 0} · 失败 {task.progress?.failed || 0}</p>
      </button>)}</div>
    </section>
    {courses.length > 0 && <>
      <CourseListSection courses={courses} selectedCourses={selectedCourses} setSelectedCourses={setSelectedCourses}
        chapters={chapters} expanded={expanded} toggleExpand={toggleExpand} loadCourses={loadCourses}
        chapterLimits={chapterLimits} setChapterLimit={(id, value) => {
          setChapterLimits(previous => ({ ...previous, [id]: value }))
          if (value) setSelectedCourses(previous => previous.includes(id) ? previous : [...previous, id])
        }} />
      <ConfigSection {...config} />
      <button type="button" disabled={loading || Boolean(activeTask) || !selectedCourses.length} onClick={() => { void startTask() }}
        className="rounded-xl bg-primary px-6 py-3 text-white disabled:opacity-40">{activeTask ? '此账号已有任务，可切换其他账号' : '为所选账号启动任务'}</button>
    </>}
    {selectedTask && <>
      <p className="font-semibold">查看任务：{selectedTask.account_name || '旧任务'} {selectedTask.account_label || ''}</p>
      <TaskControlSection taskId={selectedTask.task_id} taskStatus={selectedTask} loading={loading}
        isRunning statusText={status} startTask={startTask} controlTask={controlTask}
        showStartButton={false} controlLoading={{ pause: loading, resume: loading, stop: loading }} />
      <LogSection logs={selectedTask.logs || []} />
    </>}
  </div>
}
