'use client'

import { cn } from '@/lib/utils'
import { toPct } from '@/lib/format'

interface RadialGaugeProps {
  value: number
  min: number
  max: number
  label: string
  unit?: string
  size?: number
  color?: string
  className?: string
}

/** SVG semicircular gauge — the signature telemetry readout. */
export function RadialGauge({
  value,
  min,
  max,
  label,
  unit,
  size = 160,
  color = 'var(--color-primary)',
  className,
}: RadialGaugeProps) {
  const pct = toPct(value, min, max)
  const radius = size / 2 - 12
  const cx = size / 2
  const cy = size / 2
  // semicircle: 180deg sweep from left (180) to right (360/0)
  const startAngle = Math.PI
  const endAngle = 2 * Math.PI
  const angle = startAngle + (pct / 100) * (endAngle - startAngle)

  const arc = (a0: number, a1: number) => {
    const x0 = cx + radius * Math.cos(a0)
    const y0 = cy + radius * Math.sin(a0)
    const x1 = cx + radius * Math.cos(a1)
    const y1 = cy + radius * Math.sin(a1)
    const large = a1 - a0 > Math.PI ? 1 : 0
    return `M ${x0} ${y0} A ${radius} ${radius} 0 ${large} 1 ${x1} ${y1}`
  }

  return (
    <div className={cn('flex flex-col items-center', className)}>
      <svg width={size} height={size / 2 + 24} viewBox={`0 0 ${size} ${size / 2 + 24}`}>
        <path
          d={arc(startAngle, endAngle)}
          fill="none"
          stroke="var(--color-secondary)"
          strokeWidth={10}
          strokeLinecap="round"
        />
        <path
          d={arc(startAngle, angle)}
          fill="none"
          stroke={color}
          strokeWidth={10}
          strokeLinecap="round"
          style={{ transition: 'all 0.2s ease-out' }}
        />
        <text
          x={cx}
          y={cy - 2}
          textAnchor="middle"
          className="tabular fill-foreground text-2xl font-semibold"
        >
          {Math.round(value)}
        </text>
        {unit && (
          <text
            x={cx}
            y={cy + 14}
            textAnchor="middle"
            className="fill-muted-foreground text-[10px] uppercase tracking-widest"
          >
            {unit}
          </text>
        )}
      </svg>
      <span className="mt-1 text-[0.7rem] font-medium uppercase tracking-widest text-muted-foreground">
        {label}
      </span>
    </div>
  )
}
