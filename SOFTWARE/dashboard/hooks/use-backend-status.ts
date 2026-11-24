'use client'

import { useEffect, useState } from 'react'
import { pingBackend } from '@/services/api'

/** Polls the FastAPI backend's /api/health. Returns 'checking' briefly on
 * mount, then 'online' | 'offline'. Pages use this to decide whether to
 * show recorded-session data or fall back to local demo data — never to
 * block rendering. */
export function useBackendStatus(intervalMs = 15000) {
  const [status, setStatus] = useState<'checking' | 'online' | 'offline'>('checking')

  useEffect(() => {
    let cancelled = false
    const check = async () => {
      const ok = await pingBackend()
      if (!cancelled) setStatus(ok ? 'online' : 'offline')
    }
    check()
    const id = window.setInterval(check, intervalMs)
    return () => {
      cancelled = true
      window.clearInterval(id)
    }
  }, [intervalMs])

  return status
}
