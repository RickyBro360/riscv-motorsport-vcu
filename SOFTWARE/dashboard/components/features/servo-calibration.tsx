'use client'

import { useCallback, useEffect, useRef, useState } from 'react'
import { Copy, RotateCcw, Save, SlidersHorizontal } from 'lucide-react'
import { Panel } from '@/components/common/panel'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { useTelemetry } from '@/contexts/telemetry-context'
import { cn } from '@/lib/utils'
import { vehicleApi, type CalibrationState, type ConfigKeyInfo, type VehicleCommand } from '@/services/api'

/**
 * FW 2.3 — servo calibration, live on the car.
 *
 * Every value here is also a #define in HARDWARE/esp32/main_updated_corrected__1_/vcu_calibration.h
 * (the defaults). A change is a CONFIG_WRITE (keys 30-45): the VCU applies it at once and moves
 * THAT servo to the new pulse for 2.5 s so the end point can be seen on the car. "Save on car"
 * keeps the values through a power cycle; "Copy vcu_calibration.h" gives the #define block to paste
 * into the file so the values become the firmware defaults. An edited file always wins over values
 * saved on the car (the firmware fingerprints the file).
 */
const FLAPS = [
  { code: 'Fl', label: 'Front-left', servo: 'SG90' },
  { code: 'Fr', label: 'Front-right', servo: 'SG90' },
  { code: 'Rl', label: 'Rear-left', servo: 'MG996R' },
  { code: 'Rr', label: 'Rear-right', servo: 'MG996R' },
] as const

const STEER = [
  { name: 'steerLeftUs', label: 'Full LEFT lock' },
  { name: 'steerCenterUs', label: 'Wheels straight' },
  { name: 'steerRightUs', label: 'Full RIGHT lock' },
] as const

const LAW = [
  { name: 'flapNeutralCdeg', step: 1 },
  { name: 'flapSwingCdeg', step: 1 },
  { name: 'steerLockCdeg', step: 1 },
  { name: 'knobCdegPerClick', step: 0.5 },
] as const

