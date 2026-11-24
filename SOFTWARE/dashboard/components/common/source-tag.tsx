import { cn } from '@/lib/utils'

export type DataSource = 'measured' | 'derived' | 'modelled' | 'vcu' | 'simulated'

const STYLE: Record<DataSource, string> = {
  measured: 'border-success/40 text-success',
  derived: 'border-info/40 text-info',
  modelled: 'border-chart-5/50 text-[var(--color-chart-5)]',
  vcu: 'border-primary/40 text-primary',
  simulated: 'border-warning/40 text-warning',
}
const TITLE: Record<DataSource, string> = {
  measured: 'Read directly from a sensor on the car',
  derived: 'Calculated from measured values',
  modelled: 'From the digital twin / circuit model — not measured on the dyno',
  vcu: 'Decided and reported by the VCU (the actuator authority)',
  simulated: 'Offline simulation — no car connected',
}

/** Honesty label: every panel says where its numbers come from. */
export function SourceTag({ source, className }: { source: DataSource; className?: string }) {
  return (
    <span title={TITLE[source]}
      className={cn('rounded border px-1.5 py-px font-mono text-[0.6rem] uppercase tracking-wider', STYLE[source], className)}>
      {source}
    </span>
  )
}
