import * as Y from 'yjs'
import { WebsocketProvider } from 'y-websocket'
import { EditorContent, useEditor } from '@tiptap/react'
import StarterKit from '@tiptap/starter-kit'
import Collaboration from '@tiptap/extension-collaboration'

// Module-level singleton, not per-render `useState`: React StrictMode (see
// main.tsx) intentionally double-invokes both a lazy `useState` initializer
// and the mount->cleanup->mount effect cycle in dev. A cleanup that calls
// `provider.destroy()` fires once "for free" on the very doc/provider the
// editor renders with -- y-websocket's `destroy()` is a terminal teardown
// (it unhooks the doc's 'update' listener and sets `shouldConnect = false`;
// unlike `disconnect()`, a later `connect()` does not undo that), so the
// editor is left silently unable to sync before the user ever types.
// Hoisting construction out of component lifecycle sidesteps that; fine for
// this single throwaway spike page (deleted in Phase 1).
const doc = new Y.Doc()
const proto = location.protocol === 'https:' ? 'wss' : 'ws'
// Constructed for its side effect (opens the connection and binds to `doc`);
// nothing here needs to hold a reference to the provider itself.
new WebsocketProvider(`${proto}://${location.host}/api/collab`, 'spike-doc', doc)

export function CollabSpike() {
  const editor = useEditor({
    extensions: [StarterKit.configure({ undoRedo: false }), Collaboration.configure({ document: doc })],
  })
  return <EditorContent editor={editor} />
}
