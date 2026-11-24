'use client'

import { useCallback, useEffect, useState } from 'react'
import { Clapperboard, Crosshair, Flag, Gauge, OctagonX, Play, Power, RotateCcw, Square, Wind, Wrench, Zap } from 'lucide-react'
import { Panel } from '@/components/common/panel'
import { SegmentedControl } from '@/components/common/segmented-control'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { useTelemetry } from '@/contexts/telemetry-context'
import { cn } from '@/lib/utils'
import { vehicleApi, type VehicleCommand } from '@/services/api'
import { loadTracks, type TrackDef } from '@/lib/tracks'

/**
 * FW 2.2 — the car's operating controls. Every button is a REQUEST: it is queued as a
 * CONFIG_WRITE, travels Pi → CAN (or LoRa → Pi → CAN), and the VCU decides against its
 * live state. What is shown under the buttons is the VCU's own answer (CONFIG_ACK), never
 * a guess. In Simulation Mode the same buttons drive the offline simulator.
 *
 *   Drive mode   DYNO  = car on the rollers: circuit runs + dyno calibration allowed
 *                TRACK = car on the floor: only a driver holding ARM moves it
 *   Start run    the stored circuit drives steering, motor and flaps (DYNO, IDLE, no fault, link up)
 *   Motor        FW 2.3: run the rear motor at a set duty from here, in EITHER drive mode
 *                (IDLE, no fault, link up — the link is the deadman). Drag the slider while it runs.
 *   Steering     FW 2.3: the KY-040 knob on the car is the steering wheel; the flaps follow it
 *                (straight: all MAX; turning: the inside pair drops toward FLAT). "Centre" = straight ahead.
 *   DRS          FW 2.3.3: all four flaps FLAT for 5 s whatever the steering, all MAX for 1 s,
 *                then back to following the steering. The VCU runs the timer (key 25).
 *   E-STOP       latches on the VCU (motor off, kill switch off, flaps + steering to failsafe)
 */
const DRS_OPEN_MS = 5000   // = VCU_CAL_DRS_OPEN_MS / _HOLD_MS in vcu_calibration.h (the VCU owns the timing)
const DRS_HOLD_MS = 1000
const HW_LABEL: Record<string, string> = { prototype: 'Prototype (hobby modules)', pcb_rev_b: 'PCB rev B' }
const STATUS_TEXT: Record<string, string> = {
  queued: 'queued', sent: 'sent — waiting for the VCU', applied: 'done (VCU ACK)', staged_next_lap: 'staged',
  rejected_state: 'REFUSED by the VCU (not allowed in this state)', rejected_range: 'REFUSED (out of range)',
  rejected_unknown_key: 'REFUSED (firmware too old?)', expired: 'no answer from the car', superseded: 'superseded',
}

