'use client'

import { useEffect, useMemo, useRef, useState } from 'react'
import { Panel } from '@/components/common/panel'
import { TelemetryChart } from '@/components/common/telemetry-chart'
import { SourceTag, type DataSource } from '@/components/common/source-tag'
import type { TelemetryFrame } from '@/types/telemetry'

interface ChartPanelProps {
  title: string
  data: TelemetryFrame[]
  series: {
    accessor: (f: TelemetryFrame) => number
    color: string
    label: string
  }[]
  unit: string
  current: string
  domain?: [number | 'auto', number | 'auto']
  type?: 'area' | 'line'
  source?: DataSource
  refLines?: { y: number; label: string; color?: string }[]
  window?: number
  height?: number
  footer?: React.ReactNode
}

/** FW 2.3.10 (dashboard lag): a chart is the expensive part of a page (SVG re-layout), and the Live
 * Telemetry page has 14 of them. Redrawing all of them on every frame kept the browser busy ~100 % of
 * the time, so the whole page fell behind. Now each chart redraws at most every CHART_REDRAW_MS, and
 * only while it is actually on screen. The numbers in the panel headers still update with every frame. */
const CHART_REDRAW_MS = 250
function useChartData<T>(data: T[], host: React.RefObject<HTMLDivElement | null>): T[] {
  const [shown, setShown] = useState(data)
  const latest = useRef(data)
  const visible = useRef(true)
  useEffect(() => { latest.current = data }, [data])
  useEffect(() => {
    const el = host.current
    let io: IntersectionObserver | null = null
    if (el && typeof IntersectionObserver !== 'undefined') {
      io = new IntersectionObserver((entries) => { visible.current = entries[0]?.isIntersecting ?? true }, { rootMargin: '150px' })
      io.observe(el)
    }
    const id = window.setInterval(() => {
      if (visible.current && document.visibilityState === 'visible') setShown((prev) => (prev === latest.current ? prev : latest.current))
    }, CHART_REDRAW_MS)
    return () => { window.clearInterval(id); io?.disconnect() }
  }, [host])
  return shown
}

export function ChartPanel({
  title,
  data,
  series,
  unit,
  current,
  domain,
  type = 'area',
  source,
  refLines,
  window = 80,
  height = 180,
  footer,
}: ChartPanelProps) {
  const host = useRef<HTMLDivElement | null>(null)
  const shown = useChartData(data, host)
  const refKey = (refLines ?? []).map((r) => `${r.y}|${r.label}`).join(';')
  const seriesKey = series.map((s) => `${s.label}|${s.color}`).join(';')
  // the chart element is rebuilt only when what it draws changes (series accessors are stable per label)
  const chart = useMemo(() => (
    <TelemetryChart
      data={shown}
      series={series.map((s) => ({ ...s, key: s.label }))}
      unit={unit}
      domain={domain}
      height={height}
      type={type}
      refLines={refLines}
      window={window}
    />
  ), [shown, seriesKey, refKey, unit, domain?.[0], domain?.[1], height, type, window])
  return (
    <Panel
      title={title}
      action={
        <span className="flex items-center gap-2">
          {source && <SourceTag source={source} />}
          <span className="tabular text-sm font-semibold text-foreground">
            {current}
            <span className="ml-1 text-xs font-normal text-muted-foreground">{unit}</span>
          </span>
        </span>
      }
    >
      <div ref={host}>{chart}</div>
      {series.length > 1 && (
        <div className="mt-2 flex flex-wrap gap-3">
          {series.map((s) => (
            <div key={s.label} className="flex items-center gap-1.5">
              <span className="size-2 rounded-full" style={{ backgroundColor: s.color }} />
              <span className="text-[0.7rem] text-muted-foreground">{s.label}</span>
            </div>
          ))}
        </div>
      )}
      {footer}
    </Panel>
  )
}
