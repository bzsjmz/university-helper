import { act, cleanup, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, expect, test, vi } from 'vitest'
import BatchChaoxing from './BatchChaoxing'

const mocks = vi.hoisted(() => ({ api: vi.fn() }))
vi.mock('../utils/api', () => ({ api: mocks.api }))
const accounts = [
  { id: 'a', label: '账号甲', account_label: '138****0000' },
  { id: 'b', label: '账号乙', account_label: '139****0000' },
  { id: 'c', label: '账号丙', account_label: '137****0000' },
]
const active = [{ account_id: 'c', task_id: 'existing', status: 'paused' }]
const config = { speed: 1.5, concurrency: 2, unopenedStrategy: 'retry', tikuProvider: ['LocalCache', 'AI'],
  tikuToken: '', coverageThreshold: 0.9, submitMode: 'submit', correctOptions: '对', wrongOptions: '错', notifyService: '', notifyUrl: '' }
const chapters = [{ id: 'p1', title: '第一节' }, { id: 'p2', title: '第二节' }]

beforeEach(() => {
  mocks.api.mockReset()
  mocks.api.mockImplementation(async path => {
    if (path === '/course/tasks') return { data: active }
    if (path.endsWith('/chapters/shared_class_a') || path.endsWith('/chapters/shared_class_b')) return { chapters }
    if (path.endsWith('/courses')) {
      const id = path.includes('/a/') ? 'a' : 'b'
      return { courses: [{ courseId: 'shared', classId: `class_${id}`, name: `${id === 'a' ? '甲' : '乙'}的共同课程` },
        { courseId: `other-${id}`, name: '不选的课程' }] }
    }
    if (path.endsWith('/tasks')) return { task_id: `task-${path.split('/')[2]}` }
    throw new Error(`Unexpected request: ${path}`)
  })
})
afterEach(cleanup)

async function setup() {
  const user = userEvent.setup()
  const refreshTasks = vi.fn().mockResolvedValue()
  const view = render(<BatchChaoxing accounts={accounts} tasks={active} config={config} refreshTasks={refreshTasks} />)
  await user.click(screen.getByRole('button', { name: '勾选全部可用账号' }))
  await user.click(screen.getByRole('button', { name: '连接并读取所选账号课程' }))
  await waitFor(() => expect(screen.queryByText('正在逐个处理，请等待当前操作完成…')).not.toBeInTheDocument())
  return { user, refreshTasks, view }
}

const article = name => within(screen.getByRole('article', { name: `${name}的批量课程` }))

test('shows each account courses and requires review, preserving explicit course and chapter scope', async () => {
  const { user, refreshTasks } = await setup()
  expect(mocks.api).not.toHaveBeenCalledWith('/learning-accounts/c/courses', expect.anything())
  expect(screen.getByRole('checkbox', { name: /账号丙/ })).toBeDisabled()
  expect(screen.getByRole('button', { name: '预览启动清单（0 个账号）' })).toBeDisabled()
  expect(mocks.api.mock.calls.filter(([path, options]) => path.endsWith('/tasks') && options?.method === 'POST')).toHaveLength(0)
  await user.click(article('账号甲').getByRole('checkbox', { name: /甲的共同课程/ }))
  await user.click(article('账号乙').getByRole('checkbox', { name: /乙的共同课程/ }))
  await user.click(article('账号甲').getAllByRole('button', { name: '选章节' })[0])
  await user.click(await article('账号甲').findByRole('radio', { name: /第二节/ }))
  await user.click(article('账号甲').getByRole('button', { name: '刷新课程' }))
  await waitFor(() => expect(screen.queryByText('正在逐个处理，请等待当前操作完成…')).not.toBeInTheDocument())
  await user.click(screen.getByRole('button', { name: '预览启动清单（2 个账号）' }))
  const review = within(screen.getByLabelText('启动确认清单'))
  expect(review.getByText(/甲的共同课程 · 学到 第二节/)).toBeInTheDocument()
  expect(review.getByText(/乙的共同课程 · 全部章节/)).toBeInTheDocument()
  expect(review.queryByText(/不选的课程/)).not.toBeInTheDocument()
  expect(mocks.api.mock.calls.filter(([path, options]) => path.endsWith('/tasks') && options?.method === 'POST')).toHaveLength(0)
  await user.click(review.getByRole('button', { name: '确认启动 2 个账号' }))
  await screen.findByText(/启动请求成功 2 个，跳过 0 个/)
  const starts = mocks.api.mock.calls.filter(([path, options]) => path.endsWith('/tasks') && options?.method === 'POST')
  expect(starts).toHaveLength(2)
  const bodyA = JSON.parse(starts.find(([path]) => path.includes('/a/'))[1].body)
  const bodyB = JSON.parse(starts.find(([path]) => path.includes('/b/'))[1].body)
  expect(bodyA).toMatchObject({ course_ids: ['shared_class_a'], chapter_limits: { shared_class_a: 'p2' }, speed: 1.5, concurrency: 2, tiku_config: { provider: 'LocalCache,AI' } })
  expect(bodyB).toMatchObject({ course_ids: ['shared_class_b'], chapter_limits: {} })
  expect(JSON.stringify([bodyA, bodyB])).not.toMatch(/username|password/)
  expect(refreshTasks).toHaveBeenCalledTimes(1)
})

