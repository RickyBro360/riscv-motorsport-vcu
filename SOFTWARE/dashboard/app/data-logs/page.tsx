'use client'

import { useEffect, useMemo, useState } from 'react'
import Link from 'next/link'
import { Database, Download, Search, Flag, Timer, Gauge, TriangleAlert, WifiOff, PlayCircle, LineChart } from 'lucide-react'
import { PageHeader } from '@/components/common/page-header'
import { Panel } from '@/components/common/panel'
import { Badge } from '@/components/ui/badge'
import { SESSION_LOGS } from '@/constants/sessions'
import { cn } from '@/lib/utils'
import type { SessionLog } from '@/types/telemetry'
import { downloadFile, rowsToCsv } from '@/lib/export'
import { sessionApi, type BackendSession } from '@/services/api'
import { useBackendStatus } from '@/hooks/use-backend-status'

type StatusFilter = 'All' | SessionLog['status']

const STATUS_VARIANT: Record<SessionLog['status'], 'success' | 'danger' | 'info'> = {
  Completed: 'success',
  Aborted: 'danger',
  Testing: 'info',
}

/** Backend sessions are recorded fact (mode/status/frameCount); the demo
 * constants have narrative fields (name/track/date) the backend doesn't
 * track yet. This maps one onto the SessionLog shape the table already
 * knows how to render, rather than building a second table component. */
function toSessionLog(s: BackendSession): SessionLog {
  const durationS = s.endedAt && s.startedAt ? s.endedAt - s.startedAt : null
  const statusMap: Record<BackendSession['status'], SessionLog['status']> = {
    idle: 'Testing', running: 'Testing', paused: 'Testing', completed: 'Completed', aborted: 'Aborted',
  }
  return {
    id: s.id,
    name: `${s.mode[0].toUpperCase()}${s.mode.slice(1)} session`,
    track: s.track ?? 'Unspecified',
    date: new Date(s.createdAt * 1000).toLocaleString(),
    duration: durationS ? `${Math.floor(durationS / 60)}m ${Math.round(durationS % 60)}s` : '—',
    laps: s.lapCount,
    bestLap: s.bestLapS ? s.bestLapS.toFixed(3) : '—',
    topSpeed: 0,
    avgSpeed: 0,
    faults: 0,
    status: statusMap[s.status],
  }
}

