'use client'

import { useState } from 'react'
import dynamic from 'next/dynamic'
import { Boxes, RotateCw, Gauge, Wind, Move3d, Navigation, RotateCcw, Camera, BrainCircuit } from 'lucide-react'
import { useTelemetry } from '@/contexts/telemetry-context'
import { PageHeader } from '@/components/common/page-header'
import { Panel } from '@/components/common/panel'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { cn } from '@/lib/utils'
import { replayStatusLabel } from '@/lib/format'
import type { CameraPreset } from '@/types/telemetry'
import { TwinPanel } from '@/components/features/twin-panel'

const CarScene = dynamic(() => import('@/components/features/car-scene').then((m) => m.CarScene), { ssr: false, loading: () => <div className="flex h-full items-center justify-center text-sm text-muted-foreground">Initialising digital twin…</div> })
function AxisBar({ label, value, range }: { label: string; value: number; range: number }) { const pct = ((value + range) / (range * 2)) * 100; return <div><div className="mb-1.5 flex items-center justify-between"><span className="text-[0.7rem] uppercase tracking-widest text-muted-foreground">{label}</span><span className="tabular text-sm font-semibold">{value.toFixed(1)}°</span></div><div className="relative h-1.5 w-full rounded-full bg-secondary"><span className="absolute left-1/2 top-1/2 h-3 w-px -translate-y-1/2 bg-border" /><span className="absolute top-1/2 size-2.5 -translate-x-1/2 -translate-y-1/2 rounded-full bg-primary transition-all duration-150" style={{ left: `${Math.max(2, Math.min(98, pct))}%` }} /></div></div> }
function Readout({ label, value, accent }: { label: string; value: string; accent?: boolean }) { return <div className="rounded-lg border border-border bg-secondary/40 p-3"><span className="text-[0.65rem] uppercase tracking-widest text-muted-foreground">{label}</span><p className={cn('tabular mt-1 text-lg font-semibold', accent && 'text-primary')}>{value}</p></div> }

function AeroChannelRow({ channel, actual, target, recommended }: { channel: string; actual: number; target: number; recommended: number | null }) {
  return (
    <div className="grid grid-cols-4 items-center gap-2 rounded-lg border border-border bg-secondary/40 px-3 py-2 text-sm">
      <span className="text-xs uppercase tracking-widest text-muted-foreground">{channel}</span>
      <span className="tabular text-right font-semibold text-primary" title="Actual measured flap angle (position sensor)">{actual.toFixed(1)}°</span>
      <span className="tabular text-right text-foreground" title="VCU-approved target the servo is chasing">{target.toFixed(1)}°</span>
      <span className="tabular text-right text-muted-foreground" title="Racing Engineer Station AI recommendation — simulated, never commands the actuator directly">{recommended !== null ? `${recommended.toFixed(1)}°` : '—'}</span>
    </div>
  )
}

