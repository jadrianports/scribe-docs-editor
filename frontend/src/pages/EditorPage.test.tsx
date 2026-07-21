/**
 * Single owner for every EditorPage collaboration test: the multi-client seed race (Phase 10
 * inverted this to model server-side seeding per D-15/D-17), the mutation-validated criterion-1
 * React key, the `immediatelyRender: true` synchronous-view contract (D-13), and
 * flush-on-unmount. The two client-side seed-effect tests that used to live here ('seeds a
 * brand-new document exactly once', 'does not double-insert when reopening an already-seeded
 * document') were removed in Phase 10 -- their subject, EditorPage's client seed effect, no
 * longer exists once the client became purely subtractive (D-15).
 *
 * EditorPage calls `useCollab(doc.id)` internally -- it does not accept a `conn` prop -- so the
 * only way to compose against `collabHarness.ts`'s `connect:false` `makeConnection()` (as Task 1's
 * plan action requires) is to replace the WHOLE `useCollab` hook via `vi.mock('../hooks/useCollab')`
 * for this file, then supply real, harness-built connections through it. This stays inside D-02's
 * boundary ("vi.mock('y-websocket') only in useCollab.test.ts") because this file never touches
 * 'y-websocket' directly -- every connection it hands out is still the REAL WebsocketProvider
 * `makeConnection()` builds, just injected through a different seam.
 *
 * The `useCollabFromQueue` hook-mock shape (used by most tests in this file) is a
 * `useState(() => queue.shift())` lazy initializer. The initializer runs exactly once per
 * component INSTANCE (React memoizes it across that instance's re-renders), so pushing N
 * connections onto a shared queue before rendering N EditorPage trees deterministically binds
 * tree 1 -> connection 1, tree 2 -> connection 2, etc, stable across every subsequent re-render of
 * that same instance (doc-query settling, title edits, ...).
 *
 * CRITERION-1 TEST DESIGN NOTE (D-11/D-24) -- read before touching that test: the first
 * implementation of this test wrapped EditorPage in `<StrictMode>` and relied on React's
 * automatic dev-only mount -> cleanup -> mount to swap the connection, exactly mirroring
 * CollaborativeEditor's own doc comment. Mutation-validating it (deleting the key, re-running)
 * did NOT go red -- empirically reproducing (and confirming, in a fresh way) D-11's own probe
 * finding that the naive StrictMode approach is toothless. The reason, confirmed by a throwaway
 * probe (run and deleted): React's StrictMode dev double-invoke runs effect -> cleanup -> effect
 * synchronously, back-to-back, BEFORE it ever commits an intermediate render -- so the two
 * `setConn(...)` calls inside that sequence collapse into ONE final state update. The CHILD
 * component (CollaborativeEditor) never actually mounts against the short-lived first connection
 * at all; it mounts directly against the surviving one regardless of whether the key is present.
 * StrictMode's automatic double-invoke therefore cannot be used to construct the discriminating
 * scenario in a test.
 *
 * The real bug the key defends against needs the SAME CollaborativeEditor instance to receive TWO
 * genuinely separate, COMMITTED renders with different `conn` identities (whether that swap is
 * triggered in production by StrictMode, a real reconnect, or anything else that replaces
 * useCollab's returned `conn` without unmounting EditorInner). The test below constructs that
 * directly and controllably via RTL's `rerender` -- render once bound to connA, destroy connA and
 * flip the mock to connB, then `rerender()` a fresh tree so React reconciles CollaborativeEditor
 * against a new `conn` without ever unmounting EditorInner. This is what a keyed
 * CollaborativeEditor forces to fully unmount/remount, and what an unkeyed one persists through
 * (permanently bound to the old EditorInstanceManager/editor from useEditor's no-deps-array
 * memoization -- see @tiptap/react's useEditor.ts, read during investigation). StrictMode is kept
 * around this test as a faithful backdrop (matching the real dev environment and ROADMAP
 * criterion 1's own wording) even though the discriminating transition itself is driven by
 * `rerender`, not by StrictMode's own scheduling.
 */
