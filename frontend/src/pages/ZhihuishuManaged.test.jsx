import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, expect, test, vi } from 'vitest'
import { api } from '../utils/api'
import ZhihuishuManaged from './ZhihuishuManaged'

vi.mock('../utils/api', () => ({ api: vi.fn() }))
const BASE = '/course/zhihuishu/managed'
const accounts = [
  { account_id: 'a', label: '账号甲', status: 'saved' },
  { account_id: 'b', label: '账号乙', status: 'saved' },
]
const courses = [{ course_id: 'one', course_name: '课程一', progress: '0%' },
  { course_id: 'two', course_name: '课程二', progress: '20%' }]
const response = (data) => Promise.resolve({ data })
let queueRows

beforeEach(() => {
  queueRows = []
  vi.spyOn(window, 'confirm').mockReturnValue(true)
  api.mockImplementation((path) => {
    if (path === `${BASE}/accounts`) return response(accounts)
    if (path === `${BASE}/queues`) return response(queueRows)
    if (path === `${BASE}/accounts/a/courses`) return response(courses)
    if (path === `${BASE}/accounts/b/courses`) return response([{ course_id: 'other', course_name: '乙的课程' }])
    if (path === `${BASE}/accounts/a/queue`) return response({ task_id: 'q' })
    if (path.startsWith(`${BASE}/queues/q/`)) return response({})
    throw new Error(`Unexpected request ${path}`)
  })
})
afterEach(() => { cleanup(); vi.restoreAllMocks(); api.mockReset() })

test('loads saved accounts without asking for another login', async () => {
  render(<ZhihuishuManaged />)
  expect(await screen.findByRole('checkbox', { name: /课程一/ }, { timeout: 10000 })).toBeInTheDocument()
  expect(screen.getByRole('button', { name: '账号甲' })).toHaveAttribute('aria-pressed', 'true')
  expect(api.mock.calls.some(([path]) => path.includes('/login/'))).toBe(false)
})

test('selects all courses and sends one ordered queue with chosen speed', async () => {
  render(<ZhihuishuManaged />)
  await screen.findByRole('checkbox', { name: /课程一/ })
  fireEvent.click(screen.getByRole('button', { name: '全选课程' }))
  fireEvent.change(screen.getByLabelText('倍速'), { target: { value: '2' } })
  fireEvent.click(screen.getByRole('button', { name: '按顺序学习所选 2 门课程' }))
  await waitFor(() => expect(api).toHaveBeenCalledWith(`${BASE}/accounts/a/queue`, expect.anything()))
  const [, options] = api.mock.calls.find(([path]) => path === `${BASE}/accounts/a/queue`)
  expect(JSON.parse(options.body)).toEqual({ courses: [
    { course_id: 'one', end_chapter_id: null }, { course_id: 'two', end_chapter_id: null }],
  speed: 2, task_type: 'course', auto_answer: true })
})

test('switching accounts clears selections and course scope', async () => {
  render(<ZhihuishuManaged />)
  await screen.findByRole('checkbox', { name: /课程一/ })
  fireEvent.click(screen.getByRole('button', { name: '全选课程' }))
  fireEvent.click(screen.getByRole('button', { name: '账号乙' }))
  await screen.findByRole('checkbox', { name: /乙的课程/ })
  expect(screen.queryByRole('checkbox', { name: /课程一/ })).not.toBeInTheDocument()
  expect(screen.getByRole('button', { name: '按顺序学习所选 0 门课程' })).toBeDisabled()
})

test('late course response from previous account cannot overwrite selected account', async () => {
  let resolveOld
  const previous = api.getMockImplementation()
  api.mockImplementation((path, options) => path === `${BASE}/accounts/a/courses`
    ? new Promise((resolve) => { resolveOld = resolve }) : previous(path, options))
  render(<ZhihuishuManaged />)
  fireEvent.click(await screen.findByRole('button', { name: '账号乙' }))
  await screen.findByRole('checkbox', { name: /乙的课程/ })
  await act(async () => resolveOld({ data: courses }))
  expect(screen.queryByRole('checkbox', { name: /课程一/ })).not.toBeInTheDocument()
})

test('active queue prevents deleting, updating login or starting duplicate courses', async () => {
  queueRows = [{ task_id: 'q', account_id: 'a', account_label: '账号甲', status: 'running', courses: [] }]
  render(<ZhihuishuManaged />)
  await screen.findByRole('checkbox', { name: /课程一/ })
  expect(screen.getByRole('button', { name: '删除所选账号' })).toBeDisabled()
  expect(screen.getByRole('button', { name: '更新所选账号登录' })).toBeDisabled()
  expect(screen.getByRole('button', { name: '全选课程' })).toBeDisabled()
})

test('queue controls target queue ID and show all accounts', async () => {
  queueRows = [{ task_id: 'q', account_id: 'b', account_label: '账号乙', status: 'paused', courses: [
    { course_id: 'other', course_name: '乙的课程', status: 'waiting' }] }]
  render(<ZhihuishuManaged />)
  fireEvent.click(await screen.findByRole('button', { name: '继续队列' }))
  await waitFor(() => expect(api).toHaveBeenCalledWith(`${BASE}/queues/q/resume`, expect.objectContaining({ method: 'POST' })))
  await waitFor(() => expect(screen.getByRole('button', { name: '停止队列' })).not.toBeDisabled())
  fireEvent.click(screen.getByRole('button', { name: '停止队列' }))
  await waitFor(() => expect(api).toHaveBeenCalledWith(`${BASE}/queues/q/cancel`, expect.objectContaining({ method: 'POST' })))
})

test('chapter limit applies only to the selected course', async () => {
  const previous = api.getMockImplementation()
  api.mockImplementation((path, options) => path === `${BASE}/accounts/a/videos/one`
    ? response([{ chapter_id: 'chapter', chapter_title: '第一章', title: '视频' }]) : previous(path, options))
  render(<ZhihuishuManaged />)
  await screen.findByRole('checkbox', { name: /课程一/ })
  fireEvent.click(screen.getAllByRole('button', { name: '读取章节范围' })[0])
  await screen.findByRole('option', { name: '第一章' })
  fireEvent.change(screen.getByLabelText('课程一截止章节'), { target: { value: 'chapter' } })
  fireEvent.click(screen.getByRole('button', { name: '全选课程' }))
  fireEvent.click(screen.getByRole('checkbox', { name: 'AI模式' }))
  fireEvent.click(screen.getByRole('button', { name: '按顺序学习所选 2 门课程' }))
  await waitFor(() => expect(api).toHaveBeenCalledWith(`${BASE}/accounts/a/queue`, expect.anything()))
  const body = JSON.parse(api.mock.calls.find(([path]) => path.endsWith('/queue'))[1].body)
  expect(body.courses).toEqual([{ course_id: 'one', end_chapter_id: 'chapter' }, { course_id: 'two', end_chapter_id: null }])
  expect(body.task_type).toBe('ai-course')
})

test('platform session expiry displays update-login guidance', async () => {
  const previous = api.getMockImplementation()
  api.mockImplementation((path, options) => path === `${BASE}/accounts/a/courses`
    ? Promise.reject(new Error('登录已失效，请更新登录。')) : previous(path, options))
  render(<ZhihuishuManaged />)
  expect(await screen.findByRole('alert')).toHaveTextContent('请更新登录')
  expect(screen.getByRole('button', { name: '获取登录二维码' })).toBeInTheDocument()
})