export function ServoCalibration() {
  const { mode } = useTelemetry()
  const live = mode === 'live'
  const [cal, setCal] = useState<CalibrationState | null>(null)
  const [cmds, setCmds] = useState<VehicleCommand[]>([])
  const [local, setLocal] = useState<Record<string, number>>({})   // set here, not yet reported back by the car
  const [msg, setMsg] = useState<string | null>(null)
  const [showHeader, setShowHeader] = useState(false)
  const timers = useRef<Record<string, number>>({})

  const refresh = useCallback(async () => {
    try {
      const [c, k] = await Promise.all([vehicleApi.calibration(), vehicleApi.commands()])
      setCal(c); setCmds(k)
      // drop a local value once the car reports it, or once the VCU refused it
      setLocal((cur) => {
        const next = { ...cur }
        for (const key of c.keys) {
          if (!(key.name in next)) continue
          const last = k.find((x) => x.key === key.key)
          const refused = last && (last.status.startsWith('rejected') || last.status === 'expired')
          if (Math.abs(key.value - next[key.name]) < 1e-6 || refused) delete next[key.name]
        }
        return next
      })
    } catch (e) { setMsg((e as Error).message) }
  }, [])
  useEffect(() => { refresh(); const id = window.setInterval(refresh, 1500); return () => window.clearInterval(id) }, [refresh])
  useEffect(() => () => { Object.values(timers.current).forEach((t) => window.clearTimeout(t)) }, [])

  const byName = (name: string): ConfigKeyInfo | undefined => cal?.keys.find((k) => k.name === name)
  const valueOf = (name: string) => local[name] ?? byName(name)?.value ?? 0
  const lastFor = (key?: ConfigKeyInfo) => (key ? cmds.find((c) => c.key === key.key) : undefined)

  /** set a value (display units); sent after 250 ms of no further change, so +/- clicks coalesce */
  const set = (name: string, value: number, immediate = false) => {
    const k = byName(name)
    if (!k) return
    const v = Math.max(k.min, Math.min(k.max, Math.round(value * 100) / 100))
    setLocal((cur) => ({ ...cur, [name]: v }))
    window.clearTimeout(timers.current[name])
    const go = async () => {
      try {
        const c = await vehicleApi.write(name, v)
        setMsg(`${c.label} → ${c.displayValue} ${c.unit} sent${k.unit === 'µs' ? ' — that servo moves there for 2.5 s' : ''}`)
      } catch (e) { setMsg((e as Error).message.replace(/^API POST \S+ failed: \d+ /, '')) }
    }
    if (immediate) go(); else timers.current[name] = window.setTimeout(go, 250)
  }

  const action = async (a: 'save' | 'revert') => {
    if (a === 'revert' && !window.confirm('Put every servo value back to what is in vcu_calibration.h?')) return
    try {
      const c = await vehicleApi.calibrationAction(a)
      setMsg(a === 'save' ? `Save requested (#${c.id}) — kept on the car until vcu_calibration.h is edited` : `Revert requested (#${c.id})`)
      if (a === 'revert') setLocal({})
      refresh()
    } catch (e) { setMsg((e as Error).message) }
  }

  const copyHeader = async () => {
    if (!cal) return
    // values the operator set but the car has not reported yet are included too
    let text = cal.header
    for (const [name, v] of Object.entries(local)) text = applyLocal(text, name, v)
    try { await navigator.clipboard.writeText(text); setMsg('Copied — paste over the matching #define lines in vcu_calibration.h') }
    catch { setShowHeader(true) }
  }

  const lastCal = cal?.lastCommand
  return (
    <Panel title="Servo calibration (live on the car)" icon={SlidersHorizontal}
      action={<div className="flex flex-wrap items-center gap-1.5">
        <Badge variant={cal?.reported ? 'success' : 'outline'}>{cal?.reported ? 'reported by the VCU' : 'file defaults (car not reporting)'}</Badge>
        {Object.keys(local).length > 0 && <Badge variant="warning">{Object.keys(local).length} waiting for the car</Badge>}
      </div>}>
      <div className="space-y-4">
        <p className="text-[0.7rem] text-muted-foreground">
          Each pulse you change moves <b>that</b> servo to the new value for 2.5 s. Step it until the horn just reaches the end point without the servo buzzing against a stop.
          Defaults live in <code>vcu_calibration.h</code>. On the console: <code>pulse &lt;id&gt; &lt;us&gt;</code> jogs a servo, <code>cal</code> prints everything.
          {!live && ' Switch to Live mode with the car connected to send changes.'}
        </p>

        <section>
          <h4 className="mb-1.5 text-xs font-medium">Steering servo (MG995)</h4>
          <div className="grid gap-2 sm:grid-cols-3">
            {STEER.map((s) => (
              <PulseCell key={s.name} label={s.label} k={byName(s.name)} value={valueOf(s.name)} pending={s.name in local}
                last={lastFor(byName(s.name))} disabled={!live} onSet={(v, now) => set(s.name, v, now)} />
            ))}
          </div>
        </section>

        <section>
          <h4 className="mb-1.5 text-xs font-medium">Flap servos — FLAT = 0° (least downforce), MAX = 45° (most downforce)</h4>
          <div className="grid gap-2 md:grid-cols-2">
            {FLAPS.map((f) => (
              <div key={f.code} className="rounded-md border border-border p-2">
                <p className="mb-1 text-[0.7rem] text-muted-foreground">{f.label} <span className="opacity-70">({f.servo})</span></p>
                <div className="grid grid-cols-2 gap-2">
                  {(['Flat', 'Max'] as const).map((end) => {
                    const name = `flap${f.code}${end}Us`
                    return <PulseCell key={name} label={end === 'Flat' ? 'Flat (0°)' : 'Max (45°)'} k={byName(name)} value={valueOf(name)}
                      pending={name in local} last={lastFor(byName(name))} disabled={!live} onSet={(v, now) => set(name, v, now)} />
                  })}
                </div>
              </div>
            ))}
          </div>
          <p className="mt-1 text-[0.65rem] text-muted-foreground">A servo mounted mirror-image simply has FLAT &gt; MAX. On the rear MG996R the pot reading at FLAT / MAX is captured automatically, so the measured flap angles stay right.</p>
        </section>

        <section>
          <h4 className="mb-1.5 text-xs font-medium">Steering → flaps, and the knob</h4>
          <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-5">
            {LAW.map((l) => {
              const k = byName(l.name)
              return <NumberCell key={l.name} k={k} value={valueOf(l.name)} step={l.step} pending={l.name in local}
                last={lastFor(k)} disabled={!live} onSet={(v) => set(l.name, v)} />
            })}
            <div className="rounded-md border border-border p-2">
              <p className="text-[0.7rem] text-muted-foreground">Knob direction</p>
              <Button size="sm" variant="outline" className="mt-1 w-full" disabled={!live}
                onClick={() => set('knobReverse', valueOf('knobReverse') ? 0 : 1, true)}>
                {valueOf('knobReverse') ? 'Clockwise = LEFT' : 'Clockwise = RIGHT'}
              </Button>
            </div>
          </div>
          <p className="mt-1 text-[0.65rem] text-muted-foreground">
            Wheels straight: all flaps at the neutral angle. Turning right, the left (outside) flaps rise toward neutral + swing and the right (inside) flaps drop toward neutral − swing, in proportion to the steering angle (full effect at full lock).
          </p>
        </section>

        <div className="flex flex-wrap items-center gap-2 border-t border-border pt-3">
          <Button size="sm" onClick={() => action('save')} disabled={!live}><Save className="size-3.5" /> Save on car</Button>
          <Button size="sm" variant="outline" onClick={() => action('revert')} disabled={!live}><RotateCcw className="size-3.5" /> Revert to file</Button>
          <Button size="sm" variant="outline" onClick={copyHeader} disabled={!cal}><Copy className="size-3.5" /> Copy vcu_calibration.h</Button>
          <Button size="sm" variant="ghost" onClick={() => setShowHeader((x) => !x)} disabled={!cal}>{showHeader ? 'Hide' : 'Show'} #defines</Button>
          {lastCal && <span className={cn('text-[0.7rem]', statusTone(lastCal.status))}>last {lastCal.value === 1 ? 'save' : 'revert'}: {lastCal.status.replace(/_/g, ' ')}</span>}
        </div>
        {showHeader && cal && <pre className="max-h-64 overflow-auto rounded-md bg-secondary/50 p-2 text-[0.7rem] leading-relaxed">{
          Object.entries(local).reduce((t, [n, v]) => applyLocal(t, n, v), cal.header)}</pre>}
        {msg && <p className="text-xs">{msg}</p>}
      </div>
    </Panel>
  )
}

