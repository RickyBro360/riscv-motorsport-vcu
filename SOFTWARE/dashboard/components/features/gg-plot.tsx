'use client'

import { memo } from 'react'

import { CartesianGrid, ResponsiveContainer, Scatter, ScatterChart, Tooltip, XAxis, YAxis, ZAxis, Legend } from 'recharts'

/** Friction-circle (g-g) plot: measured IMU points next to the modelled full-scale
 * points for the same positions on the circuit. On the dyno the chassis cannot
 * corner, so the measured cloud sits near the origin — shown, not hidden. */
// FW 2.3.10: memoised — it redraws only when its points change (the page passes them at <= 4 Hz)
export const GGPlot = memo(function GGPlot({ measured, modelled, limit = 6 }: {
  measured: { x: number; y: number }[]
  modelled: { x: number; y: number }[]
  limit?: number
}) {
  return (
    <ResponsiveContainer width="100%" height={260}>
      <ScatterChart margin={{ top: 8, right: 12, bottom: 4, left: -8 }}>
        <CartesianGrid stroke="var(--color-border)" strokeDasharray="3 3" />
        <XAxis type="number" dataKey="x" domain={[-limit, limit]} tick={{ fill: 'var(--color-muted-foreground)', fontSize: 10 }}
          label={{ value: 'lateral g', position: 'insideBottomRight', fill: 'var(--color-muted-foreground)', fontSize: 10 }} />
        <YAxis type="number" dataKey="y" domain={[-limit, limit]} width={34} tick={{ fill: 'var(--color-muted-foreground)', fontSize: 10 }} />
        <ZAxis range={[14, 14]} />
        <Tooltip cursor={false} formatter={(v) => Number(v).toFixed(2)} />
        <Legend wrapperStyle={{ fontSize: 11 }} />
        <Scatter name="Modelled (full scale)" data={modelled} fill="var(--color-chart-5)" fillOpacity={0.5} isAnimationActive={false} />
        <Scatter name="Measured (IMU on dyno)" data={measured} fill="var(--color-success)" isAnimationActive={false} />
      </ScatterChart>
    </ResponsiveContainer>
  )
})
