import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { afterEach, beforeEach, expect, test, vi } from 'vitest'
import { RuntimeProfileContext } from '../components/runtimeProfileContext'
import Dashboard from './Dashboard'

const mocks = vi.hoisted(() => ({ api: vi.fn() }))
vi.mock('../utils/api', () => ({ api: mocks.api }))
const accounts = [
  { id: 'a', label: '账号甲', account_label: '138****0000' },
  { id: 'b', label: '账号乙', account_label: '139****0000' },
]
const makeTasks = () => [
  { task_id: 'task-a', account_id: 'a', account_name: '账号甲', account_label: '138****0000', status: 'running', updated_at: '2026-10-10T05:00:00Z', progress: { current_course: '课程甲', current_chapter: '章节甲', total_chapters: 10, completed_chapters: 5 }, logs: [{ timestamp: '2026-10-10T05:00:00Z', message: '甲的日志' }] },
  { task_id: 'task-b', account_id: 'b', account_name: '账号乙', account_label: '139****0000', status: 'paused', updated_at: '2026-10-10T05:01:00Z', progress: { current_course: '课程乙', total: 4, completed: 1 }, logs: [{ timestamp: '2026-10-10T05:00:00Z', message: '乙的日志' }] },
  { task_id: 'legacy', status: 'failed', progress: {}, logs: [] },
]
let tasks
beforeEach(() => {
  tasks = makeTasks()
  mocks.api.mockReset()
  mocks.api.mockImplementation(async path => {
    if (path === '/learning-accounts') return { accounts }
    if (path === '/course/tasks') return { data: tasks.map(task => ({ ...task })) }
    const action = path.split('/').at(-1)
    const id = path.split('/').at(-2)
    tasks = tasks.map(task => task.task_id !== id ? task : { ...task, status: { pause: 'paused', resume: 'running', stop: 'cancelling' }[action] })
    return { status: 'success' }
  })
})
afterEach(() => { cleanup(); vi.useRealTimers(); vi.restoreAllMocks() })

function renderConsole() {
  return render(<RuntimeProfileContext.Provider value={{ isLocal: false }}>
    <MemoryRouter initialEntries={['/dashboard']} future={{ v7_startTransition: true, v7_relativeSplatPath: true }}><Routes>
      <Route path="/dashboard" element={<Dashboard />} />
      <Route path="/chaoxing-fanya" element={<h1>账号管理页面</h1>} />
    </Routes></MemoryRouter>
  </RuntimeProfileContext.Provider>)
}
const card = id => screen.getByRole('article', { name: new RegExp(` ${id}$`) })

test('server dashboard shows concurrent accounts, accurate chapter progress and legacy history', async () => {
  renderConsole()
  await screen.findByRole('article', { name: / task-a$/ })
  expect(screen.getByRole('heading', { level: 1, name: '任务控制台' })).toBeInTheDocument()
  expect(within(card('task-a')).getByRole('progressbar', { name: '章节进度' })).toHaveAttribute('aria-valuenow', '50')
  expect(within(card('task-b')).getByRole('progressbar', { name: '课程进度' })).toHaveAttribute('aria-valuenow', '25')
  expect(card('legacy')).toHaveTextContent('旧任务（账号未记录）')
  expect(mocks.api.mock.calls.every(([path]) => !path.includes('signin') && !path.includes('zhihuishu'))).toBe(true)
})

test('account and status filtering and search limit the task list and detail', async () => {
  const user = userEvent.setup()
  renderConsole()
  const aside = await screen.findByRole('complementary', { name: '账号筛选' })
  await user.click(await within(aside).findByRole('button', { name: /账号乙/ }))
  expect(screen.getAllByRole('article')).toHaveLength(1)
  expect(screen.getByRole('region', { name: '所选任务详情' })).toHaveTextContent('乙的日志')
  await user.click(within(aside).getByRole('button', { name: '全部账号' }))
  await user.click(within(screen.getByRole('group', { name: '任务状态筛选' })).getByRole('button', { name: '失败' }))
  expect(screen.getAllByRole('article')).toHaveLength(1)
  expect(card('legacy')).toBeInTheDocument()
  await user.click(screen.getByRole('button', { name: '已结束', exact: true }))
  expect(card('legacy')).toBeInTheDocument()
  await user.click(screen.getByRole('button', { name: '全部', exact: true }))
  await user.type(screen.getByRole('searchbox'), '课程甲')
  expect(screen.getAllByRole('article')).toHaveLength(1)
  expect(card('task-a')).toBeInTheDocument()
})