export default function DataLogsPage() {
  const [query, setQuery] = useState('')
  const [status, setStatus] = useState<StatusFilter>('All')
  const [selected, setSelected] = useState<SessionLog | null>(null)
  const backendStatus = useBackendStatus()
  const [backendSessions, setBackendSessions] = useState<SessionLog[] | null>(null)

  useEffect(() => {
    if (backendStatus !== 'online') return
    let cancelled = false
    sessionApi
      .list()
      .then((sessions) => { if (!cancelled) setBackendSessions(sessions.map(toSessionLog)) })
      .catch(() => { if (!cancelled) setBackendSessions(null) })
    return () => { cancelled = true }
  }, [backendStatus])

  // Real recorded sessions when the backend has them; otherwise the local
  // demo dataset, clearly labelled below so nobody mistakes it for a real
  // flight-recorder archive (Phase 9 requires the backend be authoritative,
  // not this constant — see constants/sessions.ts for why it still exists).
  const usingLiveData = backendStatus === 'online' && !!backendSessions?.length
  const sourceLogs = usingLiveData ? backendSessions! : SESSION_LOGS

  const filtered = useMemo(() => {
    return sourceLogs.filter((s) => {
      const matchesQuery =
        s.name.toLowerCase().includes(query.toLowerCase()) ||
        s.track.toLowerCase().includes(query.toLowerCase()) ||
        s.id.toLowerCase().includes(query.toLowerCase())
      const matchesStatus = status === 'All' || s.status === status
      return matchesQuery && matchesStatus
    })
  }, [query, status, sourceLogs])

  const totals = useMemo(
    () => ({
      sessions: sourceLogs.length,
      laps: sourceLogs.reduce((a, s) => a + s.laps, 0),
      faults: sourceLogs.reduce((a, s) => a + s.faults, 0),
    }),
    [sourceLogs],
  )

  return (
    <div>
      <PageHeader
        title="Data Logs"
        description="Archived telemetry sessions from the VCU flight recorder. Search, filter and export any run for offline analysis."
        actions={
          usingLiveData ? (
            <Badge variant="success" className="gap-1.5 px-2.5 py-1">
              <Database className="size-3.5" /> {totals.sessions} recorded sessions
            </Badge>
          ) : (
            <Badge variant="warning" className="gap-1.5 px-2.5 py-1">
              <WifiOff className="size-3.5" /> Backend offline — showing demo data
            </Badge>
          )
        }
      />

      <div className="mb-4 grid grid-cols-3 gap-4">
        <Summary icon={Database} label="Sessions" value={String(totals.sessions)} />
        <Summary icon={Flag} label="Total Laps" value={String(totals.laps)} />
        <Summary icon={TriangleAlert} label="Logged Faults" value={String(totals.faults)} />
      </div>

      <Panel bodyClassName="p-0">
        {/* toolbar */}
        <div className="flex flex-col gap-3 border-b border-border p-4 sm:flex-row sm:items-center sm:justify-between">
          <div className="relative w-full sm:max-w-xs">
            <Search className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
            <input
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Search sessions…"
              className="h-9 w-full rounded-md border border-border bg-background pl-9 pr-3 text-sm outline-none placeholder:text-muted-foreground focus:border-primary/60"
            />
          </div>
          <div className="flex items-center gap-1">
            {(['All', 'Completed', 'Aborted', 'Testing'] as StatusFilter[]).map((f) => (
              <button
                key={f}
                onClick={() => setStatus(f)}
                className={cn(
                  'rounded-md px-3 py-1.5 text-xs font-medium transition-colors',
                  status === f
                    ? 'bg-primary text-primary-foreground'
                    : 'text-muted-foreground hover:text-foreground',
                )}
              >
                {f}
              </button>
            ))}
          </div>
        </div>

        {/* table */}
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-border text-left text-[0.7rem] uppercase tracking-widest text-muted-foreground">
                <th className="px-4 py-3 font-medium">Session</th>
                <th className="px-4 py-3 font-medium">Track</th>
                <th className="hidden px-4 py-3 font-medium md:table-cell">Date</th>
                <th className="px-4 py-3 font-medium">Laps</th>
                <th className="hidden px-4 py-3 font-medium lg:table-cell">Best Lap</th>
                <th className="hidden px-4 py-3 font-medium lg:table-cell">Top Spd</th>
                <th className="px-4 py-3 font-medium">Status</th>
                <th className="px-4 py-3 text-right font-medium">Export</th>
              </tr>
            </thead>
            <tbody>
              {filtered.map((s) => (
                <tr
                  key={s.id}
                  onClick={() => setSelected(s)}
                  className="cursor-pointer border-b border-border/60 transition-colors last:border-0 hover:bg-secondary/40"
                >
                  <td className="px-4 py-3">
                    <p className="font-medium text-foreground">{s.name}</p>
                    <span className="tabular text-xs text-muted-foreground">{s.id}</span>
                  </td>
                  <td className="px-4 py-3 text-muted-foreground">{s.track}</td>
                  <td className="tabular hidden px-4 py-3 text-muted-foreground md:table-cell">{s.date}</td>
                  <td className="tabular px-4 py-3">{s.laps}</td>
                  <td className="tabular hidden px-4 py-3 lg:table-cell">{s.bestLap}</td>
                  <td className="tabular hidden px-4 py-3 lg:table-cell">{s.topSpeed.toFixed(1)}</td>
                  <td className="px-4 py-3">
                    <Badge variant={STATUS_VARIANT[s.status]}>{s.status}</Badge>
                  </td>
                  <td className="px-4 py-3">
                    <div className="flex items-center justify-end gap-1">
                      {usingLiveData && (
                        <>
                          <Link
                            href={`/replay?session=${s.id}`}
                            onClick={(e) => e.stopPropagation()}
                            className="inline-flex size-8 items-center justify-center rounded-md text-muted-foreground transition-colors hover:bg-secondary hover:text-foreground"
                            aria-label={`Open ${s.name} in Replay`}
                            title="Open in Replay"
                          >
                            <PlayCircle className="size-4" />
                          </Link>
                          <Link
                            href={`/analytics?session=${s.id}`}
                            onClick={(e) => e.stopPropagation()}
                            className="inline-flex size-8 items-center justify-center rounded-md text-muted-foreground transition-colors hover:bg-secondary hover:text-foreground"
                            aria-label={`Open ${s.name} in Analytics`}
                            title="Open in Analytics"
                          >
                            <LineChart className="size-4" />
                          </Link>
                        </>
                      )}
                      <button
                        onClick={(e) => {
                          e.stopPropagation()
                          if (usingLiveData) {
                            window.open(sessionApi.exportCsvUrl(s.id), '_blank')
                          } else {
                            downloadFile(`${s.id}.csv`, rowsToCsv([s], ['id', 'name', 'track', 'date', 'duration', 'laps', 'bestLap', 'topSpeed', 'avgSpeed', 'faults', 'status']))
                          }
                        }}
                        className="inline-flex size-8 items-center justify-center rounded-md text-muted-foreground transition-colors hover:bg-secondary hover:text-foreground"
                        aria-label={`Export ${s.name}`}
                        title="Export CSV"
                      >
                        <Download className="size-4" />
                      </button>
                    </div>
                  </td>
                </tr>
              ))}
              {filtered.length === 0 && (
                <tr>
                  <td colSpan={8} className="px-4 py-12 text-center text-sm text-muted-foreground">
                    No sessions match your filters.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </Panel>

      {selected && <DetailDrawer session={selected} live={usingLiveData} onClose={() => setSelected(null)} />}
    </div>
  )
}

function Summary({ icon: Icon, label, value }: { icon: typeof Database; label: string; value: string }) {
  return (
    <Panel bodyClassName="flex items-center gap-3 p-4">
      <div className="flex size-10 items-center justify-center rounded-lg bg-primary/10 text-primary">
        <Icon className="size-5" />
      </div>
      <div>
        <p className="tabular text-xl font-bold">{value}</p>
        <p className="text-xs text-muted-foreground">{label}</p>
      </div>
    </Panel>
  )
}

function DetailDrawer({ session, live, onClose }: { session: SessionLog; live: boolean; onClose: () => void }) {
  const exportSession = () =>
    live
      ? window.open(sessionApi.exportCsvUrl(session.id), '_blank')
      : downloadFile(`${session.id}.csv`, rowsToCsv([session], ['id', 'name', 'track', 'date', 'duration', 'laps', 'bestLap', 'topSpeed', 'avgSpeed', 'faults', 'status']))

  const rows = [
    { icon: Flag, label: 'Laps completed', value: String(session.laps) },
    { icon: Timer, label: 'Best lap', value: session.bestLap },
    { icon: Timer, label: 'Duration', value: session.duration },
    { icon: Gauge, label: 'Top speed', value: `${session.topSpeed.toFixed(1)} km/h` },
    { icon: Gauge, label: 'Average speed', value: `${session.avgSpeed.toFixed(1)} km/h` },
    { icon: TriangleAlert, label: 'Faults logged', value: String(session.faults) },
  ]
  return (
    <div className="fixed inset-0 z-50 flex justify-end" role="dialog" aria-modal="true">
      <div className="absolute inset-0 bg-background/70 backdrop-blur-sm" onClick={onClose} />
      <div className="glass relative flex h-full w-full max-w-md flex-col border-l border-border p-6">
        <div className="mb-6 flex items-start justify-between">
          <div>
            <Badge variant={STATUS_VARIANT[session.status]} className="mb-2">
              {session.status}
            </Badge>
            <h2 className="text-lg font-semibold">{session.name}</h2>
            <p className="tabular text-xs text-muted-foreground">
              {session.id} · {session.track} · {session.date}
            </p>
          </div>
          <button
            onClick={onClose}
            className="rounded-md border border-border px-2.5 py-1 text-xs text-muted-foreground hover:text-foreground"
          >
            Close
          </button>
        </div>
        <ul className="space-y-2.5">
          {rows.map((r) => (
            <li
              key={r.label}
              className="flex items-center justify-between rounded-lg border border-border bg-secondary/40 px-4 py-3"
            >
              <span className="flex items-center gap-2.5 text-sm text-muted-foreground">
                <r.icon className="size-4" /> {r.label}
              </span>
              <span className="tabular font-semibold">{r.value}</span>
            </li>
          ))}
        </ul>
        {live && (
          <div className="mt-6 grid grid-cols-2 gap-2">
            <Link href={`/replay?session=${session.id}`} className="inline-flex items-center justify-center gap-2 rounded-md border border-border px-4 py-2.5 text-sm font-semibold text-foreground transition-colors hover:bg-secondary">
              <PlayCircle className="size-4" /> Open in Replay
            </Link>
            <Link href={`/analytics?session=${session.id}`} className="inline-flex items-center justify-center gap-2 rounded-md border border-border px-4 py-2.5 text-sm font-semibold text-foreground transition-colors hover:bg-secondary">
              <LineChart className="size-4" /> Open in Analytics
            </Link>
          </div>
        )}
        <button onClick={exportSession} className="mt-2 inline-flex w-full items-center justify-center gap-2 rounded-md bg-primary px-4 py-2.5 text-sm font-semibold text-primary-foreground transition-opacity hover:opacity-90">
          <Download className="size-4" /> Export session (.csv)
        </button>
      </div>
    </div>
  )
}
