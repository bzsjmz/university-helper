import { cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, expect, test, vi } from 'vitest'
import EnglishLearning from './EnglishLearning'

const mocks = vi.hoisted(() => ({ api: vi.fn() }))
vi.mock('../utils/api', () => ({ api: mocks.api }))
beforeEach(() => {
  mocks.api.mockReset()
  mocks.api.mockImplementation(async (path) => {
    if (path === '/english-learning/tasks') return { tasks: [] }
    if (path === '/english-learning/sessions') return { session_id: 's', courses: [{ id: 'c', name: 'English A' }, { id: 'd', name: 'English B' }] }
    if (path.endsWith('/items')) return { items: [{ id: 'i', name: 'Reading' }] }
    if (path.endsWith('/tasks')) return { id: 't', status: 'running', item_name: 'Reading', seconds: 60 }
    return {}
  })
})
afterEach(cleanup)

test('requires explicit course/content selection; credentials excluded from task and browser storage', async () => {
  const user = userEvent.setup()
  render(<EnglishLearning />)
  await user.click(screen.getByText('高级：导入已有登录会话'))
  await user.type(screen.getByLabelText('Cookie'), 'sid=private')
  await user.click(screen.getByRole('button', { name: '连接并读取课程' }))
  await screen.findByRole('button', { name: '读取课程内容' })
  expect(screen.queryByLabelText('Cookie')).not.toBeInTheDocument()
  expect(screen.getByRole('button', { name: '确认并启动' })).toBeDisabled()
  await user.selectOptions(screen.getByLabelText('课程'), 'c')
  await user.click(screen.getByRole('button', { name: '读取课程内容' }))
  await screen.findAllByRole('option', { name: /Reading/ })
  expect(screen.getByRole('button', { name: '确认并启动' })).toBeDisabled()
  await user.selectOptions(screen.getByLabelText('范围起点'), 'i')
  await user.click(screen.getByRole('button', { name: '确认并启动' }))
  await waitFor(() => expect(mocks.api).toHaveBeenCalledWith('/english-learning/sessions/s/tasks', expect.anything()))
  const body = mocks.api.mock.calls.find(([path]) => path.endsWith('/s/tasks'))[1].body
  expect(JSON.parse(body)).toEqual({ course: 'c', item: 'i', end_item: 'i', seconds: 300, mode: 'answer', submit: false, allow_ai: true })
  expect(body).not.toContain('private')
  expect(JSON.stringify(localStorage)).not.toContain('private')
  await user.selectOptions(screen.getByLabelText('课程'), 'd')
  expect(screen.getByLabelText('范围起点')).toHaveValue('')
  expect(screen.getByRole('button', { name: '确认并启动' })).toBeDisabled()
})

test('U-campus AI defaults to password login and hides technical import fields', async () => {
  const user = userEvent.setup()
  render(<EnglishLearning />)
  await user.selectOptions(screen.getByLabelText('平台'), 'unipus_ai')
  expect(screen.getByLabelText('账号')).toBeVisible()
  expect(screen.getByLabelText('密码')).toBeVisible()
  expect(screen.getByLabelText('Storage state JSON')).not.toBeVisible()
  expect(screen.queryByLabelText('课程目录网址')).not.toBeInTheDocument()
  expect(screen.getByText(/自动提交须单独勾选/)).toBeInTheDocument()
})

test('submission and paid AI require distinct explicit options in task preview', async () => {
  const user = userEvent.setup()
  render(<EnglishLearning />)
  await user.click(screen.getByText('高级：导入已有登录会话'))
  await user.type(screen.getByLabelText('Cookie'), 'sid=private')
  await user.click(screen.getByRole('button', { name: '连接并读取课程' }))
  await screen.findByRole('button', { name: '读取课程内容' })
  await user.selectOptions(screen.getByLabelText('课程'), 'c')
  await user.click(screen.getByRole('button', { name: '读取课程内容' }))
  await screen.findAllByRole('option', { name: /Reading/ })
  await user.selectOptions(screen.getByLabelText('范围起点'), 'i')
  const autoSubmit = screen.getByRole('checkbox', { name: /整页答案完整/ })
  const paidAi = screen.getByRole('checkbox', { name: /课件与缓存均未命中/ })
  expect(autoSubmit).not.toBeChecked()
  expect(paidAi).toBeChecked()
  await user.click(autoSubmit)
  await user.click(paidAi)
  expect(screen.getByText(/允许完整提交/)).toBeInTheDocument()
  await user.click(screen.getByRole('button', { name: '确认并启动' }))
  await waitFor(() => expect(mocks.api).toHaveBeenCalledWith('/english-learning/sessions/s/tasks', expect.anything()))
  const body = JSON.parse(mocks.api.mock.calls.find(([path]) => path.endsWith('/s/tasks'))[1].body)
  expect(body).toMatchObject({ mode: 'answer', submit: true, allow_ai: false })
})

test('range requires explicit endpoints and sends one sequential range task', async () => {
  mocks.api.mockImplementation(async (path) => {
    if (path === '/english-learning/tasks') return { tasks: [] }
    if (path === '/english-learning/sessions') return { session_id: 's', courses: [{ id: 'c', name: 'English A' }] }
    if (path.endsWith('/items')) return { items: ['a', 'b', 'c'].map(id => ({ id, name: `Lesson ${id}` })) }
    return { id: 'range', total_items: 3, completed_items: 0, status: 'running' }
  })
  const user = userEvent.setup()
  render(<EnglishLearning />)
  await user.click(screen.getByText('高级：导入已有登录会话'))
  await user.type(screen.getByLabelText('Cookie'), 'sid=private')
  await user.click(screen.getByRole('button', { name: '连接并读取课程' }))
  await user.selectOptions(await screen.findByLabelText('课程'), 'c')
  await user.click(screen.getByRole('button', { name: '读取课程内容' }))
  await screen.findAllByRole('option', { name: /Lesson a/ })
  expect(screen.getByRole('button', { name: '确认并启动' })).toBeDisabled()
  await user.selectOptions(screen.getByLabelText('范围起点'), 'a')
  await user.selectOptions(screen.getByLabelText('范围终点'), 'c')
  expect(screen.getByText(/已选 3 节/)).toBeInTheDocument()
  await user.click(screen.getByRole('button', { name: '确认并启动' }))
  const call = mocks.api.mock.calls.find(([path]) => path.endsWith('/s/tasks'))
  expect(JSON.parse(call[1].body)).toMatchObject({ item: 'a', end_item: 'c', course: 'c' })
  expect(mocks.api.mock.calls.filter(([path]) => path.endsWith('/s/tasks'))).toHaveLength(1)
})
