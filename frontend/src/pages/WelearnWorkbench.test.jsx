import { cleanup, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, expect, test, vi } from 'vitest'
import WelearnWorkbench from './WelearnWorkbench'

const mocks = vi.hoisted(() => ({ api: vi.fn(), accounts: [], tasks: [] }))
vi.mock('../utils/api', () => ({ api: mocks.api }))
vi.mock('./EnglishLogin', () => ({ default: ({ onConnected, fixedPlatform }) => <button onClick={() => onConnected({ session_id: 'temporary', platform: fixedPlatform })}>模拟登录成功</button> }))

beforeEach(() => {
  mocks.accounts = [{ id: 'a', label: 'Alice', updated_at: '1', busy: false }, { id: 'b', label: 'Bob', updated_at: '1', busy: false }]
  mocks.tasks = []
  mocks.api.mockReset()
  mocks.api.mockImplementation(async (path, options) => {
    if (path === '/welearn/accounts') {
      if (options?.method === 'POST') return { account: mocks.accounts[0] }
      return { accounts: [...mocks.accounts] }
    }
    if (path === '/welearn/tasks') return { tasks: [...mocks.tasks] }
    if (path.endsWith('/courses')) return { courses: [{ id: 'c', name: 'College English' }, { id: 'd', name: 'Listening' }] }
    if (path.endsWith('/items')) {
      const course = JSON.parse(options.body).course
      return { items: [{ id: course + '-1', name: course === 'c' ? 'Unit 1' : 'Unit 2', progress: 'false' }, { id: course + '-2', name: 'Reading', progress: 'true' }] }
    }
    return {}
  })
})
afterEach(cleanup)
const panel = (name) => within(screen.getByRole('article', { name: `账号 ${name}` }))
const posts = () => mocks.api.mock.calls.filter(([path, options]) => path.endsWith('/tasks') && options?.method === 'POST')

test('restores saved accounts without asking for login; explicit select-all previews and starts both independently', async () => {
  const user = userEvent.setup()
  render(<WelearnWorkbench />)
  await screen.findByRole('article', { name: '账号 Alice' })
  expect(screen.queryByText('模拟登录成功')).not.toBeInTheDocument()
  expect(screen.getByRole('button', { name: '确认并启动所选账号' })).toBeDisabled()
  for (const name of ['Alice', 'Bob']) {
    await user.click(panel(name).getByRole('button', { name: '全选全部课程与课节' }))
    await waitFor(() => expect(panel(name).getByText('已选 2 门课程 · 4 节')).toBeInTheDocument())
  }
  expect(posts()).toHaveLength(0)
  await user.click(screen.getByRole('button', { name: '全选空闲账号' }))
  expect(screen.getByText(/本次将启动 2 个账号 · 8 节/)).toBeInTheDocument()
  await user.click(screen.getByRole('button', { name: '确认并启动所选账号' }))
  await waitFor(() => expect(posts()).toHaveLength(2))
  expect(posts().map(([path]) => path)).toEqual(['/welearn/accounts/a/tasks', '/welearn/accounts/b/tasks'])
  const body = JSON.parse(posts()[0][1].body)
  expect(body).toEqual({ selections: [{ course: 'c', items: ['c-1', 'c-2'] }, { course: 'd', items: ['d-1', 'd-2'] }], seconds: 300, submit: true, allow_ai: true, recording_bypass: false })
  expect(JSON.stringify(localStorage)).not.toContain('temporary')
})

test('single-course selection stays exact; changing courses retains separate choices and refresh filters removed ids', async () => {
  const user = userEvent.setup()
  render(<WelearnWorkbench />)
  await screen.findByRole('article', { name: '账号 Alice' })
  const a = panel('Alice')
  await user.click(a.getByRole('button', { name: '读取课程' }))
  await user.selectOptions(await a.findByLabelText('课程'), 'c')
  await user.click(a.getByRole('button', { name: '读取／刷新课节' }))
  await user.click(await a.findByRole('button', { name: '全选本课程课节' }))
  await user.click(a.getByRole('checkbox', { name: /Reading/ }))
  await user.selectOptions(a.getByLabelText('课程'), 'd')
  expect(a.queryByText('Unit 1')).not.toBeInTheDocument()
  await user.selectOptions(a.getByLabelText('课程'), 'c')
  expect(a.getByRole('checkbox', { name: 'Unit 1' })).toBeChecked()
  await user.click(a.getByRole('checkbox', { name: 'Alice' }))
  await user.click(screen.getByRole('button', { name: '确认并启动所选账号' }))
  await waitFor(() => expect(posts()).toHaveLength(1))
  expect(JSON.parse(posts()[0][1].body).selections).toEqual([{ course: 'c', items: ['c-1'] }])
})

