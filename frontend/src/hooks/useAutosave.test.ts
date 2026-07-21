/**
 * Drives useAutosave's debounce coalescing, error-retry re-queue, and Ctrl/Cmd-S force-flush
 * directly via `renderHook` + `vi.useFakeTimers()`, mocking `../api` (D-17) since the hook's
 * own timing/branch behaviour -- not the network -- is what this file pins (D-26).
 *
 * Flush-on-unmount is intentionally NOT tested here: it isn't part of useAutosave itself, it's
 * the cleanup effect at EditorPage.tsx:115 (`useEffect(() => () => void flush(), [flush])`),
 * and is covered in EditorPage.test.tsx instead. REQ-frontend-ui-tests mis-locates it onto
 * useAutosave; this comment records that rather than silently reinterpreting the requirement
 * (D-26 traceability note).
 */
import { act, renderHook } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { useAutosave } from './useAutosave'
import { api } from '../api'

vi.mock('../api')

beforeEach(() => {
  // api.patch is a single shared auto-mocked vi.fn() for the whole file (vi.mock('../api')
  // mocks the module once) -- clear call history/implementations between cases so one test's
  // queued resolved/rejected values can't leak into the next.
  vi.clearAllMocks()
  vi.useFakeTimers()
})

afterEach(() => {
  vi.runOnlyPendingTimers()
  vi.useRealTimers()
  vi.restoreAllMocks()
})

describe('useAutosave', () => {
  it('debounce coalesces rapid edits into one save', async () => {
    const patchMock = vi.mocked(api.patch).mockResolvedValue({
      id: 'doc-1',
      title: 'Third title',
      content_html: '',
      role: 'owner',
      owner: { name: 'Alice' },
      created_at: '',
      updated_at: '',
    })
    const { result, unmount } = renderHook(() => useAutosave('doc-1', true))

    act(() => {
      result.current.schedule({ title: 'First title' })
    })
    act(() => {
      // Each schedule() call resets the 800ms window (useAutosave.ts:59), so advancing only
      // part of it between edits must NOT trigger a save.
      vi.advanceTimersByTime(400)
      result.current.schedule({ title: 'Second title' })
    })
    act(() => {
      vi.advanceTimersByTime(400)
      result.current.schedule({ title: 'Third title' })
    })

    expect(patchMock).not.toHaveBeenCalled()

    await act(async () => {
      await vi.advanceTimersByTimeAsync(800)
    })

    // Coalesced into exactly one PATCH carrying only the merged, most-recent payload -- not
    // three separate saves (the idempotency/concurrency edge D-26 targets).
    expect(patchMock).toHaveBeenCalledTimes(1)
    expect(patchMock).toHaveBeenCalledWith('/documents/doc-1', { title: 'Third title' })
    expect(result.current.status).toBe('saved')

    unmount()
  })

  it('a failed save re-queues changes and reports error, and a later flush retries them', async () => {
    const patchMock = vi
      .mocked(api.patch)
      .mockRejectedValueOnce(new Error('network down'))
      .mockResolvedValueOnce({
        id: 'doc-1',
        title: 'Retry me',
        content_html: '',
        role: 'owner',
        owner: { name: 'Alice' },
        created_at: '',
        updated_at: '',
      })
    const { result, unmount } = renderHook(() => useAutosave('doc-1', true))

    act(() => {
      result.current.schedule({ title: 'Retry me' })
    })
    await act(async () => {
      await vi.advanceTimersByTimeAsync(800)
    })

    // First save rejected: pending.current re-queues the failed payload (useAutosave.ts:49)
    // instead of dropping it, and status surfaces the failure.
    expect(patchMock).toHaveBeenCalledTimes(1)
    expect(result.current.status).toBe('error')

    // A subsequent flush (e.g. Ctrl/Cmd-S, or another schedule()) retries the re-queued
    // changes -- proving they were actually held, not just that status flipped to 'error'.
    await act(async () => {
      await result.current.flush()
    })

    expect(patchMock).toHaveBeenCalledTimes(2)
    expect(patchMock).toHaveBeenLastCalledWith('/documents/doc-1', { title: 'Retry me' })
    expect(result.current.status).toBe('saved')

    unmount()
  })

  it('Ctrl/Cmd-S forces a flush and prevents default', async () => {
    const patchMock = vi.mocked(api.patch).mockResolvedValue({
      id: 'doc-1',
      title: 'Saved via shortcut',
      content_html: '',
      role: 'owner',
      owner: { name: 'Alice' },
      created_at: '',
      updated_at: '',
    })
    const { result, unmount } = renderHook(() => useAutosave('doc-1', true))

    act(() => {
      result.current.schedule({ title: 'Saved via shortcut' })
    })
    // Force the flush well before the 800ms debounce would otherwise fire, so a pass here
    // can only be explained by the keydown handler calling flush() directly (useAutosave.ts:68-77).
    expect(patchMock).not.toHaveBeenCalled()

    const event = new KeyboardEvent('keydown', { key: 's', ctrlKey: true, cancelable: true })
    const preventDefaultSpy = vi.spyOn(event, 'preventDefault')

    await act(async () => {
      window.dispatchEvent(event)
      await Promise.resolve()
    })

    expect(preventDefaultSpy).toHaveBeenCalledTimes(1)
    expect(patchMock).toHaveBeenCalledTimes(1)
    expect(patchMock).toHaveBeenCalledWith('/documents/doc-1', { title: 'Saved via shortcut' })
    expect(result.current.status).toBe('saved')

    unmount()
  })
})
