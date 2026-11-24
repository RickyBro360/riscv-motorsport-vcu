'use client'

import { useEffect, useState } from 'react'
import { Orbit } from 'lucide-react'
import { Panel } from '@/components/common/panel'
import { SourceTag } from '@/components/common/source-tag'
import { useTelemetry } from '@/contexts/telemetry-context'
import { useTrack } from '@/hooks/use-tracks'
import { effectiveConfig, sampleAt } from '@/lib/tracks'
import { formatLapTime } from '@/lib/format'
import { vehicleApi, type TwinState } from '@/services/api'

/** Same illustrative aero map as backend app/twin.py (saturating downforce, rising drag). */
function aero(front: number, rear: number) {
  const th = Math.max(0, (front + rear) / 2)
  return { cla: 1.782 + 3.372 * (1 - Math.exp(-th / 18)), cda: 0.923 + 0.004 * th + 0.000281 * th * th }
}

/** The digital twin: what the FULL-SIZE car would be doing at the position the 1:10
 * car is at, with the flap angles the 1:10 car actually has right now. */
export function TwinPanel() {
  const { frame, mode } = useTelemetry()
  const cfg = effectiveConfig(frame?.vcuConfig)
  const track = useTrack(frame?.track?.id ?? cfg.trackId)
  const [twin, setTwin] = useState<TwinState | null>(null)
  useEffect(() => {
    if (mode !== 'live') return
    let off = false
    const poll = () => vehicleApi.twin().then((t) => { if (!off) setTwin(t) }).catch(() => {})
    poll()
    const id = window.setInterval(poll, 1000)
    return () => { off = true; window.clearInterval(id) }
  }, [mode])

  if (!frame || !track) return null
  const pos = frame.track?.pos ?? 0
  const s = sampleAt(track, pos)
  const front = (frame.wing.frontLeft + frame.wing.frontRight) / 2
  const rear = (frame.wing.rearLeft + frame.wing.rearRight) / 2
  const a = aero(front, rear)
  const v = s.speedKmh / 3.6
  const local = { down: 0.5 * 1.2 * a.cla * v * v, drag: 0.5 * 1.2 * a.cda * v * v }
  const f = mode === 'live' && twin ? twin.forces : null
  const rows: [string, string][] = [
    ['Full-scale speed here', `${(f?.twinSpeedKmh ?? s.speedKmh).toFixed(0)} km/h`],
    ['Downforce (current flaps)', `${((f?.downforceN ?? local.down) / 1000).toFixed(1)} kN`],
    ['Drag (current flaps)', `${((f?.dragN ?? local.drag) / 1000).toFixed(2)} kN`],
    ['ClA / CdA', `${(f?.clA ?? a.cla).toFixed(2)} / ${(f?.cdA ?? a.cda).toFixed(2)} m²`],
    ['Lateral / longitudinal g', `${s.latG.toFixed(2)} / ${s.longG.toFixed(2)}`],
    ['Twin lap, current settings', twin ? formatLapTime(twin.lapTimeS) : formatLapTime(track.model.lapTimeS)],
    ['Twin lap, firmware defaults', twin ? formatLapTime(twin.defaultsLapTimeS) : formatLapTime(track.model.lapTimeS)],
  ]
  return (
    <Panel title={`Digital twin — full-size car at this point of ${track.name}`} icon={Orbit} action={<SourceTag source="modelled" />}>
      <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
        {rows.map(([k, val]) => (
          <div key={k} className="flex items-center justify-between rounded-md border border-border bg-secondary/40 px-3 py-2 text-sm">
            <span className="text-xs text-muted-foreground">{k}</span><span className="tabular font-semibold">{val}</span>
          </div>
        ))}
      </div>
      <p className="mt-2 text-[0.7rem] text-muted-foreground">Point-mass lap model with illustrative 2026-style aero (backend app/twin.py). The dyno cannot feel downforce, so these values are modelled, never measured. The asymmetric left/right split is a yaw-balance tool the point-mass model does not represent.</p>
    </Panel>
  )
}
