import { useRef } from 'react'
import { useNavigate } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api, ApiError } from '../api'
import type { DocFull, DocList, DocSummary } from '../api'
import { useAuth } from '../auth/AuthContext'
import { DocumentCard } from '../components/DocumentCard'

export function DashboardPage() {
  const { user, logout } = useAuth()
  const navigate = useNavigate()
  const qc = useQueryClient()
  const fileInput = useRef<HTMLInputElement>(null)

  const docsQuery = useQuery({
    queryKey: ['documents'],
    queryFn: () => api.get<DocList>('/documents'),
  })

  const createDoc = useMutation({
    mutationFn: () => api.post<DocFull>('/documents'),
    onSuccess: (doc) => navigate(`/documents/${doc.id}`),
  })

  const uploadDoc = useMutation({
    mutationFn: (file: File) => api.upload<DocFull>('/documents/upload', file),
    onSuccess: (doc) => {
      qc.invalidateQueries({ queryKey: ['documents'] })
      navigate(`/documents/${doc.id}`)
    },
  })

  const actionError = uploadDoc.error ?? createDoc.error
  const actionErrorMessage = actionError
    ? actionError instanceof ApiError
      ? actionError.message
      : 'Something went wrong. Please try again.'
    : null

  return (
    <div className="min-h-screen bg-slate-50">
      <header className="flex items-center justify-between border-b border-slate-200 bg-white px-6 py-3">
        <h1 className="text-xl font-bold text-slate-800">Scribe</h1>
        <div className="flex items-center gap-3 text-sm">
          <span className="text-slate-500">{user?.name}</span>
          <button
            onClick={() => logout()}
            className="rounded border border-slate-300 px-2 py-1 transition-colors hover:bg-slate-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-slate-400"
          >
            Log out
          </button>
        </div>
      </header>

      <main className="mx-auto max-w-5xl px-6 py-8">
        <div className="mb-2 flex flex-wrap items-center gap-2">
          <button
            onClick={() => createDoc.mutate()}
            disabled={createDoc.isPending}
            className="rounded bg-slate-800 px-4 py-2 text-sm font-medium text-white transition-colors hover:bg-slate-700 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-slate-400 focus-visible:ring-offset-1 disabled:cursor-not-allowed disabled:opacity-50"
          >
            + New document
          </button>
          <button
            onClick={() => fileInput.current?.click()}
            disabled={uploadDoc.isPending}
            className="rounded border border-slate-300 px-4 py-2 text-sm text-slate-700 transition-colors hover:bg-slate-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-slate-400 disabled:cursor-not-allowed disabled:opacity-50"
          >
            {uploadDoc.isPending ? 'Uploading…' : 'Upload .txt / .md'}
          </button>
          <input
            ref={fileInput}
            type="file"
            accept=".txt,.md,text/plain,text/markdown"
            className="hidden"
            onChange={(e) => {
              const file = e.target.files?.[0]
              if (file) uploadDoc.mutate(file)
              e.target.value = ''
            }}
          />
        </div>
        <p className="mb-6 text-xs text-slate-400">
          Upload a <code className="rounded bg-slate-100 px-1">.txt</code> or{' '}
          <code className="rounded bg-slate-100 px-1">.md</code> file (max 1&nbsp;MB). Import
          preserves basic formatting — headings, bold, italic, and lists.
        </p>
        {actionErrorMessage && (
          <p className="mb-6 rounded border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700">
            {actionErrorMessage}
          </p>
        )}

        {docsQuery.isLoading ? (
          <DashboardSkeleton />
        ) : docsQuery.isError ? (
          <div className="text-sm">
            <p className="text-red-600">Could not load your documents.</p>
            <button
              onClick={() => docsQuery.refetch()}
              className="mt-2 rounded border border-slate-300 px-3 py-1 transition-colors hover:bg-slate-100"
            >
              Try again
            </button>
          </div>
        ) : (
          <div className="space-y-10">
            <Section
              title="My documents"
              docs={docsQuery.data!.owned}
              emptyText="No documents yet — create one or upload a file to get started."
            />
            <Section
              title="Shared with me"
              docs={docsQuery.data!.shared}
              emptyText="Nothing has been shared with you yet."
              showOwner
            />
          </div>
        )}
      </main>
    </div>
  )
}

function Section({
  title,
  docs,
  emptyText,
  showOwner = false,
}: {
  title: string
  docs: DocSummary[]
  emptyText: string
  showOwner?: boolean
}) {
  return (
    <section>
      <h2 className="mb-3 text-sm font-semibold uppercase tracking-wide text-slate-500">
        {title} <span className="text-slate-400">({docs.length})</span>
      </h2>
      {docs.length === 0 ? (
        <p className="rounded-lg border border-dashed border-slate-200 bg-white p-6 text-sm text-slate-400">
          {emptyText}
        </p>
      ) : (
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {docs.map((doc) => (
            <DocumentCard key={doc.id} doc={doc} showOwner={showOwner} />
          ))}
        </div>
      )}
    </section>
  )
}

// Skeleton that mirrors the real card grid, so loading reads as "content is
// arriving here" rather than a bare line of gray text.
function DashboardSkeleton() {
  return (
    <div className="space-y-10">
      {[0, 1].map((section) => (
        <div key={section}>
          <div className="mb-3 h-4 w-40 animate-pulse rounded bg-slate-200" />
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
            {Array.from({ length: 3 }).map((_, i) => (
              <div
                key={i}
                className="h-32 animate-pulse rounded-lg border border-slate-200 bg-white"
              />
            ))}
          </div>
        </div>
      ))}
    </div>
  )
}
