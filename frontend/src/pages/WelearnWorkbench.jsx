import { useCallback, useEffect, useRef, useState } from 'react'
import { BookOpen, CheckCircle, Users } from 'lucide-react'
import { api } from '../utils/api'
import EnglishLogin from './EnglishLogin'

const button = 'min-h-[40px] rounded-xl border border-border bg-surface px-4 py-2 text-sm font-semibold hover:bg-surface-hover disabled:opacity-50'
const primary = `${button.replace('bg-surface', 'bg-primary').replace('hover:bg-surface-hover', 'hover:opacity-90')} text-white`
const field = 'w-full rounded-xl border border-border bg-surface px-3 py-2 text-text'
const active = (task) => ['running', 'stopping'].includes(task.status)
const states = { running: '执行中', stopping: '停止中', finished: '执行结束', needs_manual: '需人工处理', failed: '失败', cancelled: '已停止', interrupted: '重启中断' }

function AccountPanel({ account, checked, onChecked, onPlan, onLogin, onRefresh, perform, blocked }) {
  const [courses, setCourses] = useState([])
  const [course, setCourse] = useState('')
  const [catalogs, setCatalogs] = useState({})
  const [selected, setSelected] = useState({})
  const [loading, setLoading] = useState(false)
  const [label, setLabel] = useState(account.label)
  const version = useRef(0)
  const busy = blocked || loading || account.busy
  const count = Object.values(selected).reduce((n, ids) => n + ids.length, 0)

  useEffect(() => {
    onPlan(account.id, Object.entries(selected).filter(([, ids]) => ids.length).map(([cid, ids]) => ({ course: cid, items: ids })))
  }, [selected, account.id, onPlan])
  useEffect(() => () => { version.current++ }, [])
  useEffect(() => {
    // Reauthentication invalidates server-side catalogs; discard stale selections too.
    version.current++
    setCourses([]); setCourse(''); setCatalogs({}); setSelected({}); setLabel(account.label)
  }, [account.updated_at, account.label])

  const load = async (operation) => {
    setLoading(true)
    try { await perform(operation) } finally { setLoading(false) }
  }
  const readCourses = () => load(async () => {
    const revision = version.current
    const result = await api(`/welearn/accounts/${account.id}/courses`, { timeoutMs: 125000 })
    if (revision !== version.current) return
    setCourses(result.courses)
    setSelected(old => Object.fromEntries(Object.entries(old).filter(([id]) => result.courses.some(c => c.id === id))))
    setCourse(old => result.courses.some(c => c.id === old) ? old : result.courses.length === 1 ? result.courses[0].id : '')
  })
  const readItems = () => load(async () => {
    const revision = version.current
    const result = await api(`/welearn/accounts/${account.id}/items`, { method: 'POST', timeoutMs: 125000, body: JSON.stringify({ course }) })
    if (revision !== version.current) return
    setCatalogs(old => ({ ...old, [course]: result.items }))
    setSelected(old => ({ ...old, [course]: (old[course] || []).filter(id => result.items.some(i => i.id === id)) }))
  })
  const allCourses = () => load(async () => {
    const revision = version.current
    const result = await api(`/welearn/accounts/${account.id}/courses`, { timeoutMs: 125000 })
    const next = {}
    for (const c of result.courses) {
      const data = await api(`/welearn/accounts/${account.id}/items`, { method: 'POST', timeoutMs: 125000, body: JSON.stringify({ course: c.id }) })
      if (revision !== version.current) return
      next[c.id] = data.items
    }
    if (revision !== version.current) return
    setCourses(result.courses); setCatalogs(next)
    setCourse(result.courses[0]?.id || '')
    setSelected(Object.fromEntries(Object.entries(next).map(([id, items]) => [id, items.map(i => i.id)])))
  })
  const items = catalogs[course] || []
  const changeItems = (ids) => setSelected(old => ({ ...old, [course]: ids }))

  return <article className="space-y-4 rounded-2xl border border-border bg-surface p-5" aria-label={`账号 ${account.label}`}>
    <div className="flex flex-wrap items-center gap-3">
      <label className="flex flex-1 items-center gap-3 font-semibold"><input type="checkbox" checked={checked} disabled={busy} onChange={e => onChecked(account.id, e.target.checked)} />{account.label}</label>
      <span className="text-xs text-text-muted">{account.busy ? '账号忙碌' : '登录已保存'}</span>
      <button className={button} disabled={busy} onClick={() => onLogin(account)}>更新登录</button>
    </div>
    <div className="flex flex-wrap gap-2">
      <button className={button} disabled={busy} onClick={readCourses}>读取课程</button>
      <button className={button} disabled={busy} onClick={allCourses}>全选全部课程与课节</button>
      <button className={button} disabled={busy || !count} onClick={() => setSelected({})}>清空选择</button>
    </div>
    {loading && <p role="status" className="text-sm text-text-muted">正在读取课程目录…</p>}
    {courses.length > 0 && <>
      <label className="block text-sm">课程
        <select className={`${field} mt-1`} value={course} disabled={busy} onChange={e => setCourse(e.target.value)}>
          <option value="">请选择课程</option>{courses.map(c => <option key={c.id} value={c.id}>{c.name}（已选 {selected[c.id]?.length || 0} 节）</option>)}
        </select>
      </label>
      <button className={button} disabled={busy || !course} onClick={readItems}>读取／刷新课节</button>
    </>}
    {catalogs[course] && <div className="space-y-3">
      <div className="flex flex-wrap gap-2">
        <button className={button} disabled={busy || !items.length} onClick={() => changeItems(items.map(i => i.id))}>全选本课程课节</button>
        <button className={button} disabled={busy} onClick={() => changeItems([])}>取消本课程选择</button>
      </div>
      <div className="max-h-64 space-y-1 overflow-y-auto rounded-xl border border-border p-2">
        {!items.length && <p className="p-2 text-sm">该课程暂未开放课节</p>}
        {items.map(item => <label key={item.id} className="flex items-start gap-2 rounded-lg p-2 text-sm hover:bg-surface-hover">
          <input type="checkbox" className="mt-1" checked={(selected[course] || []).includes(item.id)} disabled={busy} onChange={e => changeItems(e.target.checked ? [...(selected[course] || []), item.id] : (selected[course] || []).filter(id => id !== item.id))} />
          <span>{item.name}{['true', '1', '已完成', 'completed'].includes(String(item.progress).toLowerCase()) && <span className="ml-2 text-success">已完成，将跳过</span>}</span>
        </label>)}
      </div>
    </div>}
    <p className="text-sm font-semibold">已选 {Object.values(selected).filter(ids => ids.length).length} 门课程 · {count} 节</p>
    {courses.filter(c => selected[c.id]?.length).map(c => <p key={c.id} className="text-xs text-text-muted">{c.name} · {selected[c.id].length} 节</p>)}
    <details className="text-sm"><summary className="cursor-pointer text-text-muted">账号管理</summary>
      <form className="mt-3 flex flex-wrap gap-2" onSubmit={event => { event.preventDefault(); load(async () => {
        await api(`/welearn/accounts/${account.id}`, { method: 'PATCH', body: JSON.stringify({ label: label.trim() }) }); await onRefresh()
      }) }}>
        <label className="flex-1">显示名称<input className={field} value={label} maxLength={128} required disabled={busy} onChange={e => setLabel(e.target.value)} /></label>
        <button className={button} disabled={busy || !label.trim()}>保存名称</button>
      </form>
      <button className={`${button} mt-3 text-danger`} disabled={busy} onClick={() => {
        if (window.confirm(`删除 ${account.label} 的已保存登录？任务历史会保留。`)) load(async () => {
          await api(`/welearn/accounts/${account.id}`, { method: 'DELETE' }); await onRefresh()
        })
      }}>删除账号</button>
    </details>
  </article>
}

