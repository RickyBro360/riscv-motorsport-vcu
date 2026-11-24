import type { LucideIcon } from 'lucide-react'
import { cn } from '@/lib/utils'
import { LEVEL_TEXT } from '@/lib/format'
import type { HealthLevel } from '@/types/telemetry'

interface ReadoutProps {
  label: string
  value: string | number
  unit?: string
  icon?: LucideIcon
  accent?: boolean
  level?: HealthLevel
  className?: string
}

/** Compact bordered stat tile. Single source of truth for scalar readouts. */
export function Readout({ label, value, unit, icon: Icon, accent, level, className }: ReadoutProps) {
  const valueColor = level ? LEVEL_TEXT[level] : accent ? 'text-primary' : 'text-foreground'
  return (
    <div className={cn('rounded-lg border border-border bg-secondary/40 p-3', className)}>
      <div className="mb-1 flex items-center gap-1.5 text-muted-foreground">
        {Icon && <Icon className="size-3.5" />}
        <span className="text-[0.65rem] font-medium uppercase tracking-widest">{label}</span>
      </div>
      <p className={cn('tabular text-lg font-semibold leading-none', valueColor)}>
        {value}
        {unit && <span className="ml-1 text-xs font-normal text-muted-foreground">{unit}</span>}
      </p>
    </div>
  )
}