test('recording experiment is opt-in and submitting off clears it; AI and invalid budget remain explicit', async () => {
  const user = userEvent.setup()
  render(<WelearnWorkbench />)
  await screen.findByRole('article', { name: '账号 Alice' })
  await user.click(panel('Alice').getByRole('button', { name: '全选全部课程与课节' }))
  await user.click(panel('Alice').getByRole('checkbox', { name: 'Alice' }))
  const recording = screen.getByRole('checkbox', { name: '启用录音实验处理' })
  expect(recording).not.toBeChecked()
  await user.click(recording)
  expect(screen.getByText(/不生成可播放音频/)).toBeVisible()
  await user.click(screen.getByRole('checkbox', { name: '答案完整并复核后自动提交' }))
  expect(recording).not.toBeChecked(); expect(recording).toBeDisabled()
  await user.click(screen.getByRole('checkbox', { name: /课件与缓存未命中/ }))
  await user.click(screen.getByText('更多设置'))
  const budget = screen.getByLabelText(/每节答题预算/)
  await user.clear(budget); await user.type(budget, '10')
  expect(screen.getByRole('button', { name: '确认并启动所选账号' })).toBeDisabled()
  await user.clear(budget); await user.type(budget, '300')
  await user.click(screen.getByRole('button', { name: '确认并启动所选账号' }))
  await waitFor(() => expect(posts()).toHaveLength(1))
  expect(JSON.parse(posts()[0][1].body)).toMatchObject({ submit: false, allow_ai: false, recording_bypass: false })
})

test('partial batch failure reports which account failed and never automatically repeats successful starts', async () => {
  const user = userEvent.setup()
  const original = mocks.api.getMockImplementation()
  mocks.api.mockImplementation(async (path, options) => {
    if (path === '/welearn/accounts/b/tasks') throw new Error('登录已过期')
    return original(path, options)
  })
  render(<WelearnWorkbench />)
  await screen.findByRole('article', { name: '账号 Alice' })
  for (const name of ['Alice', 'Bob']) await user.click(panel(name).getByRole('button', { name: '全选全部课程与课节' }))
  await user.click(screen.getByRole('button', { name: '全选空闲账号' }))
  await user.click(screen.getByRole('button', { name: '确认并启动所选账号' }))
  await screen.findByText('已启动 1 个账号。Bob：登录已过期')
  expect(posts()).toHaveLength(2)
  expect(panel('Alice').getByRole('checkbox', { name: 'Alice' })).not.toBeChecked()
  expect(panel('Bob').getByRole('checkbox', { name: 'Bob' })).toBeChecked()
})

test('saving a login sends only temporary session id and remark; failure offers save retry instead of losing login', async () => {
  const user = userEvent.setup()
  let first = true
  const original = mocks.api.getMockImplementation()
  mocks.api.mockImplementation(async (path, options) => {
    if (path === '/welearn/accounts' && options?.method === 'POST' && first) { first = false; throw new Error('暂时失败') }
    return original(path, options)
  })
  render(<WelearnWorkbench />)
  await screen.findByRole('article', { name: '账号 Alice' })
  await user.click(screen.getByRole('button', { name: '添加账号' }))
  await user.type(screen.getByLabelText('账号备注'), 'My account')
  await user.click(screen.getByText('模拟登录成功'))
  await screen.findByText('暂时失败')
  await user.click(screen.getByRole('button', { name: '重试保存登录' }))
  await screen.findByText('账号登录已加密保存，可读取课程并全选课节。')
  const saved = mocks.api.mock.calls.filter(([p, o]) => p === '/welearn/accounts' && o?.method === 'POST')
  expect(JSON.parse(saved[0][1].body)).toEqual({ session_id: 'temporary', label: 'My account', account_id: '' })
  expect(screen.queryByLabelText('账号备注')).not.toBeInTheDocument()
})

test('busy accounts cannot edit or start; saved interrupted tasks have restart and preserve manual result text', async () => {
  const user = userEvent.setup()
  mocks.accounts[0].busy = true
  mocks.tasks = [{ id: 'old', account_id: 'b', account_label: 'Bob', status: 'interrupted', total_items: 1, completed_items: 0,
    counts: {}, results: [{ course: 'College', name: 'Speaking', status: 'needs_manual', message: '录音未确认' }] }]
  render(<WelearnWorkbench />)
  await screen.findByRole('article', { name: '账号 Alice' })
  expect(panel('Alice').getByRole('button', { name: '更新登录' })).toBeDisabled()
  expect(panel('Alice').getByRole('button', { name: '全选全部课程与课节' })).toBeDisabled()
  await user.click(screen.getByText('逐节结果'))
  expect(screen.getByText(/Speaking · 需人工处理 · 录音未确认/)).toBeVisible()
  await user.click(screen.getByRole('button', { name: '重新启动' }))
  await waitFor(() => expect(mocks.api).toHaveBeenCalledWith('/welearn/tasks/old/restart', expect.objectContaining({ method: 'POST' })))
})

test('refreshing a saved workbench never automatically selects accounts or lessons or starts tasks', async () => {
  const view = render(<WelearnWorkbench />)
  await screen.findByRole('article', { name: '账号 Alice' })
  view.unmount()
  render(<WelearnWorkbench />)
  await screen.findByRole('article', { name: '账号 Alice' })
  expect(panel('Alice').getByRole('checkbox', { name: 'Alice' })).not.toBeChecked()
  expect(posts()).toHaveLength(0)
  expect(screen.getByRole('button', { name: '确认并启动所选账号' })).toBeDisabled()
})
