import { cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, expect, test, vi } from 'vitest'
import ManagedChaoxing from './ManagedChaoxing'

const mocks = vi.hoisted(() => ({ api: vi.fn() }))
vi.mock('../utils/api', () => ({ api: mocks.api }))
vi.mock('../components', () => ({ StatusBadge: ({ status }) => <span>{status}</span> }))
vi.mock('./chaoxing-fanya/components/ConfigSection', () => ({ default: () => null }))
vi.mock('./chaoxing-fanya/components/LogSection', () => ({ default: () => null }))
vi.mock('./chaoxing-fanya/components/CourseListSection', () => ({
  default: ({ courses, setSelectedCourses }) => <button onClick={() => setSelectedCourses([courses[0].courseId])}>选择 {courses[0].name}</button>,
}))

const accounts = [
  { id: 'a', label: '账号甲', account_label: '138****0000' },
  { id: 'b', label: '账号乙', account_label: '139****0000' },
]
const tasks = [{ task_id: 'task-a', account_id: 'a', account_name: '账号甲', status: 'running', progress: {}, logs: [] }]

beforeEach(() => {
  mocks.api.mockReset()
  mocks.api.mockImplementation(async (path) => {
    if (path === '/learning-accounts') return { accounts }
    if (path === '/course/tasks') return { tasks }
    if (path.endsWith('/courses')) return { courses: [{ courseId: 'course-b', name: '乙的课程' }] }
    if (path.endsWith('/tasks')) return { task_id: 'task-b' }
    return { status: 'success' }
  })
})
afterEach(cleanup)

test('another account can start while account A runs, without resending credentials', async () => {
  const user = userEvent.setup()
  render(<ManagedChaoxing />)
  await user.click(await screen.findByRole('button', { name: /账号乙 ·/ }))
  await user.click(screen.getByRole('button', { name: '连接并读取课程' }))
  await user.click(await screen.findByRole('button', { name: '选择 乙的课程' }))
  const start = screen.getByRole('button', { name: '为所选账号启动任务' })
  expect(start).toBeEnabled()
  await user.click(start)
  await waitFor(() => expect(mocks.api).toHaveBeenCalledWith('/learning-accounts/b/tasks', expect.any(Object)))
  const options = mocks.api.mock.calls.find(([path]) => path === '/learning-accounts/b/tasks')[1]
  expect(JSON.parse(options.body)).toMatchObject({ course_ids: ['course-b'], concurrency: 1 })
  expect(options.body).not.toMatch(/username|password/)
})

test('late course response from A is ignored after selecting B', async () => {
  let resolveCourses
  const defaultApi = mocks.api.getMockImplementation()
  mocks.api.mockImplementation(path => path === '/learning-accounts/a/courses'
    ? new Promise(resolve => { resolveCourses = resolve }) : defaultApi(path))
  const user = userEvent.setup()
  render(<ManagedChaoxing />)
  await screen.findByRole('button', { name: /账号甲 ·/ })
  await user.click(screen.getByRole('button', { name: '连接并读取课程' }))
  await user.click(screen.getByRole('button', { name: /账号乙 ·/ }))
  resolveCourses({ courses: [{ courseId: 'a-course', name: '甲的课程' }] })
  await waitFor(() => expect(screen.getByRole('button', { name: '连接并读取课程' })).toBeEnabled())
  expect(screen.queryByRole('button', { name: '选择 甲的课程' })).not.toBeInTheDocument()
})

test('pause targets the explicitly selected task even while viewing another account', async () => {
  const user = userEvent.setup()
  render(<ManagedChaoxing />)
  await user.click(await screen.findByRole('button', { name: /账号乙 ·/ }))
  await user.click(screen.getByRole('button', { name: /账号甲 running/ }))
  await user.click(screen.getByRole('button', { name: '暂停' }))
  await waitFor(() => expect(mocks.api).toHaveBeenCalledWith('/course/task/task-a/pause', { method: 'POST' }))
  expect(screen.queryByRole('button', { name: '开始刷课' })).not.toBeInTheDocument()
})
