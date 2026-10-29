import { render, screen, cleanup } from '@testing-library/react'
import { afterEach, expect, test, vi } from 'vitest'
import { RuntimeProfileContext } from '../components/runtimeProfileContext'
import Dashboard from './Dashboard'
import ChaoxingFanya from './ChaoxingFanya'

vi.mock('./TaskConsole', () => ({ default: () => <h1>当前任务控制台</h1> }))
vi.mock('./ManagedChaoxing', () => ({ default: () => <h1>当前多账号工作台</h1> }))
afterEach(cleanup)

test.each([true, false])('desktop and server share the current workbench (local=%s)', isLocal => {
  render(<RuntimeProfileContext.Provider value={{ isLocal }}><Dashboard /><ChaoxingFanya /></RuntimeProfileContext.Provider>)
  expect(screen.getByRole('heading', { name: '当前任务控制台' })).toBeInTheDocument()
  expect(screen.getByRole('heading', { name: '当前多账号工作台' })).toBeInTheDocument()
})
