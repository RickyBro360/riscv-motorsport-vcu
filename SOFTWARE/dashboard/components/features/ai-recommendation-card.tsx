'use client'

import { motion } from 'framer-motion'
import { BrainCircuit, Wind, Gauge } from 'lucide-react'
import { Panel } from '@/components/common/panel'
import { Badge } from '@/components/ui/badge'
import { useTelemetry } from '@/contexts/telemetry-context'
import { avgWing } from '@/lib/format'

export function AiRecommendationCard() {
  const { ai } = useTelemetry()
  if (!ai) return null

  const conf = Math.round(ai.confidence * 100)
  const grip = Math.round(ai.grip * 100)

  return (
    <Panel title="AI Engineer" icon={BrainCircuit} className="h-full">
      <div className="flex flex-col gap-4">
        <div className="flex items-center justify-between">
          <Badge variant="accent">{ai.corner}</Badge>
          <span className="text-[0.7rem] uppercase tracking-widest text-muted-foreground">
            {conf}% confidence
          </span>
        </div>

        <p className="text-sm leading-relaxed text-foreground">{ai.racingLine}</p>

        <div className="grid grid-cols-3 gap-3">
          <div className="rounded-lg border border-border bg-secondary/40 p-2.5">
            <div className="flex items-center gap-1.5 text-muted-foreground">
              <Wind className="size-3.5" />
              <span className="text-[0.65rem] uppercase tracking-wide">Wing</span>
            </div>
            <p className="tabular mt-1 text-lg font-semibold">
              {avgWing(ai.recommendedWing).toFixed(0)}
              <span className="text-xs text-muted-foreground">°</span>
            </p>
          </div>
          <div className="rounded-lg border border-border bg-secondary/40 p-2.5">
            <div className="flex items-center gap-1.5 text-muted-foreground">
              <Gauge className="size-3.5" />
              <span className="text-[0.65rem] uppercase tracking-wide">Grip</span>
            </div>
            <p className="tabular mt-1 text-lg font-semibold">
              {grip}
              <span className="text-xs text-muted-foreground">%</span>
            </p>
          </div>
          <div className="rounded-lg border border-border bg-secondary/40 p-2.5">
            <div className="flex items-center gap-1.5 text-muted-foreground">
              <BrainCircuit className="size-3.5" />
              <span className="text-[0.65rem] uppercase tracking-wide">Eff.</span>
            </div>
            <p className="tabular mt-1 text-lg font-semibold">
              {ai.efficiency.toFixed(0)}
              <span className="text-xs text-muted-foreground">%</span>
            </p>
          </div>
        </div>

        <div>
          <div className="mb-1.5 flex items-center justify-between text-[0.7rem] text-muted-foreground">
            <span>Confidence</span>
            <span className="tabular">{conf}%</span>
          </div>
          <div className="h-1.5 w-full overflow-hidden rounded-full bg-secondary">
            <motion.div
              className="h-full rounded-full bg-primary"
              animate={{ width: `${conf}%` }}
              transition={{ duration: 0.4 }}
            />
          </div>
        </div>

        <p className="text-xs text-muted-foreground">{ai.note}</p>
      </div>
    </Panel>
  )
}
