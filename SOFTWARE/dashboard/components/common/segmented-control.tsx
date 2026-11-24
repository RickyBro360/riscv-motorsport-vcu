'use client'

import { cn } from '@/lib/utils'

interface SegmentedControlProps<T extends string | number> {
  label?: string
  options: { label: string; value: T }[]
  value: T
  onChange: (value: T) => void
  className?: string
}

/** Generic segmented button group. Replaces the per-page SegRow implementations. */
export function SegmentedControl<T extends string | number>({
  label,
  options,
  value,
  onChange,
  className,
}: SegmentedControlProps<T>) {
  return (
    <div className={className}>
      {label && <p className="mb-2 text-sm font-medium text-foreground">{label}</p>}
      <div
        role="radiogroup"
        aria-label={label}
        className="flex items-center gap-1 rounded-lg border border-border bg-background p-1"
      >
        {options.map((o) => {
          const active = value === o.value
          return (
            <button
              key={String(o.value)}
              type="button"
              role="radio"
              aria-checked={active}
              onClick={() => onChange(o.value)}
              className={cn(
                'flex-1 rounded-md px-3 py-1.5 text-xs font-medium transition-colors outline-none focus-visible:ring-2 focus-visible:ring-ring',
                active
                  ? 'bg-primary text-primary-foreground'
                  : 'text-muted-foreground hover:text-foreground',
              )}
            >
              {o.label}
            </button>
          )
        })}
      </div>
    </div>
  )
}
