'use client'

import { useEffect, useState } from 'react'
import { SlidersHorizontal, Wind, Gauge, Zap, RotateCcw, Save, Sparkles, Download, Upload, WifiOff } from 'lucide-react'
import { downloadFile } from '@/lib/export'
import { avgWing } from '@/lib/format'
import { useTelemetry } from '@/contexts/telemetry-context'
import { PageHeader } from '@/components/common/page-header'
import { Panel } from '@/components/common/panel'
import { Badge } from '@/components/ui/badge'
import { cn } from '@/lib/utils'
import { DEFAULT_SETUP } from '@/constants'
import type { VehicleSetup } from '@/types/telemetry'
import { configurationApi } from '@/services/api'
import { useBackendStatus } from '@/hooks/use-backend-status'
import { VcuTunables } from '@/components/features/vcu-tunables'
import { CarControl } from '@/components/features/car-control'
import { ServoCalibration } from '@/components/features/servo-calibration'

// The full VehicleSetup shape (PID gains, servo calibration, CAN/LoRa comms,
// battery pack config — see types/telemetry.ts and DEFAULT_SETUP) is what
// the backend persists and what Import/Export JSON already round-trips.
// This page's sliders only expose the subset a race engineer tunes between
// runs; the rest passes through untouched via `setup` rather than being
// silently dropped, which is what the previous narrower local state did.

