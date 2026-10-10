import { useCallback, useEffect, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { ArrowRight, BookOpen, CheckCircle, GraduationCap, RefreshCw, Trash2 } from 'lucide-react'
import { Card, StatusBadge } from '../components'
import { api } from '../utils/api'
import TaskControlSection from './chaoxing-fanya/components/TaskControlSection'
import LogSection from './chaoxing-fanya/components/LogSection'
import { normalizeCourseText, toNum } from './chaoxing-fanya/utils'

const ACTIVE = new Set(['running', 'pending', 'started', 'paused', 'cancelling', 'stopping'])
const FINISHED = new Set(['completed', 'cancelled', 'failed', 'error'])
const FAILED = new Set(['failed', 'error'])
const FILTERS = [['all', '全部'], ['active', '活动任务'], ['paused', '已暂停'], ['failed', '失败'], ['finished', '已结束']]
const time = value => {
  const date = new Date(value)
  return value && Number.isFinite(date.getTime()) ? date.toLocaleString('zh-CN', { hour12: false }) : '尚无更新时间'
}
const updated = task => new Date(task.updated_at || task.started_at || task.created_at || 0).getTime() || 0
const title = (task, accounts) => accounts.find(account => account.id === task.account_id)?.label || task.account_name || '旧任务（账号未记录）'

function Progress({ label, done, total }) {
  const completed = Math.max(0, toNum(done))
  const count = Math.max(0, toNum(total))
  const percent = count ? Math.min(100, completed / count * 100) : 0
  return <div>
    <div className="mb-1 flex justify-between gap-2 text-xs text-text-muted"><span>{label}</span><span>{completed}/{count || '—'}</span></div>
    <div role="progressbar" aria-label={label} aria-valuemin={0} aria-valuemax={100} aria-valuenow={percent}
      className="h-2 overflow-hidden rounded-full bg-surface-hover"><div className="h-full rounded-full bg-primary transition-all" style={{ width: `${percent}%` }} /></div>
  </div>
}

export default function TaskConsole() {
  const navigate = useNavigate()
  const [accounts, setAccounts] = useState([])
  const [tasks, setTasks] = useState([])
  const [accountId, setAccountId] = useState('all')
  const [filter, setFilter] = useState('all')
  const [search, setSearch] = useState('')
  const [selectedId, setSelectedId] = useState('')
  const [loading, setLoading] = useState(true)
  const [syncing, setSyncing] = useState(false)
  const [errors, setErrors] = useState({})
  const [actionError, setActionError] = useState('')
  const [notice, setNotice] = useState('')
  const [lastSync, setLastSync] = useState(null)
  const [pending, setPending] = useState({})
  const [deleteId, setDeleteId] = useState('')
  const [deletingId, setDeletingId] = useState('')
  const deletingRef = useRef('')
  const inFlight = useRef(null)
  const pendingRef = useRef(new Set())
  const generation = useRef(0)

  const refresh = useCallback(() => {
    if (inFlight.current) return inFlight.current
    const current = generation.current
    setSyncing(true)
    const request = Promise.allSettled([
      api('/learning-accounts', { timeoutMs: 10000 }),
      api('/course/tasks', { timeoutMs: 10000 }),
    ]).then(([accountResult, taskResult]) => {
      if (generation.current !== current) return
      const nextErrors = {}
      if (accountResult.status === 'fulfilled') setAccounts(accountResult.value.accounts || [])
      else nextErrors.accounts = '账号列表同步失败，保留上次数据'
      if (taskResult.status === 'fulfilled') {
        const nextTasks = taskResult.value.data || taskResult.value.tasks || []
        setTasks(nextTasks)
        setSelectedId(previous => previous || nextTasks.find(task => ACTIVE.has(task.status))?.task_id || nextTasks[0]?.task_id || '')
        setLastSync(new Date())
      } else nextErrors.tasks = '任务同步失败，当前显示上次进度'
      setErrors(nextErrors)
      setLoading(false)
    }).finally(() => {
      if (inFlight.current === request) inFlight.current = null
      if (generation.current === current) setSyncing(false)
    })
    inFlight.current = request
    return request
  }, [])

  useEffect(() => {
    generation.current += 1
    void refresh()
    const timer = setInterval(() => { void refresh() }, 5000)
    return () => { generation.current += 1; inFlight.current = null; clearInterval(timer) }
  }, [refresh])

  const sorted = [...tasks].sort((a, b) => Number(ACTIVE.has(b.status)) - Number(ACTIVE.has(a.status)) || updated(b) - updated(a))
  const visible = sorted.filter(task => {
    if (accountId !== 'all' && (accountId === 'legacy' ? Boolean(task.account_id) : task.account_id !== accountId)) return false
    if (filter === 'active' && !ACTIVE.has(task.status)) return false
    if (filter === 'paused' && task.status !== 'paused') return false
    if (filter === 'failed' && !FAILED.has(task.status)) return false
    if (filter === 'finished' && !FINISHED.has(task.status)) return false
    const text = [title(task, accounts), task.account_label, normalizeCourseText(task.progress?.current_course), task.task_id].join(' ').toLowerCase()
    return text.includes(search.trim().toLowerCase())
  })
  const selected = visible.find(task => task.task_id === selectedId) || visible[0]

  const control = async (task, action) => {
    if (errors.tasks || pendingRef.current.has(task.task_id)) return
    const current = generation.current
    pendingRef.current.add(task.task_id)
    setPending(previous => ({ ...previous, [task.task_id]: action }))
    setActionError('')
    try {
      await api(`/course/task/${encodeURIComponent(task.task_id)}/${action}`, { method: 'POST' })
      if (generation.current !== current) return
      setNotice(`${title(task, accounts)}：${{ pause: '暂停', resume: '继续', stop: '停止' }[action]}请求已发送`)
      // Drain a snapshot that began before the mutation, then query again.
      if (inFlight.current) await inFlight.current
      if (generation.current === current) await refresh()
    } catch (error) {
      if (generation.current === current) setActionError(error.message || '任务操作失败')
    } finally {
      pendingRef.current.delete(task.task_id)
      if (generation.current === current) setPending(previous => {
        const next = { ...previous }; delete next[task.task_id]; return next
      })
    }
  }

  const stats = [
    ['已保存账号', accounts.length],
    ['运行中', tasks.filter(task => ACTIVE.has(task.status) && task.status !== 'paused').length],
    ['已暂停', tasks.filter(task => task.status === 'paused').length],
    ['失败记录', tasks.filter(task => FAILED.has(task.status)).length],
  ]

  const deleteAccount = async account => {
    if (deletingRef.current || errors.tasks || errors.accounts || tasks.some(task => task.account_id === account.id && ACTIVE.has(task.status))) return
    const current = generation.current
    deletingRef.current = account.id
    setDeletingId(account.id)
    setActionError('')
    try {
      await api(`/learning-accounts/${encodeURIComponent(account.id)}`, { method: 'DELETE' })
      if (inFlight.current) await inFlight.current
      if (generation.current !== current) return
      setAccounts(previous => previous.filter(item => item.id !== account.id))
      setAccountId(previous => previous === account.id ? 'all' : previous)
      setDeleteId('')
      setNotice(`${account.label} 已删除，历史任务记录保留`)
      await refresh()
    } catch (error) {
      if (generation.current === current) setActionError(error.message || '账号删除失败，请重试')
    } finally {
      deletingRef.current = ''
      if (generation.current === current) setDeletingId('')
    }
  }

  return <div className="space-y-6">
    <header className="flex flex-wrap items-start justify-between gap-4">
      <div><h1 className="text-3xl font-black text-text">任务控制台</h1>
        <p className="mt-2 text-sm text-text-muted">集中查看所有学习通账号的任务，操作各自独立。</p></div>
      <div className="flex gap-2">
        <button type="button" disabled={syncing} onClick={() => { void refresh() }} className="flex items-center gap-2 rounded-xl border border-border px-4 py-2 disabled:opacity-50"><RefreshCw className={`h-4 w-4 ${syncing ? 'animate-spin' : ''}`} aria-hidden="true" />刷新</button>
        <button type="button" onClick={() => navigate('/chaoxing-fanya')} className="rounded-xl bg-primary px-4 py-2 font-semibold text-white">管理账号 / 启动任务</button>
      </div>
    </header>
    <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">{stats.map(([label, count]) => <Card key={label} padding="compact">
      <p className="text-sm text-text-muted">{label}</p><p className="mt-2 text-3xl font-bold">{loading ? '—' : count}</p>
    </Card>)}</div>
    <p role="status" className="text-xs text-text-muted">每 5 秒自动刷新 · {lastSync ? `最近同步 ${time(lastSync)}` : '尚未同步任务'} · 进度来自执行记录，未核验平台任务点</p>
    {(Object.keys(errors).length > 0 || actionError) && <div role="alert" className="rounded-xl border border-danger/30 bg-danger-surface p-3 text-sm text-danger">
      {Object.values(errors).map(message => <p key={message}>{message}</p>)}{actionError && <p>{actionError}</p>}
    </div>}
    {notice && <p role="status" className="text-sm text-success">{notice}</p>}
    <div className="grid items-start gap-5 xl:grid-cols-[240px_minmax(0,1fr)]">
      <Card as="aside" padding="compact" aria-label="账号筛选">
        <h2 className="mb-3 font-bold">学习通账号</h2>
        <div className="space-y-2">
          <button type="button" aria-pressed={accountId === 'all'} onClick={() => setAccountId('all')} className={`w-full rounded-xl border p-3 text-left ${accountId === 'all' ? 'border-primary bg-primary/10' : 'border-border'}`}>全部账号</button>
          {accounts.map(account => {
            const task = sorted.find(item => item.account_id === account.id && ACTIVE.has(item.status))
            const deletionBlocked = Boolean(task || errors.tasks || errors.accounts || deletingId)
            return <div key={account.id} className={`rounded-xl border p-3 ${accountId === account.id ? 'border-primary bg-primary/10' : 'border-border'}`}>
              <button type="button" aria-pressed={accountId === account.id} onClick={() => setAccountId(account.id)} className="w-full text-left">
              <span className="block font-semibold">{account.label}</span><span className="mt-1 block text-xs text-text-muted">{account.account_label}</span>
              <span className="mt-2 block"><StatusBadge status={task?.status || 'idle'} /></span>
              </button>
              <div className="mt-3 flex items-center justify-between gap-2">
                <span className="text-xs text-text-muted">{task ? '停止任务后可删除' : '可删除保存的账号'}</span>
                <button type="button" aria-label={`删除${account.label}`} disabled={deletionBlocked}
                  title={task ? '此账号有运行或暂停任务，请先停止任务' : '删除账号'}
                  onClick={() => { setDeleteId(account.id); setActionError('') }}
                  className="flex items-center gap-1 rounded-lg border border-danger/30 px-2 py-1 text-xs text-danger disabled:opacity-40"><Trash2 className="h-3 w-3" aria-hidden="true" />删除</button>
              </div>
              {deleteId === account.id && <div role="group" aria-label={`确认删除${account.label}`} className="mt-3 space-y-2 border-t border-border pt-3">
                <p className="text-xs">确认删除此账号？保存的登录信息会移除，历史任务记录保留。</p>
                <div className="flex gap-2">
                  <button type="button" disabled={deletionBlocked} onClick={() => { void deleteAccount(account) }} className="rounded-lg bg-danger px-3 py-2 text-xs text-white disabled:opacity-40">{deletingId === account.id ? '删除中…' : '确认删除'}</button>
                  <button type="button" disabled={Boolean(deletingId)} onClick={() => setDeleteId('')} className="rounded-lg border border-border px-3 py-2 text-xs">取消</button>
                </div>
              </div>}
            </div>
          })}
          {tasks.some(task => !task.account_id) && <button type="button" aria-pressed={accountId === 'legacy'} onClick={() => setAccountId('legacy')} className="w-full rounded-xl border border-border p-3 text-left">旧任务（账号未记录）</button>}
          {!loading && !accounts.length && !errors.accounts && <p className="text-sm text-text-muted">尚未保存账号，点击上方按钮添加。</p>}
        </div>
      </Card>
      <div className="min-w-0 space-y-4">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div role="group" aria-label="任务状态筛选" className="flex flex-wrap gap-2">{FILTERS.map(([value, label]) => <button key={value} type="button" aria-pressed={filter === value} onClick={() => setFilter(value)}
            className={`rounded-xl border px-3 py-2 text-sm ${filter === value ? 'border-primary bg-primary/10 text-primary' : 'border-border'}`}>{label}</button>)}</div>
          <input aria-label="搜索账号、课程或任务编号" type="search" value={search} onChange={event => setSearch(event.target.value)} placeholder="搜索账号 / 课程 / 任务" className="rounded-xl border border-border bg-surface px-3 py-2 text-sm" />
        </div>
        <h2 className="font-semibold">任务列表 · {visible.length}</h2>
        {loading && <Card>正在读取任务…</Card>}
        {!loading && !visible.length && <Card>{errors.tasks ? '暂时无法读取任务，请刷新重试。' : tasks.length ? '没有符合筛选条件的任务。' : '暂无任务。添加账号并启动课程后，会在这里显示。'}</Card>}
        <div className="grid gap-4 lg:grid-cols-2">{visible.map(task => {
          const progress = task.progress || {}
          const name = title(task, accounts)
          const busy = Boolean(pending[task.task_id]) || Boolean(errors.tasks)
          return <Card as="article" key={task.task_id} aria-label={`任务 ${name} ${task.task_id}`} className={selected?.task_id === task.task_id ? 'ring-1 ring-primary' : ''}>
            <div className="flex justify-between gap-3"><div className="min-w-0"><h3 className="truncate font-bold">{name}</h3><p className="text-xs text-text-muted">{task.account_label || '未记录登录账号'}</p></div><StatusBadge status={task.status} /></div>
            <p className="mt-4 truncate font-semibold">{normalizeCourseText(progress.current_course) || '等待课程信息'}</p>
            <p className="mt-1 truncate text-sm text-text-muted">{normalizeCourseText(progress.current_chapter) || task.current_task || '准备中'}</p>
            <div className="mt-4 space-y-3"><Progress label="课程进度" done={progress.completed} total={progress.total} /><Progress label="章节进度" done={progress.completed_chapters} total={progress.total_chapters} /></div>
            <p className="mt-3 text-xs text-text-muted">失败课程 {toNum(progress.failed)} · 更新 {time(task.updated_at)}</p>
            <p className="mt-1 truncate text-xs text-text-muted" title={task.task_id}>任务 {task.task_id}</p>
            <div className="mt-4 flex flex-wrap gap-2">
              <button type="button" onClick={() => setSelectedId(task.task_id)} className="rounded-lg bg-primary/10 px-3 py-2 text-sm text-primary">查看详情</button>
              <button type="button" disabled={busy || task.status !== 'running'} onClick={() => { void control(task, 'pause') }} className="rounded-lg border border-border px-3 py-2 text-sm disabled:opacity-40">暂停</button>
              <button type="button" disabled={busy || task.status !== 'paused'} onClick={() => { void control(task, 'resume') }} className="rounded-lg border border-border px-3 py-2 text-sm disabled:opacity-40">继续</button>
              <button type="button" disabled={busy || !ACTIVE.has(task.status) || ['cancelling', 'stopping'].includes(task.status)} onClick={() => {
                if (window.confirm(`停止 ${name} 的任务？`)) void control(task, 'stop')
              }} className="rounded-lg border border-danger/30 px-3 py-2 text-sm text-danger disabled:opacity-40">停止</button>
            </div>
          </Card>
        })}</div>
        {selected && <section aria-label="所选任务详情" className="space-y-3">
          <h2 className="text-xl font-bold">任务详情 · {title(selected, accounts)}</h2>
          {selected.message && <p className="text-sm text-text-muted">{selected.message}</p>}
          <TaskControlSection taskId={selected.task_id} taskStatus={selected} statusText={selected.status} showStartButton={false}
            controlTask={action => control(selected, action)} controlLoading={{ pause: Boolean(pending[selected.task_id] || errors.tasks), resume: Boolean(pending[selected.task_id] || errors.tasks), stop: Boolean(pending[selected.task_id] || errors.tasks) }} />
          <p className="text-xs text-text-muted">显示最近 100 条日志</p><LogSection logs={(selected.logs || []).slice(-100)} />
        </section>}
      </div>
    </div>
    <section aria-label="其他课程服务" className="flex flex-wrap gap-3">{[
      ['/chaoxing-fanya', '学习通账号管理', BookOpen], ['/chaoxing-signin', '学习通签到', CheckCircle], ['/zhihuishu-panel', '智慧树', GraduationCap],
    ].map(([path, label, Icon]) => <button key={path} type="button" onClick={() => navigate(path)} className="flex items-center gap-2 rounded-xl border border-border bg-surface px-4 py-3 text-sm"><Icon className="h-4 w-4" aria-hidden="true" />{label}<ArrowRight className="h-4 w-4" aria-hidden="true" /></button>)}</section>
  </div>
}
