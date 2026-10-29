import { useEffect, useState } from 'react'
import { Card } from '../components'
import { api } from '../utils/api'

export default function DesktopSettings() {
  const [settings, setSettings] = useState(null)
  const [primaryKey, setPrimaryKey] = useState('')
  const [fallbackKey, setFallbackKey] = useState('')
  const [clearPrimary, setClearPrimary] = useState(false)
  const [clearFallback, setClearFallback] = useState(false)
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState('')
  const [error, setError] = useState('')
  useEffect(() => {
    let active = true
    api('/desktop/settings').then(value => { if (active) setSettings(value) })
      .catch(err => { if (active) setError(err.message) })
    return () => { active = false }
  }, [])
  const save = async event => {
    event.preventDefault()
    if (busy) return
    setBusy(true)
    setMessage('')
    setError('')
    try {
      const value = await api('/desktop/settings', { method: 'PUT', body: JSON.stringify({
        endpoint: settings.endpoint, model: settings.model,
        primary_key: clearPrimary ? '' : primaryKey || null,
        fallback_key: clearFallback ? '' : fallbackKey || null,
      }) })
      setSettings(value)
      setPrimaryKey('')
      setFallbackKey('')
      setClearPrimary(false)
      setClearFallback(false)
      setMessage('设置已保存，新启动的任务会使用新配置。未发送 AI 请求。')
    } catch (err) { setError(err.message || '保存失败，请重试') }
    finally { setBusy(false) }
  }
  return <div className="mx-auto max-w-2xl space-y-5">
    <h1 className="text-3xl font-bold">桌面设置</h1>
    <Card className="space-y-3">
      <h2 className="text-xl font-semibold">在这台电脑上使用</h2>
      <p className="text-sm text-text-muted">添加学习通账号后，读取课程、选择范围并预览，再启动任务。账号与任务历史保存在当前系统用户的数据目录中。</p>
      <p className="text-sm text-text-muted">退出应用或电脑休眠会影响运行任务。重新打开后可按原范围重新启动并读取平台进度。请先停止任务，再关闭或更新应用。</p>
    </Card>
    {error && <p role="alert" className="text-danger">{error}</p>}
    {message && <p role="status" className="text-success">{message}</p>}
    {settings ? <Card><form onSubmit={save} className="space-y-4">
      <h2 className="text-xl font-semibold">AI 答题配置</h2>
      <p className="text-sm text-text-muted">默认顺序为本地缓存 → AI。使用 AI 需填写自己的接口、模型和 Key，可能产生费用。无需 AI 时，可在任务设置中只选本地缓存。</p>
      <label className="block text-sm">接口地址<input required type="url" maxLength={2048} value={settings.endpoint} onChange={e => setSettings({ ...settings, endpoint: e.target.value })} className="mt-1 w-full rounded-xl border border-border bg-surface px-3 py-2" /></label>
      <label className="block text-sm">模型名称<input maxLength={128} value={settings.model} placeholder="填写服务商提供的模型名称" onChange={e => setSettings({ ...settings, model: e.target.value })} className="mt-1 w-full rounded-xl border border-border bg-surface px-3 py-2" /></label>
      <p className="text-xs text-text-muted">更换接口地址时会清除原 Key，请同时填写新接口的 Key。</p>
      <label className="block text-sm">优先 Key（{settings.primary_configured ? '已配置' : '未配置'}）<input type="password" autoComplete="new-password" maxLength={4096} value={primaryKey} onChange={e => setPrimaryKey(e.target.value)} disabled={clearPrimary} placeholder="留空保留当前 Key" className="mt-1 w-full rounded-xl border border-border bg-surface px-3 py-2" /></label>
      <label className="flex gap-2 text-sm"><input type="checkbox" checked={clearPrimary} onChange={e => setClearPrimary(e.target.checked)} />清除优先 Key</label>
      <label className="block text-sm">备用 Key（{settings.fallback_configured ? '已配置' : '未配置'}）<input type="password" autoComplete="new-password" maxLength={4096} value={fallbackKey} onChange={e => setFallbackKey(e.target.value)} disabled={clearFallback} placeholder="可选；留空保留当前 Key" className="mt-1 w-full rounded-xl border border-border bg-surface px-3 py-2" /></label>
      <label className="flex gap-2 text-sm"><input type="checkbox" checked={clearFallback} onChange={e => setClearFallback(e.target.checked)} />清除备用 Key</label>
      <button type="submit" disabled={busy} className="rounded-xl bg-primary px-4 py-2 font-semibold text-white disabled:opacity-50">{busy ? '保存中…' : '保存设置'}</button>
    </form></Card> : !error && <p role="status">正在读取设置…</p>}
  </div>
}
