/**
 * Covers ShareModal's UI surface only (success criterion 3, D-22): the viewer/editor role
 * <select>, the shares list + revoke affordance, add-share wiring, and ApiError vs generic
 * error-message rendering. Renders via renderWithProviders (QueryClientProvider + real
 * AuthProvider, D-18) since ShareModal uses react-query's useQuery/useMutation.
 *
 * Scope boundary (D-22): the authorization matrix itself -- who is *allowed* to view/edit/share a
 * document, and the resulting 200/403/404 outcomes -- is covered end-to-end against the real
 * backend in backend/tests/test_access.py (test_owner_can_crud_others_cannot_see,
 * test_viewer_reads_only_then_editor_can_edit, test_listing_separates_owned_and_shared,
 * test_share_errors_for_unknown_email_and_self). Those tests stay the source of truth for "who can
 * do what" -- a mocked `api` here only proves this component renders whatever the mock returns, so
 * no test below asserts a role/permission outcome through the mock. What IS asserted is purely the
 * frontend's own responsibility: the role select's options, the shares list/revoke rendering, and
 * the addShare/revoke mutations' onError -> setError(.message) mapping.
 */
import { cleanup, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { renderWithProviders, screen } from '../test/renderWithProviders'
import { ShareModal } from './ShareModal'
import { api, ApiError } from '../api'
import type { Share, User } from '../api'

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
const DOC_ID = 'doc-1'

function makeShare(overrides: Partial<Share> = {}): Share {
  return { user_id: 2, name: 'Bob', email: 'bob@example.com', role: 'viewer', ...overrides }
}

let shares: Share[] = []

beforeEach(() => {
  shares = []
  vi.mocked(api.get).mockImplementation((path: string) => {
    if (path === '/auth/me') return Promise.resolve(TEST_USER)
    if (path === `/documents/${DOC_ID}/shares`) return Promise.resolve(shares)
    return Promise.reject(new Error(`ShareModal.test.tsx: unmocked api.get(${path})`))
  })
})

afterEach(() => {
  cleanup()
})

describe('ShareModal', () => {
  it('exposes exactly the viewer and editor role options', async () => {
    renderWithProviders(<ShareModal docId={DOC_ID} onClose={vi.fn()} />)
    await screen.findByText('Not shared with anyone yet.')

    const select = screen.getByRole('combobox') as HTMLSelectElement
    const optionValues = Array.from(select.options).map((o) => o.value)
    expect(optionValues).toEqual(['viewer', 'editor'])
  })

  it('renders the current shares list, each with a revoke affordance', async () => {
    shares = [
      makeShare({ user_id: 2, name: 'Bob' }),
      makeShare({ user_id: 3, name: 'Carol', role: 'editor' }),
    ]

    renderWithProviders(<ShareModal docId={DOC_ID} onClose={vi.fn()} />)

    expect(await screen.findByText(/Bob/)).toBeInTheDocument()
    expect(screen.getByText(/Carol/)).toBeInTheDocument()
    const removeButtons = screen.getAllByRole('button', { name: 'Remove' })
    expect(removeButtons).toHaveLength(2)
  })

  it('adding a share drives api.post and clears the input on success', async () => {
    const user = userEvent.setup()
    vi.mocked(api.post).mockResolvedValue(makeShare())
    renderWithProviders(<ShareModal docId={DOC_ID} onClose={vi.fn()} />)
    await screen.findByText('Not shared with anyone yet.')

    const emailInput = screen.getByPlaceholderText('teammate@example.com') as HTMLInputElement
    await user.type(emailInput, 'bob@example.com')
    await user.click(screen.getByRole('button', { name: 'Add' }))

    expect(api.post).toHaveBeenCalledWith(`/documents/${DOC_ID}/shares`, {
      email: 'bob@example.com',
      role: 'viewer',
    })
    await waitFor(() => expect(emailInput.value).toBe(''))
  })

  it('renders an ApiError message when adding a share fails', async () => {
    const user = userEvent.setup()
    vi.mocked(api.post).mockRejectedValue(new ApiError(409, 'Already shared with this user.'))
    renderWithProviders(<ShareModal docId={DOC_ID} onClose={vi.fn()} />)
    await screen.findByText('Not shared with anyone yet.')

    await user.type(screen.getByPlaceholderText('teammate@example.com'), 'bob@example.com')
    await user.click(screen.getByRole('button', { name: 'Add' }))

    expect(await screen.findByText('Already shared with this user.')).toBeInTheDocument()
  })

  it('renders the generic fallback message when revoke fails with a non-ApiError', async () => {
    const user = userEvent.setup()
    shares = [makeShare()]
    vi.mocked(api.del).mockRejectedValue(new Error('network down'))
    renderWithProviders(<ShareModal docId={DOC_ID} onClose={vi.fn()} />)

    await user.click(await screen.findByRole('button', { name: 'Remove' }))

    expect(await screen.findByText('Could not remove access')).toBeInTheDocument()
  })
})
