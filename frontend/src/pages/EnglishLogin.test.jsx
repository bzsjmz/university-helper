import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, expect, test, vi } from 'vitest'
import EnglishLearning from './EnglishLearning'

const mocks = vi.hoisted(() => ({ api: vi.fn() }))
vi.mock('../utils/api', () => ({ api: mocks.api }))
const connected = { status: 'connected', session: { session_id: 's', courses: [{ id: 'c', name: 'My English' }] } }
const verify = { login_id: 'login1', status: 'verification', image: 'ZmFrZQ==', message: '请完成验证码' }

beforeEach(() => {
  mocks.api.mockReset()
  mocks.api.mockImplementation(async (path, options) => {
    if (path === '/english-learning/tasks') return { tasks: [] }
    if (path === '/english-learning/logins') return options?.method === 'POST' ? connected : { logins: [] }
    return {}
  })
})
afterEach(cleanup)

async function login(user, platform = 'welearn') {
  await user.selectOptions(screen.getByLabelText('平台'), platform)
  await user.type(screen.getByLabelText('账号'), 'account')
  await user.type(screen.getByLabelText('密码'), 'private-password')
  await user.click(screen.getByRole('button', { name: '登录并读取课程' }))
}

test('password login for legacy U-campus needs no cookies, JSON or course URL', async () => {
  const user = userEvent.setup()
  render(<EnglishLearning />)
  await login(user, 'unipus')
  await screen.findByRole('button', { name: '读取课程内容' })
  const body = JSON.parse(mocks.api.mock.calls.find(([p, o]) => p === '/english-learning/logins' && o?.method === 'POST')[1].body)
  expect(body).toEqual({ platform: 'unipus', username: 'account', password: 'private-password' })
  expect(screen.getByRole('option', { name: 'My English' })).toBeInTheDocument()
  expect(screen.queryByLabelText('密码')).not.toBeInTheDocument()
  expect(JSON.stringify(localStorage)).not.toContain('private-password')
  expect(JSON.stringify(sessionStorage)).not.toContain('private-password')
  expect(mocks.api.mock.calls.some(([p]) => p === '/english-learning/sessions')).toBe(false)
})

test('human verification stays in page and subsequent operations never resend password', async () => {
  mocks.api.mockImplementation(async (path, options) => {
    if (path === '/english-learning/tasks') return { tasks: [] }
    if (path === '/english-learning/logins') return options?.method === 'POST' ? verify : { logins: [] }
    if (path.endsWith('/actions')) return JSON.parse(options.body).action === 'key' ? connected : verify
    return verify
  })
  const user = userEvent.setup()
  render(<EnglishLearning />)
  await login(user)
  await screen.findByRole('img', { name: '平台登录及验证码画面' })
  await user.type(screen.getByLabelText('验证文字'), '1234')
  await user.click(screen.getByRole('button', { name: '填写到选中位置' }))
  await waitFor(() => expect(mocks.api).toHaveBeenCalledWith('/english-learning/logins/login1/actions',
    expect.objectContaining({ body: JSON.stringify({ action: 'text', text: '1234' }) })))
  await waitFor(() => expect(screen.getByRole('button', { name: '回车' })).toBeEnabled())
  await user.click(screen.getByRole('button', { name: '回车' }))
  await screen.findByRole('button', { name: '读取课程内容' })
  expect(JSON.stringify(mocks.api.mock.calls.filter(([p]) => p.includes('/actions')))).not.toContain('private-password')
})

test('scaled verification image sends bounded viewport coordinates and cancel closes browser', async () => {
  mocks.api.mockImplementation(async (path, options) => {
    if (path === '/english-learning/tasks') return { tasks: [] }
    if (path === '/english-learning/logins') return options?.method === 'POST' ? verify : { logins: [] }
    if (options?.method === 'DELETE') return { cancelled: true }
    return verify
  })
  const user = userEvent.setup()
  render(<EnglishLearning />)
  await login(user)
  const imageButton = await screen.findByRole('button', { name: '平台验证页面' })
  imageButton.getBoundingClientRect = () => ({ left: 10, top: 10, width: 500, height: 380 })
  fireEvent.click(imageButton, { detail: 1, clientX: 260, clientY: 200 })
  await waitFor(() => expect(mocks.api).toHaveBeenCalledWith('/english-learning/logins/login1/actions',
    expect.objectContaining({ body: JSON.stringify({ action: 'click', x: 500, y: 380 }) })))
  await waitFor(() => expect(screen.getByRole('button', { name: '取消登录' })).toBeEnabled())
  await user.click(screen.getByRole('button', { name: '取消登录' }))
  await screen.findByRole('button', { name: '登录并读取课程' })
  expect(mocks.api).toHaveBeenCalledWith('/english-learning/logins/login1', { method: 'DELETE' })
  expect(screen.getByLabelText('密码')).toHaveValue('')
})

