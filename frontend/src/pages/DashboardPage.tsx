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
    onError: (e) => alert(e instanceof ApiError ? e.message : 'Upload failed'),
  })

  return (
    <div className="min-h-screen bg-slate-50">
      <header className="flex items-center justify-between border-b border-slate-200 bg-white px-6 py-3">
        <h1 className="text-xl font-bold text-slate-800">Scribe</h1>
        <div className="flex items-center gap-3 text-sm">
          <span className="text-slate-500">{user?.name}</span>
          <button
            onClick={() => logout()}
            className="rounded border border-slate-300 px-2 py-1 hover:bg-slate-100"
          >
            Log out
          </button>
        </div>
      </header>

      <main className="mx-auto max-w-5xl px-6 py-8">
        <div className="mb-8 flex flex-wrap gap-2">
          <button
            onClick={() => createDoc.mutate()}
            disabled={createDoc.isPending}
            className="rounded bg-slate-800 px-4 py-2 text-sm font-medium text-white hover:bg-slate-700 disabled:opacity-50"
          >
            + New document
          </button>
          <button
            onClick={() => fileInput.current?.click()}
            disabled={uploadDoc.isPending}
            className="rounded border border-slate-300 px-4 py-2 text-sm text-slate-700 hover:bg-slate-100 disabled:opacity-50"
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

        {docsQuery.isLoading ? (
          <p className="text-slate-500">Loading documents…</p>
        ) : docsQuery.isError ? (
          <p className="text-red-600">Could not load documents.</p>
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
