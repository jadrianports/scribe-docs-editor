/**
 * Drives useCollab's connection lifecycle and awareness subscription directly via
 * `renderHook`, using the module-mock injection seam below (D-02) -- useCollab builds its
 * WebsocketProvider internally (useCollab.ts:88-90) with no injection point and no
 * `connect: false`, so this file wraps the REAL provider class in a subclass that forces
 * `connect: false` in its constructor. The hook still gets a real, fully-functional
 * WebsocketProvider instance -- just one that never opens a socket -- so destroy()
 * ordering, status events and awareness are pinned against the real library, not a belief
 * about it (extends D-01's reasoning to this seam).
 *
 * Kept LOCAL to this file on purpose (Pitfall 1, RESEARCH.md): every other collab test
 * wants the real, unmocked WebsocketProvider via src/test/collabHarness.ts. Adding this
 * mock to src/test/setup.ts would leak it into those tests too.
 */
import { act, renderHook } from '@testing-library/react'
import { StrictMode } from 'react'
import { afterEach, describe, expect, it, vi } from 'vitest'

vi.mock('y-websocket', async (importOriginal) => {
  const actual = await importOriginal<typeof import('y-websocket')>()
  class ForcedNoConnectProvider extends actual.WebsocketProvider {
    constructor(
      serverUrl: string,
      roomname: string,
      doc: ConstructorParameters<typeof actual.WebsocketProvider>[2],
      opts: ConstructorParameters<typeof actual.WebsocketProvider>[3] = {},
    ) {
      super(serverUrl, roomname, doc, { ...opts, connect: false })
    }
  }
  return { ...actual, WebsocketProvider: ForcedNoConnectProvider }
})

// Imported after the mock declaration for readability only -- vi.mock's factory is hoisted
// to the top of the file by Vitest regardless of where it's written (RESEARCH.md Pattern 2).
import { WebsocketProvider } from 'y-websocket'
import { useCollab } from './useCollab'

afterEach(() => {
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
})

