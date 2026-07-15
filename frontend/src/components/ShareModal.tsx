import { useEffect, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api, ApiError } from '../api'
import type { Role, Share } from '../api'
import { roleBadge } from '../lib/permissions'

export function ShareModal({ docId, onClose }: { docId: string; onClose: () => void }) {
  const qc = useQueryClient()
  const [email, setEmail] = useState('')
  const [role, setRole] = useState<Role>('viewer')
  const [error, setError] = useState('')

  // Close on Escape, like a native dialog.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

  const sharesQuery = useQuery({
    queryKey: ['shares', docId],
    queryFn: () => api.get<Share[]>(`/documents/${docId}/shares`),
  })

  const addShare = useMutation({
    mutationFn: () => api.post<Share>(`/documents/${docId}/shares`, { email, role }),
    onSuccess: () => {
      setEmail('')
      setError('')
      qc.invalidateQueries({ queryKey: ['shares', docId] })
    },
    onError: (e) => setError(e instanceof ApiError ? e.message : 'Could not share document'),
  })

  const revoke = useMutation({
    mutationFn: (userId: number) => api.del(`/documents/${docId}/shares/${userId}`),
    onSuccess: () => {
      setError('')
      qc.invalidateQueries({ queryKey: ['shares', docId] })
    },
    onError: (e) => setError(e instanceof ApiError ? e.message : 'Could not remove access'),
  })

  const shares = sharesQuery.data ?? []

  return (
    <div
      className="fixed inset-0 z-30 flex items-center justify-center bg-black/40 p-4"
      onClick={onClose}
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby="share-modal-title"
        className="w-full max-w-md rounded-lg bg-white p-5 shadow-xl"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="mb-4 flex items-center justify-between">
          <h2 id="share-modal-title" className="text-lg font-semibold text-slate-800">
            Share document
          </h2>
          <button
            onClick={onClose}
            className="rounded text-slate-400 hover:text-slate-600 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-slate-400"
            aria-label="Close"
          >
            ✕
          </button>
        </div>

        <form
          onSubmit={(e) => {
            e.preventDefault()
            if (email.trim()) addShare.mutate()
          }}
          className="flex flex-col gap-2 sm:flex-row"
        >
          <input
            type="email"
            required
            autoFocus
            placeholder="teammate@example.com"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            className="flex-1 rounded border border-slate-300 px-2 py-1.5 text-sm focus:border-slate-500 focus:outline-none focus:ring-2 focus:ring-slate-300"
          />
          <select
            value={role}
            onChange={(e) => setRole(e.target.value as Role)}
            className="rounded border border-slate-300 px-2 py-1.5 text-sm focus:border-slate-500 focus:outline-none focus:ring-2 focus:ring-slate-300"
          >
            <option value="viewer">Viewer</option>
            <option value="editor">Editor</option>
          </select>
          <button
            type="submit"
            disabled={addShare.isPending}
            className="rounded bg-slate-800 px-3 py-1.5 text-sm text-white transition-colors hover:bg-slate-700 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-slate-400 focus-visible:ring-offset-1 disabled:cursor-not-allowed disabled:opacity-50"
          >
            {addShare.isPending ? 'Adding…' : 'Add'}
          </button>
        </form>
        {error && <p className="mt-2 text-sm text-red-600">{error}</p>}

        <ul className="mt-4 space-y-2">
          {shares.map((s) => (
            <li
              key={s.user_id}
              className="flex items-center justify-between rounded bg-slate-50 px-3 py-2 text-sm"
            >
              <span className="text-slate-700">
                {s.name} <span className="text-slate-400">· {s.email}</span>
              </span>
              <span className="flex items-center gap-3">
                <span className="text-xs font-medium text-slate-500">{roleBadge(s.role)}</span>
                <button
                  onClick={() => revoke.mutate(s.user_id)}
                  disabled={revoke.isPending}
                  className="rounded text-red-600 hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-red-300 disabled:opacity-50"
                >
                  Remove
                </button>
              </span>
            </li>
          ))}
          {shares.length === 0 && (
            <li className="px-1 py-2 text-sm text-slate-400">Not shared with anyone yet.</li>
          )}
        </ul>
      </div>
    </div>
  )
}
