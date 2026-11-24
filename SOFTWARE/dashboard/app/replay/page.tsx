'use client'

import { useEffect, useState, useCallback } from 'react'
import {
  Play,
  Pause,
  Square,
  CircleDot,
  Camera,
  Gauge,
  Thermometer,
  Battery,
  Wind,
  FolderOpen,
} from 'lucide-react'
import { useTelemetry } from '@/contexts/telemetry-context'
import { PageHeader } from '@/components/common/page-header'
import { Panel } from '@/components/common/panel'
import { TelemetryChart } from '@/components/common/telemetry-chart'
import { Badge } from '@/components/ui/badge'
import { StatusDot } from '@/components/common/status-dot'
import { cn } from '@/lib/utils'
import { avgWing, replayStatusLabel } from '@/lib/format'
import type { ReplayRate } from '@/types/telemetry'
import { sessionApi, type BackendSession } from '@/services/api'
import { useBackendStatus } from '@/hooks/use-backend-status'

const C = {
  primary: 'var(--color-primary)',
  info: 'var(--color-info)',
  success: 'var(--color-success)',
}

const RATES: ReplayRate[] = [0.25, 0.5, 1, 2, 4, 10]

export default function ReplayPage() {
  const { frame, history, replay, replayFrames, loadReplaySession, loadReplayCapture, playReplay, pauseReplay, stopReplay, clearReplay, seekReplay, setReplayRate } = useTelemetry()

  const backendStatus = useBackendStatus()
  const [recordedSessions, setRecordedSessions] = useState<BackendSession[]>([])
  const [loadingSession, setLoadingSession] = useState<string | null>(null)

  useEffect(() => {
    if (backendStatus !== 'online') { setRecordedSessions([]); return }
    let cancelled = false
    sessionApi
      .list()
      .then((sessions) => { if (!cancelled) setRecordedSessions(sessions.filter((s) => s.frameCount > 0)) })
      .catch(() => { if (!cancelled) setRecordedSessions([]) })
    return () => { cancelled = true }
  }, [backendStatus])

  // Deep link from Data Logs: /replay?session=SESS-XXXX
  useEffect(() => {
    if (typeof window === 'undefined') return
    const params = new URLSearchParams(window.location.search)
    const sid = params.get('session')
    if (sid) {
      setLoadingSession(sid)
      loadReplaySession(sid).finally(() => setLoadingSession(null))
    }
  }, [])

  const capture = useCallback(() => {
    if (history.length < 5) return
    loadReplayCapture([...history])
  }, [history, loadReplayCapture])

  const loadRecordedSession = useCallback(async (sessionId: string) => {
    setLoadingSession(sessionId)
    try {
      await loadReplaySession(sessionId)
    } finally {
      setLoadingSession(null)
    }
  }, [loadReplaySession])

  const hasSession = replay.sessionId !== null && replay.totalFrames > 0
  const cur = frame
  const durationS = replay.endTimestamp > replay.startTimestamp ? (replay.endTimestamp - replay.startTimestamp) / 1000 : 0
  const elapsedS = replay.currentTimestamp > replay.startTimestamp ? (replay.currentTimestamp - replay.startTimestamp) / 1000 : 0
  const pct = durationS > 0 ? (elapsedS / durationS) * 100 : 0
  const scrubbed = replayFrames.slice(0, replay.index + 1)

  if (!hasSession) {
    return (
      <div>
        <PageHeader
          title="Session Replay"
          description="Load an authoritative recorded session from the backend, or capture the rolling telemetry buffer, then play, pause, seek and scrub through it."
          actions={
            <Badge variant="outline" className="gap-1.5 px-2.5 py-1">
              <StatusDot level="ok" /> {history.length} frames buffered
            </Badge>
          }
        />
        <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
          <Panel className="flex min-h-[420px] flex-col items-center justify-center text-center" bodyClassName="flex flex-col items-center justify-center gap-4 p-10">
            <div className="flex size-16 items-center justify-center rounded-full bg-primary/10">
              <Camera className="size-7 text-primary" />
            </div>
            <div>
              <h2 className="text-lg font-semibold">Capture the live buffer</h2>
              <p className="mt-1 max-w-md text-sm text-muted-foreground text-pretty">
                Snapshot the current rolling in-memory window and scrub through it locally — this
                does not need the backend, but also won&apos;t survive a page refresh.
              </p>
            </div>
            <button
              onClick={capture}
              disabled={history.length < 5}
              className="inline-flex items-center gap-2 rounded-md bg-primary px-4 py-2.5 text-sm font-semibold text-primary-foreground transition-opacity hover:opacity-90 disabled:opacity-40"
            >
              <CircleDot className="size-4" /> Capture buffer
            </button>
          </Panel>

          <Panel title="Load a Recorded Session" icon={FolderOpen}>
            {backendStatus !== 'online' ? (
              <p className="p-2 text-sm text-muted-foreground">
                Backend not reachable — recorded-session replay needs the FastAPI backend running
                (see NEXT_PUBLIC_API_BASE_URL). The in-memory capture on the left still works offline.
              </p>
            ) : recordedSessions.length === 0 ? (
              <p className="p-2 text-sm text-muted-foreground">
                No recorded sessions with frames yet. Start a session on the Dashboard with data
                recording enabled, then come back here.
              </p>
            ) : (
              <ul className="space-y-2">
                {recordedSessions.map((s) => (
                  <li key={s.id}>
                    <button
                      onClick={() => loadRecordedSession(s.id)}
                      disabled={loadingSession === s.id}
                      className="flex w-full items-center justify-between rounded-lg border border-border bg-secondary/40 px-4 py-3 text-left transition-colors hover:bg-secondary disabled:opacity-50"
                    >
                      <span>
                        <span className="block font-medium text-foreground">{s.mode} · {s.id}</span>
                        <span className="tabular text-xs text-muted-foreground">
                          {s.frameCount} frames · {s.status}
                        </span>
                      </span>
                      <Play className="size-4 text-muted-foreground" />
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </Panel>
        </div>
      </div>
    )
  }

  return (
    <div>
      <PageHeader
        title="Session Replay"
        description="Playback is timestamp-driven from the recorded session and owns the whole dashboard's telemetry state — Digital Twin, Track Map and Diagnostics all follow this same playhead."
        actions={
          <div className="flex items-center gap-2">
            <Badge variant="info" className="gap-1.5 px-2.5 py-1">
              {replayStatusLabel(replay.status)} · Frame {replay.index + 1}/{replay.totalFrames}
            </Badge>
            <button
              onClick={() => { clearReplay() }}
              className="rounded-md border border-border px-3 py-1.5 text-xs text-muted-foreground transition-colors hover:text-foreground"
            >
              New session
            </button>
          </div>
        }
      />

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-3">
        <Panel title="Playhead Readout" icon={Gauge} className="lg:col-span-1">
          <div className="grid grid-cols-2 gap-3">
            <Readout icon={Gauge} label="Speed" value={`${cur?.speed.toFixed(0) ?? '—'}`} unit="km/h" accent />
            <Readout icon={Thermometer} label="Motor" value={`${cur?.motorTemp.toFixed(0) ?? '—'}`} unit="°C" />
            <Readout icon={Battery} label="Battery" value={`${cur?.battery.toFixed(0) ?? '—'}`} unit="%" />
            <Readout icon={Wind} label="Wing" value={`${(cur ? avgWing(cur.wing) : 0).toFixed(0)}`} unit="°" />
            <Readout icon={Gauge} label="Throttle" value={`${cur?.throttle.toFixed(0) ?? '—'}`} unit="%" />
            <Readout icon={Gauge} label="Steering" value={`${cur?.steering.toFixed(0) ?? '—'}`} unit="°" />
          </div>
        </Panel>

        <Panel title="Speed Trace" className="lg:col-span-2" action={<span className="tabular text-sm font-semibold">{cur?.speed.toFixed(1) ?? '—'} <span className="text-xs font-normal text-muted-foreground">km/h</span></span>}>
          <TelemetryChart
            data={scrubbed}
            series={[{ key: 'speed', accessor: (f) => f.speed, color: C.primary, label: 'Speed' }]}
            unit="km/h"
            domain={[0, 70]}
            height={180}
            window={replayFrames.length}
          />
        </Panel>

        <Panel title="Temperatures" className="lg:col-span-2">
          <TelemetryChart
            data={scrubbed}
            series={[
              { key: 'motorTemp', accessor: (f) => f.motorTemp, color: C.primary, label: 'Motor' },
              { key: 'batteryTemp', accessor: (f) => f.batteryTemp, color: C.info, label: 'Battery' },
            ]}
            unit="°C"
            domain={[20, 100]}
            height={180}
            window={replayFrames.length}
          />
        </Panel>

        <Panel title="Inputs" className="lg:col-span-1">
          <TelemetryChart
            data={scrubbed}
            series={[
              { key: 'throttle', accessor: (f) => f.throttle, color: C.success, label: 'Throttle' },
              { key: 'brake', accessor: (f) => f.brake, color: C.primary, label: 'Brake' },
            ]}
            unit="%"
            domain={[0, 100]}
            height={180}
            window={replayFrames.length}
            type="line"
          />
        </Panel>
      </div>

      {/* transport controls */}
      <div className="mt-4">
        <Panel bodyClassName="p-4">
          <div className="flex flex-wrap items-center gap-4">
            <div className="flex items-center gap-1">
              <span className="text-xs text-muted-foreground">Rate</span>
              {RATES.map((rate) => <button key={rate} onClick={() => setReplayRate(rate)} className={cn('rounded px-2 py-1 text-xs', replay.rate === rate ? 'bg-primary text-primary-foreground' : 'text-muted-foreground hover:bg-secondary')}>{rate}×</button>)}
            </div>
            <TransportButton onClick={stopReplay} label="Stop">
              <Square className="size-4" />
            </TransportButton>
            <button
              onClick={() => { if (replay.status === 'playing') pauseReplay(); else playReplay() }}
              className="flex size-11 items-center justify-center rounded-md bg-primary text-primary-foreground transition-opacity hover:opacity-90"
              aria-label={replay.status === 'playing' ? 'Pause' : 'Play'}
            >
              {replay.status === 'playing' ? <Pause className="size-5" /> : <Play className="size-5" />}
            </button>

            <div className="flex-1">
              <input
                type="range"
                min={0}
                max={1000}
                value={Math.round(pct * 10)}
                onChange={(e) => { seekReplay(Number(e.target.value) / 1000) }}
                className="f1-range w-full"
                aria-label="Replay position"
              />
              <div className="mt-1.5 flex justify-between text-[0.7rem] text-muted-foreground">
                <span className="tabular">{elapsedS.toFixed(1)}s</span>
                <span className="tabular">{durationS.toFixed(1)}s</span>
              </div>
            </div>
          </div>
          <div className="mt-3 h-1 overflow-hidden rounded-full bg-secondary">
            <div className="h-full rounded-full bg-primary transition-all duration-100" style={{ width: `${pct}%` }} />
          </div>
        </Panel>
      </div>
    </div>
  )
}

function Readout({
  icon: Icon,
  label,
  value,
  unit,
  accent,
}: {
  icon: typeof Gauge
  label: string
  value: string
  unit: string
  accent?: boolean
}) {
  return (
    <div className="rounded-lg border border-border bg-secondary/40 p-3">
      <div className="mb-1 flex items-center gap-1.5 text-muted-foreground">
        <Icon className="size-3.5" />
        <span className="text-[0.65rem] uppercase tracking-widest">{label}</span>
      </div>
      <p className={cn('tabular text-lg font-semibold', accent && 'text-primary')}>
        {value}
        <span className="ml-1 text-xs font-normal text-muted-foreground">{unit}</span>
      </p>
    </div>
  )
}

function TransportButton({
  onClick,
  label,
  children,
}: {
  onClick: () => void
  label: string
  children: React.ReactNode
}) {
  return (
    <button
      onClick={onClick}
      aria-label={label}
      className="flex size-9 items-center justify-center rounded-md border border-border text-muted-foreground transition-colors hover:text-foreground"
    >
      {children}
    </button>
  )
}
