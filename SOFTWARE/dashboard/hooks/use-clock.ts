'use client'

import { useEffect, useState } from 'react'
import { formatClock } from '@/lib/format'

/** Live wall-clock string, updated once per second. SSR-safe placeholder. */
export function useClock(): string {
  const [clock, setClock] = useState('--:--:--')

  useEffect(() => {
    const tick = () => setClock(formatClock(new Date()))
    tick()
    const id = setInterval(tick, 1000)
    return () => clearInterval(id)
  }, [])

  return clock
}