export default function VehicleSetupPage() {
  const { ai } = useTelemetry()
  const [setup, setSetup] = useState<VehicleSetup>(DEFAULT_SETUP)
  const [saved, setSaved] = useState(false)
  const [savingToBackend, setSavingToBackend] = useState(false)
  const backendStatus = useBackendStatus()

  useEffect(() => {
    if (backendStatus !== 'online') return
    let cancelled = false
    configurationApi
      .load()
      .then((res) => { if (!cancelled) setSetup(res.setup) })
      .catch(() => { /* nothing saved yet, or backend just came online with no config row — keep defaults */ })
    return () => { cancelled = true }
  }, [backendStatus])

  const recWing = ai ? avgWing(ai.recommendedWing) : 22
  const wingDelta = setup.wingAngle - recWing

  function update<K extends keyof VehicleSetup>(key: K, value: VehicleSetup[K]) {
    setSetup((s) => ({ ...s, [key]: value }))
    setSaved(false)
  }

  async function flashToVcu() {
    setSavingToBackend(true)
    try {
      await configurationApi.save(setup)
    } catch {
      // Backend unreachable — this still represents "flashed to VCU" in the
      // simulation sense (no real ESP32-C6 exists to flash yet either way),
      // but the config won't be there next time /api/config is polled.
    } finally {
      setSavingToBackend(false)
      setSaved(true)
    }
  }

  return (
    <div>
      <PageHeader
        title="Vehicle Setup"
        description="Top: the VCU's real tunables — every change is a CONFIG_WRITE the VCU validates and acknowledges. Below: the legacy engineering setup sheet, stored in the backend only (it is not sent to the car)."
        actions={
          <div className="flex items-center gap-2">
            {backendStatus === 'offline' && (
              <Badge variant="warning" className="gap-1.5 px-2.5 py-1">
                <WifiOff className="size-3.5" /> Backend offline
              </Badge>
            )}
            <button onClick={() => downloadFile('vehicle-setup.json', JSON.stringify(setup, null, 2), 'application/json')} className="inline-flex items-center gap-1.5 rounded-md border border-border px-3 py-1.5 text-xs text-muted-foreground transition-colors hover:text-foreground"><Download className="size-3.5" /> Export</button>
            <label className="inline-flex cursor-pointer items-center gap-1.5 rounded-md border border-border px-3 py-1.5 text-xs text-muted-foreground transition-colors hover:text-foreground"><Upload className="size-3.5" /> Import<input type="file" accept="application/json" className="sr-only" onChange={(event) => { const file = event.target.files?.[0]; if (!file) return; const reader = new FileReader(); reader.onload = () => { try { setSetup({ ...DEFAULT_SETUP, ...JSON.parse(String(reader.result)) }); setSaved(false) } catch { /* invalid config stays unchanged */ } }; reader.readAsText(file) }} /></label>
            <button
              onClick={() => {
                setSetup(DEFAULT_SETUP)
                setSaved(false)
              }}
              className="inline-flex items-center gap-1.5 rounded-md border border-border px-3 py-1.5 text-xs text-muted-foreground transition-colors hover:text-foreground"
            >
              <RotateCcw className="size-3.5" /> Reset
            </button>
            <button
              onClick={flashToVcu}
              disabled={savingToBackend}
              className="inline-flex items-center gap-1.5 rounded-md bg-primary px-3.5 py-1.5 text-xs font-semibold text-primary-foreground transition-opacity hover:opacity-90 disabled:opacity-60"
            >
              <Save className="size-3.5" /> {savingToBackend ? 'Saving…' : saved ? 'Saved' : 'Save setup sheet'}
            </button>
          </div>
        }
      />
      <div className="mb-6"><CarControl /></div>
      <div className="mb-6"><ServoCalibration /></div>
      <div className="mb-6"><VcuTunables /></div>

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-3">
        <div className="space-y-4 lg:col-span-2">
          <Panel title="Aerodynamics" icon={Wind}>
            <SliderRow
              label="Rear Wing Angle"
              value={setup.wingAngle}
              min={0}
              max={45}
              unit="°"
              onChange={(v) => update('wingAngle', v)}
              hint={
                <span className={cn(Math.abs(wingDelta) > 4 ? 'text-warning' : 'text-success')}>
                  AI recommends {recWing.toFixed(0)}° ({wingDelta >= 0 ? '+' : ''}
                  {wingDelta.toFixed(0)}°)
                </span>
              }
            />
            <ToggleRow
              label="Adaptive Aero"
              description="Let the VCU auto-trim wing angle per corner"
              checked={setup.adaptiveAero}
              onChange={(v) => update('adaptiveAero', v)}
            />
          </Panel>

          <Panel title="Chassis & Brakes" icon={Gauge}>
            <SliderRow
              label="Brake Bias (front)"
              value={setup.brakeBias}
              min={40}
              max={70}
              unit="%"
              onChange={(v) => update('brakeBias', v)}
            />
            <SliderRow
              label="Steering Rate"
              value={setup.steeringRate}
              min={20}
              max={100}
              unit="%"
              onChange={(v) => update('steeringRate', v)}
            />
            <ToggleRow
              label="Traction Control"
              description="Limit wheelspin on corner exit"
              checked={setup.tractionControl}
              onChange={(v) => update('tractionControl', v)}
            />
          </Panel>

          <Panel title="Energy Management" icon={Zap}>
            <SliderRow
              label="ERS Deployment Map"
              value={setup.ersMap}
              min={1}
              max={6}
              unit=""
              onChange={(v) => update('ersMap', v)}
              hint={<span className="text-muted-foreground">1 = conservation · 6 = qualifying</span>}
            />
            <SliderRow
              label="Regen Strength"
              value={setup.regen}
              min={0}
              max={100}
              unit="%"
              onChange={(v) => update('regen', v)}
            />
          </Panel>
        </div>

        <div className="space-y-4">
          <Panel title="AI Verdict" icon={Sparkles} className="h-fit">
            <div className="mb-4 flex items-center justify-between rounded-lg border border-border bg-secondary/40 px-4 py-3">
              <span className="text-sm text-muted-foreground">Setup Efficiency</span>
              <span className="tabular text-2xl font-bold text-primary">
                {ai ? ai.efficiency.toFixed(0) : '—'}%
              </span>
            </div>
            <p className="text-sm leading-relaxed text-muted-foreground">
              {ai?.note ?? 'Awaiting telemetry…'} Current corner phase:{' '}
              <span className="font-medium text-foreground">{ai?.corner ?? '—'}</span>. Estimated grip{' '}
              <span className="tabular font-medium text-foreground">
                {ai ? `${(ai.grip * 100).toFixed(0)}%` : '—'}
              </span>
              .
            </p>
            <div className="mt-4 space-y-2">
              {[
                ['Confidence', ai ? `${(ai.confidence * 100).toFixed(0)}%` : '—'],
                ['Racing line', ai?.racingLine ?? '—'],
              ].map(([k, v]) => (
                <div key={k} className="flex items-start justify-between gap-3 text-sm">
                  <span className="text-muted-foreground">{k}</span>
                  <span className="text-right font-medium">{v}</span>
                </div>
              ))}
            </div>
          </Panel>

          <Panel title="Config Summary" icon={SlidersHorizontal}>
            <ul className="space-y-2 text-sm">
              {[
                ['Wing', `${setup.wingAngle}°`],
                ['Brake Bias', `${setup.brakeBias}%`],
                ['Steering Rate', `${setup.steeringRate}%`],
                ['ERS Map', `${setup.ersMap}`],
                ['Regen', `${setup.regen}%`],
                ['TC', setup.tractionControl ? 'On' : 'Off'],
                ['Adaptive Aero', setup.adaptiveAero ? 'On' : 'Off'],
              ].map(([k, v]) => (
                <li key={k} className="flex items-center justify-between border-b border-border/50 pb-2 last:border-0 last:pb-0">
                  <span className="text-muted-foreground">{k}</span>
                  <span className="tabular font-semibold">{v}</span>
                </li>
              ))}
            </ul>
            {saved && (
              <Badge variant={backendStatus === 'online' ? 'success' : 'warning'} className="mt-4 w-full justify-center py-1.5">
                {backendStatus === 'online' ? 'Configuration saved to backend' : 'Applied locally — backend offline, not persisted'}
              </Badge>
            )}
          </Panel>
        </div>
      </div>
    </div>
  )
}

