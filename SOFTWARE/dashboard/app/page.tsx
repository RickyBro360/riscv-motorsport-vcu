'use client'

import {
  Gauge,
  Thermometer,
  Zap,
  Activity,
  Wind,
  Signal,
  AlertTriangle,
  CircleGauge,
  Flag,
} from 'lucide-react'
import { useTelemetry } from '@/contexts/telemetry-context'
import { useUnits } from '@/hooks/use-units'
import { LIMITS } from '@/constants'
import { levelFromLimit, toPct, fmt, connToLevel, avgWing } from '@/lib/format'
import { PageHeader } from '@/components/common/page-header'
import { MetricCard } from '@/components/common/metric-card'
import { Panel } from '@/components/common/panel'
import { StatusDot } from '@/components/common/status-dot'
import { Badge } from '@/components/ui/badge'
import { Stagger, StaggerItem } from '@/components/common/fade-in'
import { TelemetrySkeleton } from '@/components/common/skeleton'
import { AiRecommendationCard } from '@/components/features/ai-recommendation-card'
import { FaultsList } from '@/components/features/faults-list'
import { QuickActions } from '@/components/features/quick-actions'
import { SessionControls } from '@/components/features/session-controls'
import { TrackStatus } from '@/components/features/track-status'
import { CarControl } from '@/components/features/car-control'
import type { HealthLevel } from '@/types/telemetry'

