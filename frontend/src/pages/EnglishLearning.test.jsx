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
  await user.type(screen.getByLabelText('Cookie'), 'sid=private')
  await user.click(screen.getByRole('button', { name: '连接并读取课程' }))
  await screen.findByRole('button', { name: '读取课程内容' })
  expect(screen.queryByLabelText('Cookie')).not.toBeInTheDocument()
  expect(screen.getByRole('button', { name: '确认并启动' })).toBeDisabled()
  await user.selectOptions(screen.getByLabelText('课程'), 'c')
  await user.click(screen.getByRole('button', { name: '读取课程内容' }))
  await screen.findByRole('option', { name: /Reading/ })
  expect(screen.getByRole('button', { name: '确认并启动' })).toBeDisabled()
  await user.selectOptions(screen.getByLabelText('本次内容'), 'i')
  await user.click(screen.getByRole('button', { name: '确认并启动' }))
  await waitFor(() => expect(mocks.api).toHaveBeenCalledWith('/english-learning/sessions/s/tasks', expect.anything()))
  const body = mocks.api.mock.calls.find(([path]) => path.endsWith('/s/tasks'))[1].body
  expect(JSON.parse(body)).toEqual({ course: 'c', item: 'i', seconds: 60 })
  expect(body).not.toContain('private')
  expect(JSON.stringify(localStorage)).not.toContain('private')
  await user.selectOptions(screen.getByLabelText('课程'), 'd')
  expect(screen.getByLabelText('本次内容')).toHaveValue('')
  expect(screen.getByRole('button', { name: '确认并启动' })).toBeDisabled()
})

test('legacy U-campus explains browser state and manual interaction requirements', async () => {
  const user = userEvent.setup()
  render(<EnglishLearning />)
  await user.selectOptions(screen.getByLabelText('平台'), 'unipus')
  expect(screen.getByLabelText('Storage state JSON')).toBeInTheDocument()
  expect(screen.getByLabelText('课程目录网址')).toBeRequired()
  expect(screen.getByText(/需要后端安装 Playwright/)).toBeInTheDocument()
  expect(screen.getByText(/本页暂不自动答题/)).toBeInTheDocument()
})
