/**
 * Covers ExportMenu's two export paths (success criterion 3, D-20): the Markdown download via
 * the bare-`download` `<a>` anchor 09-01 introduced (replacing window.location.href), and the
 * unchanged PDF path via window.print(). No providers needed -- ExportMenu has no react-query or
 * auth dependency.
 *
 * Per D-20 / RESEARCH.md Pitfall 5: the server's Content-Disposition header
 * (backend/app/routers/export.py:17-34) is authoritative for the downloaded filename on
 * same-origin responses and silently overrides any value on the anchor's `download` attribute.
 * The Markdown test below therefore asserts the attribute's PRESENCE and the anchor's `href`,
 * never a filename value -- asserting a filename would pass in jsdom while misrepresenting real
 * browser behaviour.
 */
import { cleanup, render, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { screen } from '../test/renderWithProviders'
import { ExportMenu } from './ExportMenu'

const DOC_ID = 'doc-42'

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
})

describe('ExportMenu', () => {
  it('Markdown export creates a same-origin download anchor and closes the menu', async () => {
    const user = userEvent.setup()

    // Intercept anchor creation so we can inspect the element's attributes and click count
    // after ExportMenu appends/clicks/removes it synchronously -- the reference survives removal
    // from the DOM since we hold it directly.
    const realCreateElement = document.createElement.bind(document)
    let capturedAnchor: HTMLAnchorElement | null = null
    vi.spyOn(document, 'createElement').mockImplementation(((
      tagName: string,
      options?: ElementCreationOptions,
    ) => {
      const el = realCreateElement(tagName, options)
      if (tagName === 'a') {
        capturedAnchor = el as HTMLAnchorElement
        // Prevent jsdom's unimplemented-navigation warning; existence of the call is what matters.
        vi.spyOn(capturedAnchor, 'click').mockImplementation(() => {})
      }
      return el
    }) as typeof document.createElement)

    render(<ExportMenu docId={DOC_ID} />)
    await user.click(screen.getByRole('button', { name: /Export/ }))
    await user.click(screen.getByRole('button', { name: /Markdown/ }))

    expect(capturedAnchor).not.toBeNull()
    expect(capturedAnchor!.getAttribute('href')).toBe(`/api/documents/${DOC_ID}/export?format=md`)
    // Presence of `download`, bare (no filename value) -- per D-20/Pitfall 5, never assert a
    // specific filename here.
    expect(capturedAnchor!.hasAttribute('download')).toBe(true)
    expect(capturedAnchor!.getAttribute('download')).toBe('')
    expect(capturedAnchor!.click).toHaveBeenCalledTimes(1)

    // Menu closes after export (setOpen(false)) -- the Markdown option is no longer rendered.
    expect(screen.queryByRole('button', { name: /Markdown/ })).not.toBeInTheDocument()
  })

  it('PDF export invokes window.print()', async () => {
    const user = userEvent.setup()
    const printSpy = vi.spyOn(window, 'print').mockImplementation(() => {})

    render(<ExportMenu docId={DOC_ID} />)
    await user.click(screen.getByRole('button', { name: /Export/ }))
    await user.click(screen.getByRole('button', { name: /PDF/ }))

    // exportPdf defers window.print() by a short setTimeout so the dropdown can close first --
    // waitFor polls past that real timer instead of faking it.
    await waitFor(() => expect(printSpy).toHaveBeenCalledTimes(1))
  })
})
