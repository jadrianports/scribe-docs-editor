import { useEffect, useRef, useState } from 'react'

// Export dropdown: Markdown downloads from the API; PDF uses the browser's
// print-to-PDF against the print stylesheet (see index.css).
export function ExportMenu({ docId }: { docId: string }) {
  const [open, setOpen] = useState(false)
  const ref = useRef<HTMLDivElement>(null)

  useEffect(() => {
    const onClick = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false)
    }
    document.addEventListener('mousedown', onClick)
    return () => document.removeEventListener('mousedown', onClick)
  }, [])

  const exportMarkdown = () => {
    // A real same-origin download anchor, not a window.location.href
    // assignment -- carries the session cookie the same way, but is
    // testable without stubbing jsdom navigation. The `download` attribute
    // is intentionally bare (no filename): for a same-origin response the
    // server's Content-Disposition header (backend/app/routers/export.py)
    // is authoritative and silently overrides any filename given here.
    const a = document.createElement('a')
    a.href = `/api/documents/${docId}/export?format=md`
    a.download = ''
    document.body.appendChild(a)
    a.click()
    document.body.removeChild(a)
    setOpen(false)
  }
  const exportPdf = () => {
    setOpen(false)
    // Let the dropdown close before opening the print dialog.
    setTimeout(() => window.print(), 60)
  }

  return (
    <div className="relative" ref={ref}>
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        className="rounded border border-slate-300 px-3 py-1.5 text-sm text-slate-700 hover:bg-slate-100"
      >
        Export ▾
      </button>
      {open && (
        <div className="absolute right-0 z-20 mt-1 w-44 overflow-hidden rounded-md border border-slate-200 bg-white shadow-lg">
          <button
            onClick={exportMarkdown}
            className="block w-full px-3 py-2 text-left text-sm hover:bg-slate-100"
          >
            Markdown (.md)
          </button>
          <button
            onClick={exportPdf}
            className="block w-full px-3 py-2 text-left text-sm hover:bg-slate-100"
          >
            PDF (print)
          </button>
        </div>
      )}
    </div>
  )
}
