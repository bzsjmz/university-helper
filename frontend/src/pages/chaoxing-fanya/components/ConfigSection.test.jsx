import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, expect, test } from 'vitest'
import ConfigSection from './ConfigSection'
import useTaskConfig from '../hooks/useTaskConfig'

function Config() {
  return <ConfigSection {...useTaskConfig()} />
}
afterEach(cleanup)

test('defaults use two chapters and local answers before AI without external banks', () => {
  render(<Config />)
  expect(screen.getByRole('spinbutton', { name: /并发章节/ })).toHaveValue(2)
  expect(screen.getByRole('button', { name: '本地缓存，回退顺序 1' })).toHaveAttribute('aria-pressed', 'true')
  expect(screen.getByRole('button', { name: 'AI 智能答题，回退顺序 2' })).toHaveAttribute('aria-pressed', 'true')
  expect(screen.getByRole('button', { name: 'GO 题库' })).toHaveAttribute('aria-pressed', 'false')
  expect(screen.getByRole('button', { name: '言溪题库' })).toHaveAttribute('aria-pressed', 'false')
})

test('chapter concurrency can be typed above the former maximum', async () => {
  render(<Config />)
  const input = screen.getByRole('spinbutton', { name: /并发章节/ })
  expect(input).not.toHaveAttribute('max')
  const user = userEvent.setup()
  await user.clear(input)
  await user.type(input, '32')
  expect(input).toHaveValue(32)
  expect(screen.getByLabelText('并发章节：32')).toBe(input)
  fireEvent.change(input, { target: { value: '' } })
  fireEvent.blur(input)
  expect(input).toHaveValue(1)
})
