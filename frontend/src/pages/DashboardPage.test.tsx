/**
 * Covers DashboardPage's owned/shared rendering (criterion 3), plus the D-23 chosen expansion:
 * empty states, create-document, upload happy-path + 413/415 error message rendering, and
 * navigation into the editor. Renders via renderWithProviders (real AuthProvider + mocked api,
 * D-18/D-17).
 *
 * `../api` is mocked via an explicit factory (not the bare `vi.mock('../api')` automock other
 * files in this phase use) so `ApiError` stays the REAL class -- Vitest's automocking algorithm
 * for exported classes is documented only at "objects and class instances are deeply cloned",
 * with no guarantee the constructor logic that sets `this.status`/`this.message` survives intact.
 * The upload-error test below needs `actionError instanceof ApiError` (DashboardPage.tsx:35) to
 * hold for a mocked rejection, so this file spreads `...actual` to keep the genuine class rather
 * than depending on undocumented automock behaviour for it.
 *
 * `useNavigate` is mocked at the react-router-dom module boundary, keeping MemoryRouter/Routes/
 * Route/Link real (DocumentCard renders real <Link> elements the owned/shared tests assert
 * against) -- renderWithProviders only mounts one route per render, so a navigate spy is simpler
 * than adding a second stub route to the shared helper for this file alone (executor's choice,
 * per the plan's "either via renderWithProviders's router or by spying on the navigate hook").
 *
 * Upload conversion/limits (size, MIME/extension enforcement) stay backend-tested
 * (backend/tests/test_upload.py) -- the 413/415 test below only asserts the FRONTEND's message
 * mapping for a mocked ApiError, not that the server enforces those limits (D-23; T-09-12 in
 * 09-07-PLAN.md's threat model).
 */
import { cleanup, fireEvent, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { renderWithProviders, screen } from '../test/renderWithProviders'
import { DashboardPage } from './DashboardPage'
import { api, ApiError } from '../api'
import type { DocFull, DocList, DocSummary, User } from '../api'

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

const navigateMock = vi.fn()
vi.mock('react-router-dom', async (importOriginal) => {
  const actual = await importOriginal<typeof import('react-router-dom')>()
  return { ...actual, useNavigate: () => navigateMock }
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

function makeDocFull(overrides: Partial<DocFull> = {}): DocFull {
  return {
    id: 'doc-1',
    title: 'Untitled',
    content_html: '',
    role: 'owner',
    owner: { name: 'Alice' },
    created_at: '2026-01-01T00:00:00Z',
    updated_at: '2026-01-01T00:00:00Z',
    ...overrides,
  }
}

let docList: DocList = { owned: [], shared: [] }

beforeEach(() => {
  docList = { owned: [], shared: [] }
  navigateMock.mockClear()
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

  it('create-document navigates to the new editor', async () => {
    const user = userEvent.setup()
    vi.mocked(api.post).mockResolvedValue(makeDocFull({ id: 'new-doc-1' }))

    renderWithProviders(<DashboardPage />)
    await screen.findByText(/My documents/)

    await user.click(screen.getByRole('button', { name: /New document/ }))

    await waitFor(() => {
      expect(navigateMock).toHaveBeenCalledWith('/documents/new-doc-1')
    })
  })

  it('upload navigates to the new document on success', async () => {
    vi.mocked(api.upload).mockResolvedValue(makeDocFull({ id: 'uploaded-doc-1' }))

    const { container } = renderWithProviders(<DashboardPage />)
    await screen.findByText(/My documents/)

    // fireEvent.change (not userEvent.upload) so the input's `accept` attribute never filters
    // out the file we hand it -- this test wires the SAME happy-path file through both this test
    // and the 413/415 error test below, keeping the boundary purely about api.upload's
    // resolve/reject, not about jsdom's applyAccept filtering (userEvent default: true).
    const fileInput = container.querySelector('input[type="file"]') as HTMLInputElement
    const file = new File(['hello world'], 'notes.txt', { type: 'text/plain' })
    fireEvent.change(fileInput, { target: { files: [file] } })

    await waitFor(() => {
      expect(api.upload).toHaveBeenCalledWith('/documents/upload', file)
    })
    await waitFor(() => {
      expect(navigateMock).toHaveBeenCalledWith('/documents/uploaded-doc-1')
    })
  })

  it('upload surfaces a 413/415 error message', async () => {
    const { container } = renderWithProviders(<DashboardPage />)
    await screen.findByText(/My documents/)
    const fileInput = container.querySelector('input[type="file"]') as HTMLInputElement

    vi.mocked(api.upload).mockRejectedValueOnce(new ApiError(413, 'File is too large (max 1 MB).'))
    fireEvent.change(fileInput, {
      target: { files: [new File(['x'], 'big.txt', { type: 'text/plain' })] },
    })
    expect(await screen.findByText('File is too large (max 1 MB).')).toBeInTheDocument()

    vi.mocked(api.upload).mockRejectedValueOnce(new ApiError(415, 'Unsupported file type.'))
    fireEvent.change(fileInput, {
      target: { files: [new File(['x'], 'notes.txt', { type: 'text/plain' })] },
    })
    expect(await screen.findByText('Unsupported file type.')).toBeInTheDocument()
  })
})