test('blank login view gives recovery controls without an empty image or text entry', async () => {
  mocks.api.mockImplementation(async (path, options) => {
    if (path === '/english-learning/tasks') return { tasks: [] }
    if (path === '/english-learning/logins') return options?.method === 'POST'
      ? { ...verify, image: '', view_state: 'blank' } : { logins: [] }
    return { ...verify, image_type: 'png', view_state: 'ready' }
  })
  const user = userEvent.setup()
  render(<EnglishLearning />)
  await login(user)
  await screen.findByText('登录页面尚未显示内容，请稍候或重新加载页面。')
  expect(screen.queryByRole('img', { name: '平台登录及验证码画面' })).not.toBeInTheDocument()
  expect(screen.queryByLabelText('验证文字')).not.toBeInTheDocument()
  await user.click(screen.getByRole('button', { name: '重新加载页面' }))
  await waitFor(() => expect(mocks.api).toHaveBeenCalledWith('/english-learning/logins/login1/actions',
    expect.objectContaining({ body: JSON.stringify({ action: 'reload' }) })))
  expect(await screen.findByRole('img', { name: '平台登录及验证码画面' })).toHaveAttribute('src', 'data:image/png;base64,ZmFrZQ==')
  expect(JSON.stringify(mocks.api.mock.calls.filter(([p]) => p.includes('/actions')))).not.toContain('private-password')
})

test('WeChat QR login needs no credentials, can renew QR, and hands off to courses', async () => {
  const qr = { ...verify, display_mode: 'qr', image_type: 'png', qr_status: 'waiting', message: '请用微信扫码' }
  mocks.api.mockImplementation(async (path, options) => {
    if (path === '/english-learning/tasks') return { tasks: [] }
    if (path === '/english-learning/logins') return { logins: [] }
    if (path === '/english-learning/logins/wechat') return qr
    if (path.endsWith('/actions') && JSON.parse(options.body).action === 'reload') return connected
    return qr
  })
  const user = userEvent.setup()
  render(<EnglishLearning />)
  await user.click(await screen.findByRole('button', { name: '微信扫码登录' }))
  const image = await screen.findByRole('img', { name: 'WE Learn 微信登录二维码' })
  expect(image).toHaveAttribute('src', 'data:image/png;base64,ZmFrZQ==')
  expect(screen.queryByLabelText('验证文字')).not.toBeInTheDocument()
  expect(screen.queryByRole('button', { name: '平台验证页面' })).not.toBeInTheDocument()
  expect(mocks.api.mock.calls.find(([path]) => path.endsWith('/wechat'))[1].body).toBeUndefined()
  await user.click(screen.getByRole('button', { name: '刷新二维码' }))
  await screen.findByRole('button', { name: '读取课程内容' })
})

test('expired QR is hidden, pending QR can be recovered, and U-campus has no WeChat entry', async () => {
  const user = userEvent.setup()
  const view = render(<EnglishLearning />)
  await user.selectOptions(screen.getByLabelText('平台'), 'unipus')
  expect(screen.queryByRole('button', { name: '微信扫码登录' })).not.toBeInTheDocument()
  view.unmount()
  mocks.api.mockImplementation(async (path) => path === '/english-learning/tasks' ? { tasks: [] }
    : path === '/english-learning/logins' ? { logins: [{ ...verify, display_mode: 'qr', qr_status: 'expired' }] } : {})
  render(<EnglishLearning />)
  await screen.findByRole('button', { name: '刷新二维码' })
  expect(screen.queryByRole('img', { name: 'WE Learn 微信登录二维码' })).not.toBeInTheDocument()
  await user.click(screen.getByRole('button', { name: '取消登录' }))
  await screen.findByRole('button', { name: '微信扫码登录' })
})

test('OAuth return shows course connection progress instead of a blank-page warning', async () => {
  mocks.api.mockImplementation(async (path, options) => {
    if (path === '/english-learning/tasks') return { tasks: [] }
    if (path === '/english-learning/logins') return options?.method === 'POST'
      ? { ...verify, image: '', view_state: 'loading', message: '微信授权已返回，正在读取课程。' } : { logins: [] }
    return {}
  })
  const user = userEvent.setup()
  render(<EnglishLearning />)
  await login(user)
  await screen.findByText('正在连接课程，请稍候…')
  expect(screen.queryByText('登录页面尚未显示内容，请稍候或重新加载页面。')).not.toBeInTheDocument()
  expect(screen.queryByRole('button', { name: '重新加载页面' })).not.toBeInTheDocument()
  expect(screen.getByRole('button', { name: '取消登录' })).toBeEnabled()
})
