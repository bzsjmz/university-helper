import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, expect, test } from 'vitest'
import ConfigSection from './ConfigSection'
import useTaskConfig from '../hooks/useTaskConfig'

function Config() {
  return <ConfigSection {...useTaskConfig()} />
}
afterEach(cleanup)

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
