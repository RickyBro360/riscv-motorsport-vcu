import { cn } from '@/lib/utils'
import type { HealthLevel } from '@/types/telemetry'

const COLORS: Record<HealthLevel, string> = {
  ok: 'bg-success shadow-[0_0_8px_1px_var(--color-success)]',
  warning: 'bg-warning shadow-[0_0_8px_1px_var(--color-warning)]',
  critical: 'bg-danger shadow-[0_0_8px_1px_var(--color-danger)]',
}

export function StatusDot({
  level,
  pulse = true,
  className,
}: {
  level: HealthLevel
  pulse?: boolean
  className?: string
}) {
  return (
    <span
      aria-hidden
      className={cn(
        'inline-block size-2 rounded-full',
        COLORS[level],
        pulse && level !== 'ok' && 'animate-pulse-dot',
        className,
      )}
    />
  )
}
