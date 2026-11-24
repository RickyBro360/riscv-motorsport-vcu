/**
 * Where the FastAPI backend lives, as seen from THIS browser.
 *
 * Stage 2c: the dashboard used to fall back to http://localhost:8000, which is
 * only right when the browser runs on the backend laptop AND the page was
 * opened as localhost. Opened as http://10.49.218.128:3000 — or from the second
 * laptop — "localhost" pointed at the wrong machine and LIVE mode never started.
 *
 * Order: NEXT_PUBLIC_API_BASE_URL (explicit) -> same host as this page, port
 * 8000 (backend and dashboard run on the same laptop) -> localhost (SSR only).
 */
export function backendHttpBase(): string {
  const explicit = process.env.NEXT_PUBLIC_API_BASE_URL
  if (explicit) return explicit.replace(/\/$/, '')
  if (typeof window !== 'undefined' && window.location?.hostname) {
    return `${window.location.protocol}//${window.location.hostname}:8000`
  }
  return 'http://localhost:8000'
}

export function backendWsBase(): string {
  return backendHttpBase().replace(/^http/, 'ws')
}