import { StrictMode, useState } from 'react'
import { act, cleanup, fireEvent, render, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { Editor } from '@tiptap/react'
import StarterKit from '@tiptap/starter-kit'
import Collaboration from '@tiptap/extension-collaboration'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { renderWithProviders, screen } from '../test/renderWithProviders'
import { makeConnection, sync } from '../test/collabHarness'
import type { TestCollabConnection } from '../test/collabHarness'
import { createManualRelay } from '../test/relayPump'
import { FULL_SCHEMA_HTML } from '../test/fullSchemaFixture'
import { EditorPage } from './EditorPage'
import { AuthProvider } from '../auth/AuthContext'
import { api } from '../api'
import type { DocFull, User } from '../api'
import { useCollab } from '../hooks/useCollab'

vi.mock('../api')
vi.mock('../hooks/useCollab', () => ({ useCollab: vi.fn() }))

const TEST_USER: User = { id: 1, name: 'Alice', email: 'alice@example.com' }

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

// Populated per-test via registerDoc(); the mocked api.get('/documents/:id') route reads from it.
let docsById: Record<string, DocFull> = {}
function registerDoc(doc: DocFull) {
  docsById[doc.id] = doc
}

// Shared by every test using the lazy-init hook mock -- see file header.
let connQueue: TestCollabConnection[] = []
function useCollabFromQueue(_docId: string) {
  const [conn] = useState<TestCollabConnection>(() => {
    const next = connQueue.shift()
    if (!next) {
      throw new Error('EditorPage.test.tsx: connQueue exhausted -- push a connection before rendering')
    }
    return next
  })
  return { conn, status: 'connected' as const, peers: [] }
}

// All connections any test constructs get pushed here so afterEach can unconditionally destroy()
// every one of them -- Pitfall 2 (leaked _checkInterval).
let connectionsToDestroy: TestCollabConnection[] = []

beforeEach(() => {
  docsById = {}
  connQueue = []
  connectionsToDestroy = []

  vi.mocked(api.get).mockImplementation((path: string) => {
    if (path === '/auth/me') return Promise.resolve(TEST_USER)
    const match = /^\/documents\/(.+)$/.exec(path)
    if (match && docsById[match[1]]) return Promise.resolve(docsById[match[1]])
    return Promise.reject(new Error(`EditorPage.test.tsx: unmocked/unregistered api.get(${path})`))
  })
  vi.mocked(api.patch).mockImplementation((path: string, body: unknown) => {
    const match = /^\/documents\/(.+)$/.exec(path)
    const existing = match ? docsById[match[1]] : undefined
    return Promise.resolve({ ...(existing ?? makeDocFull()), ...(body as object) } as DocFull)
  })
  vi.mocked(useCollab).mockImplementation(useCollabFromQueue)
})

afterEach(() => {
  cleanup()
  connectionsToDestroy.forEach((conn) => conn.destroy())
})

// --- Fragment-shape helpers (D-05/D-12: assert on ydoc.getXmlFragment(...), never getHTML()) ---

/** Counts (non-overlapping) occurrences of a literal open-tag substring in a fragment string. */
function countTag(fragmentString: string, tagOpen: string): number {
  return fragmentString.split(tagOpen).length - 1
}

/**
 * Writes `html` directly into `doc`'s shared fragment via a real, disposable Editor+Collaboration
 * instance bound to `doc` -- i.e. from OUTSIDE any mounted EditorPage tree, simulating another
 * peer (or, for FULL_SCHEMA_HTML, the server) writing into the shared CRDT. The reference editor
 * is destroyed immediately after so it doesn't linger as a second view over `doc`.
 */
function writeContent(doc: TestCollabConnection['doc'], html: string): void {
  const referenceEditor = new Editor({
    extensions: [
      StarterKit.configure({
        heading: { levels: [1, 2, 3] },
        link: false,
        code: false,
        codeBlock: false,
        horizontalRule: false,
        undoRedo: false,
      }),
      Collaboration.configure({ document: doc }),
    ],
  })
  referenceEditor.commands.setContent(html)
  referenceEditor.destroy()
}

/**
 * Seeds a real, connection-backed doc the way the server now does (D-08's seed-before-serve
 * contract, backend/app/collab/seeding.py): `writeContent(doc, FULL_SCHEMA_HTML)` then flips
 * `config.seeded`.
 */
function seedDocServerStyle(doc: TestCollabConnection['doc']): void {
  writeContent(doc, FULL_SCHEMA_HTML)
  doc.getMap('config').set('seeded', true)
}

describe('EditorPage', () => {
  describe('multi-client seed race -- closed server-side (D-04/D-08/D-09/D-15/D-17)', () => {
    /**
     * PHASE 10 CLOSURE -- this test used to be a documented-red `it.fails(...)` reproduction of a
     * genuine multi-client seed race (see 09-06-SUMMARY.md and this phase's 10-CONTEXT.md for the
     * full history: two independent connect:false connections, both driven to `synced` in the true
     * simultaneous window before any relay pump, would each independently pass the client-side
     * `!config.get('seeded')` guard and insert FULL_SCHEMA_HTML, so the merged fragment ended up
     * with the content TWICE -- a genuine data-corruption finding, not a test artifact, and not
     * fixable with a better client-side guard since Y.XmlFragment inserts are concurrent-append
     * CRDT operations that Yjs merges rather than deduplicates).
     *
     * Phase 10 closed the race SERVER-SIDE (D-15): the client's seed effect is deleted entirely
     * (EditorPage.tsx no longer calls `editor.commands.setContent` at all), and the server seeds
     * the room's canonical Y.Doc once, before any client ever connects
     * (backend/app/collab/seeding.py). No client can observe `seeded === false` for a doc that has
     * content, so the race is unreachable from the client by construction.
     *
     * This test now models that reality directly (D-17): the shared doc is pre-seeded, mirroring
     * the server's seed-before-serve contract, BEFORE either tree renders -- then both connections
     * are driven through the same true-simultaneous-window `sync()` timing the original
     * reproduction used. It's a normal passing `it(...)`, not `it.fails(...)`, and stays a LIVING
     * reproduction: if client-side seeding were ever reintroduced, this test would go red again
     * exactly like the original did (two inserts into an already-seeded doc, exactly-once
     * assertion fails).
     */
    it('asserts seeded content appears exactly once when both clients observe an already server-seeded doc', async () => {
      const connA = makeConnection('race-doc')
      const connB = makeConnection('race-doc')
      connectionsToDestroy.push(connA, connB)
      connQueue.push(connA, connB)
      registerDoc(makeDocFull({ id: 'race-doc', content_html: FULL_SCHEMA_HTML }))

      const relay = createManualRelay(connA.doc, connB.doc)

      // Server-side pre-seed (D-08/D-15): the room's canonical doc is already seeded before any
      // client connects. Seed connA's doc directly, then relay that single seed update to connB
      // right away -- both connections observe an already-seeded doc from their very first render,
      // exactly like a real WebsocketProvider handshake replaying a canonical room's existing
      // state, rather than either client inserting anything itself.
      seedDocServerStyle(connA.doc)
      relay.pumpAtoB()

      const treeA = renderWithProviders(<EditorPage />, {
        path: '/documents/:id',
        initialEntries: ['/documents/race-doc'],
      })
      await treeA.findByLabelText('Document title')

      const treeB = renderWithProviders(<EditorPage />, {
        path: '/documents/:id',
        initialEntries: ['/documents/race-doc'],
      })
      await treeB.findByLabelText('Document title')

      // Same true-simultaneous window as the original reproduction: both connections reach
      // `synced` before any further pump. Post-D-15 this window is inert by construction -- neither
      // EditorPage instance has a seed effect left to run.
      await act(async () => {
        sync(connA)
        sync(connB)
        await Promise.resolve()
      })

      await act(async () => {
        relay.pumpAtoB()
        relay.pumpBtoA()
        await Promise.resolve()
      })

      const mergedFragmentString = connA.doc.getXmlFragment('default').toString()
      expect(countTag(mergedFragmentString, '<heading level="1">')).toBe(1)
    })
  })

  describe('criterion 1: React key on conn.doc.guid (D-11/D-24, mutation-validated)', () => {
    it('rebinds the editor to a replacement connection instead of staying wired to the destroyed one', async () => {
      const connA = makeConnection('doc-key-swap')
      const connB = makeConnection('doc-key-swap')
      connectionsToDestroy.push(connA, connB)
      registerDoc(makeDocFull({ id: 'doc-key-swap', content_html: FULL_SCHEMA_HTML }))

      // A plain (non-hook-state) mock: `currentConn` is read fresh on every invocation, so
      // flipping it and forcing a `rerender()` produces a genuine, committed conn-identity change
      // on the SAME EditorInner instance -- see the file header's design note for why this
      // (rather than relying on StrictMode's own double-invoke) is what actually discriminates.
      let currentConn: TestCollabConnection = connA
      vi.mocked(useCollab).mockImplementation(() => ({
        conn: currentConn,
        status: 'connected' as const,
        peers: [],
      }))

      // Built locally (not via renderWithProviders) so the SAME QueryClient/Router/AuthProvider
      // instances survive the rerender below -- renderWithProviders builds a fresh QueryClient
      // per call, which would blow away the cached document query and unmount EditorInner instead
      // of reconciling it, defeating the whole point of this test. `buildTree()` is a FUNCTION
      // (not a single pre-built element reused by reference) deliberately: react-router's
      // `<Routes>` bailed out of even re-invoking `EditorPage` when `rerender()` was called with
      // the literal same element object both times (confirmed via a throwaway probe -- the
      // `useCollab` mock was never called a second time) -- passing a FRESH element tree each call
      // is what RTL's `rerender` actually expects and is what makes the swap below observable.
      const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
      const buildTree = () => (
        <QueryClientProvider client={queryClient}>
          <MemoryRouter initialEntries={['/documents/doc-key-swap']}>
            <AuthProvider>
              <StrictMode>
                <Routes>
                  <Route path="/documents/:id" element={<EditorPage />} />
                </Routes>
              </StrictMode>
            </AuthProvider>
          </MemoryRouter>
        </QueryClientProvider>
      )

      const { rerender } = render(buildTree())
      await screen.findByLabelText('Document title')

      // Bind against connA first and confirm CollaborativeEditor genuinely mounted against it
      // -- otherwise the assertion below wouldn't prove anything. PHASE 10 (D-15): the original
      // evidence mechanism here was the client's own seed effect (writing into connA.doc on
      // `sync`); that effect no longer exists post-D-15, so this write comes from OUTSIDE the
      // mounted tree instead (`writeContent`, simulating another peer/the server writing into the
      // shared CRDT) -- discriminating on whether the mounted editor's rendered view reflects a
      // live doc it's actually bound to, which is the same property the original assertion pinned.
      await act(async () => {
        sync(connA)
        await Promise.resolve()
      })
      writeContent(connA.doc, '<p>marker-connA</p>')
      await waitFor(() => {
        expect(screen.getByText('marker-connA')).toBeInTheDocument()
      })

      // Replace the connection WITHOUT unmounting EditorInner -- exactly what a real reconnection
      // (StrictMode's dev double-mount, a genuine socket drop-and-reconnect) does to useCollab's
      // returned `conn` in production: the old connection is torn down and a new one takes its
      // place on the same component instance.
      connA.destroy()
      currentConn = connB
      rerender(buildTree())

      await act(async () => {
        sync(connB)
        await Promise.resolve()
      })

      // Discriminating assertion: with the key, CollaborativeEditor was torn down and rebuilt
      // against connB, so a write into connB.doc becomes visible in the rendered view. Without the
      // key, the persisted CollaborativeEditor instance's `useEditor()` -- called with no explicit
      // `deps` array, so @tiptap/react's EditorInstanceManager.refreshEditorInstance only recreates
      // the editor when `deps.length !== 0` (frontend/node_modules/@tiptap/react/src/useEditor.ts,
      // read during investigation) -- never rebinds, so the editor stays wired to connA's
      // now-destroyed Y.Doc and a write into connB.doc never renders.
      writeContent(connB.doc, '<p>marker-connB</p>')
      await waitFor(() => {
        expect(screen.getByText('marker-connB')).toBeInTheDocument()
      })

      // MUTATION-VALIDATION (D-11/D-24, executor-enforced, performed not deferred, DESIGN
      // ITERATION RECORDED): the first version of this test wrapped EditorPage in <StrictMode>
      // and relied solely on its automatic dev-only mount -> cleanup -> mount to swap the
      // connection (mirroring CollaborativeEditor's own doc comment literally). Deleting
      // `key={conn.doc.guid}` at EditorPage.tsx:156 and re-running `npm test -- EditorPage` did
      // NOT turn that version red -- independently reproducing D-11's own probe finding that the
      // naive StrictMode framing is toothless. A follow-up throwaway probe (run and deleted)
      // confirmed why: React's StrictMode dev double-invoke runs effect -> cleanup -> effect
      // synchronously, collapsing the two `setConn(...)` calls into one final committed render, so
      // CollaborativeEditor never actually mounts against the short-lived first connection at all
      // -- there is nothing for the key to protect in that construction. This CURRENT version --
      // driving the conn swap explicitly via `rerender()` instead of depending on StrictMode's
      // internal batching -- was then mutation-validated the same way: deleted the key, re-ran
      // `npm test -- EditorPage`, confirmed THIS test went RED. Restored EditorPage.tsx verbatim
      // afterward (`git diff` confirmed empty) and re-ran the suite green. Full transcript of both
      // rounds recorded in 09-06-SUMMARY.md.
      //
      // PHASE 10 RE-VALIDATION (D-15): the discriminating write/assertion pair above was swapped
      // from seed-effect fragment checks to direct `writeContent`/DOM-text checks (the seed effect
      // it originally pinned on no longer exists). Re-ran the same mutation-validation procedure
      // against THIS version: deleted the key, re-ran `npm test -- EditorPage`, confirmed this test
      // went RED again (the second `waitFor` above timed out -- 'marker-connB' never appeared
      // because the stale CollaborativeEditor instance stayed bound to connA's destroyed Y.Doc).
      // Restored EditorPage.tsx verbatim (`git diff` confirmed empty) and re-ran the suite green.
    })
  })

  describe('immediatelyRender: true synchronous view (D-13)', () => {
    it('makes the editor view (and the Toolbar it gates) available in the same commit CollaborativeEditor mounts in', async () => {
      const conn = makeConnection('doc-sync-view')
      connectionsToDestroy.push(conn)
      connQueue.push(conn)
      registerDoc(makeDocFull({ id: 'doc-sync-view', content_html: '<p>hello</p>' }))

      renderWithProviders(<EditorPage />, {
        path: '/documents/:id',
        initialEntries: ['/documents/doc-sync-view'],
      })

      // The only async gate before CollaborativeEditor mounts is the react-query doc fetch --
      // waited for here via `findByLabelText`. Once that resolves, `conn` (from the queue mock)
      // is available synchronously, so CollaborativeEditor mounts in that SAME commit. Toolbar
      // only renders once `editor` is non-null (EditorPage.tsx:263's `{editor && editable && ...}`)
      // -- asserting it with a synchronous `getByTitle` (not an additional `findBy`/`waitFor`)
      // right after that single await is the positive-evidence check for `immediatelyRender:
      // true`: if the editor view were built asynchronously instead, this synchronous query would
      // fail because Toolbar would not exist yet in this same commit.
      await screen.findByLabelText('Document title')
      expect(screen.getByTitle('Bold (Ctrl+B)')).toBeInTheDocument()
    })
  })

  describe('flush-on-unmount (D-26 -- tested here, not in useAutosave.test.ts; see traceability note below)', () => {
    // REQ-frontend-ui-tests locates flush-on-unmount onto useAutosave, but the cleanup that fires
    // it (`useEffect(() => () => void flush(), [flush])`) lives at EditorPage.tsx:115, not inside
    // useAutosave.ts itself -- useAutosave.test.ts's own header comment records this same split
    // (D-26; 09-CONTEXT.md's traceability note).
    it('flushes a pending title change on unmount, before the 800ms debounce timer would have fired', async () => {
      const conn = makeConnection('doc-flush')
      connectionsToDestroy.push(conn)
      connQueue.push(conn)
      registerDoc(makeDocFull({ id: 'doc-flush', title: 'Original title', content_html: '<p>x</p>' }))

      const { unmount } = renderWithProviders(<EditorPage />, {
        path: '/documents/:id',
        initialEntries: ['/documents/doc-flush'],
      })
      const titleInput = await screen.findByLabelText<HTMLInputElement>('Document title')

      fireEvent.change(titleInput, { target: { value: 'Updated title' } })

      // No fake timers, no advancing 800ms -- flush-on-unmount must fire the pending save
      // immediately via the cleanup effect, independent of the debounce timer ever elapsing.
      unmount()

      await waitFor(() => {
        expect(api.patch).toHaveBeenCalledWith('/documents/doc-flush', { title: 'Updated title' })
      })
    })
  })
})