export default function WelearnWorkbench() {
  const [accounts, setAccounts] = useState([])
  const [tasks, setTasks] = useState([])
  const [selected, setSelected] = useState([])
  const [plans, setPlans] = useState({})
  const [loginTarget, setLoginTarget] = useState(null)
  const [label, setLabel] = useState('')
  const [session, setSession] = useState(null)
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState('')
  const [submit, setSubmit] = useState(true)
  const [allowAi, setAllowAi] = useState(true)
  const [recording, setRecording] = useState(false)
  const [seconds, setSeconds] = useState(300)
  const alive = useRef(true)
  const polling = useRef(false)
  const refresh = useCallback(async () => {
    if (polling.current) return
    polling.current = true
    try {
      const [a, t] = await Promise.all([api('/welearn/accounts'), api('/welearn/tasks')])
      if (alive.current) { setAccounts(a.accounts); setTasks(t.tasks) }
    } finally { polling.current = false }
  }, [])
  useEffect(() => {
    alive.current = true
    let timer, active = true
    const poll = async () => {
      try { await refresh() } catch (error) { if (active) setMessage(error.message) }
      if (active) timer = setTimeout(poll, 3000)
    }
    poll()
    return () => { active = false; alive.current = false; clearTimeout(timer) }
  }, [refresh])
  const perform = useCallback(async (operation) => {
    setMessage('')
    try { return await operation() } catch (error) { if (alive.current) setMessage(error.message) }
  }, [])
  const plan = useCallback((aid, selections) => setPlans(old => ({ ...old, [aid]: selections })), [])
  const check = (aid, value) => setSelected(old => value ? [...new Set([...old, aid])] : old.filter(id => id !== aid))
  const ready = accounts.filter(a => selected.includes(a.id) && plans[a.id]?.length && !a.busy)
  const total = ready.reduce((n, a) => n + plans[a.id].reduce((v, s) => v + s.items.length, 0), 0)
  const valid = Number.isInteger(Number(seconds)) && seconds >= 30 && seconds <= 1800
  const save = async (connected) => {
    setSession(connected); setBusy(true)
    await perform(async () => {
      const result = await api('/welearn/accounts', { method: 'POST', timeoutMs: 125000,
        body: JSON.stringify({ session_id: connected.session_id, label, account_id: loginTarget?.id || '' }) })
      if (!alive.current) return
      setLoginTarget(null); setSession(null); setLabel('')
      check(result.account.id, true)
      await refresh()
      setMessage('账号登录已加密保存，可读取课程并全选课节。')
    })
    if (alive.current) setBusy(false)
  }
  const start = async () => {
    setBusy(true); setMessage('')
    let succeeded = 0
    const errors = []
    // Independent threads begin as soon as each request succeeds; failures never replay successful accounts.
    for (const account of ready) {
      try {
        await api(`/welearn/accounts/${account.id}/tasks`, { method: 'POST', body: JSON.stringify({ selections: plans[account.id], seconds: Number(seconds), submit, allow_ai: allowAi, recording_bypass: recording && submit }) })
        succeeded++
        check(account.id, false)
      } catch (error) { errors.push(`${account.label}：${error.message}`) }
    }
    await perform(refresh)
    if (alive.current) { setMessage(`已启动 ${succeeded} 个账号。${errors.join('；')}`); setBusy(false) }
  }

  return <div className="space-y-6">
    <header className="rounded-2xl border border-primary/20 bg-primary/5 p-6">
      <div className="flex items-center gap-3"><BookOpen className="h-7 w-7 text-primary" /><h1 className="text-2xl font-bold">WE Learn 工作台</h1><span className="rounded-full bg-surface px-2 py-1 text-xs text-text-muted">测试版</span></div>
      <p className="mt-3 text-sm leading-6 text-text-muted">保存登录、选择账号、全选课节后即可批量启动。不同账号同时执行，同一账号按课程和课节顺序处理；平台已完成的课节会跳过。</p>
      <div className="mt-4 flex flex-wrap gap-5 text-sm"><span className="inline-flex items-center gap-2"><Users className="h-4 w-4" />{accounts.length} 个已保存账号</span><span className="inline-flex items-center gap-2"><CheckCircle className="h-4 w-4" />{tasks.filter(active).length} 个活动任务</span></div>
    </header>
    <div className="flex flex-wrap items-center justify-between gap-3"><h2 className="text-lg font-bold">账号与课程</h2><button className={button} disabled={busy || loginTarget !== null} onClick={() => { setLoginTarget({}); setLabel(''); setSession(null) }}>添加账号</button></div>
    {loginTarget !== null && <section className="space-y-4 rounded-2xl border border-border bg-surface p-5" aria-label="保存 WE Learn 登录">
      <h3 className="font-semibold">{loginTarget.id ? `更新 ${loginTarget.label} 的登录` : '添加 WE Learn 账号'}</h3>
      <label className="block text-sm">账号备注<input className={`${field} mt-1`} value={label} maxLength={128} disabled={busy || !!session} onChange={e => setLabel(e.target.value)} placeholder="例如：我的英语账号" /></label>
      <p className="text-sm text-text-muted">登录成功后加密保存，不保存密码。刷新页面或重启服务后仍可使用；平台登录过期时，请点“更新登录”。</p>
      {session ? <button className={button} disabled={busy} onClick={() => save(session)}>重试保存登录</button> : <EnglishLogin key={loginTarget.id || 'new'} fixedPlatform="welearn" onConnected={save} />}
      <button className={button} disabled={busy} onClick={() => { setLoginTarget(null); setSession(null) }}>关闭添加</button>
    </section>}
    {!accounts.length && <p className="rounded-2xl border border-dashed border-border p-8 text-center text-text-muted">先添加账号。后续直接选择已保存的账号即可。</p>}
    <div className="grid gap-4 lg:grid-cols-2">{accounts.map(account => <AccountPanel key={account.id} account={account} checked={selected.includes(account.id)} onChecked={check} onPlan={plan}
      blocked={busy} perform={perform} onRefresh={refresh} onLogin={a => { setLoginTarget(a); setLabel(a.label); setSession(null) }} />)}</div>
    <section className="space-y-4 rounded-2xl border border-border bg-surface p-5" aria-label="批量启动设置">
      <div className="flex flex-wrap items-center gap-3"><h2 className="mr-auto text-lg font-bold">批量启动</h2><button className={button} disabled={busy} onClick={() => setSelected(accounts.filter(a => !a.busy).map(a => a.id))}>全选空闲账号</button><button className={button} disabled={busy} onClick={() => setSelected([])}>取消账号选择</button></div>
      <label className="flex items-start gap-2 text-sm"><input type="checkbox" checked={submit} disabled={busy} onChange={e => { setSubmit(e.target.checked); if (!e.target.checked) setRecording(false) }} />答案完整并复核后自动提交</label>
      <label className="flex items-start gap-2 text-sm"><input type="checkbox" checked={allowAi} disabled={busy} onChange={e => setAllowAi(e.target.checked)} />课件与缓存未命中时使用 AI（可能产生费用）</label>
      <label className="flex items-start gap-2 text-sm"><input type="checkbox" checked={recording && submit} disabled={busy || !submit} onChange={e => setRecording(e.target.checked)} />启用录音实验处理</label>
      {recording && <p className="text-sm text-text-muted">录音实验不生成可播放音频；朗读／对话为 0 分占位，不代表真实测评，完成状态以平台回读为准。</p>}
      <details className="text-sm"><summary className="cursor-pointer text-text-muted">更多设置</summary><label className="mt-3 block">每节答题预算（秒，30–1800）<input className={`${field} mt-1 max-w-xs`} type="number" min={30} max={1800} step={1} value={seconds} disabled={busy} onChange={e => setSeconds(e.target.value)} /></label><p className="mt-2 text-text-muted">每节至少停留 10 秒；新的 AI 查题请求间隔随机 20–40 秒。预算是最长处理时间。</p></details>
      <div className="rounded-xl bg-background p-4 text-sm"><p className="font-semibold">本次将启动 {ready.length} 个账号 · {total} 节 · {submit ? '允许自动提交' : '仅尝试保存'} · {allowAi ? '允许 AI' : '不使用 AI'}</p>
        {ready.map(a => <p key={a.id} className="mt-1 text-text-muted">{a.label}：{plans[a.id].length} 门课程，{plans[a.id].reduce((n, s) => n + s.items.length, 0)} 节</p>)}
        {selected.some(id => !ready.some(a => a.id === id)) && <p className="mt-2 text-text-muted">忙碌或尚未选择课节的账号不会启动。</p>}
      </div>
      <button className={primary} disabled={busy || !ready.length || !valid} onClick={start}>{busy ? '正在处理…' : '确认并启动所选账号'}</button>
    </section>
    {message && <p role="status" className="rounded-xl border border-border bg-surface p-4 text-sm">{message}</p>}
    <section className="space-y-3" aria-label="WE Learn 任务"><h2 className="text-lg font-bold">任务记录</h2><p className="text-sm text-text-muted">历史和范围会保存。服务重启中断的任务需点“重新启动”，随后读取平台进度；不会自动扩大范围。</p>
      {!tasks.length && <p className="text-sm text-text-muted">暂无任务</p>}
      {tasks.map(task => <article key={task.id} className="space-y-2 rounded-2xl border border-border bg-surface p-5">
        <div className="flex flex-wrap items-center justify-between gap-2"><h3 className="font-semibold">{task.account_label}</h3><span className="text-sm">{states[task.status] || task.status}</span></div>
        <p className="text-sm">已处理 {task.completed_items}/{task.total_items} 节 · 平台确认完成 {task.counts?.platform_completed || 0} · 已完成跳过 {task.counts?.skipped || 0}</p>
        <p className="text-sm text-text-muted">{task.message}</p>{active(task) && <p className="text-sm text-text-muted">当前：{task.current_item || '正在读取课节'}</p>}
        <p className="text-xs text-text-muted">题目 {task.counts?.questions || 0} · 有答案 {task.counts?.answered || 0} · 已填 {task.counts?.filled || 0} · 课件答案 {task.counts?.native_answers || 0} · 缓存 {task.counts?.cache_hits || 0} · AI {task.counts?.ai_answers || 0} · 缺失 {task.counts?.missing || 0} · 不支持 {task.counts?.unsupported || 0} · 已提交 {task.counts?.submitted || 0} · 拒绝 {task.counts?.submission_rejected || 0} · 回执未确认 {task.counts?.submission_unknown || 0}</p>
        {task.results?.length > 0 && <details className="text-sm"><summary className="cursor-pointer">逐节结果</summary><div className="mt-2 max-h-80 space-y-2 overflow-auto">{task.results.map((r, i) => <p key={i}>{r.course} &gt; {r.name} · {states[r.status] || r.status} · {r.message}</p>)}</div></details>}
        <div className="flex gap-2">{active(task) ? <button className={button} disabled={busy || task.status === 'stopping'} onClick={() => perform(async () => { await api(`/welearn/tasks/${task.id}/stop`, { method: 'POST' }); await refresh() })}>停止</button> : <>
          <button className={button} disabled={busy || accounts.find(a => a.id === task.account_id)?.busy || !accounts.some(a => a.id === task.account_id)} onClick={async () => { setBusy(true); await perform(async () => { await api(`/welearn/tasks/${task.id}/restart`, { method: 'POST', timeoutMs: 125000 }); await refresh() }); setBusy(false) }}>重新启动</button>
          <button className={button} disabled={busy} onClick={() => perform(async () => { await api(`/welearn/tasks/${task.id}`, { method: 'DELETE' }); await refresh() })}>删除记录</button>
        </>}</div>
      </article>)}
    </section>
  </div>
}
