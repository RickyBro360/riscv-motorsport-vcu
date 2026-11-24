'use client'

import type { WingData } from '@/types/telemetry'
import { cn } from '@/lib/utils'

function FlapBar({ label, target, measured, feedback, straight }: { label: string; target: number; measured: number; feedback: boolean; straight?: boolean }) {
  const err = Math.abs(measured - target)
  return (
    <div className="min-w-0">
      <div className="flex items-baseline justify-between text-xs">
        <span className="font-semibold">{label}</span>
        <span className="tabular text-muted-foreground">{target.toFixed(0)}°</span>
      </div>
      <div className="relative mt-1 h-2 rounded-full bg-secondary">
        <div className={cn('h-full rounded-full', straight ? 'bg-success' : 'bg-primary')} style={{ width: `${(Math.max(0, Math.min(45, target)) / 45) * 100}%` }} />
        <div className={cn('absolute top-1/2 size-2.5 -translate-x-1/2 -translate-y-1/2 rounded-full border-2 border-foreground', feedback ? 'bg-foreground' : 'bg-transparent')}
          style={{ left: `${(Math.max(0, Math.min(45, measured)) / 45) * 100}%` }} />
      </div>
      <div className="mt-1 truncate text-[0.65rem] text-muted-foreground">
        {feedback ? <>meas {measured.toFixed(1)}° <span className={err > 3 ? 'text-warning' : ''}>Δ{err.toFixed(1)}</span></> : 'open loop (SG90)'}
      </div>
    </div>
  )
}

/** Top-down car with the four flaps. Bar = angle the VCU commands (target); dot =
 * angle measured by the MG996R pot feedback (rear LR/RR only — the front SG90s have no
 * feedback, so their dot is the command, drawn hollow). */
export function FlapDiagram({ wing, target, straightMode }: { wing: WingData; target: WingData; straightMode?: boolean }) {
  return (
    <div>
      <div className="grid grid-cols-[1fr_56px_1fr] items-center gap-x-3 gap-y-6">
        <FlapBar label="LF" target={target.frontLeft} measured={wing.frontLeft} feedback={false} straight={straightMode} />
        <svg viewBox="0 0 40 90" className="row-span-2 h-40 w-14" aria-hidden>
          <rect x="11" y="4" width="18" height="82" rx="8" fill="var(--color-secondary)" stroke="var(--color-border)" />
          <rect x="15" y="34" width="10" height="18" rx="3" fill="var(--color-muted-foreground)" fillOpacity={0.35} />
          {[[5, 16], [35, 16], [5, 74], [35, 74]].map(([x, y]) => <rect key={`${x}${y}`} x={x - 4} y={y - 7} width="8" height="14" rx="2" fill="var(--color-foreground)" fillOpacity={0.5} />)}
          <rect x="6" y="1" width="28" height="3" rx="1" fill={straightMode ? 'var(--color-success)' : 'var(--color-primary)'} />
          <rect x="8" y="86" width="24" height="3" rx="1" fill={straightMode ? 'var(--color-success)' : 'var(--color-primary)'} />
        </svg>
        <FlapBar label="RF" target={target.frontRight} measured={wing.frontRight} feedback={false} straight={straightMode} />
        <FlapBar label="LR" target={target.rearLeft} measured={wing.rearLeft} feedback straight={straightMode} />
        <FlapBar label="RR" target={target.rearRight} measured={wing.rearRight} feedback straight={straightMode} />
      </div>
      <div className={cn('mx-auto mt-3 w-fit rounded px-2 py-0.5 text-[0.65rem] font-semibold uppercase tracking-wider',
        straightMode ? 'bg-success/15 text-success' : 'bg-primary/15 text-primary')}>
        {straightMode ? 'Straight mode · low drag' : 'Corner mode · high downforce'}
      </div>
    </div>
  )
}
