import { render, screen } from '@testing-library/react'
import { MemoryRouter, Routes, Route } from 'react-router-dom'
import { afterEach, describe, expect, test, vi } from 'vitest'
import { act, cleanup } from '@testing-library/react'

vi.mock('./utils/api', () => ({ api: vi.fn(async () => ({ tasks: [], accounts: [] })) }))
afterEach(cleanup)

import { AppLayout, ThemeProvider, ToastProvider } from './components'
import { RuntimeProfileContext } from './components/runtimeProfileContext'
import Dashboard from './pages/Dashboard'

// Smoke test: the full provider stack + shell + redesigned dashboard mount
// without throwing, dark-mode wiring runs, and key chrome renders.
describe('app shell smoke', () => {
  test('AppLayout + Dashboard render inside the providers', async () => {
    render(
      <ThemeProvider>
        <RuntimeProfileContext.Provider
          value={{ profile: 'server', isLocal: false, requiresAuth: true, loading: false }}
        >
          <ToastProvider>
            <MemoryRouter
              initialEntries={['/dashboard']}
              future={{ v7_startTransition: true, v7_relativeSplatPath: true }}
            >
              <Routes>
                <Route element={<AppLayout />}>
                  <Route path="/dashboard" element={<Dashboard />} />
                </Route>
              </Routes>
            </MemoryRouter>
          </ToastProvider>
        </RuntimeProfileContext.Provider>
      </ThemeProvider>
    )

    await act(async () => {})

    // AppLayout brand + nav
    expect(screen.getAllByText('学道').length).toBeGreaterThan(0)
    // Dashboard heading (unique to the home page)
    expect(screen.getByRole('heading', { level: 1, name: '任务控制台' })).toBeTruthy()
    // Service entries appear in both the nav and the cards
    expect(screen.getAllByText('学习通签到').length).toBeGreaterThan(0)
    expect(screen.getAllByText('智慧树').length).toBeGreaterThan(0)
    expect(screen.getAllByRole('link', { name: 'WE Learn' }).every(link => link.getAttribute('href') === '/welearn')).toBe(true)
    expect(screen.getAllByRole('link', { name: 'U校园试用' }).every(link => link.getAttribute('href') === '/english-learning')).toBe(true)
    // Theme toggle is present (radiogroup)
    expect(screen.getByRole('radiogroup', { name: '主题' })).toBeTruthy()
  })
})
