import { useCallback, useEffect, useState } from 'react'
import type { ReactNode } from 'react'
import { Link, useParams } from 'react-router-dom'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { EditorContent, useEditor } from '@tiptap/react'
import StarterKit from '@tiptap/starter-kit'
import Collaboration from '@tiptap/extension-collaboration'
import CollaborationCaret from '@tiptap/extension-collaboration-caret'
import { api } from '../api'
import type { DocFull, User } from '../api'
import { canEdit, isOwner, roleBadge } from '../lib/permissions'
import { useAutosave } from '../hooks/useAutosave'
import type { SaveStatus } from '../hooks/useAutosave'
import { useCollab } from '../hooks/useCollab'
import type { CollabConnection, CollabStatus, Peer } from '../hooks/useCollab'
import { useAuth } from '../auth/AuthContext'
import { Toolbar } from '../components/Toolbar'
import { ShareModal } from '../components/ShareModal'
import { ExportMenu } from '../components/ExportMenu'

function titleStatusLabel(status: SaveStatus): string {
  switch (status) {
    case 'saving':
      return 'Saving…'
    case 'saved':
      return 'All changes saved'
    case 'error':
      return 'Save failed — retry'
    default:
      return ''
  }
}

function collabStatusLabel(status: CollabStatus): string {
  return status === 'connected' ? 'Live' : 'Reconnecting…'
}

// Deterministic per-user caret color. Stepping the hue by the golden angle
// (~137.508°) per user id spreads consecutive ids around the color wheel so
// a handful of concurrent collaborators rarely land on visually similar
// colors, without needing a server-assigned palette. Good enough for now —
// presence polish is a later task.
function caretColor(userId: number): string {
  const hue = Math.round((userId * 137.508) % 360)
  return `hsl(${hue}, 70%, 45%)`
}

// "Alice" -> "AL", "Alice Nguyen" -> "AN". Good enough for a small chip; the
// full name is still available via the tooltip.
function initials(name: string): string {
  const parts = name.trim().split(/\s+/).filter(Boolean)
  if (parts.length === 0) return '?'
  if (parts.length === 1) return parts[0].slice(0, 2).toUpperCase()
  return (parts[0][0] + parts[1][0]).toUpperCase()
}

// "Who's here": one small colored initials chip per connected peer, shown
// next to the Live/Reconnecting status. Colors come straight from awareness
// (see `Peer` in useCollab) so they always match that person's caret.
function PresenceStack({ peers }: { peers: Peer[] }) {
  if (peers.length === 0) return null
  return (
    <div className="hidden items-center -space-x-1.5 sm:flex" aria-label="People viewing this document">
      {peers.map((peer) => (
        <span
          key={peer.clientId}
          title={peer.self ? `${peer.name} (you)` : peer.name}
          style={{ backgroundColor: peer.color }}
          className="flex h-6 w-6 items-center justify-center rounded-full border-2 border-white text-[10px] font-semibold text-white shadow-sm"
        >
          {initials(peer.name)}
        </span>
      ))}
    </div>
  )
}

export function EditorPage() {
  const { id = '' } = useParams()
  const {
    data: doc,
    isLoading,
    error,
  } = useQuery({
    queryKey: ['document', id],
    queryFn: () => api.get<DocFull>(`/documents/${id}`),
  })

  if (isLoading) return <CenteredMessage>Loading document…</CenteredMessage>
  if (error || !doc) return <NotAccessible />
  // Remount when the id changes so the editor re-initializes with fresh content.
  return <EditorInner key={doc.id} doc={doc} />
}

function EditorInner({ doc }: { doc: DocFull }) {
  const editable = canEdit(doc.role)
  const queryClient = useQueryClient()
  const { user } = useAuth()
  const [title, setTitle] = useState(doc.title)
  const [shareOpen, setShareOpen] = useState(false)

  // Keep the cached document in sync with each save so that leaving the editor
  // and reopening it in-session shows the latest title instead of the stale
  // copy React Query cached when the document was first opened.
  const onSaved = useCallback(
    (saved: DocFull) => {
      queryClient.setQueryData(['document', doc.id], saved)
    },
    [queryClient, doc.id],
  )
  const { status, schedule, flush } = useAutosave(doc.id, editable, onSaved)
  const { conn, status: collabStatus, peers } = useCollab(doc.id)

  // Flush any pending title changes when navigating away from the editor.
  useEffect(() => () => void flush(), [flush])

  return (
    <div className="min-h-screen bg-slate-100">
      <header className="no-print flex flex-wrap items-center gap-3 border-b border-slate-200 bg-white px-4 py-2">
        <Link to="/" className="text-sm text-slate-500 hover:text-slate-800">
          ← Documents
        </Link>
        <input
          value={title}
          disabled={!editable}
          onChange={(e) => {
            setTitle(e.target.value)
            schedule({ title: e.target.value })
          }}
          aria-label="Document title"
          className="min-w-0 flex-1 rounded px-2 py-1 text-lg font-semibold text-slate-800 outline-none focus:bg-slate-100 disabled:bg-transparent"
        />
        <span className="hidden text-xs text-slate-400 sm:inline">
          {collabStatusLabel(collabStatus)}
        </span>
        <PresenceStack peers={peers} />
        <span className="hidden text-xs text-slate-400 sm:inline">{titleStatusLabel(status)}</span>
        {!editable && (
          <span className="rounded bg-amber-100 px-2 py-0.5 text-xs font-medium text-amber-800">
            {roleBadge(doc.role)}
          </span>
        )}
        <ExportMenu docId={doc.id} />
        {isOwner(doc.role) && (
          <button
            onClick={() => setShareOpen(true)}
            className="rounded bg-slate-800 px-3 py-1.5 text-sm text-white hover:bg-slate-700"
          >
            Share
          </button>
        )}
      </header>

      {conn ? (
        <CollaborativeEditor
          key={conn.doc.guid}
          conn={conn}
          contentHtml={doc.content_html}
          editable={editable}
          title={title}
          user={user}
        />
      ) : (
        <main className="mx-auto my-6 max-w-3xl px-4">
          <article className="document-print rounded-lg bg-white p-8 shadow-sm sm:p-12">
            <p className="text-sm text-slate-400">Connecting…</p>
          </article>
        </main>
      )}

      {shareOpen && <ShareModal docId={doc.id} onClose={() => setShareOpen(false)} />}
    </div>
  )
}