export default function DashboardPage() {
  const { frame, faults, connection } = useTelemetry()
  const u = useUnits()

  if (!frame) {
    return (
      <div>
        <PageHeader
          title="Pit Wall Overview"
          description="Real-time state of the RISC-V VCU and 1:10 scale race car."
        />
        <TelemetrySkeleton />
      </div>
    )
  }

  const connLevel = connToLevel(connection)
  const v = frame.vcu
  const trackMode = v?.driveMode === 'track'
  const driver = v?.hwProfile === 'prototype' ? 'L298N, forward only' : v?.hwProfile === 'pcb_rev_b'
    ? `BTN8982 ×2 · kill switch ${v.motorPower ? 'ON' : 'off'}` : 'forward only'

  return (
    <div>
      <PageHeader
        title="Pit Wall Overview"
        description="Real-time state of the RISC-V VCU and 1:10 scale race car. All figures stream live from the onboard telemetry stack."
        actions={<div className="flex flex-wrap items-center justify-end gap-2"><SessionControls /><Badge variant={connLevel === 'ok' ? 'success' : 'warning'} className="gap-1.5 px-2.5 py-1"><StatusDot level={connLevel} /> Telemetry {connection}</Badge></div>}
      />

      <Stagger className="grid grid-cols-2 gap-4 sm:grid-cols-3 lg:grid-cols-4">
        <StaggerItem>
          <MetricCard
            label="Speed"
            value={u.speed(frame.speed).toFixed(2)}
            unit={u.speedUnit}
            icon={Gauge}
            level={levelFromLimit(frame.speed, LIMITS.speed.warn, LIMITS.speed.max)}
            fill={toPct(frame.speed, LIMITS.speed.min, LIMITS.speed.max)}
            hint={`Wheel ${fmt(frame.rpm)} rpm · ${Number(frame.vcuConfig?.speedSource ?? 0) === 1 ? 'estimated from motor duty' : trackMode ? 'front wheels' : 'dyno, rear wheels'}`}
          />
        </StaggerItem>
        <StaggerItem>
          <MetricCard
            label="Lap"
            value={frame.track?.runActive ? `${frame.track.lap + 1}` : '—'}
            unit={frame.track?.runActive ? `${(frame.track.pos * 100).toFixed(0)}%` : ''}
            icon={Flag}
            fill={frame.track?.runActive ? frame.track.pos * 100 : 0}
            hint={trackMode ? `track mode · flaps ${(v?.aeroStraight ?? frame.track?.straightMode) ? 'straight' : 'corner'}` : frame.track?.runActive ? `${frame.track.straightMode ? 'straight' : 'corner'} mode` : 'no circuit run'}
          />
        </StaggerItem>
        <StaggerItem>
          <MetricCard
            label="Motor Temp"
            value={u.temp(frame.motorTemp).toFixed(1)}
            unit={u.tempUnit}
            icon={Thermometer}
            level={levelFromLimit(frame.motorTemp, LIMITS.motorTemp.warn, LIMITS.motorTemp.max)}
            fill={toPct(frame.motorTemp, LIMITS.motorTemp.min, LIMITS.motorTemp.max)}
            hint={`Limit ${u.temp(LIMITS.motorTemp.max).toFixed(0)}${u.tempUnit}`}
          />
        </StaggerItem>
        <StaggerItem>
          <MetricCard
            label="Battery Temp"
            value={u.temp(frame.batteryTemp).toFixed(1)}
            unit={u.tempUnit}
            icon={Thermometer}
            level={levelFromLimit(frame.batteryTemp, LIMITS.batteryTemp.warn, LIMITS.batteryTemp.max)}
            fill={toPct(frame.batteryTemp, LIMITS.batteryTemp.min, LIMITS.batteryTemp.max)}
            hint={`Limit ${u.temp(LIMITS.batteryTemp.max).toFixed(0)}${u.tempUnit}`}
          />
        </StaggerItem>
        <StaggerItem>
          <MetricCard
            label="Supply Current"
            value={frame.current.toFixed(2)}
            unit="A"
            icon={Zap}
            level={levelFromLimit(frame.current, LIMITS.current.warn, LIMITS.current.max)}
            fill={toPct(frame.current, LIMITS.current.min, LIMITS.current.max)}
            hint="INA260 · adapter 2.2 A"
          />
        </StaggerItem>
        <StaggerItem>
          <MetricCard
            label="Voltage"
            value={frame.voltage.toFixed(2)}
            unit="V"
            icon={Activity}
            level={levelFromLimit(frame.voltage, LIMITS.voltage.warn, 0, true)}
            fill={toPct(frame.voltage, LIMITS.voltage.min, LIMITS.voltage.max)}
            hint="12 V adapter / 3S"
          />
        </StaggerItem>
        <StaggerItem>
          <MetricCard
            label="Motor Duty"
            value={frame.throttle.toFixed(0)}
            unit="%"
            icon={CircleGauge}
            fill={frame.throttle}
            hint={frame.track?.runActive && frame.track.profileDutyPct != null ? `map ${frame.track.profileDutyPct.toFixed(0)} % · ${driver}` : `${driver}${v?.tcActive ? ` · TC −${v.tcCutPct ?? 0}%` : ''}`}
          />
        </StaggerItem>
        <StaggerItem>
          <MetricCard
            label="Wing Position"
            value={avgWing(frame.wing).toFixed(1)}
            unit="°"
            icon={Wind}
            fill={toPct(avgWing(frame.wing), LIMITS.wing.min, LIMITS.wing.max)}
            hint={`FL ${frame.wing.frontLeft.toFixed(0)}° FR ${frame.wing.frontRight.toFixed(0)}° RL ${frame.wing.rearLeft.toFixed(0)}° RR ${frame.wing.rearRight.toFixed(0)}°`}
          />
        </StaggerItem>
      </Stagger>

      <div className="mt-4 grid grid-cols-1 gap-4 lg:grid-cols-3">
        <CarControl compact />
        <AiRecommendationCard />
        <TrackStatus />
      </div>

      <div className="mt-4 grid grid-cols-1 gap-4 lg:grid-cols-3">
        <Panel title="Recent Faults" icon={AlertTriangle} className="h-full">
          <FaultsList faults={faults} max={5} />
        </Panel>
        <Panel title="Connection Status" icon={Signal} className="h-full">
          <ConnectionDetails />
        </Panel>
        <QuickActions />
      </div>
    </div>
  )
}

function ConnectionDetails() {
  const { diagnostics: d } = useTelemetry()
  if (!d) return null
  const rows: { label: string; value: string; level: HealthLevel }[] = [
    { label: 'CAN Bus', value: d.can, level: connToLevel(d.can) },
    { label: 'LoRa Link', value: d.lora, level: connToLevel(d.lora) },
    { label: 'Packet Loss', value: `${d.packetLoss.toFixed(1)}%`, level: d.packetLoss > 3 ? 'warning' : 'ok' },
    { label: 'Round-trip Ping', value: `${d.ping.toFixed(0)} ms`, level: d.ping > 60 ? 'warning' : 'ok' },
    { label: 'Packet Rate', value: `${d.packetRate.toFixed(0)} /s`, level: 'ok' },
  ]

  return (
    <ul className="space-y-2.5">
      {rows.map((r) => (
        <li
          key={r.label}
          className="flex items-center justify-between rounded-lg border border-border bg-secondary/40 px-3 py-2.5"
        >
          <span className="text-xs text-muted-foreground">{r.label}</span>
          <span className="flex items-center gap-2">
            <span className="tabular text-xs font-semibold capitalize">{r.value}</span>
            <StatusDot level={r.level} pulse={false} />
          </span>
        </li>
      ))}
    </ul>
  )
}
