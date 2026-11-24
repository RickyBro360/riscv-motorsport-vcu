'use client'

import { useEffect, useRef, useState } from 'react'

/** FW 2.3.10 (dashboard lag): hand a fast-changing value to expensive children at most every `ms`.
 * The newest value is always the one delivered; nothing is queued. */
export function useThrottled<T>(value: T, ms: number): T {
  const [shown, setShown] = useState(value)
  const latest = useRef(value)
  useEffect(() => { latest.current = value }, [value])
  useEffect(() => {
    const id = window.setInterval(() => setShown((prev) => (Object.is(prev, latest.current) ? prev : latest.current)), ms)
    return () => window.clearInterval(id)
  }, [ms])
  return shown
}