/**
 * Owns the actual TipTap editor instance, created only once a live collab
 * connection exists. The caller keys this component on `conn.doc.guid`, so
 * React StrictMode's dev-only extra mount->cleanup->mount of `useCollab`
 * (which replaces `conn` with a fresh Y.Doc/provider pair) tears this whole
 * subtree down and rebuilds it against the new, live connection instead of
 * reusing an editor bound to an already-destroyed Y.Doc.
 */
function CollaborativeEditor({
  conn,
  contentHtml,
  editable,
  title,
  user,
}: {
  conn: CollabConnection
  contentHtml: string
  editable: boolean
  title: string
  user: User | null
}) {
  const { doc: ydoc, provider } = conn

  const editor = useEditor({
    editable,
    extensions: [
      StarterKit.configure({
        heading: { levels: [1, 2, 3] },
        // Keep the editor schema aligned with the server's sanitizer allow-list.
        link: false,
        code: false,
        codeBlock: false,
        horizontalRule: false,
        undoRedo: false, // Collaboration extension provides Yjs-based (per-user) undo instead.
      }),
      Collaboration.configure({ document: ydoc }),
      CollaborationCaret.configure({
        provider,
        user: { name: user?.name ?? 'Anonymous', color: caretColor(user?.id ?? 0) },
      }),
    ],
    // No `content:` -- the document body comes from the shared Y.Doc, not
    // from `content_html` directly (that's only used once, below, to seed a
    // document that has never been opened for collaboration before).
  })

  // Seed the shared doc once from the document's last-saved HTML. Guarded so
  // that N clients opening the same never-collaborated-on document don't
  // each insert their own copy: `doc.getMap('config')` is itself part of the
  // synced CRDT state, so once the first client to observe "synced but not
  // yet seeded" flips the flag, that update propagates to every other
  // client and none of them re-run `setContent`.
  //
  // Listens for the (correctly-typed) 'sync' event -- the runtime also
  // emits an identically-timed 'synced' event, but the installed type
  // definitions only declare 'sync', so 'synced' fails `tsc --noEmit`.
  // Also checks `provider.synced` directly, both immediately after
  // subscribing (the event is edge-triggered -- only fires on a state
  // *transition* -- so a listener that subscribes after the handshake
  // already completed would otherwise miss it forever) and inside the
  // handler itself (the same event also fires on false->true *and*
  // true->false, e.g. a disconnect; re-checking the live getter rather than
  // trusting "the event fired" keeps a disconnect from being treated as a
  // sync).
  useEffect(() => {
    const trySeed = () => {
      if (!provider.synced) return
      const config = ydoc.getMap('config')
      if (!config.get('seeded') && editor && contentHtml) {
        config.set('seeded', true)
        editor.commands.setContent(contentHtml)
      }
    }
    provider.on('sync', trySeed)
    trySeed()
    return () => {
      provider.off('sync', trySeed)
    }
  }, [provider, ydoc, editor, contentHtml])

  return (
    <>
      {editor && editable && <Toolbar editor={editor} />}
      <main className="mx-auto my-6 max-w-3xl px-4">
        <article className="document-print rounded-lg bg-white p-8 shadow-sm sm:p-12">
          <h1 className="mb-6 hidden text-3xl font-bold print:block">{title}</h1>
          <EditorContent editor={editor} />
        </article>
      </main>
    </>
  )
}

function CenteredMessage({ children }: { children: ReactNode }) {
  return (
    <div className="flex min-h-screen items-center justify-center text-slate-500">{children}</div>
  )
}

function NotAccessible() {
  return (
    <div className="flex min-h-screen flex-col items-center justify-center gap-3 text-center">
      <p className="text-lg font-semibold text-slate-700">Document not available</p>
      <p className="text-sm text-slate-500">
        It may have been deleted, or you don&apos;t have access to it.
      </p>
      <Link to="/" className="rounded bg-slate-800 px-4 py-2 text-sm text-white hover:bg-slate-700">
        Back to documents
      </Link>
    </div>
  )
}