test('pause and resume target separate task IDs and refresh their statuses', async () => {
  const user = userEvent.setup()
  renderConsole()
  await screen.findByRole('article', { name: / task-a$/ })
  await user.click(within(card('task-a')).getByRole('button', { name: '暂停' }))
  await waitFor(() => expect(within(card('task-a')).getByRole('button', { name: '继续' })).toBeEnabled())
  expect(mocks.api).toHaveBeenCalledWith('/course/task/task-a/pause', { method: 'POST' })
  expect(within(card('task-b')).getByRole('button', { name: '继续' })).toBeEnabled()
  await user.click(within(card('task-b')).getByRole('button', { name: '继续' }))
  await waitFor(() => expect(within(card('task-b')).getByRole('button', { name: '暂停' })).toBeEnabled())
  expect(mocks.api).toHaveBeenCalledWith('/course/task/task-b/resume', { method: 'POST' })
})

test('stop requires confirmation and never controls the other account', async () => {
  const user = userEvent.setup()
  const confirm = vi.spyOn(window, 'confirm').mockReturnValue(false)
  renderConsole()
  await screen.findByRole('article', { name: / task-a$/ })
  await user.click(within(card('task-a')).getByRole('button', { name: '停止' }))
  expect(mocks.api.mock.calls.some(([path]) => path.endsWith('/stop'))).toBe(false)
  confirm.mockReturnValue(true)
  await user.click(within(card('task-a')).getByRole('button', { name: '停止' }))
  await waitFor(() => expect(mocks.api).toHaveBeenCalledWith('/course/task/task-a/stop', { method: 'POST' }))
  expect(mocks.api.mock.calls.some(([path]) => path === '/course/task/task-b/stop')).toBe(false)
})

test('a snapshot started before pause cannot leave the final state stale', async () => {
  const user = userEvent.setup()
  renderConsole()
  await screen.findByRole('article', { name: / task-a$/ })
  const oldTasks = makeTasks()
  let finishOldQuery
  let queries = 0
  const implementation = mocks.api.getMockImplementation()
  mocks.api.mockImplementation(path => {
    if (path === '/course/tasks' && queries++ === 0) return new Promise(resolve => { finishOldQuery = resolve })
    return implementation(path)
  })
  await user.click(screen.getByRole('button', { name: '刷新' }))
  await user.click(within(card('task-a')).getByRole('button', { name: '暂停' }))
  expect(mocks.api).toHaveBeenCalledWith('/course/task/task-a/pause', { method: 'POST' })
  await act(async () => { finishOldQuery({ data: oldTasks }); await Promise.resolve() })
  await waitFor(() => expect(within(card('task-a')).getByRole('button', { name: '继续' })).toBeEnabled())
  expect(queries).toBe(2)
})

test('failed refresh preserves progress, disables stale controls and recovers', async () => {
  const user = userEvent.setup()
  renderConsole()
  await screen.findByRole('article', { name: / task-a$/ })
  const implementation = mocks.api.getMockImplementation()
  mocks.api.mockImplementation(path => path === '/course/tasks' ? Promise.reject(new Error('offline')) : implementation(path))
  await user.click(screen.getByRole('button', { name: '刷新' }))
  expect(await screen.findByRole('alert')).toHaveTextContent('任务同步失败')
  expect(within(card('task-a')).getByRole('button', { name: '暂停' })).toBeDisabled()
  expect(within(card('task-a')).getByRole('progressbar', { name: '章节进度' })).toHaveAttribute('aria-valuenow', '50')
  mocks.api.mockImplementation(implementation)
  await user.click(screen.getByRole('button', { name: '刷新' }))
  await waitFor(() => expect(screen.queryByRole('alert')).not.toBeInTheDocument())
})

test('polls every five seconds without overlapping requests and cleans up', async () => {
  vi.useFakeTimers()
  const { unmount } = renderConsole()
  await act(async () => { await Promise.resolve() })
  expect(mocks.api.mock.calls.filter(([path]) => path === '/course/tasks')).toHaveLength(1)
  let complete
  const implementation = mocks.api.getMockImplementation()
  mocks.api.mockImplementation(path => path === '/course/tasks' ? new Promise(resolve => { complete = resolve }) : implementation(path))
  await act(async () => { await vi.advanceTimersByTimeAsync(15000) })
  expect(mocks.api.mock.calls.filter(([path]) => path === '/course/tasks')).toHaveLength(2)
  await act(async () => { complete({ data: tasks }); await Promise.resolve() })
  unmount()
  await act(async () => { await vi.advanceTimersByTimeAsync(10000) })
  expect(mocks.api.mock.calls.filter(([path]) => path === '/course/tasks')).toHaveLength(2)
})

test('opening account management does not issue task commands', async () => {
  renderConsole()
  await screen.findByRole('article', { name: / task-a$/ })
  fireEvent.click(screen.getByRole('button', { name: '管理账号 / 启动任务' }))
  expect(screen.getByRole('heading', { name: '账号管理页面' })).toBeInTheDocument()
  expect(mocks.api.mock.calls.some(([, options]) => options?.method === 'POST')).toBe(false)
})
