'use client'

import { useCallback, useEffect, useMemo, useState } from 'react'
import { Activity, Battery, Gauge, Thermometer, Database, RefreshCw, GitCompare } from 'lucide-react'
import { useTelemetry } from '@/contexts/telemetry-context'
import { PageHeader } from '@/components/common/page-header'
import { Panel } from '@/components/common/panel'
import { TelemetryChart } from '@/components/common/telemetry-chart'
import { Stagger, StaggerItem } from '@/components/common/fade-in'
import { CHART_COLORS } from '@/constants'
import { formatLapTime } from '@/lib/format'
import { computeSessionAnalytics, fmtMetric, type SessionAnalytics, type AnalyticsFrameRecord } from '@/lib/analytics'
import { sessionApi, faultsApi, type BackendSession } from '@/services/api'
import { useBackendStatus } from '@/hooks/use-backend-status'
import type { Fault, TelemetryFrame } from '@/types/telemetry'

type Tab = 'live' | 'session'

export default function AnalyticsPage() {
  const { frame, history, lap } = useTelemetry()
  const backendStatus = useBackendStatus()

  const [tab, setTab] = useState<Tab>('live')
  const [sessions, setSessions] = useState<BackendSession[]>([])
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [compareId, setCompareId] = useState<string | null>(null)
  const [records, setRecords] = useState<AnalyticsFrameRecord[] | null>(null)
  const [sessionFaults, setSessionFaults] = useState<Fault[]>([])
  const [compareRecords, setCompareRecords] = useState<AnalyticsFrameRecord[] | null>(null)
  const [compareFaults, setCompareFaults] = useState<Fault[]>([])
  const [loading, setLoading] = useState(false)

  // Deep link from Data Logs: /analytics?session=SESS-XXXX
  useEffect(() => {
    if (typeof window === 'undefined') return
    const params = new URLSearchParams(window.location.search)
    const sid = params.get('session')
    if (sid) { setTab('session'); setSelectedId(sid) }
  }, [])

  useEffect(() => {
    if (backendStatus !== 'online') { setSessions([]); return }
    let cancelled = false
    sessionApi.list().then((s) => { if (!cancelled) setSessions(s.filter((x) => x.frameCount > 0)) }).catch(() => { if (!cancelled) setSessions([]) })
    return () => { cancelled = true }
  }, [backendStatus])

  const loadSession = useCallback(async (id: string, target: 'primary' | 'compare') => {
    setLoading(true)
    try {
      const [frames, faults] = await Promise.all([
        sessionApi.frames(id),
        faultsApi.list(id).catch(() => [] as Fault[]),
      ])
      const mapped: AnalyticsFrameRecord[] = frames.map((r) => ({ frame: r.frame, ai: r.ai, diagnostics: r.diagnostics }))
      if (target === 'primary') { setRecords(mapped); setSessionFaults(faults) }
      else { setCompareRecords(mapped); setCompareFaults(faults) }
    } catch {
      if (target === 'primary') { setRecords(null); setSessionFaults([]) }
      else { setCompareRecords(null); setCompareFaults([]) }
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { if (selectedId) loadSession(selectedId, 'primary') }, [selectedId, loadSession])
  useEffect(() => { if (compareId) loadSession(compareId, 'compare'); else { setCompareRecords(null); setCompareFaults([]) } }, [compareId, loadSession])

  const analytics = useMemo(() => (records ? computeSessionAnalytics(records, sessionFaults) : null), [records, sessionFaults])
  const compareAnalytics = useMemo(() => (compareRecords ? computeSessionAnalytics(compareRecords, compareFaults) : null), [compareRecords, compareFaults])

  const samples = history.length ? history : frame ? [frame] : []
  const liveMetrics = useMemo(() => {
    if (!samples.length) return null
    const avgv = (values: number[]) => values.reduce((sum, value) => sum + value, 0) / values.length
    return {
      avgSpeed: avgv(samples.map((s) => s.speed)),
      peakSpeed: Math.max(...samples.map((s) => s.speed)),
      peakMotorTemp: Math.max(...samples.map((s) => s.motorTemp)),
      avgBattery: avgv(samples.map((s) => s.battery)),
    }
  }, [samples])

  const chartFrames = records?.map((r) => r.frame) ?? []

  return (
    <div>
      <PageHeader
        title="Analytics"
        description="Performance, power, thermal, dynamics, aero, reliability and AI metrics — calculated from recorded session frames, not hard-coded values."
        actions={
          <div className="flex items-center gap-2">
            <button onClick={() => setTab('live')} className={`rounded-md px-3 py-1.5 text-xs font-medium transition-colors ${tab === 'live' ? 'bg-primary text-primary-foreground' : 'text-muted-foreground hover:text-foreground'}`}>Live window</button>
            <button onClick={() => setTab('session')} className={`rounded-md px-3 py-1.5 text-xs font-medium transition-colors ${tab === 'session' ? 'bg-primary text-primary-foreground' : 'text-muted-foreground hover:text-foreground'}`}>Recorded session</button>
          </div>
        }
      />

      {tab === 'live' ? (
        !samples.length ? (
          <div className="flex h-64 items-center justify-center text-sm text-muted-foreground">Buffering telemetry history…</div>
        ) : (
          <>
            <Stagger className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
              <StaggerItem><Metric icon={Gauge} label="Average speed" value={liveMetrics ? `${liveMetrics.avgSpeed.toFixed(1)} km/h` : 'Waiting'} /></StaggerItem>
              <StaggerItem><Metric icon={Activity} label="Peak speed" value={liveMetrics ? `${liveMetrics.peakSpeed.toFixed(1)} km/h` : 'Waiting'} /></StaggerItem>
              <StaggerItem><Metric icon={Thermometer} label="Peak motor temp" value={liveMetrics ? `${liveMetrics.peakMotorTemp.toFixed(1)} °C` : 'Waiting'} /></StaggerItem>
              <StaggerItem><Metric icon={Battery} label="Average SoC" value={liveMetrics ? `${liveMetrics.avgBattery.toFixed(1)}%` : 'Waiting'} /></StaggerItem>
            </Stagger>
            <div className="mt-4 grid gap-4 lg:grid-cols-2">
              <Panel title="Speed trace">
                <TelemetryChart data={samples} series={[{ key: 'speed', label: 'Speed', color: CHART_COLORS.primary, accessor: (s) => s.speed }]} unit="km/h" height={260} />
              </Panel>
              <Panel title="Thermal load">
                <TelemetryChart data={samples} series={[{ key: 'motorTemp', label: 'Motor', color: CHART_COLORS.warning, accessor: (s) => s.motorTemp }, { key: 'batteryTemp', label: 'Battery', color: CHART_COLORS.info, accessor: (s) => s.batteryTemp }]} unit="°C" height={260} />
              </Panel>
            </div>
            <DerivedPanel frame={frame} />
            <Panel className="mt-4" title="Session context">
              <div className="grid gap-3 sm:grid-cols-3">
                <ContextValue label="Current lap" value={String(lap.lap)} />
                <ContextValue label="Lap elapsed" value={formatLapTime(lap.currentLapTime)} />
                <ContextValue label="Samples available" value={String(history.length)} />
              </div>
            </Panel>
          </>
        )
      ) : (
        <div className="space-y-4">
          <Panel title="Select a recorded session" icon={Database}>
            {backendStatus !== 'online' ? (
              <p className="p-2 text-sm text-muted-foreground">Backend not reachable — session analytics needs the FastAPI backend running.</p>
            ) : sessions.length === 0 ? (
              <p className="p-2 text-sm text-muted-foreground">No recorded sessions with frames yet. Start a session with data recording enabled, then come back here.</p>
            ) : (
              <div className="grid gap-3 sm:grid-cols-2">
                <SessionPicker label="Session" sessions={sessions} value={selectedId} onChange={setSelectedId} />
                <SessionPicker label="Compare against (optional)" sessions={sessions.filter((s) => s.id !== selectedId)} value={compareId} onChange={setCompareId} allowNone />
              </div>
            )}
          </Panel>

          {loading && <div className="flex h-32 items-center justify-center text-sm text-muted-foreground"><RefreshCw className="mr-2 size-4 animate-spin" /> Loading session frames…</div>}

          {!loading && analytics && (
            <>
              <Stagger className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
                <StaggerItem><Metric icon={Gauge} label="Max speed" value={fmtMetric(analytics.performance.maxSpeed, 1, 'km/h')} /></StaggerItem>
                <StaggerItem><Metric icon={Activity} label="Avg speed" value={fmtMetric(analytics.performance.avgSpeed, 1, 'km/h')} /></StaggerItem>
                <StaggerItem><Metric icon={Thermometer} label="Max motor temp" value={fmtMetric(analytics.thermal.maxMotorTemp, 1, '°C')} /></StaggerItem>
                <StaggerItem><Metric icon={Battery} label="Battery used" value={fmtMetric(analytics.power.batteryUsedPct, 1, '%')} /></StaggerItem>
              </Stagger>

              <div className="grid gap-4 lg:grid-cols-2">
                <Panel title="Speed trace"><TelemetryChart data={chartFrames} series={[{ key: 'speed', label: 'Speed', color: CHART_COLORS.primary, accessor: (s) => s.speed }]} unit="km/h" height={240} window={chartFrames.length} /></Panel>
                <Panel title="Battery voltage"><TelemetryChart data={chartFrames} series={[{ key: 'voltage', label: 'Voltage', color: CHART_COLORS.info, accessor: (s) => s.voltage }]} unit="V" height={240} window={chartFrames.length} /></Panel>
                <Panel title="Current draw"><TelemetryChart data={chartFrames} series={[{ key: 'current', label: 'Current', color: CHART_COLORS.warning, accessor: (s) => s.current }]} unit="A" height={240} window={chartFrames.length} /></Panel>
                <Panel title="Temperatures"><TelemetryChart data={chartFrames} series={[{ key: 'motorTemp', label: 'Motor', color: CHART_COLORS.warning, accessor: (s) => s.motorTemp }, { key: 'batteryTemp', label: 'Battery', color: CHART_COLORS.info, accessor: (s) => s.batteryTemp }]} unit="°C" height={240} window={chartFrames.length} /></Panel>
              </div>

              <MetricGroup title="Performance">
                <ContextValue label="Max speed" value={fmtMetric(analytics.performance.maxSpeed, 1, 'km/h')} />
                <ContextValue label="Avg speed" value={fmtMetric(analytics.performance.avgSpeed, 1, 'km/h')} />
                <ContextValue label="Distance" value={fmtMetric(analytics.performance.distanceM, 0, 'm')} />
                <ContextValue label="Duration" value={fmtMetric(analytics.performance.durationS, 1, 's')} />
                <ContextValue label="Lap count" value={fmtMetric(analytics.performance.lapCount, 0)} />
                <ContextValue label="Best lap" value={fmtMetric(analytics.performance.bestLapS, 3, 's')} />
              </MetricGroup>

              <MetricGroup title="Power">
                <ContextValue label="Avg voltage" value={fmtMetric(analytics.power.avgVoltage, 2, 'V')} />
                <ContextValue label="Min voltage" value={fmtMetric(analytics.power.minVoltage, 2, 'V')} />
                <ContextValue label="Max current" value={fmtMetric(analytics.power.maxCurrent, 2, 'A')} />
                <ContextValue label="Avg current" value={fmtMetric(analytics.power.avgCurrent, 2, 'A')} />
                <ContextValue label="Energy used" value={fmtMetric(analytics.power.energyWh, 2, 'Wh')} />
                <ContextValue label="Efficiency" value={fmtMetric(analytics.power.efficiencyWhPerKm, 2, 'Wh/km')} />
              </MetricGroup>

              <MetricGroup title="Thermal">
                <ContextValue label="Max motor temp" value={fmtMetric(analytics.thermal.maxMotorTemp, 1, '°C')} />
                <ContextValue label="Max battery temp" value={fmtMetric(analytics.thermal.maxBatteryTemp, 1, '°C')} />
                <ContextValue label="Avg motor temp" value={fmtMetric(analytics.thermal.avgMotorTemp, 1, '°C')} />
                <ContextValue label="Avg battery temp" value={fmtMetric(analytics.thermal.avgBatteryTemp, 1, '°C')} />
              </MetricGroup>

              <MetricGroup title="Dynamics">
                <ContextValue label="Peak lateral g" value={fmtMetric(analytics.dynamics.peakLateralG, 2, 'g')} />
                <ContextValue label="Peak longitudinal g" value={fmtMetric(analytics.dynamics.peakLongitudinalG, 2, 'g')} />
                <ContextValue label="Peak yaw rate" value={fmtMetric(analytics.dynamics.peakYawRate, 1, '°/s')} />
                <ContextValue label="Max roll" value={fmtMetric(analytics.dynamics.maxRoll, 1, '°')} />
                <ContextValue label="Max pitch" value={fmtMetric(analytics.dynamics.maxPitch, 1, '°')} />
              </MetricGroup>

              <MetricGroup title="Aerodynamics">
                <ContextValue label="Avg FL angle" value={fmtMetric(analytics.aero.avgFrontLeft, 1, '°')} />
                <ContextValue label="Avg FR angle" value={fmtMetric(analytics.aero.avgFrontRight, 1, '°')} />
                <ContextValue label="Avg RL angle" value={fmtMetric(analytics.aero.avgRearLeft, 1, '°')} />
                <ContextValue label="Avg RR angle" value={fmtMetric(analytics.aero.avgRearRight, 1, '°')} />
                <ContextValue label="Max wing angle" value={fmtMetric(analytics.aero.maxWingAngle, 1, '°')} />
                <ContextValue label="Wing activity" value={fmtMetric(analytics.aero.wingActivity, 2, '°/frame')} />
              </MetricGroup>

              <MetricGroup title="Reliability">
                <ContextValue label="Faults" value={fmtMetric(analytics.reliability.faultCount, 0)} />
                <ContextValue label="Warnings" value={fmtMetric(analytics.reliability.warningCount, 0)} />
                <ContextValue label="Critical events" value={fmtMetric(analytics.reliability.criticalCount, 0)} />
                <ContextValue label="Comms events" value={fmtMetric(analytics.reliability.communicationEvents, 0)} />
              </MetricGroup>

              <MetricGroup title="AI recommendations">
                <ContextValue label="Recommendations" value={fmtMetric(analytics.ai.recommendationCount, 0)} />
                <ContextValue label="Avg confidence" value={fmtMetric(analytics.ai.avgConfidence, 2)} />
                <ContextValue label="Categories" value={Object.keys(analytics.ai.categoryCounts).length ? Object.entries(analytics.ai.categoryCounts).map(([k, v]) => `${k}: ${v}`).join(', ') : 'N/A'} />
              </MetricGroup>

              {compareAnalytics && (
                <Panel title="Session comparison" icon={GitCompare}>
                  <ComparisonTable a={analytics} b={compareAnalytics} labelA={selectedId ?? 'A'} labelB={compareId ?? 'B'} />
                </Panel>
              )}
            </>
          )}

          {!loading && selectedId && !analytics && (
            <div className="flex h-32 items-center justify-center text-sm text-muted-foreground">Could not load frames for this session.</div>
          )}
        </div>
      )}
    </div>
  )
}

function SessionPicker({ label, sessions, value, onChange, allowNone }: { label: string; sessions: BackendSession[]; value: string | null; onChange: (id: string | null) => void; allowNone?: boolean }) {
  return (
    <label className="block">
      <span className="mb-1.5 block text-xs text-muted-foreground">{label}</span>
      <select
        value={value ?? ''}
        onChange={(e) => onChange(e.target.value || null)}
        className="h-9 w-full rounded-md border border-border bg-background px-3 text-sm outline-none focus:border-primary/60"
      >
        <option value="">{allowNone ? 'None' : 'Select a session…'}</option>
        {sessions.map((s) => (
          <option key={s.id} value={s.id}>{s.mode} · {s.id} · {s.frameCount} frames</option>
        ))}
      </select>
    </label>
  )
}

function MetricGroup({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <Panel title={title}>
      <div className="grid gap-3 sm:grid-cols-3 lg:grid-cols-6">{children}</div>
    </Panel>
  )
}

function ComparisonTable({ a, b, labelA, labelB }: { a: SessionAnalytics; b: SessionAnalytics; labelA: string; labelB: string }) {
  const rows: [string, string, string][] = [
    ['Best lap', fmtMetric(a.performance.bestLapS, 3, 's'), fmtMetric(b.performance.bestLapS, 3, 's')],
    ['Avg speed', fmtMetric(a.performance.avgSpeed, 1, 'km/h'), fmtMetric(b.performance.avgSpeed, 1, 'km/h')],
    ['Max speed', fmtMetric(a.performance.maxSpeed, 1, 'km/h'), fmtMetric(b.performance.maxSpeed, 1, 'km/h')],
    ['Distance', fmtMetric(a.performance.distanceM, 0, 'm'), fmtMetric(b.performance.distanceM, 0, 'm')],
    ['Energy', fmtMetric(a.power.energyWh, 2, 'Wh'), fmtMetric(b.power.energyWh, 2, 'Wh')],
    ['Max motor temp', fmtMetric(a.thermal.maxMotorTemp, 1, '°C'), fmtMetric(b.thermal.maxMotorTemp, 1, '°C')],
    ['Max battery temp', fmtMetric(a.thermal.maxBatteryTemp, 1, '°C'), fmtMetric(b.thermal.maxBatteryTemp, 1, '°C')],
    ['Max current', fmtMetric(a.power.maxCurrent, 2, 'A'), fmtMetric(b.power.maxCurrent, 2, 'A')],
    ['Faults', fmtMetric(a.reliability.faultCount, 0), fmtMetric(b.reliability.faultCount, 0)],
    ['Wing activity', fmtMetric(a.aero.wingActivity, 2, '°/frame'), fmtMetric(b.aero.wingActivity, 2, '°/frame')],
  ]
  return (
    <table className="w-full text-sm">
      <thead>
        <tr className="border-b border-border text-left text-[0.7rem] uppercase tracking-widest text-muted-foreground">
          <th className="py-2 font-medium">Metric</th>
          <th className="py-2 font-medium">{labelA}</th>
          <th className="py-2 font-medium">{labelB}</th>
        </tr>
      </thead>
      <tbody>
        {rows.map(([label, va, vb]) => (
          <tr key={label} className="border-b border-border/60 last:border-0">
            <td className="py-2 text-muted-foreground">{label}</td>
            <td className="tabular py-2 font-medium">{va}</td>
            <td className="tabular py-2 font-medium">{vb}</td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}

function Metric({ icon: Icon, label, value }: { icon: typeof Gauge; label: string; value: string }) {
  return <Panel bodyClassName="flex items-center gap-3 p-4"><div className="flex size-10 items-center justify-center rounded-lg bg-primary/10 text-primary"><Icon className="size-5" /></div><div><p className="text-xs text-muted-foreground">{label}</p><p className="tabular text-lg font-semibold">{value}</p></div></Panel>
}

/* Values the vehicle gateway derives from sensors already on the car (CAN
 * 0x141 plus integration on the Pi) — see SOFTWARE/Documentation/DerivedMetrics.md.
 * Everything shows an em dash rather than a placeholder number when the car is
 * not connected, so nothing here can be mistaken for measured data. */
type DerivedBlock = {
  powerW?: number
  energyWh?: number
  whPerKm?: number
  socCoulombPct?: number
  packResistanceOhm?: number
  slipRatio?: number
  secondsToMotorLimit?: number | null
  secondsToBatteryLimit?: number | null
}

function DerivedPanel({ frame }: { frame: TelemetryFrame | null }) {
  const d = (frame as (TelemetryFrame & { derived?: DerivedBlock }) | null)?.derived
  const num = (v: number | null | undefined, digits: number, unit: string) =>
    v === null || v === undefined || Number.isNaN(v) ? '—' : `${v.toFixed(digits)} ${unit}`
  const seconds = (v: number | null | undefined) =>
    v === null || v === undefined ? 'not trending' : `${Math.round(v)} s`
  return (
    <Panel className="mt-4" title="Derived metrics (measured, no extra sensors)">
      {!d ? (
        <p className="p-2 text-sm text-muted-foreground">
          No derived values in this frame — they arrive from the vehicle gateway (CAN 0x141 and
          on-Pi integration) and are absent in simulation.
        </p>
      ) : (
        <div className="grid gap-3 sm:grid-cols-4">
          <ContextValue label="Power" value={num(d.powerW, 1, 'W')} />
          <ContextValue label="Energy used" value={num(d.energyWh, 3, 'Wh')} />
          <ContextValue label="Consumption" value={num(d.whPerKm, 1, 'Wh/km')} />
          <ContextValue label="SoC (coulomb)" value={num(d.socCoulombPct, 1, '%')} />
          <ContextValue label="Pack resistance" value={num(d.packResistanceOhm, 4, 'Ω')} />
          <ContextValue label="Wheel slip" value={num(d.slipRatio, 3, '')} />
          <ContextValue label="Motor to limit" value={seconds(d.secondsToMotorLimit)} />
          <ContextValue label="Battery to limit" value={seconds(d.secondsToBatteryLimit)} />
        </div>
      )}
    </Panel>
  )
}

function ContextValue({ label, value }: { label: string; value: string }) {
  return <div className="rounded-lg border border-border bg-secondary/40 p-3"><p className="text-xs text-muted-foreground">{label}</p><p className="mt-1 tabular font-semibold">{value}</p></div>
}
