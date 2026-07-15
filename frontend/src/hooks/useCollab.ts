import { useEffect, useState } from 'react'
import * as Y from 'yjs'
import { WebsocketProvider } from 'y-websocket'

export type CollabStatus = 'connecting' | 'connected' | 'disconnected'

export interface CollabConnection {
  doc: Y.Doc
  provider: WebsocketProvider
}

/**
 * One participant currently connected to the document, derived from Yjs
 * awareness state. `color` is read straight back from the awareness `user`
 * field that `CollaborationCaret` (configured in EditorPage) writes for its
 * own local user -- so a peer's avatar and their caret are always the same
 * color by construction, not by keeping two color schemes in sync.
 */
export interface Peer {
  clientId: number
  name: string
  color: string
  self: boolean
}

// Awareness states are an untyped bag (`{[key: string]: any}` upstream) --
// this is the shape CollaborationCaret happens to write into the `user` key.
type AwarenessStates = Map<number, { [key: string]: any }>

function peersFromAwareness(states: AwarenessStates, localClientId: number): Peer[] {
  const peers: Peer[] = []
  states.forEach((state, clientId) => {
    const user = state.user as { name?: unknown; color?: unknown } | undefined
    if (typeof user?.name !== 'string' || typeof user?.color !== 'string') return // not set yet
    peers.push({ clientId, name: user.name, color: user.color, self: clientId === localClientId })
  })
  // Stable order (self first, then join order) so avatars don't reshuffle on every update.
  peers.sort((a, b) => (a.self !== b.self ? (a.self ? -1 : 1) : a.clientId - b.clientId))
  return peers
}

function samePeers(a: Peer[], b: Peer[]): boolean {
  return (
    a.length === b.length &&
    a.every(
      (p, i) => p.clientId === b[i].clientId && p.name === b[i].name && p.color === b[i].color,
    )
  )
}

/**
 * Opens a Yjs/y-websocket connection to `/api/collab/{docId}`, scoped to one
 * document. `conn` is `null` until the connection for the CURRENT `docId` is
 * ready; it is `null` again for the brief instant between an old doc's
 * teardown and a new one's setup (id change, or React StrictMode's
 * mount->cleanup->mount dance in dev — see below).
 *
 * The doc + provider are constructed INSIDE the effect (not via `useState`
 * initializers) and destroyed in that same effect's cleanup, keyed on
 * `docId`. This matters because `WebsocketProvider.destroy()` is a
 * *terminal* teardown, not a pausable disconnect: it unhooks the Y.Doc's
 * `update` listener and sets `shouldConnect = false`, and a later
 * `.connect()` does NOT re-register that listener. In React StrictMode
 * (frontend/src/main.tsx), effects run mount -> cleanup -> mount once "for
 * free" in dev. If the doc/provider were created via `useState(() => ...)`
 * (surviving the double-invoke) while only the *effect* had a
 * `provider.destroy()` cleanup, that first free cleanup would permanently
 * kill the one instance the component goes on to render with -- the editor
 * would keep working locally but silently never sync again. Building the
 * connection fresh inside the effect means StrictMode's extra
 * mount->cleanup->mount cycle destroys a short-lived, never-rendered
 * instance and leaves the component with a fresh, live one from the second
 * (real) mount. This must stay per-docId (not a module-level singleton --
 * that would share one Y.Doc across every open document).
 *
 * `peers` mirrors `provider.awareness` (who else is connected to this room
 * right now). The subscription lives in this same effect -- set up right
 * next to the provider it reads, torn down in the same cleanup -- so it
 * shares the provider's lifecycle exactly and can never end up listening to
 * a destroyed instance or leaking a listener onto a replacement one.
 */
export function useCollab(docId: string) {
  const [conn, setConn] = useState<CollabConnection | null>(null)
  const [status, setStatus] = useState<CollabStatus>('connecting')
  const [peers, setPeers] = useState<Peer[]>([])

  useEffect(() => {
    const doc = new Y.Doc()
    const proto = location.protocol === 'https:' ? 'wss' : 'ws'
    const provider = new WebsocketProvider(`${proto}://${location.host}/api/collab`, docId, doc)

    const onStatus = (e: { status: string }) =>
      setStatus(e.status === 'connected' ? 'connected' : 'disconnected')
    provider.on('status', onStatus)

    // Recompute on every awareness change (peer joins/leaves, or
    // CollaborationCaret setting/updating the local `user` field once the
    // editor mounts). `change` fires for the *whole* awareness map, so we
    // always re-derive from `getStates()` rather than patching incrementally
    // -- simpler, and self-correcting if we ever miss an event. Bailing out
    // via functional `setPeers` when the derived list is unchanged (e.g. a
    // peer's cursor moved, which touches a different awareness field) avoids
    // re-rendering the header on every keystroke/selection change.
    //
    // The `setPeers` call itself is deferred to a microtask. TipTap's
    // `useEditor` builds the ProseMirror view synchronously during
    // CollaborativeEditor's render (`immediatelyRender: true`), and
    // CollaborationCaret's plugin sets ITS OWN local awareness field as part
    // of that same synchronous view construction -- so the very first
    // `change` event fires from inside CollaborativeEditor's render, and
    // calling `setPeers` (which belongs to EditorInner) straight from there
    // is a synchronous cross-component state update mid-render (confirmed via
    // React's own warning: "Cannot update a component while rendering a
    // different component"). Queuing a microtask runs it right after that
    // render finishes instead, with no user-visible delay.
    let cancelled = false
    const onAwarenessChange = () => {
      queueMicrotask(() => {
        if (cancelled) return
        setPeers((prev) => {
          const next = peersFromAwareness(provider.awareness.getStates(), doc.clientID)
          return samePeers(prev, next) ? prev : next
        })
      })
    }
    provider.awareness.on('change', onAwarenessChange)
    onAwarenessChange() // 'change' only fires on the next transition; seed synchronously too

    setStatus('connecting')
    setConn({ doc, provider })

    return () => {
      cancelled = true
      provider.off('status', onStatus)
      provider.awareness.off('change', onAwarenessChange)
      // Announce departure to everyone else in the room while the socket is
      // still open, so peers see us vanish right away instead of waiting out
      // y-protocols' ~30s awareness timeout. `y-websocket`'s browser client
      // never does this on its own (only a Node process gets a goodbye, via
      // `process.on('exit', ...)` -- confirmed by reading
      // node_modules/y-websocket/src/y-websocket.js; there's no `beforeunload`
      // equivalent), so a graceful close/switch-doc would otherwise look
      // identical to a crash until that timeout fires. Must run BEFORE
      // `provider.destroy()`: destroy() unhooks the awareness 'update'
      // listener that actually sends this over the wire and then closes the
      // socket, so afterwards there is nothing left to carry the message. A
      // truly abrupt disconnect (tab killed, network lost) still can't send
      // anything and stays bounded by the ~30s timeout on every remaining
      // peer's own client -- that's a property of the protocol, not
      // something a cooperating client can route around.
      provider.awareness.setLocalState(null)
      provider.destroy()
      doc.destroy()
      setConn(null)
      setPeers([])
    }
  }, [docId])

  return { conn, status, peers }
}
