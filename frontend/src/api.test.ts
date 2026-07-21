/**
 * Covers api.ts's OWN wrapper branches -- the 204/non-JSON/ApiError seam every component test
 * in this phase mocks away via vi.mock('../api') (D-17). This file does the opposite: it
 * exercises the REAL request()/upload() against a stubbed global fetch, so the wrapper itself
 * is proven, not just callers' assumptions about it (T-09-08 in 09-05-PLAN.md's threat model).
 */
import { afterEach, describe, expect, it, vi } from 'vitest'
import { api, ApiError } from './api'

function jsonResponse(status: number, body: unknown, ok = status >= 200 && status < 300) {
  return {
    status,
    ok,
    headers: { get: (name: string) => (name === 'content-type' ? 'application/json' : null) },
    json: async () => body,
    text: async () => JSON.stringify(body),
  } as unknown as Response
}

function textResponse(status: number, body: string, ok = status >= 200 && status < 300) {
  return {
    status,
    ok,
    headers: { get: (name: string) => (name === 'content-type' ? 'text/plain' : null) },
    json: async () => {
      throw new Error('not JSON')
    },
    text: async () => body,
  } as unknown as Response
}

function noContentResponse() {
  return {
    status: 204,
    ok: true,
    headers: { get: () => null },
    json: async () => {
      throw new Error('no body')
    },
    text: async () => '',
  } as unknown as Response
}

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('api', () => {
  describe('request() branches', () => {
    it('a 204 response resolves to undefined', async () => {
      const fetchMock = vi.fn().mockResolvedValue(noContentResponse())
      vi.stubGlobal('fetch', fetchMock)

      const result = await api.get('/documents/doc-1')

      expect(result).toBeUndefined()
      expect(fetchMock).toHaveBeenCalledWith(
        '/api/documents/doc-1',
        expect.objectContaining({ credentials: 'include' }),
      )
    })

    it('a non-JSON 200 body is returned as text, not JSON-parsed', async () => {
      vi.stubGlobal('fetch', vi.fn().mockResolvedValue(textResponse(200, 'plain body')))

      const result = await api.get('/export')

      expect(result).toBe('plain body')
    })

    it('a JSON error response throws ApiError carrying the extracted detail', async () => {
      vi.stubGlobal(
        'fetch',
        vi.fn().mockResolvedValue(jsonResponse(403, { detail: 'Not allowed' })),
      )

      await expect(api.get('/documents/doc-1')).rejects.toMatchObject({
        name: 'ApiError',
        status: 403,
        message: 'Not allowed',
      })
      await expect(api.get('/documents/doc-1')).rejects.toBeInstanceOf(ApiError)
    })

    it('an error response without a JSON detail throws ApiError with the fallback message', async () => {
      vi.stubGlobal('fetch', vi.fn().mockResolvedValue(jsonResponse(500, {})))

      await expect(api.get('/documents/doc-1')).rejects.toMatchObject({
        status: 500,
        message: 'Request failed (500)',
      })
    })

    it('sends credentials: include and the /api prefix on every request', async () => {
      const fetchMock = vi.fn().mockResolvedValue(jsonResponse(200, { ok: true }))
      vi.stubGlobal('fetch', fetchMock)

      await api.post('/documents', { title: 'New doc' })

      expect(fetchMock).toHaveBeenCalledWith(
        '/api/documents',
        expect.objectContaining({
          method: 'POST',
          credentials: 'include',
          body: JSON.stringify({ title: 'New doc' }),
        }),
      )
    })
  })

  describe('upload() -- separate fetch call, own error-body parse', () => {
    it('a success path returns the parsed body', async () => {
      const fetchMock = vi.fn().mockResolvedValue(jsonResponse(200, { id: 'doc-2' }))
      vi.stubGlobal('fetch', fetchMock)
      const file = new File(['content'], 'notes.md', { type: 'text/markdown' })

      const result = await api.upload('/documents/upload', file)

      expect(result).toEqual({ id: 'doc-2' })
      const [url, init] = fetchMock.mock.calls[0]
      expect(url).toBe('/api/documents/upload')
      expect(init).toMatchObject({ method: 'POST', credentials: 'include' })
      expect(init.body).toBeInstanceOf(FormData)
    })

    it('an error path surfaces ApiError using its own res.json().catch(() => ({})) parse', async () => {
      // upload() has its own error-body parse (api.ts's res.json().catch(() => ({}))), not
      // request()'s content-type-gated branch -- assert it independently.
      vi.stubGlobal('fetch', vi.fn().mockResolvedValue(jsonResponse(413, { detail: 'Too large' }, false)))
      const file = new File(['content'], 'huge.md')

      await expect(api.upload('/documents/upload', file)).rejects.toMatchObject({
        status: 413,
        message: 'Too large',
      })
    })

    it('falls back to a generic message when the error body fails to parse as JSON', async () => {
      const brokenJsonResponse = {
        status: 415,
        ok: false,
        json: async () => {
          throw new Error('invalid JSON')
        },
      } as unknown as Response
      vi.stubGlobal('fetch', vi.fn().mockResolvedValue(brokenJsonResponse))
      const file = new File(['content'], 'bad.exe')

      await expect(api.upload('/documents/upload', file)).rejects.toMatchObject({
        status: 415,
        message: 'Upload failed (415)',
      })
    })
  })
})
