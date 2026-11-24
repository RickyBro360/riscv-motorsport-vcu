'use client'

import { useMemo } from 'react'
import { type TrackDef, pointAt, headingAt, straightAt } from '@/lib/tracks'

interface CircuitMapProps {
  track: TrackDef
  pos: number | null           // where the VCU says the car is (smoothed)
  aheadPos?: number | null     // where the AI engineer is looking (look-ahead)
  cfg?: Record<string, number> // zone offsets from the VCU config
  straightMode?: boolean       // what the flaps are doing right now (VCU flag)
  showCorners?: boolean
  showSectors?: boolean
  compact?: boolean
}

/**
 * The circuit drawn from its real outline, with the straight-mode (low drag) zones in
 * green and the braking zones in red — exactly the zones the VCU switches the flaps
 * on, shrunk by the same open-delay / close-early offsets it uses.
 */
export function CircuitMap({ track, pos, aheadPos, cfg, straightMode, showCorners = true, showSectors = true, compact }: CircuitMapProps) {
  const geo = useMemo(() => {
    const xs = track.polyline.map((p) => p[0])
    const ys = track.polyline.map((p) => p[1])
    const w = Math.max(...xs), h = Math.max(...ys)
    const pad = Math.max(w, h) * 0.06
    const toSvg = (p: [number, number]) => [p[0], h - p[1]] as [number, number]
    const path = track.polyline.map((p, i) => `${i ? 'L' : 'M'}${toSvg(p)[0].toFixed(1)},${toSvg(p)[1].toFixed(1)}`).join(' ') + ' Z'
    return { w, h, pad, toSvg, path, stroke: Math.max(w, h) / 90 }
  }, [track])

  const open = cfg?.straightOpenDelayM ?? 0
  const close = cfg?.straightCloseEarlyM ?? 0
  const zonePaths = useMemo(() => {
    const n = track.polyline.length
    const segs: string[] = []
    const brake: string[] = []
    for (let i = 0; i < n; i++) {
      const a = geo.toSvg(track.polyline[i]), b = geo.toSvg(track.polyline[(i + 1) % n])
      const seg = `M${a[0].toFixed(1)},${a[1].toFixed(1)} L${b[0].toFixed(1)},${b[1].toFixed(1)}`
      if (straightAt(track, i / n, open, close)) segs.push(seg)
      else if (track.series.braking[i]) brake.push(seg)
    }
    return { straight: segs.join(' '), braking: brake.join(' ') }
  }, [track, geo, open, close])

  const car = pos != null ? geo.toSvg(pointAt(track, pos)) : null
  const hdg = pos != null ? (headingAt(track, pos) * 180) / Math.PI : 0
  const ahead = aheadPos != null ? geo.toSvg(pointAt(track, aheadPos)) : null
  const s = geo.stroke
  const start = geo.toSvg(pointAt(track, 0))
  const startHdg = (headingAt(track, 0) * 180) / Math.PI

  return (
    <div className="relative h-full w-full">
    <svg viewBox={`${-geo.pad} ${-geo.pad} ${geo.w + 2 * geo.pad} ${geo.h + 2 * geo.pad}`} className="absolute inset-0 h-full w-full" role="img"
      aria-label={`${track.fullName} circuit map`}>
      <path d={geo.path} fill="none" stroke="var(--color-border)" strokeWidth={s * 2.6} strokeLinejoin="round" />
      <path d={geo.path} fill="none" stroke="var(--color-muted-foreground)" strokeOpacity={0.35} strokeWidth={s * 1.2} strokeLinejoin="round" />
      <path d={zonePaths.braking} fill="none" stroke="var(--color-danger)" strokeOpacity={0.75} strokeWidth={s * 1.3} strokeLinecap="round" />
      <path d={zonePaths.straight} fill="none" stroke="var(--color-success)" strokeWidth={s * 1.3} strokeLinecap="round" />
      {/* start / finish */}
      <g transform={`translate(${start[0]},${start[1]}) rotate(${startHdg + 90})`}>
        <rect x={-s * 2} y={-s * 0.35} width={s * 4} height={s * 0.7} fill="var(--color-foreground)" />
      </g>
      {showSectors && [1 / 3, 2 / 3].map((sp) => {
        const p = geo.toSvg(pointAt(track, sp))
        return <g key={sp}><circle cx={p[0]} cy={p[1]} r={s * 0.9} fill="var(--color-info)" />
          {!compact && <text x={p[0] + s * 1.5} y={p[1]} fontSize={s * 2.2} fill="var(--color-info)">S{sp < 0.5 ? 2 : 3}</text>}</g>
      })}
      {showCorners && !compact && track.corners.map((c) => {
        const p = geo.toSvg(pointAt(track, c.pos))
        return <text key={c.n} x={p[0] + s * 1.6} y={p[1] - s * 1.2} fontSize={s * 2} fill="var(--color-muted-foreground)">{c.n}</text>
      })}
      {ahead && (
        <circle cx={ahead[0]} cy={ahead[1]} r={s * 1.4} fill="none" stroke="var(--color-chart-5)" strokeWidth={s * 0.5} strokeDasharray={`${s} ${s * 0.6}`} />
      )}
      {car && (
        <g transform={`translate(${car[0]},${car[1]}) rotate(${-hdg})`}>
          <circle r={s * 2.6} fill={straightMode ? 'var(--color-success)' : 'var(--color-primary)'} fillOpacity={0.18} />
          <path d={`M${s * 2.2},0 L${-s * 1.4},${s * 1.3} L${-s * 0.8},0 L${-s * 1.4},${-s * 1.3} Z`}
            fill={straightMode ? 'var(--color-success)' : 'var(--color-primary)'} stroke="var(--color-background)" strokeWidth={s * 0.3} />
        </g>
      )}
    </svg>
    </div>
  )
}