export function CarControl({ compact = false, className }: { compact?: boolean; className?: string }) {
  const { frame, mode, link, simulateCarCommand } = useTelemetry()
  const [laps, setLaps] = useState('0')
  const [duty, setDuty] = useState(30)
  const [msg, setMsg] = useState<string | null>(null)
  const [cmds, setCmds] = useState<VehicleCommand[]>([])
  const [drsAt, setDrsAt] = useState<number | null>(null)     // local countdown (display only)
  const [now, setNow] = useState(0)
  const live = mode === 'live'
  const replay = mode === 'replay'
  const v = frame?.vcu
  const driveMode = v?.driveMode ?? (frame?.vcuConfig?.driveMode === undefined ? null : Number(frame.vcuConfig.driveMode) === 1 ? 'track' : 'dyno')
  const hw = v?.hwProfile ?? null
  const state = frame?.vcuState ?? '—'
  const runState = frame?.runState ?? 'off'
  const estopped = state === 'EMERGENCY_STOP'
  const running = runState !== 'off' && runState !== undefined
  const reporting = live ? link === 'live_hardware' : !!frame

  const refresh = useCallback(async () => {
    if (!live) return
    try { setCmds(await vehicleApi.commands()) } catch { /* backend down: the buttons will say so */ }
  }, [live])
  useEffect(() => { refresh(); if (!live) return; const id = window.setInterval(refresh, 1000); return () => window.clearInterval(id) }, [refresh, live])

  const send = async (label: string, liveCall: () => Promise<VehicleCommand>, key: number, value: number) => {
    if (replay) return
    if (!live) { const r = simulateCarCommand(key, value); setMsg(`${label}: ${STATUS_TEXT[r] ?? r} (simulation)`); return }
    try {
      const c = await liveCall()
      setMsg(`${label}: queued for the VCU (#${c.id})`)
      refresh()
    } catch (e) { setMsg(`${label}: backend refused — ${(e as Error).message.replace(/^API POST \S+ failed: \d+ /, '')}`) }
  }

  const blocked: string[] = []
  if (driveMode !== 'dyno') blocked.push('switch to DYNO mode (car on the rollers)')
  if (state !== 'IDLE') blocked.push(`VCU must be IDLE (now ${state})`)
  if (running) blocked.push(`a run is active (${runState})`)
  if (v?.groundLinkOk === false) blocked.push('the VCU does not see the ground link')
  if (!reporting) blocked.push('car not reporting')
  const canStart = blocked.length === 0

  const lastOf = (key: number) => cmds.find((c) => c.key === key)
  const lastRun = lastOf(6), lastMode = lastOf(5), lastEstop = lastOf(8), lastMotor = lastOf(9), lastDrs = lastOf(25)

  // FW 2.3.3 — DRS. The VCU reports its phase as config key 25 (drsCmd); the countdown here is
  // started locally when the button is pressed so it ticks smoothly between reports.
  const drsReported = frame?.vcuConfig?.drsCmd === undefined ? null : Number(frame.vcuConfig.drsCmd)
  const drsElapsed = drsAt === null ? null : now - drsAt
  const drsLocal = drsElapsed === null ? 0 : drsElapsed < DRS_OPEN_MS ? 1 : drsElapsed < DRS_OPEN_MS + DRS_HOLD_MS ? 2 : 0
  const drsPhase = drsLocal || (drsReported ?? 0)
  const drsRejected = live && !!lastDrs && drsAt !== null && lastDrs.value === 1 && lastDrs.created_at * 1000 >= drsAt - 5000 &&
    (lastDrs.status.startsWith('rejected') || lastDrs.status === 'expired')
  useEffect(() => {
    if (drsAt === null) return
    const id = window.setInterval(() => {
      const t = Date.now(); setNow(t)
      if (t - drsAt > DRS_OPEN_MS + DRS_HOLD_MS + 200) setDrsAt(null)
    }, 100)
    return () => window.clearInterval(id)
  }, [drsAt])
  useEffect(() => { if (drsRejected) setDrsAt(null) }, [drsRejected])
  const drsLeft = drsElapsed === null ? 0 : drsLocal === 1 ? DRS_OPEN_MS - drsElapsed : DRS_OPEN_MS + DRS_HOLD_MS - drsElapsed
  const pressDrs = () => {
    const t = Date.now(); setNow(t); setDrsAt(t)
    send('DRS', () => vehicleApi.drs('open'), 25, 1)
  }
  const cancelDrs = () => { setDrsAt(null); send('DRS cancel', () => vehicleApi.drs('cancel'), 25, 0) }
  const canDrs = !replay && (!live || reporting) && !estopped

  // FW 2.3 — motor from the dashboard (key 9) and steering readout
  const motorRunning = runState === 'bench_motor'
  const demoLap = Number(frame?.vcuConfig?.demoCmd ?? 0) === 2     // demo 2 = one scaled lap of a circuit
  // FW 2.3.10: demo 2 on any circuit
  const [tracks, setTracks] = useState<TrackDef[]>([])
  const [demoTrack, setDemoTrack] = useState<number | null>(null)
  useEffect(() => { loadTracks().then((d) => setTracks(d.tracks)).catch(() => {}) }, [])
  const carTrack = Number(frame?.vcuConfig?.trackId ?? 12)
  const demoTrackId = demoTrack ?? carTrack
  const demoTrackName = tracks.find((t) => t.id === (demoLap ? carTrack : demoTrackId))?.name ?? 'circuit'
  const demoRunning = runState === 'demo' || demoLap          // FW 2.3.4: scripted corner sequence, run by the VCU
  const motorBlocked: string[] = []
  if (!live) motorBlocked.push('Live mode with the car connected')
  if (state !== 'IDLE' && !motorRunning) motorBlocked.push(`VCU must be IDLE (now ${state})`)
  if (running && !motorRunning) motorBlocked.push(`a circuit run is active (${runState})`)
  if (v?.groundLinkOk === false) motorBlocked.push('the VCU does not see the ground link')
  if (live && !reporting) motorBlocked.push('car not reporting')
  const canMotor = motorBlocked.length === 0
  const motorNow = frame?.throttle ?? 0
  const steer = frame?.steering ?? 0               // + = LEFT (project convention)
  const steerText = Math.abs(steer) < 0.25 ? 'straight' : `${Math.abs(steer).toFixed(1)}° ${steer > 0 ? 'left' : 'right'}`
  const sendMotor = (pct: number) => {
    const p = Math.max(0, Math.min(100, Math.round(pct)))
    send(p === 0 ? 'Motor stop' : `Motor ${p} %`, () => vehicleApi.motor(p), 9, p * 10)
  }
  // dragging the slider while the motor runs changes the duty when the slider is released
  const commitSlider = () => { if (motorRunning && live) sendMotor(duty) }

  const setDrive = (m: 'dyno' | 'track') => {
    if (m === driveMode) return
    if (m === 'dyno' && !window.confirm('DYNO mode lets the dashboard and the console start the motor with nobody holding a deadman.\n\nIs the car ON THE ROLLERS (or wheels off the ground)?')) return
    send(`Drive mode → ${m.toUpperCase()}`, () => vehicleApi.setMode(m), 5, m === 'track' ? 1 : 0)
  }
  const startRun = () => {
    const n = Math.max(0, Math.min(99, Math.round(Number(laps) || 0)))
    send(`Start run (${n ? `${n} lap${n > 1 ? 's' : ''}` : 'until stopped'})`, () => vehicleApi.run('start', n), 6, 1)
    if (!live) simulateCarCommand(7, n)
  }

  return (
    <Panel title="Car control" icon={Power} className={cn('h-full', className)}
      action={<div className="flex flex-wrap items-center gap-1.5">
        {hw && <Badge variant="outline">{HW_LABEL[hw] ?? hw}</Badge>}
        <Badge variant={driveMode === 'dyno' ? 'warning' : driveMode === 'track' ? 'info' : 'outline'}>{driveMode ? `${driveMode.toUpperCase()} MODE` : 'mode ?'}</Badge>
        {!live && !replay && <Badge variant="outline">simulation</Badge>}
      </div>}>
      <div className="space-y-3">
        <SegmentedControl<'dyno' | 'track'>
          options={[{ label: 'Dyno — on the rollers', value: 'dyno' }, { label: 'Track — on the floor', value: 'track' }]}
          value={(driveMode ?? 'track') as 'dyno' | 'track'} onChange={setDrive} />
        <div className={cn('grid gap-2 text-xs', compact ? 'grid-cols-2' : 'grid-cols-2 sm:grid-cols-3 lg:grid-cols-6')}>
          <Stat label="VCU state" value={state} tone={estopped ? 'danger' : state === 'FAULT' ? 'warning' : state === 'DRIVE' ? 'success' : undefined} />
          <Stat label="Run" value={running ? `${runState.replace('_', ' ')}${v?.remoteRun ? ' · dashboard' : ''}` : 'none'} tone={running ? 'success' : undefined} />
          <Stat label="Motor power" value={v?.killSwitchFitted ? (v.motorPower ? 'ON (BTS7004)' : 'off') : v?.killSwitchFitted === false ? 'no kill switch' : '—'}
            tone={v?.motorPower ? 'warning' : undefined} />
          <Stat label={driveMode === 'track' ? 'Traction ctrl' : 'Ground link'}
            value={driveMode === 'track' ? (v?.tcActive ? `cutting ${v.tcCutPct ?? 0}%` : v?.tcActive === false ? 'ready' : '—')
              : v?.groundLinkOk == null ? '—' : v.groundLinkOk ? 'seen by VCU' : 'LOST'}
            tone={driveMode !== 'track' && v?.groundLinkOk === false ? 'danger' : v?.tcActive ? 'warning' : undefined} />
          <Stat label="Steering (knob)" value={reporting ? `${steerText}${v?.knob != null ? ` · ${v.knob > 0 ? '+' : ''}${v.knob} clicks` : ''}` : '—'} />
          <Stat label="Motor duty" value={reporting ? `${motorNow.toFixed(0)} %` : '—'} tone={motorNow > 0 ? 'warning' : undefined} />
        </div>

        {/* FW 2.3: motor + steering, any drive mode */}
        <div className="space-y-2 rounded-md border border-border p-2.5">
          <div className="flex flex-wrap items-center gap-2">
            <Zap className="size-3.5 text-muted-foreground" />
            <span className="text-xs font-medium">Motor</span>
            <input type="range" min={0} max={100} step={1} value={duty} aria-label="Motor duty"
              onChange={(e) => setDuty(Number(e.target.value))} onPointerUp={commitSlider} onKeyUp={commitSlider}
              className="h-2 min-w-32 flex-1 accent-primary" disabled={replay} />
            <span className="w-10 text-right text-xs tabular">{duty} %</span>
            <Button size="sm" onClick={() => sendMotor(duty)} disabled={!canMotor || replay || duty === 0}>
              <Play className="size-3.5" /> {motorRunning ? 'Set' : 'Run motor'}
            </Button>
            <Button size="sm" variant="outline" onClick={() => sendMotor(0)} disabled={replay || !live}>
              <Square className="size-3.5" /> Stop motor
            </Button>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <Crosshair className="size-3.5 text-muted-foreground" />
            <span className="text-xs">Steering: <span className="font-medium tabular">{reporting ? steerText : '—'}</span> — turn the knob on the car; the flaps follow it.</span>
            <Button size="sm" variant="outline" onClick={() => send('Centre steering', () => vehicleApi.steeringCenter(), 23, 1)} disabled={replay || !live}>
              Centre steering
            </Button>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <Wind className="size-3.5 text-muted-foreground" />
            <button type="button" onClick={pressDrs} disabled={!canDrs}
              className={cn('inline-flex h-9 min-w-24 items-center justify-center gap-1.5 rounded-lg px-4 text-sm font-bold tracking-wide shadow active:translate-y-px disabled:opacity-50',
                drsPhase === 1 ? 'bg-emerald-600 text-white hover:bg-emerald-700' : drsPhase === 2 ? 'bg-amber-500 text-black hover:bg-amber-600' : 'bg-secondary text-foreground hover:bg-secondary/80 border border-border')}>
              DRS
            </button>
            <span className="text-xs tabular">
              {drsPhase === 1 ? <>OPEN — all flaps FLAT{drsAt !== null && <> · {(drsLeft / 1000).toFixed(1)} s</>}</>
                : drsPhase === 2 ? <>closing — all flaps MAX{drsAt !== null && <> · {(drsLeft / 1000).toFixed(1)} s</>}</>
                : 'off — flaps follow the steering'}
              {drsReported !== null && live && <span className="text-muted-foreground"> (VCU: {['off', 'flat', 'max hold'][drsReported] ?? drsReported})</span>}
            </span>
            {drsPhase !== 0 && <Button size="sm" variant="outline" onClick={cancelDrs} disabled={replay}>Cancel</Button>}
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <Clapperboard className="size-3.5 text-muted-foreground" />
            {demoRunning
              ? <Button size="sm" variant="outline" onClick={() => send('Demo stop', () => vehicleApi.demo('stop'), 26, 0)} disabled={replay}><Square className="size-3.5" /> Stop demo</Button>
              : <><Button size="sm" onClick={() => send('Demo sequence', () => vehicleApi.demo('start'), 26, 1)} disabled={!canMotor || motorRunning || replay}><Play className="size-3.5" /> Play demo sequence</Button>
                  <Button size="sm" variant="outline" onClick={() => send(`Demo 2: ${demoTrackName} lap`, () => vehicleApi.demo('lap', demoTrackId), 26, 100 + demoTrackId)} disabled={!canMotor || motorRunning || replay}><Flag className="size-3.5" /> Demo 2: lap of</Button>
                  <select value={demoTrackId} onChange={(e) => setDemoTrack(Number(e.target.value))} disabled={replay}
                    className="h-8 rounded-md border border-border bg-background px-2 text-xs" aria-label="Circuit for demo 2">
                    {tracks.length === 0 && <option value={demoTrackId}>circuit {demoTrackId}</option>}
                    {tracks.map((t) => <option key={t.id} value={t.id}>{t.name}</option>)}
                  </select></>}
            <span className="text-xs text-muted-foreground">{demoLap ? `running — one lap of ${demoTrackName}: steering, speed and flaps from the circuit map; watch Track status` : demoRunning ? 'running — accelerate, brake, left, right, full speed + DRS, stop' : '21 s: accelerate → brake → left → right → full speed + DRS → stop. Wheels off the ground.'}</span>
          </div>
          {!canMotor && !motorRunning && <p className="text-[0.7rem] text-muted-foreground">Motor needs: {motorBlocked.join(' · ')}</p>}
          <p className="text-[0.7rem] text-muted-foreground">DRS: all four flaps FLAT for 5 s whatever the steering, then all MAX for 1 s, then they follow the steering again (pressing again restarts the 5 s). Wheels on the rollers or off the ground for the motor. Duty is capped by the duty cap (Vehicle Setup, key 17) and derated above the supply current limit (key 21). The ground link is the deadman: if the VCU loses it for 1 s (LoRa: 3 s) the motor stops and stays stopped.</p>
        </div>

        {driveMode === 'dyno' ? (
          <div className="space-y-2">
            <div className="flex flex-wrap items-center gap-2">
              <label className="text-xs text-muted-foreground" htmlFor="run-laps">Laps</label>
              <input id="run-laps" value={laps} onChange={(e) => setLaps(e.target.value)} inputMode="numeric"
                className="h-8 w-14 rounded-md border border-border bg-background px-2 text-sm tabular" title="0 = until stopped" />
              <Button size="sm" onClick={startRun} disabled={!canStart || replay}><Play className="size-3.5" /> Start run</Button>
              <Button size="sm" variant="outline" onClick={() => send('Stop run', () => vehicleApi.run('stop'), 6, 0)} disabled={replay}><Square className="size-3.5" /> Stop</Button>
              <Button size="sm" variant="outline" onClick={() => send('Dyno calibration', () => vehicleApi.run('dyno_cal'), 6, 2)} disabled={!canStart || replay}><Gauge className="size-3.5" /> Dyno cal</Button>
            </div>
            {!canStart && !running && <p className="text-[0.7rem] text-muted-foreground">Start needs: {blocked.join(' · ')}</p>}
            <p className="text-[0.7rem] text-muted-foreground"><Flag className="mr-1 inline size-3" />A dashboard run has no one holding a deadman, so the ground link is the deadman: the VCU stops the run if it loses the link for 1 s.</p>
          </div>
        ) : (
          <p className="text-[0.7rem] text-muted-foreground"><Wrench className="mr-1 inline size-3" />Track mode: circuit runs are refused on the floor. The motor control above still works. A driver holding ARM in <code>drive_client.py</code> steers with their wheel while armed; otherwise the knob steers. Traction control limits wheelspin while a driver drives.</p>
        )}

        <div className="flex flex-wrap items-center gap-2 border-t border-border pt-3">
          <button type="button" onClick={() => send('E-STOP', () => vehicleApi.estop('trigger'), 8, 1)} disabled={replay}
            className="inline-flex h-10 items-center gap-2 rounded-lg bg-red-600 px-4 text-sm font-bold tracking-wide text-white shadow hover:bg-red-700 active:translate-y-px disabled:opacity-50">
            <OctagonX className="size-4" /> E-STOP
          </button>
          {estopped && <Button size="sm" variant="outline" onClick={() => send('Reset', () => vehicleApi.estop('reset'), 8, 2)}><RotateCcw className="size-3.5" /> Reset (re-runs self-test)</Button>}
          <p className="min-w-0 flex-1 text-[0.7rem] text-muted-foreground">Latches on the VCU: motor off{v?.killSwitchFitted ? ', kill switch off' : ''}, flaps and steering to failsafe. The driver&apos;s E key / gamepad B does the same.</p>
        </div>

        {msg && <p className="text-xs">{msg}</p>}
        {live && (lastMode || lastRun || lastEstop || lastMotor || lastDrs) && (
          <ul className="space-y-0.5 text-[0.7rem] text-muted-foreground">
            {[['Mode', lastMode], ['Run', lastRun], ['Motor', lastMotor], ['DRS', lastDrs], ['E-stop', lastEstop]].map(([label, c]) => c && typeof c !== 'string' && (
              <li key={label as string}><span className="text-foreground">{label as string}:</span> #{c.id} = {c.displayValue}{c.unit ? ` ${c.unit}` : ''} → <span className={cn(c.status === 'applied' ? 'text-success' : c.status.startsWith('rejected') || c.status === 'expired' ? 'text-danger' : 'text-warning')}>{STATUS_TEXT[c.status] ?? c.status}</span></li>
            ))}
          </ul>
        )}
      </div>
    </Panel>
  )
}

function Stat({ label, value, tone }: { label: string; value: string; tone?: 'success' | 'warning' | 'danger' }) {
  return (
    <div className="rounded-md border border-border bg-secondary/40 px-2.5 py-1.5">
      <p className="text-[0.62rem] uppercase tracking-widest text-muted-foreground">{label}</p>
      <p className={cn('truncate font-medium', tone === 'success' && 'text-success', tone === 'warning' && 'text-warning', tone === 'danger' && 'text-danger')}>{value}</p>
    </div>
  )
}
