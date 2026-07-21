/**
 * Shared render helper for component tests (D-15).
 *
 * Wraps `ui` in a fresh `QueryClientProvider` (a new `QueryClient` per call,
 * retries disabled so error paths surface immediately instead of retrying
 * silently), the REAL `AuthProvider` from `../auth/AuthContext`, and
 * `react-router-dom`'s `MemoryRouter`. `AuthContext` itself is intentionally
 * NOT exported from `AuthContext.tsx` (D-18) -- this helper renders the real
 * provider and lets its own loading -> user bootstrap run, rather than
 * reconstructing a parallel fake context.
 *
 * PRECONDITION: callers must `vi.mock('../api')` (path relative to the test
 * file) BEFORE rendering, so `AuthProvider`'s `api.get('/auth/me')` resolves
 * a mocked `User` instead of issuing a real network request. Without that
 * mock, every render stays stuck in `AuthProvider`'s initial loading state.
 *
 * Route-dependent components (`EditorPage` needs `useParams<'id'>()`,
 * `DashboardPage` needs `useNavigate()`) are supported through the optional
 * `path`/`initialEntries` params below instead of a second, bespoke render
 * helper -- one shared surface per D-15 / RESEARCH.md Open Question 1.
 *
 * Does NOT `vi.mock('y-websocket')` -- that seam stays local to
 * `useCollab.test.ts` only (Pitfall 1); component tests using this helper
 * that also need a collab connection compose it via `collabHarness.ts`.
 */
import type { ReactElement } from 'react'
import { render } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { AuthProvider } from '../auth/AuthContext'

export interface RenderWithProvidersOptions {
  /** Route path `ui` is mounted at, e.g. `/documents/:id`. Defaults to `/`. */
  path?: string
  /** MemoryRouter's initial history stack. Defaults to `[path]`. */
  initialEntries?: string[]
}

export function renderWithProviders(ui: ReactElement, options: RenderWithProvidersOptions = {}) {
  const { path = '/', initialEntries = [path] } = options

  const queryClient = new QueryClient({
    defaultOptions: {
      queries: { retry: false },
      mutations: { retry: false },
    },
  })

  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={initialEntries}>
        <AuthProvider>
          <Routes>
            <Route path={path} element={ui} />
          </Routes>
        </AuthProvider>
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

export { screen } from '@testing-library/react'