function SliderRow({
  label,
  value,
  min,
  max,
  unit,
  onChange,
  hint,
}: {
  label: string
  value: number
  min: number
  max: number
  unit: string
  onChange: (v: number) => void
  hint?: React.ReactNode
}) {
  return (
    <div className="border-b border-border/50 py-3 first:pt-0 last:border-0 last:pb-0">
      <div className="mb-2 flex items-center justify-between">
        <span className="text-sm font-medium">{label}</span>
        <span className="tabular text-sm font-semibold text-foreground">
          {value}
          {unit && <span className="text-muted-foreground">{unit}</span>}
        </span>
      </div>
      <input
        type="range"
        min={min}
        max={max}
        value={value}
        onChange={(e) => onChange(Number(e.target.value))}
        className="f1-range w-full"
        aria-label={label}
      />
      {hint && <p className="mt-1.5 text-xs">{hint}</p>}
    </div>
  )
}

function ToggleRow({
  label,
  description,
  checked,
  onChange,
}: {
  label: string
  description: string
  checked: boolean
  onChange: (v: boolean) => void
}) {
  return (
    <div className="flex items-center justify-between border-b border-border/50 py-3 first:pt-0 last:border-0 last:pb-0">
      <div>
        <p className="text-sm font-medium">{label}</p>
        <p className="text-xs text-muted-foreground">{description}</p>
      </div>
      <button
        role="switch"
        aria-checked={checked}
        aria-label={label}
        onClick={() => onChange(!checked)}
        className={cn(
          'relative h-6 w-11 shrink-0 rounded-full transition-colors',
          checked ? 'bg-primary' : 'bg-secondary',
        )}
      >
        <span
          className={cn(
            'absolute top-0.5 size-5 rounded-full bg-white transition-transform',
            checked ? 'translate-x-[22px]' : 'translate-x-0.5',
          )}
        />
      </button>
    </div>
  )
}
