/**
 * Single owner for every EditorPage collaboration/seed-path test (D-15): brand-new-doc seeding,
 * reopened-doc idempotency, the multi-client seed race, the mutation-validated criterion-1 React
 * key, the `immediatelyRender: true` synchronous-view contract (D-13), and flush-on-unmount.
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
import * as Y from 'yjs'
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

/**
 * Builds a Y.Doc + real editor completely independent of any test connection, seeds it once from
 * FULL_SCHEMA_HTML, and returns its fragment's string form -- the canonical "exactly one clean
 * seed" shape every seed test compares against. Using a live reference (not a hand-copied
 * constant) means this stays correct if fullSchemaFixture.ts's content ever changes, and it is
 * the strongest single assertion available: any duplication, omission or reordering in the
 * system-under-test's fragment changes this string.
 */
function buildSingleSeedFragmentString(): string {
  const referenceDoc = new Y.Doc()
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
      Collaboration.configure({ document: referenceDoc }),
    ],
  })
  referenceEditor.commands.setContent(FULL_SCHEMA_HTML)
  const fragmentString = referenceDoc.getXmlFragment('default').toString()
  referenceEditor.destroy()
  referenceDoc.destroy()
  return fragmentString
}

/** Counts (non-overlapping) occurrences of a literal open-tag substring in a fragment string. */
function countTag(fragmentString: string, tagOpen: string): number {
  return fragmentString.split(tagOpen).length - 1
}

