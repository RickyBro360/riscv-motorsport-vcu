'use client'

import type { LucideIcon } from 'lucide-react'
import { cn } from '@/lib/utils'
import { Card } from '@/components/ui/card'
import { LEVEL_TEXT } from '@/lib/format'
import type { HealthLevel } from '@/types/telemetry'

interface MetricCardProps {
  label: string
  value: string | number
  unit?: string
  icon?: LucideIcon
  level?: HealthLevel
  /** 0..100 fill for the bottom bar */
  fill?: number
  hint?: string
  className?: string
}

export function MetricCard({
  label,
  value,
  unit,
  icon: Icon,
  level = 'ok',
  fill,
  hint,
  className,
}: MetricCardProps) {
  const barColor =
    level === 'critical' ? 'bg-danger' : level === 'warning' ? 'bg-warning' : 'bg-primary'

  return (
    <Card className={cn('relative flex flex-col gap-3 overflow-hidden p-4', className)}>
      <div className="flex items-center justify-between">
        <span className="text-[0.7rem] font-medium uppercase tracking-widest text-muted-foreground">
          {label}
        </span>
        {Icon && <Icon className={cn('size-4', LEVEL_TEXT[level])} />}
      </div>

      <div className="flex items-baseline gap-1.5">
        <span
          className={cn(
            'tabular text-3xl font-semibold leading-none tracking-tight',
            level !== 'ok' ? LEVEL_TEXT[level] : 'text-foreground',
          )}
        >
          {value}
        </span>
        {unit && <span className="text-sm text-muted-foreground">{unit}</span>}
      </div>

      {hint && <span className="text-xs text-muted-foreground">{hint}</span>}

      {typeof fill === 'number' && (
        <div className="mt-auto h-1 w-full overflow-hidden rounded-full bg-secondary">
          <div
            className={cn('h-full rounded-full transition-all duration-200', barColor)}
            style={{ width: `${Math.max(0, Math.min(100, fill))}%` }}
          />
        </div>
      )}
    </Card>
  )
}