function statusTone(status: string) {
  return status === 'applied' ? 'text-success' : status.startsWith('rejected') || status === 'expired' ? 'text-danger' : 'text-warning'
}

function PulseCell({ label, k, value, pending, last, disabled, onSet }: {
  label: string; k?: ConfigKeyInfo; value: number; pending: boolean; last?: VehicleCommand; disabled: boolean
  onSet: (v: number, immediate: boolean) => void
}) {
  const [draft, setDraft] = useState('')
  if (!k) return <div className="rounded-md border border-border p-2 text-xs text-muted-foreground">{label}: —</div>
  const nudge = (d: number) => onSet(value + d, false)
  return (
    <div className="rounded-md border border-border p-2">
      <div className="flex items-baseline justify-between gap-2">
        <span className="text-[0.7rem] text-muted-foreground">{label}</span>
        <span className={cn('text-sm font-medium tabular', pending && 'text-warning')}>{Math.round(value)} µs</span>
      </div>
      <div className="mt-1 flex items-center gap-1">
        {[-10, -1].map((d) => <Nudge key={d} d={d} disabled={disabled} onClick={() => nudge(d)} />)}
        <input value={draft} onChange={(e) => setDraft(e.target.value)} inputMode="numeric" placeholder={String(Math.round(value))}
          onKeyDown={(e) => { if (e.key === 'Enter' && Number.isFinite(Number(draft)) && draft) { onSet(Number(draft), true); setDraft('') } }}
          className="h-7 w-full min-w-0 rounded border border-border bg-background px-1.5 text-center text-xs tabular" disabled={disabled} />
        {[1, 10].map((d) => <Nudge key={d} d={d} disabled={disabled} onClick={() => nudge(d)} />)}
        <Button size="sm" variant="outline" className="h-7 px-2 text-[0.7rem]" disabled={disabled} title="Move the servo to this pulse again"
          onClick={() => onSet(value, true)}>Go</Button>
      </div>
      {last && <p className={cn('mt-0.5 text-[0.62rem]', statusTone(last.status))}>{last.displayValue} µs: {last.status.replace(/_/g, ' ')}</p>}
    </div>
  )
}

