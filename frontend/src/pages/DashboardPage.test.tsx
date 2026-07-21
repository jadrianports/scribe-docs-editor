/**
 * Covers DashboardPage's owned/shared rendering (criterion 3) via a mocked `api` (D-17) and the
 * real `AuthProvider` (D-18), through `renderWithProviders`.
 *
 * `../api` is mocked via an explicit factory (not the bare `vi.mock('../api')` automock other
 * files in this phase use) so `ApiError` stays the REAL class -- Vitest's automocking algorithm
 * for exported classes is documented only at "objects and class instances are deeply cloned",
 * with no guarantee the constructor logic that sets `this.status`/`this.message` survives intact.
 * Task 2 below needs `actionError instanceof ApiError` (DashboardPage.tsx:35) to hold for a
 * mocked rejection, so this file spreads `...actual` to keep the genuine class rather than
 * depending on undocumented automock behaviour for it.
 */
import { cleanup, within } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { renderWithProviders, screen } from '../test/renderWithProviders'
import { DashboardPage } from './DashboardPage'
import { api } from '../api'
import type { DocList, DocSummary, User } from '../api'

vi.mock('../api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api')>()
  return {
    ...actual,
    api: {
      get: vi.fn(),
      post: vi.fn(),
      patch: vi.fn(),
      del: vi.fn(),
      upload: vi.fn(),
    },
  }
})

const TEST_USER: User = { id: 1, name: 'Alice', email: 'alice@example.com' }

function makeDocSummary(overrides: Partial<DocSummary> = {}): DocSummary {
  return {
    id: 'doc-1',
    title: 'Untitled',
    updated_at: '2026-01-01T00:00:00Z',
    owner: { name: 'Alice' },
    role: 'owner',
    ...overrides,
  }
}

let docList: DocList = { owned: [], shared: [] }

beforeEach(() => {
  docList = { owned: [], shared: [] }
  vi.mocked(api.get).mockImplementation((path: string) => {
    if (path === '/auth/me') return Promise.resolve(TEST_USER)
    if (path === '/documents') return Promise.resolve(docList)
    return Promise.reject(new Error(`DashboardPage.test.tsx: unmocked api.get(${path})`))
  })
})

afterEach(() => {
  cleanup()
})

describe('DashboardPage', () => {
  it('renders owned documents under My documents and shared documents under Shared with me', async () => {
    docList = {
      owned: [makeDocSummary({ id: 'owned-1', title: 'My doc' })],
      shared: [
        makeDocSummary({ id: 'shared-1', title: 'Shared doc', owner: { name: 'Bob' }, role: 'editor' }),
      ],
    }

    renderWithProviders(<DashboardPage />)

    const myHeading = await screen.findByText(/My documents/)
    const mySection = myHeading.closest('section')
    expect(mySection).not.toBeNull()
    expect(within(mySection!).getByText('My doc')).toBeInTheDocument()
    // Owned docs render through Section's default showOwner=false -- no "by <name>" span.
    expect(within(mySection!).queryByText(/^by /)).not.toBeInTheDocument()

    const sharedHeading = screen.getByText(/Shared with me/)
    const sharedSection = sharedHeading.closest('section')
    expect(sharedSection).not.toBeNull()
    // The showOwner path (DashboardPage.tsx:117's Section showOwner prop) -- shared docs render
    // "by <owner name>", pinning that the owner is actually shown, not just that the doc appears.
    expect(within(sharedSection!).getByText('Shared doc')).toBeInTheDocument()
    expect(within(sharedSection!).getByText('by Bob')).toBeInTheDocument()
  })

  it("renders each Section's empty-state text when its list is empty", async () => {
    docList = { owned: [], shared: [] }

    renderWithProviders(<DashboardPage />)

    expect(
      await screen.findByText('No documents yet — create one or upload a file to get started.'),
    ).toBeInTheDocument()
    expect(screen.getByText('Nothing has been shared with you yet.')).toBeInTheDocument()
  })
})