describe('EditorPage', () => {
  describe('seed-on-mount (D-03/D-05/D-12)', () => {
    it('seeds a brand-new document exactly once', async () => {
      const conn = makeConnection('doc-brand-new')
      connectionsToDestroy.push(conn)
      connQueue.push(conn)
      registerDoc(makeDocFull({ id: 'doc-brand-new', content_html: FULL_SCHEMA_HTML }))

      renderWithProviders(<EditorPage />, {
        path: '/documents/:id',
        initialEntries: ['/documents/doc-brand-new'],
      })
      await screen.findByLabelText('Document title')

      // Not synced yet -- trySeed's `if (!provider.synced) return` guard means the fragment is
      // still untouched at this point, confirming the assertions below are about the seed EFFECT
      // running, not some pre-existing content.
      expect(conn.doc.getXmlFragment('default').toString()).toBe('')

      await act(async () => {
        sync(conn) // provider.synced = true -- drives EditorPage.tsx:254's trySeed via 'sync'
        await Promise.resolve()
      })

      const seededFragmentString = conn.doc.getXmlFragment('default').toString()
      const referenceFragmentString = buildSingleSeedFragmentString()

      // Strongest evidence: the WHOLE seeded structure matches a known-clean single seed exactly
      // -- any double-insert, drop, or reorder changes this string.
      expect(seededFragmentString).toBe(referenceFragmentString)

      // Itemized per-node-name coverage (D-12's letter), each count taken from the SAME reference
      // string above rather than a hand-guessed literal, so these can't silently drift out of
      // sync with fullSchemaFixture.ts. Tags are matched as OPEN tags (`<name` or `<name attr=`)
      // -- a bare substring like "bold" also matches the closing `</bold>` tag AND the literal
      // word "bold" in the run's own text content, which is not what "appears once" means here
      // (probe-verified: bare "bulletlist" counted 2 for one legitimate element, one open + one
      // close). Structural node names that are genuinely unique in the fixture assert 1;
      // bold/italic/underline/strike assert 2 -- not 1 -- because fullSchemaFixture.ts's
      // FULL_SCHEMA_HTML deliberately includes one standalone run of each mark PLUS a combined
      // "combo" run wrapping all four together (09-02-SUMMARY.md's own deviation note), and
      // Y.XmlText's toString() renders each mark as a real nested tag, not a single merged one --
      // so a correct single seed legitimately produces two `<bold>` open-tag substrings, not one.
      // Asserting a literal "1" here would either fail on correct code or require weakening the
      // shared fixture this plan doesn't own; see 09-06-SUMMARY.md.
      for (const tag of ['<heading level="1">', '<heading level="2">', '<heading level="3">', '<bulletlist>', '<orderedlist', '<blockquote>']) {
        expect(countTag(seededFragmentString, tag)).toBe(countTag(referenceFragmentString, tag))
        expect(countTag(seededFragmentString, tag)).toBe(1)
      }
      for (const mark of ['<bold>', '<italic>', '<underline>', '<strike>']) {
        expect(countTag(seededFragmentString, mark)).toBe(countTag(referenceFragmentString, mark))
        expect(countTag(seededFragmentString, mark)).toBe(2)
      }

      // D-03: pin the exact literals the cross-language contract depends on. The counterpart is
      // backend/app/collab/snapshot.py's derive_snapshot_html, which reads
      // `ydoc.get("config", type=Map).get("seeded")` -- same map name "config", same key
      // "seeded" -- already pinned from the Python side in
      // backend/tests/test_collab_persistence.py. Changing either side's literal breaks this pin.
      expect(conn.doc.getMap('config').get('seeded')).toBe(true)
    })

    it('does not double-insert when reopening an already-seeded document', async () => {
      const conn = makeConnection('doc-reopened')
      connectionsToDestroy.push(conn)
      // Simulates reopening a document that was already collaboratively seeded in a prior
      // session -- pre-set the flag BEFORE mount, mirroring what a real second-open would see.
      conn.doc.getMap('config').set('seeded', true)
      connQueue.push(conn)
      registerDoc(makeDocFull({ id: 'doc-reopened', content_html: FULL_SCHEMA_HTML }))

      renderWithProviders(<EditorPage />, {
        path: '/documents/:id',
        initialEntries: ['/documents/doc-reopened'],
      })
      await screen.findByLabelText('Document title')

      await act(async () => {
        sync(conn)
        await Promise.resolve()
      })

      // The seed effect's `!config.get('seeded')` guard must no-op: the fragment stays exactly as
      // empty as it started, never gaining a copy of FULL_SCHEMA_HTML.
      expect(conn.doc.getXmlFragment('default').toString()).toBe('')
      expect(conn.doc.getMap('config').get('seeded')).toBe(true) // untouched, still true
    })
  })

  describe('multi-client seed race (D-04/D-05/D-08/D-09)', () => {
    /**
     * D-08/D-09 ESCALATION BOUNDARY -- determination recorded here and in 09-06-SUMMARY.md.
     *
     * Empirically reproduced (throwaway probe, run and deleted, mirroring CONTEXT.md's own
     * feasibility-probe methodology): two independent connect:false connections, both driven to
     * `synced` BEFORE any relay pump (the true simultaneous window -- neither has observed the
     * other's `config.seeded` flag), each independently pass the client-side guard and insert
     * FULL_SCHEMA_HTML. Once pumped, the merged fragment contains the content TWICE (confirmed:
     * `<heading level="1">Heading 1</heading>` appears 2 times, not 1, after
     * pumpAtoB()+pumpBtoA()) -- a genuine data-corruption finding, not a test artifact.
     *
     * This is NOT a fixable client-side guard bug: Y.XmlFragment structural inserts are
     * concurrent-append CRDT operations, not last-write-wins like a Y.Map value set. Two clients
     * that both observe `seeded === false` before either one's write propagates will ALWAYS both
     * insert, and Yjs will merge (not deduplicate) both insertions -- this holds regardless of
     * transport topology (peer-relay here, or the real star topology through the server's
     * `RoomManager`-held canonical Y.Doc), because Yjs updates are commutative/associative by
     * design. No change contained to the existing client-side effect (EditorPage.tsx:240-254)
     * closes this window; the only fix that removes the race entirely is SERVER-SIDE seeding
     * (the server seeds the room's Y.Doc once, before any client ever connects, so no client can
     * observe `seeded === false`) -- exactly D-09's second branch.
     *
     * Per D-09: STOPPED here rather than attempting a fix in this phase. Escalated to
     * ROADMAP.md as a new phase (10, "Collaborative Seed-Race: Server-Side Seeding") and recorded
     * in ROADMAP.md's Deviations Register.
     *
     * The assertion below is written as the CORRECT expected behavior (exactly once) and is
     * currently red for the reason documented above -- `it.fails` keeps this a living,
     * executable reproduction (it will flip to a genuine, visible failure the moment the
     * underlying race is fixed without this test being updated, which is the point) without
     * failing the phase's `npm test` gate.
     */
    it.fails('asserts seeded content appears exactly once after the simultaneous-window race (currently red -- see comment; escalated per D-08/D-09)', async () => {
      const connA = makeConnection('race-doc')
      const connB = makeConnection('race-doc')
      connectionsToDestroy.push(connA, connB)
      connQueue.push(connA, connB)
      registerDoc(makeDocFull({ id: 'race-doc', content_html: FULL_SCHEMA_HTML }))

      const relay = createManualRelay(connA.doc, connB.doc)

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

      // The simultaneous window: BOTH connections reach `synced` -- and each independently runs
      // its own seed effect -- BEFORE any pump call exchanges updates. Per D-04/RESEARCH.md's
      // explicit rejection of an auto-relay: pumping inside the update handler would apply each
      // side's insert to the other before its own seed effect ever re-checks the flag, closing
      // this window and passing for the wrong reason.
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
      // Currently 2, not 1 -- see the escalation comment above.
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
      // (seeded it) before the swap -- otherwise the assertion below wouldn't prove anything.
      await act(async () => {
        sync(connA)
        await Promise.resolve()
      })
      await waitFor(() => {
        expect(connA.doc.getXmlFragment('default').toString()).not.toBe('')
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
      // against connB, so seeding lands in connB's fragment. Without the key, the persisted
      // CollaborativeEditor instance's `useEditor()` -- called with no explicit `deps` array, so
      // @tiptap/react's EditorInstanceManager.refreshEditorInstance only recreates the editor when
      // `deps.length !== 0` (frontend/node_modules/@tiptap/react/src/useEditor.ts, read during
      // investigation) -- never rebinds, so the editor stays wired to connA's now-destroyed Y.Doc
      // and connB's fragment never receives anything.
      await waitFor(() => {
        expect(connB.doc.getXmlFragment('default').toString()).not.toBe('')
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
      // `npm test -- EditorPage`, confirmed THIS test went RED (the `waitFor` above timed out --
      // connB's fragment stayed empty because the stale CollaborativeEditor instance kept writing
      // into connA's destroyed Y.Doc). Restored EditorPage.tsx verbatim afterward (`git diff`
      // confirmed empty) and re-ran the suite green. Full transcript of both rounds recorded in
      // 09-06-SUMMARY.md.
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
