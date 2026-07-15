import { useCallback, useEffect, useRef, useState } from 'react'
import { api } from '../api'
import type { DocFull } from '../api'

export type SaveStatus = 'idle' | 'saving' | 'saved' | 'error'

interface Changes {
  title?: string
}

/**
 * Debounced autosave for a document's title. `schedule(changes)` coalesces
 * edits and PATCHes them 800ms after the last change; `flush()` forces an
 * immediate save (also wired to Ctrl/Cmd-S). Saving is a no-op when `enabled`
 * is false (viewers).
 *
 * Content is no longer autosaved here: it flows live over the Yjs/y-websocket
 * connection (see `useCollab`) and is persisted server-side by the collab
 * room, not via PATCH. Only the title still goes through this REST path.
 *
 * `onSaved` receives the server's updated document after each successful save.
 * The editor uses it to keep the cached copy fresh, so navigating away and
 * reopening the document in-session shows the latest title (not a stale cache).
 */
export function useAutosave(
  docId: string,
  enabled: boolean,
  onSaved?: (doc: DocFull) => void,
) {
  const [status, setStatus] = useState<SaveStatus>('idle')
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null)
  const pending = useRef<Changes>({})

  const flush = useCallback(async () => {
    if (timer.current) {
      clearTimeout(timer.current)
      timer.current = null
    }
    if (Object.keys(pending.current).length === 0) return
    const payload = pending.current
    pending.current = {}
    setStatus('saving')
    try {
      const saved = await api.patch<DocFull>(`/documents/${docId}`, payload)
      onSaved?.(saved)
      setStatus('saved')
    } catch {
      // Re-queue the failed changes so a later edit / Ctrl-S retries them.
      pending.current = { ...payload, ...pending.current }
      setStatus('error')
    }
  }, [docId, onSaved])

  const schedule = useCallback(
    (changes: Changes) => {
      if (!enabled) return
      pending.current = { ...pending.current, ...changes }
      setStatus('saving')
      if (timer.current) clearTimeout(timer.current)
      timer.current = setTimeout(() => {
        void flush()
      }, 800)
    },
    [enabled, flush],
  )

  // Ctrl/Cmd-S forces an immediate save.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 's') {
        e.preventDefault()
        void flush()
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [flush])

  return { status, schedule, flush }
}