test('course loading failure is isolated and newly active accounts are skipped before starting', async () => {
  const defaultApi = mocks.api.getMockImplementation()
  mocks.api.mockImplementation(path => path === '/learning-accounts/a/courses'
    ? Promise.reject(new Error('甲连接失败')) : defaultApi(path))
  const { user } = await setup()
  expect(article('账号甲').getByRole('alert')).toHaveTextContent('甲连接失败')
  await user.click(article('账号乙').getByRole('checkbox', { name: /乙的共同课程/ }))
  await user.click(screen.getByRole('button', { name: '预览启动清单（1 个账号）' }))
  mocks.api.mockImplementation(path => path === '/course/tasks'
    ? Promise.resolve({ data: [...active, { account_id: 'b', status: 'running' }] }) : defaultApi(path))
  await user.click(screen.getByRole('button', { name: '确认启动 1 个账号' }))
  await screen.findByText(/启动请求成功 0 个，跳过 1 个/)
  expect(mocks.api.mock.calls.filter(([path, options]) => path.endsWith('/tasks') && options?.method === 'POST')).toHaveLength(0)
})

test('ambiguous startup errors are not retried and do not prevent other accounts from starting', async () => {
  const { user } = await setup()
  await user.click(article('账号甲').getByRole('checkbox', { name: /甲的共同课程/ }))
  await user.click(article('账号乙').getByRole('checkbox', { name: /乙的共同课程/ }))
  await user.click(screen.getByRole('button', { name: '预览启动清单（2 个账号）' }))
  const defaultApi = mocks.api.getMockImplementation()
  mocks.api.mockImplementation(path => path === '/learning-accounts/a/tasks'
    ? Promise.reject(Object.assign(new Error('timeout'), { status: 0 })) : defaultApi(path))
  await user.click(screen.getByRole('button', { name: '确认启动 2 个账号' }))
  await screen.findByText(/启动请求成功 1 个，跳过 0 个，失败或待核对 1 个/)
  expect(article('账号甲').getByRole('alert')).toHaveTextContent('启动结果未确认')
  expect(screen.getByRole('button', { name: '预览启动清单（0 个账号）' })).toBeDisabled()
  expect(mocks.api.mock.calls.filter(([path]) => path === '/learning-accounts/a/tasks')).toHaveLength(1)
})

test('double confirmation sends one request and settings changes invalidate the review', async () => {
  const { user, view, refreshTasks } = await setup()
  await user.click(article('账号甲').getByRole('checkbox', { name: /甲的共同课程/ }))
  await user.click(screen.getByRole('button', { name: '预览启动清单（1 个账号）' }))
  view.rerender(<BatchChaoxing accounts={accounts} tasks={active} config={{ ...config, concurrency: 3 }} refreshTasks={refreshTasks} />)
  expect(screen.queryByLabelText('启动确认清单')).not.toBeInTheDocument()
  await user.click(screen.getByRole('button', { name: '预览启动清单（1 个账号）' }))
  let resolve
  const defaultApi = mocks.api.getMockImplementation()
  mocks.api.mockImplementation(path => path === '/learning-accounts/a/tasks'
    ? new Promise(done => { resolve = done }) : defaultApi(path))
  await user.dblClick(screen.getByRole('button', { name: '确认启动 1 个账号' }))
  expect(mocks.api.mock.calls.filter(([path]) => path === '/learning-accounts/a/tasks')).toHaveLength(1)
  await act(async () => { resolve({ task_id: 'new-task' }) })
  expect(JSON.parse(mocks.api.mock.calls.find(([path]) => path === '/learning-accounts/a/tasks')[1].body).concurrency).toBe(3)
})
