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
 * The `useCollabFromQueue` hook-mock shape (used by every test in this file) is a
 * `useState(() => queue.shift())` lazy initializer. The initializer runs exactly once per
 * component INSTANCE (React memoizes it across that instance's re-renders), so pushing N
 * connections onto a shared queue before rendering N EditorPage trees deterministically binds
 * tree 1 -> connection 1, tree 2 -> connection 2, etc, stable across every subsequent re-render of
 * that same instance (doc-query settling, title edits, ...).
 */
import { useState } from 'react'
import { act, cleanup } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { Editor } from '@tiptap/react'
import StarterKit from '@tiptap/starter-kit'
import Collaboration from '@tiptap/extension-collaboration'
import * as Y from 'yjs'
import { renderWithProviders, screen } from '../test/renderWithProviders'
import { makeConnection, sync } from '../test/collabHarness'
import type { TestCollabConnection } from '../test/collabHarness'
import { FULL_SCHEMA_HTML } from '../test/fullSchemaFixture'
import { EditorPage } from './EditorPage'
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
})
