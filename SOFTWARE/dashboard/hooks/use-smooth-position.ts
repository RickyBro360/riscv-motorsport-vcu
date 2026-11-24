'use client'

import { useEffect, useRef, useState } from 'react'
import { wrap01 } from '@/lib/tracks'

/**
 * LoRa delivers the VCU's lap position ~5 times a second. Between packets the car
 * keeps moving at the measured wheel speed, so the marker is advanced at that rate
 * (dead reckoning, capped at 0.6 s) and eased onto each new report — the marker
 * never runs away from the VCU's own number, which is what the flaps follow.
 */
export function useSmoothPosition(pos: number | null | undefined, rearKmh: number, lapLengthM: number, active: boolean) {
  const [display, setDisplay] = useState<number>(pos ?? 0)
  const ref = useRef({ reported: pos ?? 0, at: 0, rate: 0, shown: pos ?? 0 })

  useEffect(() => {
    if (pos == null || !Number.isFinite(pos)) return
    ref.current.reported = pos
    ref.current.at = performance.now()
    ref.current.rate = active && lapLengthM > 0 ? rearKmh / 3.6 / lapLengthM : 0
  }, [pos, rearKmh, lapLengthM, active])

  useEffect(() => {
    let raf = 0
    const tick = () => {
      const r = ref.current
      const dt = Math.min(0.6, (performance.now() - r.at) / 1000)
      const target = wrap01(r.reported + r.rate * dt)
      let d = target - r.shown
      if (d > 0.5) d -= 1
      if (d < -0.5) d += 1
      r.shown = Math.abs(d) > 0.05 ? target : wrap01(r.shown + d * 0.35)
      setDisplay(r.shown)
      raf = requestAnimationFrame(tick)
    }
    raf = requestAnimationFrame(tick)
    return () => cancelAnimationFrame(raf)
  }, [])

  return display
}
