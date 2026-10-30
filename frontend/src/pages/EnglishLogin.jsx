import { useEffect, useRef, useState } from 'react'
import { api } from '../utils/api'

const field = 'w-full rounded-lg border border-border bg-surface p-3 text-text'
const button = 'rounded-lg border border-border bg-surface px-4 py-2 disabled:opacity-50'

export default function EnglishLogin({ onConnected, fixedPlatform }) {
  const [platform, setPlatform] = useState(fixedPlatform || 'welearn')
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [credential, setCredential] = useState('')
  const [url, setUrl] = useState('')
  const [login, setLogin] = useState(null)
  const [text, setText] = useState('')
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState('')
  const activeId = useRef(null)
  const alive = useRef(true)
  const pointer = useRef(null)
  const dragged = useRef(false)

  const accept = (result) => {
    if (!alive.current) return
    if (fixedPlatform && result.platform && result.platform !== fixedPlatform) return
    if (result.status === 'connected') {
      if (fixedPlatform && result.session?.platform !== fixedPlatform) return
      activeId.current = null
      setLogin(null)
      onConnected(result.session)
    } else if (result.status === 'failed') {
      activeId.current = null
      setLogin(null)
      setMessage(result.message)
    } else {
      activeId.current = result.login_id
      setLogin(result)
    }
  }
  const acceptRef = useRef(accept)
  useEffect(() => { acceptRef.current = accept })

  useEffect(() => {
    alive.current = true
    // Recover a pending login after a refresh or a lost initial HTTP reply.
    api('/english-learning/logins').then((result) => {
      if (alive.current && result.logins?.some(j => !fixedPlatform || j.platform === fixedPlatform) && !activeId.current) acceptRef.current(result.logins.find(j => !fixedPlatform || j.platform === fixedPlatform))
    }).catch(() => {})
    return () => {
      alive.current = false
      // The server also expires abandoned windows after ten minutes.
      const lid = activeId.current
      if (lid) api(`/english-learning/logins/${lid}`, { method: 'DELETE' }).catch(() => {})
    }
  }, [fixedPlatform])

  const lid = login?.login_id
  useEffect(() => {
    if (!lid) return
    let active = true
    let timer
    const poll = async () => {
      try {
        const result = await api(`/english-learning/logins/${lid}`, { timeoutMs: 55000 })
        if (active) acceptRef.current(result)
      } catch (error) {
        if (active && alive.current) setMessage(error.message)
      } finally {
        if (active) timer = setTimeout(poll, 2500)
      }
    }
    timer = setTimeout(poll, 2500)
    return () => { active = false; clearTimeout(timer) }
  }, [lid])

  const loginWithPassword = async (event) => {
    event.preventDefault()
    setBusy(true)
    setMessage('正在登录并读取课程…')
    const submittedPassword = password
    setPassword('')
    try {
      const result = await api('/english-learning/logins', { method: 'POST', timeoutMs: 55000,
        body: JSON.stringify({ platform, username: username.trim(), password: submittedPassword }) })
      if (alive.current) { setMessage(''); acceptRef.current(result) }
    } catch (error) {
      if (alive.current) setMessage(error.message)
      try {
        const result = await api('/english-learning/logins')
        if (alive.current && result.logins?.some(j => !fixedPlatform || j.platform === fixedPlatform)) acceptRef.current(result.logins.find(j => !fixedPlatform || j.platform === fixedPlatform))
      } catch (_) { /* The user can retry when the connection recovers. */ }
    } finally { if (alive.current) setBusy(false) }
  }

  const loginWithWechat = async () => {
    setBusy(true)
    setMessage('正在获取微信登录二维码…')
    setPassword('')
    try {
      const result = await api('/english-learning/logins/wechat', { method: 'POST', timeoutMs: 55000 })
      if (alive.current) { setMessage(''); acceptRef.current(result) }
    } catch (error) {
      if (alive.current) setMessage(error.message)
      try {
        const result = await api('/english-learning/logins')
        if (alive.current && result.logins?.some(j => !fixedPlatform || j.platform === fixedPlatform)) acceptRef.current(result.logins.find(j => !fixedPlatform || j.platform === fixedPlatform))
      } catch (_) { /* A pending QR window can be recovered on refresh. */ }
    } finally { if (alive.current) setBusy(false) }
  }

  const act = async (command) => {
    if (!activeId.current || busy) return
    setBusy(true)
    setMessage('')
    try {
      const result = await api(`/english-learning/logins/${activeId.current}/actions`, {
        method: 'POST', timeoutMs: 55000, body: JSON.stringify(command),
      })
      if (alive.current) acceptRef.current(result)
    } catch (error) { if (alive.current) setMessage(error.message) }
    finally { if (alive.current) setBusy(false) }
  }

  const position = (event) => {
    const rect = event.currentTarget.getBoundingClientRect()
    return { x: Math.max(0, Math.min(999, Math.floor((event.clientX - rect.left) * 1000 / rect.width))),
      y: Math.max(0, Math.min(759, Math.floor((event.clientY - rect.top) * 760 / rect.height))) }
  }

  return <div className="space-y-4 rounded-xl border border-border p-5">
    {!login && <>
      {!fixedPlatform && <label className="block">平台
        <select className={field} value={platform} disabled={busy} onChange={(e) => {
          setPlatform(e.target.value); setPassword(''); setCredential(''); setUrl(''); setMessage('')
        }}><option value="welearn">WE Learn</option><option value="unipus_ai">U校园 5.0（AI版）</option></select>
      </label>}
      <form className="space-y-4" onSubmit={loginWithPassword}>
        <label className="block">账号
          <input className={field} value={username} onChange={(e) => setUsername(e.target.value)}
            required maxLength={256} disabled={busy} autoComplete="username" placeholder="手机号、邮箱或平台用户名" />
        </label>
        <label className="block">密码
          <input className={field} type="password" value={password} onChange={(e) => setPassword(e.target.value)}
            required maxLength={256} disabled={busy} autoComplete="current-password" />
        </label>
        <p className="text-sm text-text-muted">使用所选平台的账号密码。遇到验证码时在本页完成验证，登录后直接选择课程。</p>
        <button className={button} disabled={busy || !username.trim() || !password}>{busy ? '登录中…' : '登录并读取课程'}</button>
      </form>
      {platform === 'welearn' && <div className="space-y-2 border-t border-border pt-4">
        <button type="button" className={button} disabled={busy} onClick={loginWithWechat}>微信扫码登录</button>
        <p className="text-sm text-text-muted">适用于已绑定微信的 WE Learn 账号，无需填写账号密码。扫码后在手机微信中确认。</p>
      </div>}
      <details className="text-sm">
        <summary className="cursor-pointer text-text-muted">高级：导入已有登录会话</summary>
        <form className="mt-3 space-y-3" onSubmit={async (event) => {
          event.preventDefault(); setBusy(true); setMessage('')
          try {
            const result = await api('/english-learning/sessions', { method: 'POST', timeoutMs: 125000,
              body: JSON.stringify({ platform, credential, course_url: url }) })
            if (alive.current) { setCredential(''); onConnected(result) }
          } catch (error) { if (alive.current) setMessage(error.message) }
          finally { if (alive.current) setBusy(false) }
        }}>
          <label className="block">{platform === 'welearn' ? 'Cookie' : 'Storage state JSON'}
            <textarea className={field} rows={4} value={credential} onChange={(e) => setCredential(e.target.value)}
              required maxLength={100000} disabled={busy} autoComplete="off" spellCheck={false} />
          </label>
          <button className={button} disabled={busy || !credential.trim()}>连接并读取课程</button>
        </form>
      </details>
    </>}
    {login && <div className="space-y-3" aria-label="平台登录验证">
      <p>{login.message || '正在读取平台登录状态…'}</p>
      <div className="flex flex-wrap gap-2">
        {login.view_state === 'loading' ? <span role="status" className="text-sm text-text-muted">正在连接课程，请稍候…</span> : login.display_mode === 'qr' ? <button className={button} disabled={busy} onClick={() => act({ action: 'reload' })}>刷新二维码</button> : <>
        <button className={button} disabled={busy} onClick={() => act({ action: 'refresh' })}>刷新画面</button>
        <button className={button} disabled={busy} onClick={() => act({ action: 'reload' })}>重新加载页面</button>
        <button className={button} disabled={busy} onClick={() => act({ action: 'top' })}>回到页面顶部</button>
        </>}
      </div>
      {login.view_state === 'blank' && <p role="status" className="rounded-lg border border-border p-4">登录页面尚未显示内容，请稍候或重新加载页面。</p>}
      {login.display_mode === 'qr' && login.image && login.qr_status !== 'expired' && <img
        className="mx-auto h-auto w-72 max-w-full rounded-lg bg-white p-3" style={{ imageRendering: 'pixelated' }}
        src={`data:image/png;base64,${login.image}`} alt="WE Learn 微信登录二维码" draggable={false} />}
      {login.display_mode !== 'qr' && login.image && login.view_state !== 'blank' && <>
        <p className="text-sm text-text-muted">下面是平台的登录页面。可以直接点选、拖动；需要填写验证码时，先点选画面中的输入框，再在下方输入文字。</p>
        <button type="button" className="block w-full border border-border p-0" aria-label="平台验证页面"
          style={{ touchAction: 'none' }} disabled={busy}
          onPointerDown={(event) => { pointer.current = position(event); dragged.current = false; event.currentTarget.setPointerCapture?.(event.pointerId) }}
          onPointerUp={(event) => {
            const end = position(event), start = pointer.current
            pointer.current = null
            if (start && Math.hypot(end.x - start.x, end.y - start.y) > 5) {
              dragged.current = true
              act({ action: 'drag', ...start, end_x: end.x, end_y: end.y })
            }
          }}
          onClick={(event) => { if (!dragged.current && event.detail > 0) act({ action: 'click', ...position(event) }) }}>
          <img className="block h-auto w-full" src={`data:image/${login.image_type === 'png' ? 'png' : 'jpeg'};base64,${login.image}`} alt="平台登录及验证码画面" draggable={false} width="1000" height="760" />
        </button>
        <form className="flex flex-wrap gap-2" onSubmit={(event) => {
          event.preventDefault(); const value = text; setText(''); act({ action: 'text', text: value })
        }}>
          <label className="min-w-0 flex-1">验证文字
            <input className={field} value={text} onChange={(e) => setText(e.target.value)} maxLength={256} autoComplete="off" disabled={busy} />
          </label>
          <button className={button} disabled={busy || !text}>填写到选中位置</button>
        </form>
        <div className="flex flex-wrap gap-2">
          {[['Tab', '下一个输入框'], ['Backspace', '删除一字'], ['Enter', '回车']].map(([key, label]) =>
            <button key={key} className={button} disabled={busy} onClick={() => act({ action: 'key', key })}>{label}</button>)}
          <button className={button} disabled={busy} onClick={() => act({ action: 'scroll', delta_y: 500 })}>向下滚动</button>
          <button className={button} disabled={busy} onClick={() => act({ action: 'scroll', delta_y: -500 })}>向上滚动</button>
        </div>
      </>}
      <button className={button} disabled={busy} onClick={async () => {
        setBusy(true)
        try {
          await api(`/english-learning/logins/${activeId.current}`, { method: 'DELETE' })
          if (alive.current) { activeId.current = null; setLogin(null); setText(''); setMessage('已取消登录') }
        } catch (error) { if (alive.current) setMessage(error.message) }
        finally { if (alive.current) setBusy(false) }
      }}>取消登录</button>
    </div>}
    {message && <p role="status">{message}</p>}
  </div>
}