function NumberCell({ k, value, step, pending, last, disabled, onSet }: {
  k?: ConfigKeyInfo; value: number; step: number; pending: boolean; last?: VehicleCommand; disabled: boolean
  onSet: (v: number) => void
}) {
  if (!k) return null
  return (
    <div className="rounded-md border border-border p-2" title={k.description}>
      <p className="text-[0.7rem] text-muted-foreground">{k.label}</p>
      <div className="mt-1 flex items-center gap-1">
        <Nudge d={-step} disabled={disabled} onClick={() => onSet(value - step)} />
        <span className={cn('flex-1 text-center text-sm font-medium tabular', pending && 'text-warning')}>{value.toFixed(step < 1 ? 1 : 0)}{k.unit}</span>
        <Nudge d={step} disabled={disabled} onClick={() => onSet(value + step)} />
      </div>
      {last && <p className={cn('mt-0.5 text-[0.62rem]', statusTone(last.status))}>{last.status.replace(/_/g, ' ')}</p>}
    </div>
  )
}

function Nudge({ d, disabled, onClick }: { d: number; disabled: boolean; onClick: () => void }) {
  return (
    <button type="button" onClick={onClick} disabled={disabled}
      className="h-7 shrink-0 rounded border border-border px-1.5 text-[0.7rem] tabular hover:bg-secondary disabled:opacity-40">
      {d > 0 ? `+${d}` : d}
    </button>
  )
}

/** Replace one #define in the header text with a value set here but not yet reported by the car. */
const HEADER_NAME: Record<string, [string, number]> = {
  steerLeftUs: ['VCU_CAL_STEER_LEFT_US', 1], steerCenterUs: ['VCU_CAL_STEER_CENTER_US', 1], steerRightUs: ['VCU_CAL_STEER_RIGHT_US', 1],
  flapFlFlatUs: ['VCU_CAL_FLAP_FL_FLAT_US', 1], flapFlMaxUs: ['VCU_CAL_FLAP_FL_MAX_US', 1],
  flapFrFlatUs: ['VCU_CAL_FLAP_FR_FLAT_US', 1], flapFrMaxUs: ['VCU_CAL_FLAP_FR_MAX_US', 1],
  flapRlFlatUs: ['VCU_CAL_FLAP_RL_FLAT_US', 1], flapRlMaxUs: ['VCU_CAL_FLAP_RL_MAX_US', 1],
  flapRrFlatUs: ['VCU_CAL_FLAP_RR_FLAT_US', 1], flapRrMaxUs: ['VCU_CAL_FLAP_RR_MAX_US', 1],
  steerLockCdeg: ['VCU_CAL_STEER_LOCK_DEG', 1], flapNeutralCdeg: ['VCU_CAL_FLAP_NEUTRAL_DEG', 1],
  flapSwingCdeg: ['VCU_CAL_FLAP_SWING_DEG', 1], knobCdegPerClick: ['VCU_CAL_KNOB_DEG_PER_CLICK', 1], knobReverse: ['VCU_CAL_KNOB_REVERSE', 1],
}
function applyLocal(text: string, name: string, displayValue: number): string {
  const h = HEADER_NAME[name]
  if (!h) return text
  const v = Number.isInteger(displayValue) ? String(displayValue) : String(Math.round(displayValue * 100) / 100)
  return text.replace(new RegExp(`(#define ${h[0]}\\s+)\\S+`), `$1${v}`)
}
