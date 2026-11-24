'use client'

import { Flag, MapPin } from 'lucide-react'
import { Panel } from '@/components/common/panel'
import { Badge } from '@/components/ui/badge'
import { CircuitMap } from '@/components/features/circuit-map'
import { useTelemetry } from '@/contexts/telemetry-context'
import { useTrack } from '@/hooks/use-tracks'
import { useSmoothPosition } from '@/hooks/use-smooth-position'
import { effectiveConfig } from '@/lib/tracks'
import { formatLapTime } from '@/lib/format'

export function TrackStatus() {
  const { lap, frame } = useTelemetry()
  const cfg = effectiveConfig(frame?.vcuConfig)
  const tr = frame?.track
  const track = useTrack(tr?.id ?? cfg.trackId)
  const rear = frame?.wheels ? (frame.wheels.rl + frame.wheels.rr) / 2 : frame?.speed ?? 0
  const pos = useSmoothPosition(tr?.pos, rear, cfg.lapLengthCm / 100, !!tr?.runActive)

  return (
    <Panel title="Track Status" icon={Flag} className="h-full">
      <div className="flex flex-col gap-3">
        <div className="flex items-center gap-2">
          <MapPin className="size-4 text-primary" />
          <span className="text-sm font-medium">{track ? `${track.name} · ${track.grandPrix}` : '—'}</span>
          <Badge variant={tr?.runActive || frame?.vcu?.driveMode === 'track' ? (tr?.straightMode ? 'success' : 'accent') : 'outline'} className="ml-auto">
            {tr?.runActive ? (tr.straightMode ? 'Straight mode' : 'Corner mode')
              : frame?.vcu?.driveMode === 'track' ? `Track mode · ${tr?.straightMode ? 'straight' : 'corner'}` : 'No run'}
          </Badge>
        </div>
        <div className="aspect-[1.6] rounded-md bg-secondary/20 p-1">
          {track && <CircuitMap track={track} pos={tr ? pos : null} cfg={cfg} straightMode={tr?.straightMode} compact showCorners={false} />}
        </div>
        <div className="grid grid-cols-3 gap-3 text-center">
          <div>
            <p className="tabular text-2xl font-semibold">{tr ? tr.lap + 1 : lap.lap}</p>
            <p className="text-[0.65rem] uppercase tracking-widest text-muted-foreground">Lap</p>
          </div>
          <div>
            <p className="tabular text-2xl font-semibold">{formatLapTime(lap.currentLapTime)}</p>
            <p className="text-[0.65rem] uppercase tracking-widest text-muted-foreground">Current</p>
          </div>
          <div>
            <p className="tabular text-2xl font-semibold text-primary">{lap.lastLapTime ? formatLapTime(lap.lastLapTime) : '—'}</p>
            <p className="text-[0.65rem] uppercase tracking-widest text-muted-foreground">Last</p>
          </div>
        </div>
      </div>
    </Panel>
  )
}
