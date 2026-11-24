'use client'

import { useEffect, useMemo, useState, type ReactNode } from 'react'
import { Activity, BatteryCharging, Cpu, Download, Gauge, Radio, Thermometer, Waves, Wind } from 'lucide-react'
import { useTelemetry } from '@/contexts/telemetry-context'
import { PageHeader } from '@/components/common/page-header'
import { Panel } from '@/components/common/panel'
import { ChartPanel } from '@/components/features/chart-panel'
import { useThrottled } from '@/lib/use-throttled'
import { FlapDiagram } from '@/components/features/flap-diagram'
import { GGPlot } from '@/components/features/gg-plot'
import { SourceTag, type DataSource } from '@/components/common/source-tag'
import { Badge } from '@/components/ui/badge'
import { StatusDot } from '@/components/common/status-dot'
import { Button } from '@/components/ui/button'
import { downloadFile, framesToCsv } from '@/lib/export'
import { LINK_LABEL, linkToLevel } from '@/lib/format'
import { BATTERY_LIMIT_C, MOTOR_LIMIT_C, SUPPLY, speedMaxKmh, wheelRpm, type PowerSource } from '@/lib/scales'
import { effectiveConfig, sampleAt } from '@/lib/tracks'
import { useTrack } from '@/hooks/use-tracks'
import { vehicleApi } from '@/services/api'
import type { TelemetryFrame } from '@/types/telemetry'

const C = { primary: 'var(--color-primary)', info: 'var(--color-info)', success: 'var(--color-success)', warning: 'var(--color-warning)', purple: 'var(--color-chart-5)', danger: 'var(--color-danger)' }
const rearOf = (f: TelemetryFrame) => (f.wheels ? (f.wheels.rl + f.wheels.rr) / 2 : f.speed)

function Section({ title, icon: Icon, children, note }: { title: string; icon: typeof Activity; children: ReactNode; note?: string }) {
  return (
    <section className="mt-6">
      <div className="mb-3 flex items-center gap-2">
        <Icon className="size-4 text-primary" />
        <h2 className="text-sm font-semibold uppercase tracking-widest">{title}</h2>
        {note && <span className="text-xs text-muted-foreground">— {note}</span>}
      </div>
      {children}
    </section>
  )
}

function Stat({ label, value, unit, source, hint }: { label: string; value: string; unit?: string; source: DataSource; hint?: string }) {
  return (
    <div className="rounded-lg border border-border bg-card p-3">
      <div className="flex items-center justify-between gap-2">
        <span className="text-[0.65rem] uppercase tracking-widest text-muted-foreground">{label}</span>
        <SourceTag source={source} />
      </div>
      <div className="mt-1.5 flex items-baseline gap-1"><span className="tabular text-xl font-semibold">{value}</span>{unit && <span className="text-xs text-muted-foreground">{unit}</span>}</div>
      {hint && <p className="mt-0.5 text-[0.65rem] text-muted-foreground">{hint}</p>}
    </div>
  )
}

