'use client'

import { Settings2, Radio, Ruler, Bell, Cpu, Play, Pause, Database, RotateCcw } from 'lucide-react'
import { useTelemetry } from '@/contexts/telemetry-context'
import { useSettings, type AlertSettings, type LinkTransport } from '@/contexts/settings-context'
import { PageHeader } from '@/components/common/page-header'
import { Panel } from '@/components/common/panel'
import { Badge } from '@/components/ui/badge'
import { StatusDot } from '@/components/common/status-dot'
import { VEHICLE_ID, PROJECT_NAME } from '@/constants'
import { cn } from '@/lib/utils'
import type { UnitSystem } from '@/lib/format'

export default function SettingsPage() {
  const { running, toggleRunning, connection, diagnostics } = useTelemetry()
  const { settings, update, reset } = useSettings()
  const connLevel = connection === 'connected' ? 'ok' : connection === 'degraded' ? 'warning' : 'critical'
  const updateAlert = (key: keyof AlertSettings, value: boolean) => update('alerts', { ...settings.alerts, [key]: value })

  return <div>
    <PageHeader title="Settings" description="Configure the telemetry link, measurement units, alert routing and data acquisition parameters for the pit-wall suite." actions={<Badge variant={connLevel === 'ok' ? 'success' : 'warning'} className="gap-1.5 px-2.5 py-1"><StatusDot level={connLevel} /> {connection}</Badge>} />
    <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
      <Panel title="Telemetry Stream" icon={Cpu}>
        <div className="mb-4 flex items-center justify-between rounded-lg border border-border bg-secondary/40 px-4 py-3"><div><p className="text-sm font-medium">Data acquisition</p><p className="text-xs text-muted-foreground">{running ? 'Streaming live at' : 'Paused —'} {running ? `${settings.sampleRate} Hz` : 'resume to acquire'}</p></div><button onClick={toggleRunning} className={cn('inline-flex items-center gap-1.5 rounded-md px-3 py-1.5 text-xs font-semibold transition-opacity hover:opacity-90', running ? 'bg-secondary text-foreground' : 'bg-primary text-primary-foreground')}>{running ? <Pause className="size-3.5" /> : <Play className="size-3.5" />}{running ? 'Pause' : 'Resume'}</button></div>
        <SegRow label="Sample Rate" options={[5, 10, 20, 50].map((value) => ({ label: `${value} Hz`, value }))} value={settings.sampleRate} onChange={(value) => update('sampleRate', value)} />
        <ToggleRow label="Record telemetry" description="Keep the rolling stream available to analysis pages" checked={settings.dataRecording} onChange={(value) => update('dataRecording', value)} />
      </Panel>
      <Panel title="Connection Link" icon={Radio}>
        <SegRow label="Transport" options={[{ label: 'LoRa', value: 'lora' as LinkTransport }, { label: 'Wi-Fi', value: 'wifi' as LinkTransport }, { label: 'USB', value: 'usb' as LinkTransport }]} value={settings.link} onChange={(value) => update('link', value)} />
        <dl className="mt-4 space-y-2.5 text-sm">{[['Packet loss', diagnostics ? `${diagnostics.packetLoss.toFixed(1)}%` : '—'], ['Round-trip ping', diagnostics ? `${diagnostics.ping.toFixed(0)} ms` : '—'], ['Vehicle ID', VEHICLE_ID]].map(([key, value]) => <div key={key} className="flex items-center justify-between"><dt className="text-muted-foreground">{key}</dt><dd className="tabular font-semibold">{value}</dd></div>)}</dl>
      </Panel>
      <Panel title="Units & Display" icon={Ruler}>
        <SegRow label="Measurement system" options={[{ label: 'Metric', value: 'metric' as UnitSystem }, { label: 'Imperial', value: 'imperial' as UnitSystem }]} value={settings.units} onChange={(value) => update('units', value)} />
        <p className="mt-3 text-xs text-muted-foreground">{settings.units === 'metric' ? 'Speed in km/h, temperature in °C, pressure in bar.' : 'Speed in mph, temperature in °F, pressure in psi.'}</p>
      </Panel>
      <Panel title="Alerts" icon={Bell}>
        <div className="space-y-1">{([['critical', 'Critical faults', 'Always page the pit wall'], ['warning', 'Warnings', 'Threshold breaches and drift'], ['info', 'Informational', 'Routine status changes']] as const).map(([key, label, description]) => <ToggleRow key={key} label={label} description={description} checked={settings.alerts[key]} onChange={(value) => updateAlert(key, value)} />)}</div>
      </Panel>
    </div>
    <Panel className="mt-4" title="Runtime preferences" icon={Settings2}><div className="grid gap-1 sm:grid-cols-2"><ToggleRow label="Reduced motion" description="Prefer static transitions and less animation" checked={settings.reduceMotion} onChange={(value) => update('reduceMotion', value)} /><ToggleRow label="Developer mode" description="Expose diagnostic details in engineering views" checked={settings.developerMode} onChange={(value) => update('developerMode', value)} /></div></Panel>
    <div className="mt-4"><Panel bodyClassName="flex flex-col items-start justify-between gap-3 p-4 sm:flex-row sm:items-center"><div className="flex items-center gap-3"><Database className="size-5 text-primary" /><div><p className="text-sm font-medium">{PROJECT_NAME}</p><p className="text-xs text-muted-foreground">VCU firmware v2.4.1 · RISC-V · {VEHICLE_ID} · Telemetry protocol rev 7</p></div></div><button onClick={reset} className="inline-flex items-center gap-1.5 rounded-md border border-border px-3 py-1.5 text-xs font-medium text-muted-foreground hover:bg-secondary hover:text-foreground"><RotateCcw className="size-3.5" /> Reset preferences</button></Panel></div>
  </div>
}

function SegRow<T extends string | number>({ label, options, value, onChange }: { label: string; options: { label: string; value: T }[]; value: T; onChange: (value: T) => void }) { return <div><p className="mb-2 text-sm font-medium">{label}</p><div className="flex items-center gap-1 rounded-lg border border-border bg-background p-1">{options.map((option) => <button key={String(option.value)} onClick={() => onChange(option.value)} className={cn('flex-1 rounded-md px-3 py-1.5 text-xs font-medium transition-colors', value === option.value ? 'bg-primary text-primary-foreground' : 'text-muted-foreground hover:text-foreground')}>{option.label}</button>)}</div></div> }

function ToggleRow({ label, description, checked, onChange }: { label: string; description: string; checked: boolean; onChange: (value: boolean) => void }) { return <div className="flex items-center justify-between border-b border-border/50 py-3 first:pt-0 last:border-0 last:pb-0"><div><p className="text-sm font-medium">{label}</p><p className="text-xs text-muted-foreground">{description}</p></div><button role="switch" aria-checked={checked} aria-label={label} onClick={() => onChange(!checked)} className={cn('relative h-6 w-11 shrink-0 rounded-full transition-colors', checked ? 'bg-primary' : 'bg-secondary')}><span className={cn('absolute top-0.5 size-5 rounded-full bg-background transition-transform', checked ? 'translate-x-[22px]' : 'translate-x-0.5')} /></button></div> }
