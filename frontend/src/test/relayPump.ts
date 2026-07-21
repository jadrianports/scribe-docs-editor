/**
 * Bidirectional manual-pump relay for the multi-client seed-race scenario
 * (D-04). Buffers each `Y.Doc`'s `update` events and applies them to the
 * peer doc ONLY when the matching pump function (`pumpAtoB`/`pumpBtoA`) is
 * called explicitly -- never automatically inside the `update` handler.
 *
 * WHY MANUAL, NOT AUTOMATIC: an `update` handler that applies straight to
 * the peer doc closes the race window synchronously, so a seed-race test
 * built on it would pass even if the production seed effect has a genuine
 * simultaneous-seed bug -- passing for the wrong reason (D-04's explicit
 * rejection of the simpler auto-relay design, mirrored in RESEARCH.md's
 * Anti-Patterns section). This relay exists specifically so a test can hold
 * open the "both docs synced, neither has seen the other's `seeded` flag
 * yet" window and decide exactly when to close it.
 *
 * The `origin !== 'relay'` guard on each listener prevents feedback: without
 * it, applying a relayed update to docB would re-fire docB's own `update`
 * listener and re-queue that same update back toward docA.
 */
import * as Y from 'yjs'

export interface ManualRelay {
  pumpAtoB: () => void
  pumpBtoA: () => void
}

export function createManualRelay(docA: Y.Doc, docB: Y.Doc): ManualRelay {
  const bufferedToB: Uint8Array[] = []
  const bufferedToA: Uint8Array[] = []

  docA.on('update', (update: Uint8Array, origin: unknown) => {
    if (origin !== 'relay') bufferedToB.push(update)
  })
  docB.on('update', (update: Uint8Array, origin: unknown) => {
    if (origin !== 'relay') bufferedToA.push(update)
  })

  return {
    pumpAtoB: () => {
      while (bufferedToB.length > 0) Y.applyUpdate(docB, bufferedToB.shift()!, 'relay')
    },
    pumpBtoA: () => {
      while (bufferedToA.length > 0) Y.applyUpdate(docA, bufferedToA.shift()!, 'relay')
    },
  }
}
