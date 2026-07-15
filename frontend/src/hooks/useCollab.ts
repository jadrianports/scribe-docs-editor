import { useEffect, useState } from 'react'
import * as Y from 'yjs'
import { WebsocketProvider } from 'y-websocket'

export type CollabStatus = 'connecting' | 'connected' | 'disconnected'

export interface CollabConnection {
  doc: Y.Doc
  provider: WebsocketProvider
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
 */
export function useCollab(docId: string) {
  const [conn, setConn] = useState<CollabConnection | null>(null)
  const [status, setStatus] = useState<CollabStatus>('connecting')

  useEffect(() => {
    const doc = new Y.Doc()
    const proto = location.protocol === 'https:' ? 'wss' : 'ws'
    const provider = new WebsocketProvider(`${proto}://${location.host}/api/collab`, docId, doc)

    const onStatus = (e: { status: string }) =>
      setStatus(e.status === 'connected' ? 'connected' : 'disconnected')
    provider.on('status', onStatus)

    setStatus('connecting')
    setConn({ doc, provider })

    return () => {
      provider.off('status', onStatus)
      provider.destroy()
      doc.destroy()
      setConn(null)
    }
  }, [docId])

  return { conn, status }
}
