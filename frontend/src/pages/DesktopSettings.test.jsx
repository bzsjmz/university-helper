import { cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, expect, test, vi } from 'vitest'
import DesktopSettings from './DesktopSettings'
import { api } from '../utils/api'

vi.mock('../utils/api', () => ({ api: vi.fn() }))
const initial = { endpoint: 'https://api.deepseek.com/chat/completions', model: '', primary_configured: true, fallback_configured: false }
beforeEach(() => {
  api.mockReset()
  api.mockResolvedValue({ ...initial })
})
afterEach(cleanup)

test('loads status without displaying secrets and saves settings without making an AI call', async () => {
  const user = userEvent.setup()
  render(<DesktopSettings />)
  await user.type(await screen.findByLabelText('模型名称'), 'fixture-model')
  await user.type(screen.getByLabelText(/优先 Key（已配置/), 'fixture-key')
  await user.click(screen.getByRole('button', { name: '保存设置' }))
  await screen.findByText(/设置已保存/)
  const [, options] = api.mock.calls.find(([, value]) => value?.method === 'PUT')
  expect(JSON.parse(options.body)).toMatchObject({ model: 'fixture-model', primary_key: 'fixture-key', fallback_key: null })
  expect(screen.getByLabelText(/优先 Key（已配置/)).toHaveValue('')
  expect(api.mock.calls.every(([path]) => path === '/desktop/settings')).toBe(true)
})

test('keys are preserved by default and clearing sends an explicit empty value', async () => {
  const user = userEvent.setup()
  render(<DesktopSettings />)
  await screen.findByLabelText('模型名称')
  await user.click(screen.getByLabelText('清除优先 Key'))
  await user.click(screen.getByRole('button', { name: '保存设置' }))
  await waitFor(() => expect(api).toHaveBeenCalledTimes(2))
  expect(JSON.parse(api.mock.calls[1][1].body)).toMatchObject({ primary_key: '', fallback_key: null })
})

test('save failure keeps the entered key for retry and shows a useful error', async () => {
  const user = userEvent.setup()
  api.mockImplementation(async (path, options) => {
    if (options?.method === 'PUT') throw new Error('保存失败')
    return { ...initial }
  })
  render(<DesktopSettings />)
  await user.type(await screen.findByLabelText(/优先 Key（已配置/), 'fixture-key')
  await user.click(screen.getByRole('button', { name: '保存设置' }))
  expect(await screen.findByRole('alert')).toHaveTextContent('保存失败')
  expect(screen.getByLabelText(/优先 Key（已配置/)).toHaveValue('fixture-key')
})
