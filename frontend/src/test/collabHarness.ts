/**
 * `connect:false` Y.Doc + WebsocketProvider factory for collab-flavored
 * component tests (D-01/D-15). Constructs the REAL `y-websocket`
 * `WebsocketProvider` (not a hand-rolled fake) so tests pin the library's
 * actual sync/teardown contract instead of a belief about it -- the same
 * reasoning that makes D-01/D-10 reject a fake provider in CONTEXT.md.
 *
 * MANDATORY TEARDOWN (Pitfall 2): `WebsocketProvider`'s constructor starts
 * `_checkInterval` (and, once synced, `_resyncInterval`) via `setInterval`
 * UNCONDITIONALLY -- independent of the `connect` option
 * (node_modules/y-websocket/src/y-websocket.js:387-397, installed v3.0.0).
 * Only `destroy()` clears them. Every connection this factory returns MUST
 * be torn down by calling the returned `destroy()` in the test's own
 * teardown (`afterEach`/end of test), UNLESS the component under test's own
 * `useCollab` effect cleanup already owns and destroys this exact provider
 * (in which case RTL's `unmount()` covers it) -- otherwise the leaked
 * interval keeps running into whatever test case runs next in the same file.
 *
 * LISTEN ON 'sync' ONLY (Pitfall 3): the `synced` setter
 * (y-websocket.js:416-423) emits BOTH 'synced' and 'sync' at runtime, but
 * y-websocket's TS type declarations only list 'sync'
 * (dist/src/y-websocket.d.ts). A test or production listener attached to
 * 'synced' still works at runtime but fails `tsc --noEmit` -- matches
 * EditorPage.tsx's existing convention of listening on 'sync' only.
 */
import * as Y from 'yjs'
import { WebsocketProvider } from 'y-websocket'
import type { CollabConnection } from '../hooks/useCollab'

export interface TestCollabConnection extends CollabConnection {
  /** Tears down provider then doc -- see the MANDATORY TEARDOWN note above. */
  destroy: () => void
}

/**
 * Builds a real `{ doc, provider }` pair matching what `useCollab` returns,
 * with the provider constructed `{ connect: false }` so no socket ever opens
 * (D-01). `docId` only labels the room for assertions/debugging -- no
 * network request is made regardless of its value.
 */
export function makeConnection(docId = 'test-doc'): TestCollabConnection {
  const doc = new Y.Doc()
  const provider = new WebsocketProvider('ws://localhost/api/collab', docId, doc, {
    connect: false,
  })

  return {
    doc,
    provider,
    destroy: () => {
      provider.destroy()
      doc.destroy()
    },
  }
}

/**
 * Drives the provider's synced path the way a real handshake completion
 * would. Setting `provider.synced = true` emits both 'synced' and 'sync' at
 * runtime (see LISTEN ON 'sync' ONLY above) -- any assertion/listener code
 * driven by this helper must itself listen on 'sync' to stay `tsc`-clean.
 */
export function sync(connection: Pick<CollabConnection, 'provider'>): void {
  connection.provider.synced = true
}
