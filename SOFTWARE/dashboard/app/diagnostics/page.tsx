'use client'

import { Stethoscope, TriangleAlert, Cpu, Signal, ShieldCheck, Gauge } from 'lucide-react'
import { useTelemetry } from '@/contexts/telemetry-context'
import { PageHeader } from '@/components/common/page-header'
import { Panel } from '@/components/common/panel'
import { Badge } from '@/components/ui/badge'
import { StatusDot } from '@/components/common/status-dot'
import { FaultsList } from '@/components/features/faults-list'
import { Stagger, StaggerItem } from '@/components/common/fade-in'
import { LEVEL_TEXT, LEVEL_LABEL, LEVEL_BG } from '@/lib/format'
import { cn } from '@/lib/utils'
import type { HealthLevel } from '@/types/telemetry'

function healthScore(faults: { level: HealthLevel }[]): number {
  const penalty = faults.reduce(
    (acc, f) => acc + (f.level === 'critical' ? 28 : f.level === 'warning' ? 10 : 2),
    0,
  )
  return Math.max(0, 100 - penalty)
}

export default function DiagnosticsPage() {
  const { frame, faults, sensors, diagnostics } = useTelemetry()
  if (!frame || !diagnostics) return <div><PageHeader title="Diagnostics" description="Full-vehicle health analysis from the live VCU stream." /><div className="flex h-64 items-center justify-center text-sm text-muted-foreground">Collecting diagnostic frames…</div></div>

  const score = healthScore(faults)
  const scoreLevel: HealthLevel = score > 80 ? 'ok' : score > 50 ? 'warning' : 'critical'

  const subsystems = [
    { key: 'Powertrain', icon: Gauge },
    { key: 'Battery', icon: Gauge },
    { key: 'Sensors', icon: Signal },
    { key: 'Aero', icon: Gauge },
    { key: 'Chassis', icon: Gauge },
  ] as const

  const compute = [
    { label: 'CPU Load', value: `${diagnostics.cpu.toFixed(0)}%`, level: diagnostics.cpu > 85 ? 'warning' : 'ok' },
    { label: 'Memory', value: `${diagnostics.memory.toFixed(0)}%`, level: diagnostics.memory > 80 ? 'warning' : 'ok' },
    { label: 'Watchdog', value: LEVEL_LABEL[diagnostics.watchdog], level: diagnostics.watchdog },
    { label: 'Ping', value: `${diagnostics.ping.toFixed(0)} ms`, level: diagnostics.ping > 60 ? 'warning' : 'ok' },
    { label: 'Packet Loss', value: `${diagnostics.packetLoss.toFixed(1)}%`, level: diagnostics.packetLoss > 3 ? 'warning' : 'ok' },
    { label: 'Packet Rate', value: `${diagnostics.packetRate.toFixed(0)} /s`, level: 'ok' },
  ] as const

  return (
    <div>
      <PageHeader
        title="Diagnostics"
        description="Full-vehicle health analysis: sensor integrity, compute load and communication link status derived from the live VCU stream."
        actions={
          <Badge variant={scoreLevel === 'ok' ? 'success' : scoreLevel === 'warning' ? 'warning' : 'danger'} className="gap-1.5 px-2.5 py-1">
            <StatusDot level={scoreLevel} /> Integrity {score}%
          </Badge>
        }
      />

      <Stagger className="grid grid-cols-1 gap-4 lg:grid-cols-3">
        <StaggerItem className="lg:col-span-1">
          <Panel title="System Integrity" icon={ShieldCheck} className="h-full">
            <div className="flex flex-col items-center py-2">
              <div
                className={cn(
                  'flex size-32 items-center justify-center rounded-full border-4',
                  scoreLevel === 'ok'
                    ? 'border-success/60'
                    : scoreLevel === 'warning'
                      ? 'border-warning/60'
                      : 'border-danger/60',
                )}
              >
                <div className="text-center">
                  <span className={cn('tabular text-4xl font-bold', LEVEL_TEXT[scoreLevel])}>{score}</span>
                  <span className="block text-xs text-muted-foreground">/ 100</span>
                </div>
              </div>
              <p className="mt-4 text-center text-xs leading-relaxed text-muted-foreground">
                Composite score from active faults across all monitored subsystems. Recomputed every frame.
              </p>
            </div>
          </Panel>
        </StaggerItem>

        <StaggerItem className="lg:col-span-2">
          <Panel title="Sensor Bus" icon={Signal} className="h-full">
            <div className="grid gap-2.5 sm:grid-cols-2">
              {sensors.map((s) => (
                <div
                  key={s.id}
                  className="flex items-center justify-between rounded-lg border border-border bg-secondary/40 px-3 py-2.5"
                >
                  <div className="min-w-0">
                    <p className="truncate text-xs font-semibold text-foreground">{s.name}</p>
                    <span className="text-[0.65rem] uppercase tracking-widest text-muted-foreground/70">
                      {s.subsystem}
                    </span>
                  </div>
                  <div className="flex items-center gap-2 pl-2">
                    <span className="tabular text-xs text-muted-foreground">{s.value}</span>
                    <StatusDot level={s.level} pulse={false} />
                  </div>
                </div>
              ))}
            </div>
          </Panel>
        </StaggerItem>

        <StaggerItem className="lg:col-span-2">
          <Panel title="Active Faults" icon={TriangleAlert} className="h-full">
            <FaultsList faults={faults} />
          </Panel>
        </StaggerItem>

        <StaggerItem className="lg:col-span-1">
          <Panel title="Compute & Link" icon={Cpu} className="h-full">
            <ul className="space-y-2.5">
              {compute.map((c) => (
                <li key={c.label} className="rounded-lg border border-border bg-secondary/40 px-3 py-2.5">
                  <div className="mb-1.5 flex items-center justify-between">
                    <span className="text-xs text-muted-foreground">{c.label}</span>
                    <span className={cn('tabular text-xs font-semibold', LEVEL_TEXT[c.level as HealthLevel])}>
                      {c.value}
                    </span>
                  </div>
                  {c.label === 'CPU Load' || c.label === 'Memory' ? (
                    <div className="h-1 overflow-hidden rounded-full bg-secondary">
                      <div
                        className={cn('h-full rounded-full transition-all duration-500', LEVEL_BG[c.level as HealthLevel])}
                        style={{ width: c.value }}
                      />
                    </div>
                  ) : null}
                </li>
              ))}
            </ul>
          </Panel>
        </StaggerItem>
      </Stagger>

      <div className="mt-4">
        <Panel title="Subsystem Rollup" icon={Stethoscope}>
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-5">
            {subsystems.map((sub) => {
              const owned = sensors.filter((s) => s.subsystem === sub.key)
              const worst: HealthLevel = owned.some((s) => s.level === 'critical')
                ? 'critical'
                : owned.some((s) => s.level === 'warning')
                  ? 'warning'
                  : 'ok'
              return (
                <div key={sub.key} className="rounded-lg border border-border bg-secondary/40 p-4">
                  <div className="mb-2 flex items-center justify-between">
                    <span className="text-sm font-medium text-foreground">{sub.key}</span>
                    <StatusDot level={worst} pulse={worst !== 'ok'} />
                  </div>
                  <p className={cn('text-xs font-semibold', LEVEL_TEXT[worst])}>{LEVEL_LABEL[worst]}</p>
                  <p className="mt-0.5 text-[0.7rem] text-muted-foreground">{owned.length} sensors</p>
                </div>
              )
            })}
          </div>
        </Panel>
      </div>
    </div>
  )
}
