'use client'

import {
  ResponsiveContainer,
  AreaChart,
  Area,
  Line,
  LineChart,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ReferenceLine,
} from 'recharts'
import type { TelemetryFrame } from '@/types/telemetry'

interface Series {
  key: keyof TelemetryFrame | string
  accessor: (f: TelemetryFrame) => number
  color: string
  label: string
}

interface TelemetryChartProps {
  data: TelemetryFrame[]
  series: Series[]
  unit?: string
  domain?: [number | 'auto', number | 'auto']
  height?: number
  type?: 'area' | 'line'
  window?: number
  /** horizontal reference lines, e.g. the adapter's 2.2 A rating */
  refLines?: { y: number; label: string; color?: string }[]
}

function CustomTooltip({ active, payload, unit }: any) {
  if (!active || !payload?.length) return null
  return (
    <div className="glass rounded-lg border border-border px-3 py-2 text-xs shadow-lg">
      {payload.map((p: any) => (
        <div key={p.name} className="flex items-center gap-2">
          <span className="size-2 rounded-full" style={{ backgroundColor: p.stroke }} />
          <span className="text-muted-foreground">{p.name}</span>
          <span className="tabular ml-auto font-semibold text-foreground">
            {typeof p.value === 'number' ? p.value.toFixed(Math.abs(p.value) < 10 ? 2 : 1) : p.value}
            {unit ? ` ${unit}` : ''}
          </span>
        </div>
      ))}
    </div>
  )
}

/** Compact tick labels: 2.5, 0.25, 1.2k — never a 6-digit float cut off by the axis width. */
function fmtTick(v: number): string {
  if (!Number.isFinite(v)) return ''
  const a = Math.abs(v)
  if (a >= 1000) return `${(v / 1000).toFixed(a >= 10000 ? 0 : 1)}k`
  if (a >= 10 || a === 0) return v.toFixed(0)
  if (a >= 1) return v.toFixed(1).replace(/\.0$/, '')
  return v.toFixed(2)
}

export function TelemetryChart({
  data,
  series,
  unit,
  domain: domainIn = ['auto', 'auto'],
  height = 220,
  type = 'area',
  window = 80,
  refLines = [],
}: TelemetryChartProps) {
  const sliced = data.slice(-window)
  const chartData = sliced.map((f, i) => {
    const row: Record<string, number> = { i }
    for (const s of series) row[s.label] = s.accessor(f)
    return row
  })

  // FW 2.3.4: the fixed axis is only the MINIMUM range. If the data in view goes past it, the axis
  // grows to fit (with a little headroom) instead of flattening the peaks at the edge.
  let domain = domainIn
  if (typeof domainIn[0] === 'number' && typeof domainIn[1] === 'number') {
    let lo = domainIn[0], hi = domainIn[1]
    const span = hi - lo
    let dMin = Infinity, dMax = -Infinity
    for (const row of chartData) for (const sr of series) { const v = row[sr.label]; if (Number.isFinite(v)) { if (v < dMin) dMin = v; if (v > dMax) dMax = v } }
    const nice = (v: number) => { const step = Math.pow(10, Math.floor(Math.log10(Math.max(span, 1e-9) / 4))); return Math.ceil(Math.abs(v) / step) * step * Math.sign(v) }
    if (dMax > hi) hi = nice(dMax + span * 0.08)
    if (dMin < lo) lo = nice(dMin - span * 0.08)
    if (lo < 0 && domainIn[0] === -domainIn[1]) { const m = Math.max(-lo, hi); lo = -m; hi = m }   // keep symmetric axes symmetric
    domain = [lo, hi]
  }
  const numeric = typeof domain[0] === 'number' && typeof domain[1] === 'number'
  const ticks = numeric ? [0, 1, 2, 3, 4].map((k) => (domain[0] as number) + ((domain[1] as number) - (domain[0] as number)) * k / 4) : undefined
  const refs = refLines.map((r) => (
    <ReferenceLine key={r.label} y={r.y} stroke={r.color ?? 'var(--color-danger)'} strokeDasharray="4 3"
      label={{ value: r.label, position: 'insideTopRight', fill: r.color ?? 'var(--color-danger)', fontSize: 10 }} />
  ))
  return (
    <ResponsiveContainer width="100%" height={height}>
      {type === 'area' ? (
        <AreaChart data={chartData} margin={{ top: 6, right: 8, left: -8, bottom: 0 }}>
          <defs>
            {series.map((s) => (
              <linearGradient key={s.label} id={`grad-${s.label}`} x1="0" y1="0" x2="0" y2="1">
                <stop offset="0%" stopColor={s.color} stopOpacity={0.35} />
                <stop offset="100%" stopColor={s.color} stopOpacity={0} />
              </linearGradient>
            ))}
          </defs>
          <CartesianGrid stroke="var(--color-border)" strokeDasharray="3 3" vertical={false} />
          <XAxis dataKey="i" hide />
          <YAxis
            domain={domain}
            width={38}
            allowDataOverflow={numeric}
            ticks={ticks}
            tickFormatter={fmtTick}
            tick={{ fill: 'var(--color-muted-foreground)', fontSize: 10 }}
            tickLine={false}
            axisLine={false}
          />
          <Tooltip content={<CustomTooltip unit={unit} />} isAnimationActive={false} />
          {refs}
          {series.map((s) => (
            <Area
              key={s.label}
              type="monotone"
              dataKey={s.label}
              stroke={s.color}
              strokeWidth={2}
              fill={`url(#grad-${s.label})`}
              isAnimationActive={false}
              dot={false}
            />
          ))}
        </AreaChart>
      ) : (
        <LineChart data={chartData} margin={{ top: 6, right: 8, left: -8, bottom: 0 }}>
          <CartesianGrid stroke="var(--color-border)" strokeDasharray="3 3" vertical={false} />
          <XAxis dataKey="i" hide />
          <YAxis
            domain={domain}
            width={38}
            allowDataOverflow={numeric}
            ticks={ticks}
            tickFormatter={fmtTick}
            tick={{ fill: 'var(--color-muted-foreground)', fontSize: 10 }}
            tickLine={false}
            axisLine={false}
          />
          <Tooltip content={<CustomTooltip unit={unit} />} isAnimationActive={false} />
          {refs}
          {series.map((s) => (
            <Line
              key={s.label}
              type="monotone"
              dataKey={s.label}
              stroke={s.color}
              strokeWidth={2}
              isAnimationActive={false}
              dot={false}
            />
          ))}
        </LineChart>
      )}
    </ResponsiveContainer>
  )
}
