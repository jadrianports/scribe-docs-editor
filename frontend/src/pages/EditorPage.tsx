import { useCallback, useEffect, useState } from 'react'
import type { ReactNode } from 'react'
import { Link, useParams } from 'react-router-dom'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { EditorContent, useEditor } from '@tiptap/react'
import StarterKit from '@tiptap/starter-kit'
import { api } from '../api'
import type { DocFull } from '../api'
import { canEdit, isOwner, roleBadge } from '../lib/permissions'
import { useAutosave } from '../hooks/useAutosave'
import type { SaveStatus } from '../hooks/useAutosave'
import { Toolbar } from '../components/Toolbar'
import { ShareModal } from '../components/ShareModal'
import { ExportMenu } from '../components/ExportMenu'

function statusLabel(status: SaveStatus): string {
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
  const [title, setTitle] = useState(doc.title)
  const [shareOpen, setShareOpen] = useState(false)

  // Keep the cached document in sync with each save so that leaving the editor
  // and reopening it in-session shows the latest content instead of the stale
  // copy React Query cached when the document was first opened.
  const onSaved = useCallback(
    (saved: DocFull) => {
      queryClient.setQueryData(['document', doc.id], saved)
    },
    [queryClient, doc.id],
  )
  const { status, schedule, flush } = useAutosave(doc.id, editable, onSaved)

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
      }),
    ],
    content: doc.content_html || '<p></p>',
    onUpdate: ({ editor }) => schedule({ content_html: editor.getHTML() }),
  })

  // Flush any pending changes when navigating away from the editor.
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
        <span className="hidden text-xs text-slate-400 sm:inline">{statusLabel(status)}</span>
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

      {editor && editable && <Toolbar editor={editor} />}

      <main className="mx-auto my-6 max-w-3xl px-4">
        <article className="document-print rounded-lg bg-white p-8 shadow-sm sm:p-12">
          <h1 className="mb-6 hidden text-3xl font-bold print:block">{title}</h1>
          <EditorContent editor={editor} />
        </article>
      </main>

      {shareOpen && <ShareModal docId={doc.id} onClose={() => setShareOpen(false)} />}
    </div>
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
