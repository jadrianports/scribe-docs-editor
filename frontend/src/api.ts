// Thin fetch wrapper around the FastAPI backend. All requests send the session
// cookie (credentials: 'include') and throw a typed ApiError on non-2xx so
// callers / React Query can render precise messages.

export class ApiError extends Error {
  status: number
  constructor(status: number, message: string) {
    super(message)
    this.status = status
    this.name = 'ApiError'
  }
}

async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
  const res = await fetch(`/api${path}`, {
    credentials: 'include',
    headers: { 'Content-Type': 'application/json', ...(options.headers ?? {}) },
    ...options,
  })
  if (res.status === 204) return undefined as T
  const isJson = res.headers.get('content-type')?.includes('application/json')
  const body = isJson ? await res.json() : await res.text()
  if (!res.ok) {
    const detail = isJson && body && typeof body === 'object' ? body.detail : undefined
    throw new ApiError(res.status, detail || `Request failed (${res.status})`)
  }
  return body as T
}

export const api = {
  get: <T>(path: string) => request<T>(path),
  post: <T>(path: string, body?: unknown) =>
    request<T>(path, { method: 'POST', body: body ? JSON.stringify(body) : undefined }),
  patch: <T>(path: string, body: unknown) =>
    request<T>(path, { method: 'PATCH', body: JSON.stringify(body) }),
  del: <T = void>(path: string) => request<T>(path, { method: 'DELETE' }),
  upload: async <T>(path: string, file: File): Promise<T> => {
    const form = new FormData()
    form.append('file', file)
    const res = await fetch(`/api${path}`, {
      method: 'POST',
      credentials: 'include',
      body: form,
    })
    const body = await res.json().catch(() => ({}))
    if (!res.ok) throw new ApiError(res.status, body?.detail || `Upload failed (${res.status})`)
    return body as T
  },
}

// --- Shared types (mirror the backend Pydantic schemas) ---
export interface User {
  id: number
  name: string
  email: string
}
export interface Owner {
  name: string
}
export type Role = 'owner' | 'editor' | 'viewer'
export interface DocSummary {
  id: string
  title: string
  updated_at: string
  owner: Owner
  role: Role
}
export interface DocFull {
  id: string
  title: string
  content_html: string
  role: Role
  owner: Owner
  created_at: string
  updated_at: string
}
export interface DocList {
  owned: DocSummary[]
  shared: DocSummary[]
}
export interface Share {
  user_id: number
  name: string
  email: string
  role: Role
}