export default function LiveTelemetryPage() {
  const { history: liveHistory, frame, running, mode, link, diagnostics } = useTelemetry()
  // FW 2.3.10: the charts and the statistics computed over the history refresh 4x a second; the live
  // numbers (frame) still update with every telemetry frame. Redrawing 14 charts per frame stalled the page.
  const history = useThrottled(liveHistory, 250)
  const [power, setPower] = useState<PowerSource>('adapter')
  useEffect(() => { vehicleApi.station().then((s) => setPower(s.powerSource)).catch(() => {}) }, [])
  const cfg = effectiveConfig(frame?.vcuConfig)
  const track = useTrack(frame?.track?.id ?? cfg.trackId)
  const supply = SUPPLY[power]
  const vMax = speedMaxKmh(frame, history)
  const src: DataSource = mode === 'simulation' ? 'simulated' : 'measured'
  // FW 2.3.4: the VCU can estimate the rear wheel speed from the motor duty (RO key 106). Label it as such.
  const speedEst = mode !== 'simulation' && Number(frame?.vcuConfig?.speedSource ?? 0) === 1
  const speedSrc: DataSource = speedEst ? 'derived' : src
  const wheelD = Number(frame?.vcuConfig?.wheelDiameterUm ?? 0) > 0 ? Number(frame?.vcuConfig?.wheelDiameterUm) / 1e6 : 0.07

  const derivedStats = useMemo(() => {
    if (!history.length) return null
    const last = history[history.length - 1]
    // energy since the current lap started (trapezoid over the frames we have)
    let wh = 0
    const lapNo = last.track?.lap
    for (let i = 1; i < history.length; i++) {
      const a = history[i - 1], b = history[i]
      if (lapNo !== undefined && b.track?.lap !== lapNo) continue
      const dt = (b.timestamp - a.timestamp) / 1000
      if (dt > 0 && dt < 1) wh += ((a.voltage * a.current + b.voltage * b.current) / 2) * dt / 3600
    }
    const temps = history.filter((f) => f.motorTemp > 1)
    let rate = 0
    if (temps.length > 5) {
      const dt = (temps[temps.length - 1].timestamp - temps[0].timestamp) / 1000
      if (dt > 3) rate = (temps[temps.length - 1].motorTemp - temps[0].motorTemp) / dt
    }
    const toLimit = rate > 0.002 ? (MOTOR_LIMIT_C - last.motorTemp) / rate : null
    const moving = history.filter((f) => f.wheels && (f.wheels.rl + f.wheels.rr) / 2 > 0.5)
    const bal = moving.length ? moving.reduce((s, f) => s + (f.wheels!.rl - f.wheels!.rr) / ((f.wheels!.rl + f.wheels!.rr) / 2), 0) / moving.length * 100 : null
    return { wh, rate, toLimit, bal }
  }, [history])

  const gg = useMemo(() => {
    const measured = history.slice(-150).map((f) => ({ x: f.gForceLat, y: f.gForceLong }))
    const modelled = track ? history.slice(-150).filter((f) => f.track?.runActive).map((f) => {
      const s = sampleAt(track, f.track!.pos)
      return { x: f.track?.latGModel ?? s.latG, y: s.longG }
    }) : []
    return { measured, modelled }
  }, [history, track])

  const streamLevel = mode === 'live' ? linkToLevel(link) : running ? 'ok' : 'warning'
  const streamLabel = mode === 'live' ? LINK_LABEL[link] : mode === 'replay' ? 'Replay' : running ? 'Simulating (offline)' : 'Paused'

  if (!frame) {
    return <div className="flex h-64 items-center justify-center text-sm text-muted-foreground">
      {mode === 'live' ? `Waiting for the car (${LINK_LABEL[link]})…` : 'Buffering telemetry stream…'}</div>
  }
  const tr = frame.track
  const limitA = (cfg.currentLimitCa ?? 200) / 100
  const errLR = (f: TelemetryFrame) => f.wing.rearLeft - f.wingTarget.rearLeft
  const errRR = (f: TelemetryFrame) => f.wing.rearRight - f.wingTarget.rearRight

  return (
    <div>
      <PageHeader
        title="Live Telemetry"
        description={`Every signal the car's sensors can give, grouped the way a race engineer reads them. Scales come from the real hardware: ${supply.label}, two Johnson motors on an L298N, 70 mm tyres (assumed), MPU6050, INA260, DS18B20 and MG996R feedback. Each panel says whether its numbers are measured, derived, decided by the VCU, or modelled.`}
        actions={<div className="flex items-center gap-2">
          <select value={power} onChange={(e) => { const p = e.target.value as PowerSource; setPower(p); vehicleApi.setStation({ powerSource: p }).catch(() => {}) }}
            className="h-8 rounded-md border border-border bg-background px-2 text-xs">
            <option value="adapter">Supply: 12 V / 2.2 A adapter</option>
            <option value="lipo">Supply: 3S LiPo</option>
          </select>
          <Button variant="outline" size="sm" onClick={() => downloadFile('live-telemetry.csv', framesToCsv(history))} disabled={!history.length}><Download className="size-3.5" /> CSV</Button>
          <Badge variant={streamLevel === 'ok' ? 'success' : streamLevel === 'warning' ? 'warning' : 'danger'} className="gap-1.5 px-2.5 py-1"><StatusDot level={streamLevel} /> {streamLabel}</Badge>
        </div>}
      />

      <Section title="Speed & drivetrain" icon={Gauge} note="hall sensors, one magnet per wheel; the dyno turns the rear pair">
        <div className="mb-4 grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-6">
          <Stat label="Vehicle speed" value={frame.speed.toFixed(2)} unit="km/h" source={speedSrc} hint={speedEst ? 'estimated from motor duty' : `${(frame.speed / 3.6).toFixed(2)} m/s`} />
          <Stat label="Wheel rpm (rear)" value={wheelRpm(rearOf(frame), wheelD).toFixed(0)} unit="rpm" source="derived" hint={`${(wheelD * 1000).toFixed(0)} mm tyre${speedEst ? ' · from duty' : ' (assumed)'}`} />
          <Stat label="Rear balance RL−RR" value={derivedStats?.bal != null ? derivedStats.bal.toFixed(1) : '—'} unit="%" source="derived" hint="both motors share one PWM" />
          <Stat label="Motor duty" value={frame.throttle.toFixed(0)} unit="%" source="vcu" hint={tr?.profileDutyPct != null ? `map asks ${tr.profileDutyPct.toFixed(0)} %` : undefined} />
          <Stat label="Odometer" value={frame.odometerM != null ? frame.odometerM.toFixed(1) : '—'} unit="m" source="vcu" hint={frame.odometerM == null && mode === 'live' ? 'not carried over LoRa' : undefined} />
          <Stat label="Dyno top speed" value={cfg.dynoVmaxCkmh ? (cfg.dynoVmaxCkmh / 100).toFixed(2) : '—'} unit="km/h" source="vcu" hint={cfg.dynoVmaxCkmh ? `at ${(cfg.dutyCapPermille / 10).toFixed(0)} % duty` : 'run `dyno cal`'} />
        </div>
        <div className="grid grid-cols-1 gap-4 xl:grid-cols-2">
          <ChartPanel title={speedEst ? 'Wheel speeds (estimated from motor duty)' : 'Wheel speeds'} data={history} unit="km/h" current={frame.speed.toFixed(2)} domain={[0, vMax]} type="line" source={speedSrc}
            series={[
              { accessor: (f) => f.wheels?.rl ?? f.speed, color: C.primary, label: 'RL' },
              { accessor: (f) => f.wheels?.rr ?? f.speed, color: C.warning, label: 'RR' },
              { accessor: (f) => f.wheels?.fl ?? 0, color: C.info, label: 'FL' },
              { accessor: (f) => f.wheels?.fr ?? 0, color: C.success, label: 'FR' },
            ]} />
          <ChartPanel title="Motor duty vs the circuit's speed profile" data={history} unit="%" current={frame.throttle.toFixed(0)} domain={[0, 100]} type="line" source="vcu"
            refLines={[{ y: cfg.dutyCapPermille / 10, label: 'cap', color: C.warning }, { y: cfg.dutyFloorPermille / 10, label: 'floor', color: C.info }]}
            series={[
              { accessor: (f) => f.throttle, color: C.success, label: 'Applied duty' },
              { accessor: (f) => f.track?.profileDutyPct ?? f.throttle, color: C.purple, label: 'Map profile' },
            ]} />
        </div>
      </Section>

      <Section title="Active aero" icon={Wind} note="MG996R with pot feedback on the rear flaps (LR/RR), SG90 open loop on the front (LF/RF), MG995 steering">
        <div className="grid grid-cols-1 gap-4 xl:grid-cols-[300px_1fr_1fr]">
          <Panel title="Flaps" action={<SourceTag source="measured" />}>
            <FlapDiagram wing={frame.wing} target={frame.wingTarget} straightMode={tr?.straightMode} />
            <p className="mt-2 text-[0.7rem] text-muted-foreground">Aero mode: {['baseline', 'AI-assist', 'failsafe hold'][frame.aeroMode ?? 1] ?? '—'} · VCU verdict on last AI message: {frame.lastAiReason?.replace(/_/g, ' ') ?? '—'}</p>
          </Panel>
          <ChartPanel title="Flap angle: VCU target vs measured" data={history} unit="°" current={frame.wing.rearLeft.toFixed(1)} domain={[0, 45]} type="line" source="measured"
            series={[
              { accessor: (f) => f.wingTarget.rearLeft, color: C.primary, label: 'LR target' },
              { accessor: (f) => f.wing.rearLeft, color: C.warning, label: 'LR measured' },
              { accessor: (f) => f.wingTarget.frontLeft, color: C.info, label: 'LF target' },
              { accessor: (f) => f.wing.rearRight, color: C.success, label: 'RR measured' },
            ]} />
          <ChartPanel title="Flap tracking error (measured − target)" data={history} unit="°" current={errLR(frame).toFixed(1)} domain={[-15, 15]} type="line" source="derived"
            refLines={[{ y: 8, label: 'VCU fault 8°', color: C.danger }, { y: -8, label: '', color: C.danger }]}
            series={[{ accessor: errLR, color: C.warning, label: 'LR' }, { accessor: errRR, color: C.success, label: 'RR' }]} />
        </div>
      </Section>

      <Section title="Power" icon={BatteryCharging} note={`INA260 in the +12 V line — ${supply.label}`}>
        <div className="mb-4 grid grid-cols-2 gap-3 md:grid-cols-4">
          <Stat label="Power" value={(frame.voltage * frame.current).toFixed(1)} unit="W" source="derived" />
          <Stat label="Energy this lap" value={derivedStats ? (derivedStats.wh * 1000).toFixed(1) : '—'} unit="mWh" source="derived" />
          <Stat label="Supply headroom" value={((1 - frame.current / supply.limitA) * 100).toFixed(0)} unit="%" source="derived" hint={`of ${supply.limitA} A`} />
          <Stat label="VCU derates above" value={limitA.toFixed(2)} unit="A" source="vcu" hint="tunable (key 21)" />
        </div>
        <div className="grid grid-cols-1 gap-4 xl:grid-cols-3">
          <ChartPanel title="Supply voltage" data={history} unit="V" current={frame.voltage.toFixed(2)} domain={[supply.vMin, supply.vMax]} source={src}
            refLines={[{ y: 10.5, label: 'derate 10.5 V', color: C.warning }, { y: 9.9, label: 'undervolt 9.9 V', color: C.danger }]}
            series={[{ accessor: (f) => f.voltage, color: C.info, label: 'Voltage' }]} />
          <ChartPanel title="Supply current" data={history} unit="A" current={frame.current.toFixed(2)} domain={[0, supply.iMax]} source={src}
            refLines={[{ y: supply.limitA, label: `${power === 'adapter' ? 'adapter' : 'pack'} ${supply.limitA} A`, color: C.danger }, { y: limitA, label: 'VCU derate', color: C.warning }]}
            series={[{ accessor: (f) => f.current, color: C.primary, label: 'Current' }]} />
          <ChartPanel title="Electrical power" data={history} unit="W" current={(frame.voltage * frame.current).toFixed(1)} domain={[0, supply.pMax]} source="derived"
            series={[{ accessor: (f) => f.voltage * f.current, color: C.purple, label: 'Power' }]} />
        </div>
      </Section>

      <Section title="Thermal" icon={Thermometer} note="two DS18B20 on one 1-Wire bus">
        <div className="grid grid-cols-1 gap-4 xl:grid-cols-[1fr_320px]">
          <ChartPanel title="Motor and battery temperature" data={history} unit="°C" current={frame.motorTemp.toFixed(1)} domain={[15, 85]} source={src} type="line"
            refLines={[{ y: MOTOR_LIMIT_C, label: 'motor limit 80 °C', color: C.danger }, { y: BATTERY_LIMIT_C, label: 'battery 60 °C', color: C.warning }]}
            series={[{ accessor: (f) => f.motorTemp, color: C.primary, label: 'Motor' }, { accessor: (f) => f.batteryTemp, color: C.info, label: 'Battery' }]} />
          <div className="grid grid-cols-1 gap-3">
            <Stat label="Motor warming" value={derivedStats ? (derivedStats.rate * 60).toFixed(2) : '—'} unit="°C/min" source="derived" />
            <Stat label="Time to 80 °C" value={derivedStats?.toLimit != null ? (derivedStats.toLimit / 60).toFixed(1) : '∞'} unit="min" source="derived" hint="at the current trend" />
            <Stat label="VCU estimate" value={frame.derived?.secondsToMotorLimit != null ? (frame.derived.secondsToMotorLimit / 60).toFixed(1) : '—'} unit="min" source="vcu" hint="ThermalEstimator (MODE A)" />
          </div>
        </div>
      </Section>

      <Section title="Chassis & IMU" icon={Waves} note="MPU6050 — on the dyno the chassis does not corner; the model shows what the full-size car would feel">
        <div className="grid grid-cols-1 gap-4 xl:grid-cols-3">
          <Panel title="g-g diagram" action={<SourceTag source="modelled" />}><GGPlot measured={gg.measured} modelled={gg.modelled} /></Panel>
          <ChartPanel title="Acceleration" data={history} unit="g" current={frame.gForceLong.toFixed(2)} domain={[-1.5, 1.5]} type="line" source={src}
            series={[{ accessor: (f) => f.gForceLong, color: C.primary, label: 'Long (X)' }, { accessor: (f) => f.gForceLat, color: C.info, label: 'Lat (Y)' }]} />
          <ChartPanel title="Vibration RMS (VCU)" data={history} unit="mg" current={(frame.vibrationMg ?? 0).toFixed(0)} domain={[0, 400]} source={src}
            series={[{ accessor: (f) => f.vibrationMg ?? 0, color: C.warning, label: 'Vibration' }]} />
          <ChartPanel title="Roll & pitch" data={history} unit="°" current={frame.imu.roll.toFixed(1)} domain={[-10, 10]} type="line" source={src}
            series={[{ accessor: (f) => f.imu.roll, color: C.primary, label: 'Roll' }, { accessor: (f) => f.imu.pitch, color: C.info, label: 'Pitch' }]} />
          <ChartPanel title="Yaw rate" data={history} unit="°/s" current={(frame.gyroZ ?? 0).toFixed(1)} domain={[-30, 30]} type="line" source={src}
            series={[{ accessor: (f) => f.gyroZ ?? 0, color: C.success, label: 'Yaw rate' }]} />
          <ChartPanel title="Steering (knob or map, + = left)" data={history} unit="°" current={frame.steering.toFixed(1)} domain={[-30, 30]} type="line" source="vcu"
            series={[{ accessor: (f) => f.steering, color: C.purple, label: 'Steering' }]} />
        </div>
      </Section>

      <Section title="Link & VCU" icon={Radio}>
        <div className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-6">
          <Stat label="LoRa RSSI" value={diagnostics?.loraLink?.rssi ? diagnostics.loraLink.rssi.toFixed(0) : '—'} unit="dBm" source="measured" />
          <Stat label="LoRa SNR" value={diagnostics?.loraLink?.snr ? diagnostics.loraLink.snr.toFixed(1) : '—'} unit="dB" source="measured" />
          <Stat label="Packet loss" value={diagnostics ? diagnostics.packetLoss.toFixed(1) : '—'} unit="%" source="derived" />
          <Stat label="Uplink rate" value={diagnostics ? diagnostics.packetRate.toFixed(1) : '—'} unit="Hz" source="derived" />
          <Stat label="VCU state" value={frame.vcuState} source="vcu" hint={frame.runState ? `run: ${frame.runState.replace('_', ' ')}` : undefined} />
          <Stat label="Config staged" value={tr?.configPending ? 'yes' : 'no'} source="vcu" hint="applies at start/finish" />
        </div>
        <div className="mt-3 grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-6">
          <Stat label="Drive mode" value={frame.vcu?.driveMode ? frame.vcu.driveMode.toUpperCase() : '—'} source="vcu" hint={frame.vcu?.driveMode === 'dyno' ? 'on the rollers' : frame.vcu?.driveMode === 'track' ? 'on the floor, ARM deadman' : 'CAN 0x148'} />
          <Stat label="Hardware" value={frame.vcu?.hwProfile === 'pcb_rev_b' ? 'PCB rev B' : frame.vcu?.hwProfile === 'prototype' ? 'Prototype' : '—'} source="vcu" />
          <Stat label="Motor power" value={frame.vcu?.killSwitchFitted ? (frame.vcu.motorPower ? 'ON' : 'off') : frame.vcu?.killSwitchFitted === false ? 'n/a' : '—'} source="vcu" hint={frame.vcu?.killSwitchFitted ? 'BTS7004 kill switch' : 'no kill switch on this build'} />
          <Stat label="Traction ctrl" value={frame.vcu?.tcActive ? `−${frame.vcu.tcCutPct ?? 0}` : frame.vcu ? '0' : '—'} unit="%" source="vcu" hint={`limit ${(Number(frame.vcuConfig?.tcSlipPermille ?? 0) / 10).toFixed(0)} % slip (0 = off)`} />
          <Stat label="Ground link (VCU)" value={frame.vcu?.groundLinkOk == null ? '—' : frame.vcu.groundLinkOk ? 'ok' : 'LOST'} source="vcu" hint="deadman for dashboard runs" />
          <Stat label="Flap mode" value={frame.vcu?.aeroStraight == null ? '—' : frame.vcu.aeroStraight ? 'straight' : 'corner'} source="vcu" />
        </div>
        <p className="mt-3 flex items-center gap-1 text-[0.7rem] text-muted-foreground"><Cpu className="size-3" /> Frames arrive at the LoRa uplink rate (~5 Hz); the Pi logs the full 20 Hz CAN stream to its microSD.</p>
      </Section>
    </div>
  )
}