describe('useCollab', () => {
  it('announces departure before destroying the provider', () => {
    const { result, unmount } = renderHook(() => useCollab('doc-1'))
    const provider = result.current.conn!.provider
    const setLocalStateSpy = vi.spyOn(provider.awareness, 'setLocalState')
    const destroySpy = vi.spyOn(provider, 'destroy')

    unmount()

    expect(setLocalStateSpy).toHaveBeenCalledWith(null)
    expect(destroySpy).toHaveBeenCalledTimes(1)
    // Order matters: destroy() unhooks the awareness 'update' listener that carries the
    // goodbye message over the wire, so setLocalState(null) MUST run first (the cleanup at
    // the end of useCollab's connection effect).
    expect(setLocalStateSpy.mock.invocationCallOrder[0]).toBeLessThan(
      destroySpy.mock.invocationCallOrder[0],
    )
  })

  it('maps the raw connected status and treats every other value as disconnected', () => {
    const { result, unmount } = renderHook(() => useCollab('doc-1'))
    const provider = result.current.conn!.provider

    act(() => {
      provider.emit('status', [{ status: 'connected' }])
    })
    expect(result.current.status).toBe('connected')

    // Not the literal 'disconnected' string a second time -- 'connecting' proves the
    // mapping is a boolean check (connected vs. everything else), not a second literal match.
    act(() => {
      provider.emit('status', [{ status: 'connecting' }])
    })
    expect(result.current.status).toBe('disconnected')

    unmount()
  })

  it('builds a wss URL when the page is served over https, and ws otherwise', () => {
    // Default jsdom location is http://localhost:3000 -- covers the ws (else) branch with
    // no stubbing needed.
    const { result: httpResult, unmount: unmountHttp } = renderHook(() => useCollab('doc-1'))
    expect(httpResult.current.conn!.provider.serverUrl.startsWith('ws://')).toBe(true)
    unmountHttp()

    vi.stubGlobal('location', { protocol: 'https:', host: 'example.com' })
    const { result: httpsResult, unmount: unmountHttps } = renderHook(() => useCollab('doc-1'))
    expect(httpsResult.current.conn!.provider.serverUrl).toBe('wss://example.com/api/collab')
    unmountHttps()
  })

  it('keeps the surviving provider alive through StrictMode double-mount (construct-in-effect guard)', () => {
    const destroySpy = vi.spyOn(WebsocketProvider.prototype, 'destroy')

    const { result, unmount } = renderHook(() => useCollab('doc-1'), { wrapper: StrictMode })
    const survivingProvider = result.current.conn!.provider

    // StrictMode's dev-only mount -> cleanup -> mount destroys exactly one short-lived,
    // never-rendered provider before this assertion runs; the component is left with a
    // second, live instance (useCollab.ts:58-81's doc comment). The surviving provider must
    // NOT be among the instances destroy() was called on.
    expect(destroySpy).toHaveBeenCalledTimes(1)
    expect(destroySpy.mock.instances[0]).not.toBe(survivingProvider)

    // MUTATION-VALIDATION (D-07, discretionary extension of D-11/D-24 -- performed, not
    // deferred): temporarily rewrote useCollab's doc/provider construction to build them via
    // a `useState(() => ({ doc, provider }))` initializer instead of fresh inside the effect
    // -- exactly the anti-pattern useCollab.ts's doc comment (:58-81) warns against. Re-ran
    // `npm test -- useCollab`: this assertion went RED (the single useState-held provider
    // survived StrictMode's cleanup call, so destroySpy.mock.instances[0] WAS
    // survivingProvider). Restored useCollab.ts verbatim afterward and confirmed green
    // again. This test's discriminating power is proven, not assumed.
    unmount()
  })

  it('seeds peers as an empty array on mount, before any awareness change arrives', () => {
    // Pins the synchronous onAwarenessChange() call made right after
    // provider.awareness.on('change', ...) in useCollab.ts -- distinct from the
    // change-driven updates covered below. This is an isolated hook test with no editor
    // mounted (no CollaborationCaret writing a local user field), so the seed has nothing
    // to report yet -- but it must produce a real, settled [] from that call, not leave
    // peers stale/undefined pending a first 'change' event that may never come.
    const { result, unmount } = renderHook(() => useCollab('doc-1'))
    expect(result.current.peers).toEqual([])
    unmount()
  })

  it('a joining peer appears after the microtask flush', async () => {
    const { result, unmount } = renderHook(() => useCollab('doc-1'))
    const provider = result.current.conn!.provider

    await act(async () => {
      provider.awareness.setLocalState({ user: { name: 'Bob', color: '#fff' } })
      // queueMicrotask (useCollab.ts) isn't faked by Vitest's timer mock by default -- a
      // bare await is the correct and only flush needed (RESEARCH.md Pattern 4), and
      // wrapping the mutation + flush together in act() keeps the deferred setPeers call
      // inside an act() scope (RESEARCH.md Pattern 5).
      await Promise.resolve()
    })

    expect(result.current.peers).toEqual([
      { clientId: provider.doc.clientID, name: 'Bob', color: '#fff', self: true },
    ])

    unmount()
  })

  it('a cursor-only awareness change keeps peers referentially stable', async () => {
    const { result, unmount } = renderHook(() => useCollab('doc-1'))
    const provider = result.current.conn!.provider

    await act(async () => {
      provider.awareness.setLocalState({ user: { name: 'Bob', color: '#fff' } })
      await Promise.resolve()
    })
    const peersAfterJoin = result.current.peers

    await act(async () => {
      // Same name/color -- only a peer-identity-irrelevant field changes.
      provider.awareness.setLocalState({
        user: { name: 'Bob', color: '#fff' },
        cursor: { pos: 4 },
      })
      await Promise.resolve()
    })

    // samePeers' referential-stability bailout (useCollab.ts): an identical derived peer
    // list means setPeers returns the SAME array reference, not a new one.
    expect(result.current.peers).toBe(peersAfterJoin)

    unmount()
  })
})
