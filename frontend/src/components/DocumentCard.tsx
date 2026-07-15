import { Link } from 'react-router-dom'
import type { DocSummary } from '../api'
import { roleBadge } from '../lib/permissions'

function formatDate(iso: string): string {
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return ''
  return d.toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' })
}

export function DocumentCard({ doc, showOwner = false }: { doc: DocSummary; showOwner?: boolean }) {
  return (
    <Link
      to={`/documents/${doc.id}`}
      className="group flex min-h-32 flex-col justify-between rounded-lg border border-slate-200 bg-white p-4 shadow-sm transition hover:border-slate-300 hover:shadow"
    >
      <div>
        <h3 className="font-semibold text-slate-800 group-hover:text-slate-900">
          {doc.title || 'Untitled document'}
        </h3>
        <p className="mt-1 text-xs text-slate-400">Updated {formatDate(doc.updated_at)}</p>
      </div>
      <div className="mt-4 flex items-center justify-between">
        {showOwner ? <span className="text-xs text-slate-500">by {doc.owner.name}</span> : <span />}
        <span className="rounded-full bg-slate-100 px-2 py-0.5 text-xs font-medium text-slate-600">
          {roleBadge(doc.role)}
        </span>
      </div>
    </Link>
  )
}
