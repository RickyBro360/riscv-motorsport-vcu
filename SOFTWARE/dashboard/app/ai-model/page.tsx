'use client'

import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { CartesianGrid, Line, LineChart, ReferenceArea, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import { Bot, Gauge, Info, Map as MapIcon, OctagonX, Radio, RotateCcw, TrendingUp } from 'lucide-react'
import { PageHeader } from '@/components/common/page-header'
import { Panel } from '@/components/common/panel'
import { SegmentedControl } from '@/components/common/segmented-control'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { CircuitMap } from '@/components/features/circuit-map'
import { useTelemetry } from '@/contexts/telemetry-context'
import { useTracks } from '@/hooks/use-tracks'
import { backendWsBase } from '@/lib/backend-url'
import { effectiveConfig } from '@/lib/tracks'
import { LapPosition, MONZA_MODEL, lapProfile, predict, type ModelOutput } from '@/lib/track-model'
import { cn } from '@/lib/utils'
import { vehicleApi } from '@/services/api'

/**
 * AI Model — one trained model for Monza, two ways to use it.
 *
 *   AI recommends   the model reads the car's telemetry and shows what it would do. Nothing is sent.
 *   AI drives       ARM opens the backend's /ws/ai-command link (backend started with VCU_AI_COMMAND_LINK=1)
 *                   and the model's motor duty (key 9), straight mode (key 25, the car's DRS) and steering
 *                   (key 27, FW 2.3.12) are sent 5 times a second. The VCU still decides. Disarming, leaving
 *                   the page or losing the link stops the motor and centres the steering.
 *
 * Lap position: while the car runs its OWN Monza circuit run the VCU's position is used; otherwise the page
 * integrates the rear wheel speed over the model's lap length (there is no GPS).
 */
const MODEL = MONZA_MODEL
const SEND_MS = 200
// Commands take about half a second to reach the servos (backend queue -> Pi -> CAN -> servo travel), so what is
// SENT is the model's output for where the car will be by then. The values shown are for where it is now.
const COMMAND_LEAD_S = 0.5
type Mode = 'recommend' | 'drive'

function Compare({ label, ai, car, unit, digits = 0, note }: { label: string; ai: string | number | null; car: string | number | null; unit?: string; digits?: number; note?: string }) {
  const fmt = (v: string | number | null) => v === null ? '—' : typeof v === 'number' ? v.toFixed(digits) : v
  return (
    <div className="rounded-lg border border-border bg-secondary/40 p-3">
      <p className="text-[0.65rem] font-medium uppercase tracking-widest text-muted-foreground">{label}</p>
      <div className="mt-2 grid grid-cols-2 gap-2">
        <div><p className="text-[0.65rem] text-muted-foreground">AI model</p><p className="tabular text-xl font-semibold text-primary">{fmt(ai)}<span className="ml-1 text-xs font-normal text-muted-foreground">{unit}</span></p></div>
        <div><p className="text-[0.65rem] text-muted-foreground">Car now</p><p className="tabular text-xl font-semibold">{fmt(car)}<span className="ml-1 text-xs font-normal text-muted-foreground">{unit}</span></p></div>
      </div>
      {note && <p className="mt-1.5 text-[0.65rem] text-muted-foreground">{note}</p>}
    </div>
  )
}

export default function AiModelPage() {
  const { frame, mode: feed, link } = useTelemetry()
  const doc = useTracks()
  const track = useMemo(() => doc ? doc.tracks.find((t) => t.id === MODEL.track_id) ?? doc.tracks.find((t) => t.name === MODEL.track_name) ?? null : null, [doc])
  const cfg = effectiveConfig(frame?.vcuConfig)
  const [mode, setMode] = useState<Mode>('recommend')
  const [armed, setArmed] = useState(false)
  const [linkMsg, setLinkMsg] = useState('not armed')
  const [view, setView] = useState<{ pos: number; laps: number; speed: number; out: ModelOutput; ownPos: boolean } | null>(null)
  const [err, setErr] = useState<{ duty: number; steer: number; n: number } | null>(null)
  const tracker = useRef(new LapPosition(MODEL.lap_length_m))
  const outRef = useRef<ModelOutput | null>(null)
  const wsRef = useRef<WebSocket | null>(null)
  const errBuf = useRef<{ d: number; s: number }[]>([])
  const profile = useMemo(() => lapProfile(MODEL), [])
  const zones = useMemo(() => {
    const z: { a: number; b: number }[] = []
    let a: number | null = null
    profile.forEach((r, i) => {
      if (r.drs && a === null) a = r.x
      if ((!r.drs || i === profile.length - 1) && a !== null) { z.push({ a, b: r.drs ? 100 : r.x }); a = null }
    })
    return z
  }, [profile])

  const live = feed === 'live' && link === 'live_hardware'
  const tr = frame?.track
  const carOnMonza = (tr?.id ?? cfg.trackId) === MODEL.track_id
  const vcuRun = !!tr?.runActive

  // every telemetry frame: lap position -> model output
  useEffect(() => {
    if (!frame) return
    const now = performance.now()
    const rear = frame.wheels ? (frame.wheels.rl + frame.wheels.rr) / 2 : frame.speed ?? 0
    const t = tracker.current
    const followVcu = !!frame.track?.runActive && frame.track.id === MODEL.track_id && typeof frame.track.pos === 'number'
    let speed = rear
    if (followVcu) t.sync(frame.track!.pos, now); else speed = t.update(now, rear)
    const out = predict(MODEL, t.frac, speed)
    outRef.current = predict(MODEL, t.frac + (speed / 3.6) * COMMAND_LEAD_S / MODEL.lap_length_m, speed)
    if (frame.throttle > 1) {
      errBuf.current.push({ d: Math.abs(out.duty - frame.throttle), s: Math.abs(out.steer - frame.steering) })
      if (errBuf.current.length > 300) errBuf.current.shift()
      const b = errBuf.current
      setErr({ duty: b.reduce((a, r) => a + r.d, 0) / b.length, steer: b.reduce((a, r) => a + r.s, 0) / b.length, n: b.length })
    }
    setView({ pos: t.frac, laps: followVcu ? frame.track!.lap : t.laps, speed, out, ownPos: !followVcu })
  }, [frame])

  const disarm = useCallback((why?: string) => {
    const ws = wsRef.current
    wsRef.current = null
    if (ws) { try { ws.close() } catch { /* already closed */ } }   // closing the link makes the backend stop the motor + centre the steering
    setArmed(false)
    setLinkMsg(why ?? 'not armed')
  }, [])

  const arm = useCallback(() => {
    if (wsRef.current) return
    setLinkMsg('connecting…')
    const ws = new WebSocket(`${backendWsBase()}/ws/ai-command`)
    wsRef.current = ws
    ws.onmessage = (e) => {
      try {
        const m = JSON.parse(e.data)
        if (m.type === 'connected') { setArmed(true); setLinkMsg('ARMED — commands are going to the car') }
        if (m.type === 'error') { wsRef.current = null; setArmed(false); setLinkMsg(m.detail ?? 'backend refused the link') }
      } catch { /* not JSON */ }
    }
    ws.onerror = () => { if (wsRef.current === ws) { wsRef.current = null; setArmed(false); setLinkMsg('cannot reach the backend command link') } }
    ws.onclose = () => { if (wsRef.current === ws) { wsRef.current = null; setArmed(false); setLinkMsg('command link closed — motor stopped') } }
  }, [])

  // while armed: send the model's current output 5 times a second
  useEffect(() => {
    if (!armed) return
    const id = setInterval(() => {
      const ws = wsRef.current, o = outRef.current
      if (!ws || ws.readyState !== WebSocket.OPEN || !o) return
      ws.send(JSON.stringify({ type: 'ai_command', duty_pct: Math.round(o.duty * 10) / 10, steer_deg: Math.round(o.steer * 10) / 10, straight_mode: o.drs }))
    }, SEND_MS)
    return () => clearInterval(id)
  }, [armed])

  useEffect(() => { if (mode !== 'drive' || !live) disarm() }, [mode, live, disarm])
  useEffect(() => () => { const ws = wsRef.current; wsRef.current = null; if (ws) ws.close() }, [])

  const estop = async () => {
    disarm('E-STOP sent')
    try { await vehicleApi.estop('trigger') } catch (e) { setLinkMsg(`E-STOP request failed: ${(e as Error).message}`) }
  }
  const resetLap = () => { tracker.current.reset(); errBuf.current = []; setErr(null) }

  const out = view?.out ?? null
  const carStraight = frame ? (!!tr?.straightMode || Number(frame.vcuConfig?.drsCmd ?? 0) === 1) : null
  const tn = MODEL.training as Record<string, number | string>
  const num = (k: string) => Number(tn[k])

  return (
    <div>
      <PageHeader
        title="AI Model — Monza"
        description="One trained model for the Monza circuit. It reads lap position and wheel speed and outputs motor duty, steering and straight mode. Use it to watch what it recommends, or let it drive the car on the stand."
        actions={<div className="flex flex-wrap items-center gap-2">
          <SegmentedControl<Mode> options={[{ label: 'AI recommends', value: 'recommend' }, { label: 'AI drives', value: 'drive' }]} value={mode} onChange={setMode} />
          <Badge variant={live ? 'success' : 'outline'}><Radio className="size-3" /> {live ? 'car connected' : feed === 'live' ? 'waiting for the car' : `${feed} data`}</Badge>
        </div>}
      />

      {mode === 'drive' && (
        <div className={cn('mb-4 rounded-lg border p-4', armed ? 'border-danger/40 bg-danger/5' : 'border-warning/40 bg-warning/5')}>
          <div className="flex flex-wrap items-center gap-3">
            {!armed
              ? <Button size="sm" onClick={arm} disabled={!live || vcuRun}>ARM — send to the car</Button>
              : <Button size="sm" variant="outline" onClick={() => disarm('disarmed — motor stopped')}>Disarm</Button>}
            <Button size="sm" variant="destructive" onClick={estop} disabled={!live}><OctagonX className="size-3.5" /> E-STOP</Button>
            <Badge variant={armed ? 'danger' : 'outline'}>{linkMsg}</Badge>
          </div>
          <p className="mt-2 text-xs text-muted-foreground">
            The rear wheels will spin and the front wheels will steer: put the car on its stand first. Sent 5 times a second: motor duty, straight mode (the car&apos;s DRS) and steering.
            The VCU accepts them only when it is IDLE with no other run active; turning the knob on the car takes the steering back.
            {!live && ' Connect the car in Live mode to arm.'}{live && vcuRun && ' Stop the run that is active on the car before arming.'}
          </p>
        </div>
      )}

      <div className="grid gap-4 xl:grid-cols-[1fr_380px]">
        <div className="space-y-4">
          <Panel title={track ? `${track.fullName} — lap ${view ? view.laps + 1 : '—'}, ${view ? (view.pos * 100).toFixed(0) : '—'} %` : 'Monza'} icon={MapIcon} bodyClassName="p-0"
            action={<Button size="sm" variant="outline" onClick={resetLap} disabled={!view?.ownPos}><RotateCcw className="size-3.5" /> Lap position to start</Button>}>
            <div className="relative aspect-[1.9] min-h-[320px] bg-secondary/20 p-4">
              {track ? <CircuitMap track={track} pos={view ? view.pos : null} cfg={cfg} straightMode={out?.drs} showSectors={false} />
                : <p className="p-6 text-sm text-muted-foreground">Circuit outline loads from the backend.</p>}
              <p className="absolute bottom-3 left-4 text-[0.7rem] text-muted-foreground">
                Position: {view?.ownPos === false ? "the VCU's own lap position (its Monza run is active)" : `rear wheel speed integrated over a ${MODEL.lap_length_m.toFixed(0)} m lap`}
              </p>
            </div>
          </Panel>

          <Panel title="The model's lap of Monza" icon={TrendingUp}>
            <div className="h-56">
              <ResponsiveContainer width="100%" height="100%">
                <LineChart data={profile} margin={{ top: 6, right: 8, left: -18, bottom: 0 }}>
                  <CartesianGrid stroke="var(--color-border)" strokeDasharray="3 3" />
                  <XAxis dataKey="x" type="number" domain={[0, 100]} tickFormatter={(v) => `${v}%`} tick={{ fontSize: 10 }} />
                  <YAxis yAxisId="d" domain={[0, 100]} tick={{ fontSize: 10 }} />
                  <YAxis yAxisId="s" orientation="right" domain={[-30, 30]} tick={{ fontSize: 10 }} />
                  {zones.map((z, i) => <ReferenceArea key={i} yAxisId="d" x1={z.a} x2={z.b} fill="var(--color-success)" fillOpacity={0.1} />)}
                  <Tooltip formatter={(v, n) => [n === 'duty' ? `${Number(v).toFixed(0)} %` : `${Number(v).toFixed(1)}°`, n === 'duty' ? 'Motor duty' : 'Steering (+ = left)']} labelFormatter={(v) => `${Number(v).toFixed(0)} % of the lap`} />
                  <Line yAxisId="d" dataKey="duty" stroke="var(--color-chart-1)" dot={false} strokeWidth={2} isAnimationActive={false} />
                  <Line yAxisId="s" dataKey="steer" stroke="var(--color-chart-3)" dot={false} strokeWidth={1.5} isAnimationActive={false} />
                  {view && <ReferenceLine yAxisId="d" x={view.pos * 100} stroke="var(--color-foreground)" strokeWidth={1.5} />}
                </LineChart>
              </ResponsiveContainer>
            </div>
            <p className="mt-2 text-[0.7rem] text-muted-foreground">Left axis: motor duty % (thick line). Right axis: steering in degrees, + = left (thin line). Green bands: straight mode. Vertical line: where the car is.</p>
          </Panel>
        </div>

        <div className="space-y-4">
          <Panel title={mode === 'drive' && armed ? 'AI is driving' : 'AI recommends'} icon={Bot}>
            <div className="space-y-3">
              <Compare label="Motor duty" ai={out ? out.duty : null} car={frame ? frame.throttle : null} unit="%" />
              <Compare label="Steering (+ = left)" ai={out ? out.steer : null} car={frame ? frame.steering : null} unit="°" digits={1} />
              <Compare label="Straight mode (flaps flat)" ai={out ? (out.drs ? 'ON' : 'off') : null} car={carStraight === null ? null : carStraight ? 'ON' : 'off'} />
              <Compare label="Wheel speed" ai={out ? out.speedKmh : null} car={view ? view.speed : null} unit="km/h" digits={1} note="AI column: the speed the model expects at this point of the lap." />
            </div>
          </Panel>

          <Panel title="Agreement with the car" icon={Gauge}>
            {err && err.n >= 20 ? (
              <div className="space-y-1.5 text-sm">
                <div className="flex justify-between"><span className="text-muted-foreground">Motor duty, average gap</span><span className="tabular">{err.duty.toFixed(1)} %</span></div>
                <div className="flex justify-between"><span className="text-muted-foreground">Steering, average gap</span><span className="tabular">{err.steer.toFixed(1)}°</span></div>
                <p className="pt-1 text-[0.7rem] text-muted-foreground">Over the last {err.n} frames with the motor running.
                  {mode === 'drive' && armed ? ' While the AI drives this shows how closely the car follows its commands.'
                    : carOnMonza && vcuRun ? " The car is running its own Monza map: this is the model's error against it."
                    : ' To test the model, start a circuit run of Monza from Car control and compare.'}</p>
              </div>
            ) : <p className="text-xs text-muted-foreground">Appears once the motor has been running for a few seconds.</p>}
            {!carOnMonza && frame && <p className="mt-2 text-[0.7rem] text-warning">The circuit selected on the car is not Monza, so its own runs are not comparable with this model.</p>}
          </Panel>

          <Panel title="About this model" icon={Info}>
            <div className="space-y-1.5 text-sm">
              <div className="flex justify-between"><span className="text-muted-foreground">Network</span><span className="tabular">{MODEL.hidden.join(' × ')} · {MODEL.n_params} weights</span></div>
              <div className="flex justify-between"><span className="text-muted-foreground">Training laps</span><span className="tabular">{num('rows_train')} samples</span></div>
              <div className="flex justify-between"><span className="text-muted-foreground">Duty error, unseen laps</span><span className="tabular">{num('val_duty_mae_pct').toFixed(1)} % (constant guess {num('val_duty_mae_if_constant_pct').toFixed(1)} %)</span></div>
              <div className="flex justify-between"><span className="text-muted-foreground">Steering error, unseen laps</span><span className="tabular">{num('val_steer_mae_deg').toFixed(2)}° (constant guess {num('val_steer_mae_if_constant_deg').toFixed(2)}°)</span></div>
              <div className="flex justify-between"><span className="text-muted-foreground">Straight mode correct</span><span className="tabular">{(num('val_straight_mode_accuracy') * 100).toFixed(0)} %</span></div>
            </div>
            <p className="mt-2 text-[0.7rem] text-muted-foreground">Trained on laps of Monza driven by the VCU&apos;s own Demo 2 lap (89 m, about 34 s), so it imitates that lap&apos;s speed and steering. It has not learned from a human driver or from real wheel sensors.</p>
          </Panel>
        </div>
      </div>
    </div>
  )
}
