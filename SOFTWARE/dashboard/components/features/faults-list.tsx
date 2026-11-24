'use client'

import { AlertTriangle, CheckCircle2, XCircle } from 'lucide-react'
import { cn } from '@/lib/utils'
import { LEVEL_TEXT } from '@/lib/format'
import type { Fault, HealthLevel } from '@/types/telemetry'

const ICONS: Record<HealthLevel, typeof AlertTriangle> = {
  ok: CheckCircle2,
  warning: AlertTriangle,
  critical: XCircle,
}

function timeAgo(ts: number) {
  const s = Math.floor((Date.now() - ts) / 1000)
  if (s < 60) return `${s}s ago`
  const m = Math.floor(s / 60)
  return `${m}m ago`
}

export function FaultsList({ faults, max }: { faults: Fault[]; max?: number }) {
  const items = max ? faults.slice(0, max) : faults

  if (items.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center gap-2 py-8 text-center">
        <CheckCircle2 className="size-6 text-success" />
        <p className="text-sm text-muted-foreground">No active faults — all systems nominal</p>
      </div>
    )
  }

  return (
    <ul className="space-y-2">
      {items.map((f) => {
        const Icon = ICONS[f.level]
        return (
          <li
            key={f.id}
            className="flex items-start gap-3 rounded-lg border border-border bg-secondary/40 p-3"
          >
            <Icon className={cn('mt-0.5 size-4 shrink-0', LEVEL_TEXT[f.level])} />
            <div className="min-w-0 flex-1">
              <div className="flex items-center justify-between gap-2">
                <span className="tabular text-xs font-semibold text-foreground">{f.code}</span>
                <span className="text-[0.7rem] text-muted-foreground">{timeAgo(f.timestamp)}</span>
              </div>
              <p className="mt-0.5 truncate text-xs text-muted-foreground">{f.message}</p>
              <span className="mt-1 inline-block text-[0.65rem] uppercase tracking-widest text-muted-foreground/70">
                {f.subsystem}
              </span>
            </div>
          </li>
        )
      })}
    </ul>
  )
}
