export class ApiError extends Error {
  constructor(message: string, public status: number) { super(message) }
}

export async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
  const response = await fetch(`/api/v1${path}`, {
    ...options,
    headers: { ...(options.body instanceof FormData ? {} : { 'Content-Type': 'application/json' }), ...options.headers },
  })
  const body = await response.json().catch(() => ({}))
  if (!response.ok) {
    let message = body.error
    if (!message && Array.isArray(body.detail)) {
      message = body.detail.map((e: {loc: string[], msg: string}) => `${e.loc.slice(1).join('.')}: ${e.msg}`).join('；')
    }
    throw new ApiError(message || `请求失败（HTTP ${response.status}）`, response.status)
  }
  return body as T
}

export function json(method: string, body: unknown): RequestInit {
  return { method, body: JSON.stringify(body) }
}