export default function DigitalTwinPage() {
  const { frame, ai, mode, replay } = useTelemetry(); const [preset, setPreset] = useState<CameraPreset>('driver'); const [resetKey, setResetKey] = useState(0)
  if (!frame) return <div><PageHeader title="Digital Twin" description="A live 3D reconstruction of the chassis attitude." /><div className="flex h-64 items-center justify-center text-sm text-muted-foreground">Waiting for IMU telemetry…</div></div>
  const yawNorm = ((frame.imu.yaw + 180) % 360) - 180
  const modeLabel = mode === 'replay' ? replayStatusLabel(replay.status) : mode === 'live' ? 'LIVE' : 'SIMULATION'
  const modeVariant: 'warning' | 'success' | 'info' = mode === 'replay' ? 'warning' : mode === 'live' ? 'success' : 'info'
  return <div><PageHeader title="Digital Twin" description="A 3D reconstruction of chassis attitude, steering and adaptive aero — always driven by the one central telemetry source for the active mode." actions={<div className="flex items-center gap-2"><Badge variant={modeVariant} className="gap-1.5 px-2.5 py-1">{modeLabel}</Badge><Badge variant="info" className="gap-1.5 px-2.5 py-1"><Move3d className="size-3.5" /> IMU-synced</Badge></div>} /><div className="grid grid-cols-1 gap-4 lg:grid-cols-3"><Panel title="Chassis Viewport" icon={Boxes} className="lg:col-span-2" bodyClassName="p-0" action={<div className="flex items-center gap-2"><span className="hidden items-center gap-1.5 text-[0.7rem] text-muted-foreground sm:flex"><RotateCw className="size-3" /> drag to orbit · Reset also zeroes heading</span><Button variant="outline" size="sm" onClick={() => setResetKey((v) => v + 1)}><RotateCcw className="size-3.5" /> Reset</Button></div>}><div className="h-[420px] w-full sm:h-[520px]"><CarScene preset={preset} resetKey={resetKey} /></div></Panel><div className="flex flex-col gap-4"><Panel title="Camera presets" icon={Camera}><div className="grid grid-cols-2 gap-2">{(['top', 'front', 'rear', 'driver'] as CameraPreset[]).map((item) => <Button key={item} size="sm" variant={preset === item ? 'default' : 'outline'} onClick={() => { setPreset(item); setResetKey((v) => v + 1) }}>{item}</Button>)}</div></Panel><Panel title="Orientation" icon={Navigation}><div className="space-y-4"><AxisBar label="Roll" value={frame.imu.roll} range={30} /><AxisBar label="Pitch" value={frame.imu.pitch} range={20} /><AxisBar label="Yaw" value={yawNorm} range={180} /></div></Panel><Panel title="Actuators" icon={Wind}><div className="grid grid-cols-2 gap-3"><Readout label="Steering" value={`${frame.steering.toFixed(1)}°`} /><Readout label="Speed" value={`${frame.speed.toFixed(2)} km/h`} accent /><Readout label="Front-Left Wing" value={`${frame.wing.frontLeft.toFixed(1)}°`} /><Readout label="Front-Right Wing" value={`${frame.wing.frontRight.toFixed(1)}°`} /><Readout label="Rear-Left Wing" value={`${frame.wing.rearLeft.toFixed(1)}°`} /><Readout label="Rear-Right Wing" value={`${frame.wing.rearRight.toFixed(1)}°`} /></div></Panel><Panel title="Dynamics" icon={Gauge}><div className="grid grid-cols-2 gap-3"><Readout label="Lat G" value={`${frame.gForceLat.toFixed(2)} g`} /><Readout label="Long G" value={`${frame.gForceLong.toFixed(2)} g`} /></div></Panel></div></div>
    <div className="mt-4"><TwinPanel /></div>
    <Panel className="mt-4" title="Aero authority chain — actual vs VCU-approved vs AI recommendation" icon={BrainCircuit}>
      <p className="mb-3 text-xs text-muted-foreground">
        AI recommendations (Racing Engineer Station backend) never command the actuator directly — the ESP32-C6 VCU validates every request before it becomes a target the servo chases. These three columns are never the same signal, even when they read close together.
      </p>
      <div className="mb-2 grid grid-cols-4 gap-2 px-3 text-[0.65rem] uppercase tracking-widest text-muted-foreground">
        <span>Channel</span><span className="text-right">Actual</span><span className="text-right">VCU target</span><span className="text-right">AI recommends{mode !== 'live' ? ' (sim)' : ''}</span>
      </div>
      <div className="space-y-2">
        <AeroChannelRow channel="LF" actual={frame.wing.frontLeft} target={frame.wingTarget.frontLeft} recommended={ai?.recommendedWing.frontLeft ?? null} />
        <AeroChannelRow channel="RF" actual={frame.wing.frontRight} target={frame.wingTarget.frontRight} recommended={ai?.recommendedWing.frontRight ?? null} />
        <AeroChannelRow channel="LR" actual={frame.wing.rearLeft} target={frame.wingTarget.rearLeft} recommended={ai?.recommendedWing.rearLeft ?? null} />
        <AeroChannelRow channel="RR" actual={frame.wing.rearRight} target={frame.wingTarget.rearRight} recommended={ai?.recommendedWing.rearRight ?? null} />
      </div>
    </Panel>
  </div>
}
