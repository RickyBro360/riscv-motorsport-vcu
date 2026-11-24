import { cn } from '@/lib/utils'
import { LEVEL_BG } from '@/lib/format'
import type { HealthLevel } from '@/types/telemetry'

interface ProgressBarProps {
  value: number // 0..100
  level?: HealthLevel
  className?: string
  trackClassName?: string
  animated?: boolean
}

/** Themed horizontal bar. Level drives the fill color. */
export function ProgressBar({
  value,
  level,
  className,
  trackClassName,
  animated = true,
}: ProgressBarProps) {
  const width = Math.max(0, Math.min(100, value))
  return (
    <div
      className={cn('h-1.5 w-full overflow-hidden rounded-full bg-secondary', trackClassName)}
      role="progressbar"
      aria-valuenow={Math.round(width)}
      aria-valuemin={0}
      aria-valuemax={100}
    >
      <div
        className={cn(
          'h-full rounded-full',
          level ? LEVEL_BG[level] : 'bg-primary',
          animated && 'transition-all duration-300',
          className,
        )}
        style={{ width: `${width}%` }}
      />
    </div>
  )
}
