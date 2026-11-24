'use client'

import { usePathname } from 'next/navigation'
import { useState } from 'react'
import { Menu, BatteryMedium, Timer, Pause, Play, User, OctagonX } from 'lucide-react'
import { NAV_ITEMS, TOTAL_LAPS } from '@/constants'
import { useTelemetry } from '@/contexts/telemetry-context'
import { StatusDot } from '@/components/common/status-dot'
import { LINK_LABEL, formatLapTime, linkToLevel } from '@/lib/format'
import { useClock } from '@/hooks/use-clock'
import { cn } from '@/lib/utils'
import { vehicleApi } from '@/services/api'

export function TopBar({ onMenu }: { onMenu: () => void }) {
  const pathname = usePathname()
  const { frame, link, mode, setMode, lap, running, toggleRunning, simulateCarCommand } = useTelemetry()
  const clock = useClock()
  const [estopMsg, setEstopMsg] = useState<string | null>(null)
  const driveMode = frame?.vcu?.driveMode ?? null
  // FW 2.2: one-click E-STOP on every page. It is a request like any other, but the VCU always accepts it.
  const estop = async () => {
    if (mode === 'replay') return
    if (mode !== 'live') { simulateCarCommand(8, 1); setEstopMsg('E-STOP (simulation)'); return }
    try { const c = await vehicleApi.estop('trigger'); setEstopMsg(`E-STOP sent (#${c.id})`) }
    catch (e) { setEstopMsg(`E-STOP failed: ${(e as Error).message.slice(0, 60)} — use the driver's E key or the console`) }
    window.setTimeout(() => setEstopMsg(null), 6000)
  }

  const title = NAV_ITEMS.find((n) => n.href === pathname)?.label ?? 'Dashboard'
  // Stage 2d: on the 12 V bench adapter a state-of-charge % is meaningless — show
  // the supply voltage the INA260 measures instead (VCU derates below 10.5 V).
  const volts = frame?.voltage ?? 0
  const batteryLevel = volts > 0 && volts < 9.9 ? 'critical' : volts > 0 && volts < 10.5 ? 'warning' : 'ok'
  const connLevel = linkToLevel(link)

  return (
    <header className="sticky top-0 z-30 flex h-16 items-center gap-3 border-b border-border bg-background/80 px-4 backdrop-blur-md">
      <button
        onClick={onMenu}
        className="rounded-md p-1.5 text-muted-foreground hover:bg-secondary lg:hidden"
        aria-label="Open menu"
      >
        <Menu className="size-5" />
      </button>

      <div className="flex flex-col">
        <h1 className="text-base font-semibold tracking-tight">{title}</h1>
        <span className="hidden text-[0.7rem] uppercase tracking-widest text-muted-foreground sm:block">
          Pit Wall Telemetry
        </span>
      </div>

      <div className="ml-auto flex items-center gap-2 sm:gap-3">
        <button
          onClick={toggleRunning}
          aria-pressed={running}
          className={cn(
            'flex items-center gap-1.5 rounded-lg border px-2.5 py-1.5 text-xs font-medium transition-colors',
            running
              ? 'border-border text-muted-foreground hover:bg-secondary'
              : 'border-primary/40 bg-primary/10 text-primary',
          )}
        >
          {running ? <Pause className="size-3.5" /> : <Play className="size-3.5" />}
          <span className="hidden sm:inline">{running ? 'Running' : 'Paused'}</span>
        </button>

        {/* Explicit mode selection — the dashboard never silently switches
            between a real backend connection and the local simulator, it
            only does so when the user picks it here. While mode === 'replay'
            this button exits back to simulation rather than toggling to
            live, so leaving Replay never silently opens a WebSocket. */}
        <button
          onClick={() => setMode(mode === 'live' ? 'simulation' : mode === 'replay' ? 'simulation' : 'live')}
          className="hidden items-center gap-1.5 rounded-lg border border-border px-2.5 py-1.5 text-xs font-medium text-muted-foreground transition-colors hover:bg-secondary sm:flex"
          title={mode === 'replay' ? 'Exit replay back to the local simulator' : 'Toggle between the FastAPI WebSocket link and the local simulator'}
        >
          {mode === 'live' ? 'Live Mode' : mode === 'replay' ? 'Replay Mode' : 'Simulation Mode'}
        </button>

        {(frame as { demoFeed?: boolean } | null)?.demoFeed && (
          <div className="rounded-lg border border-warning/50 bg-warning/10 px-2.5 py-1.5 text-xs font-semibold text-warning" title="Frames come from SOFTWARE/backend/tools/demo_car.py, not from the car">
            DEMO FEED — not the car
          </div>
        )}
        {driveMode && (
          <div className={cn('hidden rounded-lg border px-2.5 py-1.5 text-xs font-semibold sm:block',
            driveMode === 'dyno' ? 'border-warning/50 bg-warning/10 text-warning' : 'border-info/50 bg-info/10 text-info')}
            title={driveMode === 'dyno' ? 'DYNO mode: the car must be on the rollers — runs can be started from the dashboard' : 'TRACK mode: only a driver holding ARM can move the car'}>
            {driveMode.toUpperCase()}{frame?.vcuState === 'EMERGENCY_STOP' ? ' · E-STOP' : ''}
          </div>
        )}
        <button onClick={estop} disabled={mode === 'replay'} title={estopMsg ?? 'Emergency stop: latches on the VCU (motor off, flaps/steering failsafe)'}
          className="flex items-center gap-1.5 rounded-lg bg-red-600 px-2.5 py-1.5 text-xs font-bold text-white hover:bg-red-700 disabled:opacity-40">
          <OctagonX className="size-3.5" /> <span className="hidden sm:inline">{estopMsg ? 'SENT' : 'E-STOP'}</span>
        </button>
        <div className="hidden items-center gap-2 rounded-lg border border-border px-2.5 py-1.5 md:flex">
          <StatusDot level={connLevel} />
          <span className="text-xs font-medium">{LINK_LABEL[link]}</span>
        </div>

        <div className="hidden items-center gap-1.5 rounded-lg border border-border px-2.5 py-1.5 sm:flex">
          <BatteryMedium
            className={cn(
              'size-4',
              batteryLevel === 'critical'
                ? 'text-danger'
                : batteryLevel === 'warning'
                  ? 'text-warning'
                  : 'text-success',
            )}
          />
          <span className="tabular text-xs font-medium">{volts ? `${volts.toFixed(2)} V` : '—'}</span>
        </div>

        <div className="hidden items-center gap-1.5 rounded-lg border border-border px-2.5 py-1.5 lg:flex">
          <Timer className="size-4 text-muted-foreground" />
          <span className="tabular text-xs font-medium">
            {frame?.track ? `L${lap.lap}` : `L${lap.lap}/${TOTAL_LAPS}`}
          </span>
          <span className="tabular text-xs text-muted-foreground">
            {formatLapTime(lap.currentLapTime)}
          </span>
        </div>

        <div className="tabular hidden text-xs font-medium text-muted-foreground xl:block">
          {clock}
        </div>

        <div className="flex size-8 items-center justify-center rounded-full border border-border bg-secondary">
          <User className="size-4 text-muted-foreground" />
        </div>
      </div>
    </header>
  )
}
