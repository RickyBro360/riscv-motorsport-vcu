'use client'

import { useEffect, useMemo, useState } from 'react'
import { Area, AreaChart, CartesianGrid, ReferenceArea, ReferenceLine, ResponsiveContainer, XAxis, YAxis, Tooltip } from 'recharts'
import { Flag, Gauge, Map as MapIcon, Navigation, Send, Timer, Wind } from 'lucide-react'
import { PageHeader } from '@/components/common/page-header'
import { Panel } from '@/components/common/panel'
import { SourceTag } from '@/components/common/source-tag'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { CircuitMap } from '@/components/features/circuit-map'
import { FlapDiagram } from '@/components/features/flap-diagram'
import { CarControl } from '@/components/features/car-control'
import { useTelemetry } from '@/contexts/telemetry-context'
import { useTracks } from '@/hooks/use-tracks'
import { useSmoothPosition } from '@/hooks/use-smooth-position'
import { useAiRecommendation } from '@/hooks/use-ai-recommendation'
import { cornerNear, effectiveConfig, nextEvent, sampleAt } from '@/lib/tracks'
import { formatLapTime } from '@/lib/format'
import { vehicleApi } from '@/services/api'

export default function TrackMapPage() {
  const { frame, mode, link, setSimulationTrack } = useTelemetry()
  const doc = useTracks()
  const cfg = effectiveConfig(frame?.vcuConfig)
  const tr = frame?.track
  const trackId = tr?.id ?? cfg.trackId
  const track = doc ? doc.tracks[trackId] ?? doc.tracks[doc.defaultTrackId] : null
  const [pick, setPick] = useState<number | null>(null)
  const [sendState, setSendState] = useState<string | null>(null)
  const rear = frame?.wheels ? (frame.wheels.rl + frame.wheels.rr) / 2 : frame?.speed ?? 0
  const pos = useSmoothPosition(tr?.pos, rear, cfg.lapLengthCm / 100, !!tr?.runActive)
  const rec = useAiRecommendation(1000) as (Record<string, unknown> & { posAhead?: number; positionAware?: boolean }) | null
  const { ai } = useTelemetry()
  const [bestMs, setBestMs] = useState<number | null>(null)
  const lastLapMs = tr?.lastLapMs ?? 0
  useEffect(() => { if (lastLapMs > 0) setBestMs((b) => (b === null || lastLapMs < b ? lastLapMs : b)) }, [lastLapMs])

  useEffect(() => { if (pick === null && track) setPick(track.id) }, [track, pick])

  const aheadPos = mode === 'live' ? (rec?.positionAware ? (rec.posAhead as number) : null)
    : tr ? (pos + ((rear / 3.6) / (cfg.lapLengthCm / 100)) * 0.35) % 1 : null

  const profile = useMemo(() => track ? track.series.speedKmh.map((v, i) => ({
    x: (i / track.series.speedKmh.length) * 100, speed: v, steer: track.series.steerDeg[i],
  })) : [], [track])
  const zones = useMemo(() => track ? track.straightZones.map((z) => ({ a: z.start * 100, b: (z.end < z.start ? 1 : z.end) * 100 })) : [], [track])

  const sendTrack = async () => {
    if (pick === null) return
    if (mode !== 'live') { setSimulationTrack(pick); setSendState('Simulation switched to the new circuit'); return }
    try {
      const c = await vehicleApi.selectTrack(pick)
      setSendState(`Queued for the VCU (#${c.id}) — it accepts a circuit change only while the car is stopped`)
    } catch (e) {
      setSendState(`Backend refused: ${(e as Error).message}`)
    }
  }

  const aiReason = mode === 'live' ? (typeof rec?.reason === 'string' ? rec.reason : null) : ai?.reason ?? null
  const sample = track && tr ? sampleAt(track, pos) : null
  const corner = track && tr ? cornerNear(track, pos) : null
  const ev = track && tr ? nextEvent(track, pos, cfg.straightOpenDelayM, cfg.straightCloseEarlyM) : null
  const src = mode === 'live' ? (link === 'live_hardware' ? 'VCU odometry (rear hall sensors), via LoRa' : 'waiting for the car')
    : mode === 'replay' ? 'recorded VCU position' : 'offline simulation'

  return (
    <div>
      <PageHeader
        title={track ? `Track Map — ${track.name}` : 'Track Map'}
        description="The car's position on the stored circuit as the VCU reports it. The VCU moves the flaps and the steering from this same number, so the map and the physical car cannot drift apart. Green = straight mode (low-drag flaps), red = braking zones, dashed ring = where the AI engineer is looking ahead."
        actions={<div className="flex flex-wrap items-center gap-2">
          <select value={pick ?? ''} onChange={(e) => setPick(Number(e.target.value))}
            className="h-8 rounded-md border border-border bg-background px-2 text-sm">
            {doc?.tracks.map((t) => <option key={t.id} value={t.id}>{t.code} · {t.name} ({t.grandPrix})</option>)}
          </select>
          <Button size="sm" onClick={sendTrack} disabled={pick === null || pick === track?.id}><Send className="size-3.5" /> {mode === 'live' ? 'Send to car' : 'Load'}</Button>
          <Badge variant={mode === 'live' && link === 'live_hardware' ? 'success' : 'outline'}><Navigation className="size-3" /> {src}</Badge>
        </div>}
      />
      {sendState && <p className="-mt-3 mb-4 text-xs text-muted-foreground">{sendState}</p>}
      <div className="grid gap-4 xl:grid-cols-[1fr_340px]">
        <Panel title={track ? `${track.fullName} · ${(track.lengthM / 1000).toFixed(3)} km` : 'Loading circuit…'} icon={MapIcon} bodyClassName="p-0"
          action={<SourceTag source={mode === 'live' ? 'vcu' : mode === 'replay' ? 'vcu' : 'simulated'} />}>
          <div className="relative aspect-[1.45] min-h-[420px] bg-secondary/20 p-4">
            {track && <CircuitMap track={track} pos={tr ? pos : null} aheadPos={aheadPos} cfg={cfg} straightMode={tr?.straightMode} />}
            <div className="absolute bottom-3 left-4 flex flex-wrap gap-3 text-[0.7rem] text-muted-foreground">
              <span className="flex items-center gap-1"><span className="h-1 w-4 rounded bg-success" /> straight mode</span>
              <span className="flex items-center gap-1"><span className="h-1 w-4 rounded bg-danger" /> braking</span>
              <span className="flex items-center gap-1"><span className="size-2.5 rounded-full border border-dashed border-[var(--color-chart-5)]" /> AI look-ahead</span>
              <span className="flex items-center gap-1"><span className="size-2 rounded-full bg-info" /> sector line</span>
            </div>
            {frame?.vcu?.driveMode === 'track' ? (
              <div className="absolute inset-x-0 top-4 text-center text-xs text-muted-foreground">
                TRACK mode — the car is on the floor and free-driving: there is no position on a stored circuit. Switch to DYNO mode (car on the rollers) to drive a circuit.</div>
            ) : !tr?.runActive && <div className="absolute inset-x-0 top-4 text-center text-xs text-muted-foreground">
              No run active — press <b>Start run</b> (Car control), send <code>lap run</code> on the VCU console, or hold ARM with track run enabled.</div>}
          </div>
        </Panel>
        <div className="space-y-4">
          <CarControl compact />
          <Panel title="Lap" icon={Timer} action={<SourceTag source="vcu" />}>
            <div className="grid grid-cols-2 gap-3">
              <div><p className="text-xs text-muted-foreground">Lap</p><p className="tabular mt-1 text-2xl font-semibold">{tr ? tr.lap + 1 : '—'}</p></div>
              <div><p className="text-xs text-muted-foreground">Progress</p><p className="tabular mt-1 text-2xl font-semibold">{tr ? `${(pos * 100).toFixed(0)}%` : '—'}</p></div>
              <div><p className="text-xs text-muted-foreground">Current</p><p className="tabular mt-1 font-mono text-lg">{tr ? formatLapTime(tr.currentLapMs / 1000) : '—'}</p></div>
              <div><p className="text-xs text-muted-foreground">Last</p><p className="tabular mt-1 font-mono text-lg">{tr?.lastLapMs ? formatLapTime(tr.lastLapMs / 1000) : '—'}</p></div>
              <div><p className="text-xs text-muted-foreground">Best (dyno)</p><p className="tabular mt-1 font-mono text-lg text-primary">{bestMs ? formatLapTime(bestMs / 1000) : '—'}</p></div>
              <div><p className="text-xs text-muted-foreground">Sector</p><p className="tabular mt-1 text-lg">{tr ? `S${tr.sector + 1}` : '—'}</p></div>
            </div>
            <p className="mt-3 text-[0.7rem] text-muted-foreground">Dyno lap = {(cfg.lapLengthCm / 100).toFixed(0)} m of wheel travel. Model lap at full scale: {track ? formatLapTime(track.model.lapTimeS) : '—'}.</p>
          </Panel>
          <Panel title="Where the car is" icon={Flag} action={<SourceTag source="modelled" />}>
            <div className="space-y-2 text-sm">
              <div className="flex justify-between"><span className="text-muted-foreground">Location</span><span>{corner ? `T${corner.n} (${corner.dir === 'L' ? 'left' : 'right'}, R${corner.radiusM.toFixed(0)} m)` : sample?.straight ? 'straight-mode zone' : tr ? 'corner-mode section' : '—'}</span></div>
              <div className="flex justify-between"><span className="text-muted-foreground">Next</span><span>{ev ? `${ev.type} in ${ev.inM.toFixed(0)} m` : '—'}</span></div>
              <div className="flex justify-between"><span className="text-muted-foreground">Full-scale speed here</span><span className="tabular">{sample ? `${sample.speedKmh.toFixed(0)} km/h` : '—'}</span></div>
              <div className="flex justify-between"><span className="text-muted-foreground">Lateral / long. g</span><span className="tabular">{sample ? `${sample.latG.toFixed(1)} / ${sample.longG.toFixed(1)}` : '—'}</span></div>
              <div className="flex justify-between"><span className="text-muted-foreground">Map steering</span><span className="tabular">{sample ? `${sample.steerDeg.toFixed(1)}°` : '—'}</span></div>
              <div className="flex justify-between"><span className="text-muted-foreground">Motor duty (profile)</span><span className="tabular">{tr?.profileDutyPct != null ? `${tr.profileDutyPct.toFixed(0)}%` : frame ? `${frame.throttle.toFixed(0)}%` : '—'}</span></div>
            </div>
          </Panel>
          <Panel title="Flaps now" icon={Wind} action={<SourceTag source="measured" />}>
            {frame ? <FlapDiagram wing={frame.wing} target={frame.wingTarget} straightMode={tr?.straightMode} /> : <p className="text-xs text-muted-foreground">No telemetry</p>}
            {aiReason && <p className="mt-2 text-[0.7rem] text-muted-foreground">AI: {aiReason}</p>}
          </Panel>
        </div>
      </div>
      <Panel className="mt-4" title="Speed trace along the lap (full-scale model) with the car's position" icon={Gauge} action={<SourceTag source="modelled" />}>
        <ResponsiveContainer width="100%" height={200}>
          <AreaChart data={profile} margin={{ top: 6, right: 10, left: -10, bottom: 0 }}>
            <CartesianGrid stroke="var(--color-border)" strokeDasharray="3 3" vertical={false} />
            <XAxis dataKey="x" type="number" domain={[0, 100]} tick={{ fill: 'var(--color-muted-foreground)', fontSize: 10 }} unit="%" />
            <YAxis domain={[0, 400]} width={40} tick={{ fill: 'var(--color-muted-foreground)', fontSize: 10 }} />
            <Tooltip formatter={(v) => `${Number(v).toFixed(0)} km/h`} labelFormatter={(l) => `${Number(l).toFixed(1)} % of lap`} />
            {zones.map((z, i) => <ReferenceArea key={i} x1={z.a} x2={z.b} fill="var(--color-success)" fillOpacity={0.08} />)}
            <Area dataKey="speed" type="monotone" stroke="var(--color-primary)" fill="var(--color-primary)" fillOpacity={0.12} isAnimationActive={false} dot={false} />
            {tr && <ReferenceLine x={pos * 100} stroke="var(--color-foreground)" strokeWidth={2} />}
          </AreaChart>
        </ResponsiveContainer>
        <p className="text-[0.7rem] text-muted-foreground">{doc?.attribution}</p>
      </Panel>
    </div>
  )
}
