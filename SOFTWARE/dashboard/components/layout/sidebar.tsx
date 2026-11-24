'use client'

import Link from 'next/link'
import { usePathname } from 'next/navigation'
import { X } from 'lucide-react'
import { cn } from '@/lib/utils'
import { NAV_SECTIONS, PROJECT_NAME, VEHICLE_ID } from '@/constants'
import { useTelemetry } from '@/contexts/telemetry-context'
import { StatusDot } from '@/components/common/status-dot'
import { connToLevel, replayStatusLabel } from '@/lib/format'

export function Sidebar({
  mobileOpen,
  onClose,
}: {
  mobileOpen: boolean
  onClose: () => void
}) {
  const pathname = usePathname()
  const { connection, lap, mode, replay } = useTelemetry()

  return (
    <>
      {mobileOpen && (
        <div
          className="fixed inset-0 z-40 bg-background/70 backdrop-blur-sm lg:hidden"
          onClick={onClose}
          aria-hidden
        />
      )}
      <aside
        className={cn(
          'fixed inset-y-0 left-0 z-50 flex w-64 flex-col border-r border-sidebar-border bg-sidebar transition-transform duration-300 lg:static lg:translate-x-0',
          mobileOpen ? 'translate-x-0' : '-translate-x-full',
        )}
      >
        <div className="flex h-16 items-center justify-between gap-2 border-b border-sidebar-border px-4">
          <div className="flex items-center gap-2.5">
            <div className="flex size-8 items-center justify-center rounded-md bg-primary">
              <span className="text-sm font-bold text-primary-foreground">RE</span>
            </div>
            <div className="flex flex-col">
              <span className="text-sm font-semibold leading-tight text-sidebar-foreground">
                {PROJECT_NAME}
              </span>
              <span className="text-[0.65rem] uppercase tracking-widest text-muted-foreground">
                {VEHICLE_ID}
              </span>
            </div>
          </div>
          <button
            onClick={onClose}
            className="rounded-md p-1 text-muted-foreground hover:bg-sidebar-accent lg:hidden"
            aria-label="Close menu"
          >
            <X className="size-4" />
          </button>
        </div>

        <nav className="scrollbar-thin flex-1 space-y-5 overflow-y-auto p-3">
          {NAV_SECTIONS.map((section) => (
            <div key={section.label}>
              <p className="px-3 pb-1.5 text-[0.6rem] font-semibold uppercase tracking-[0.15em] text-muted-foreground/70">
                {section.label}
              </p>
              <div className="space-y-1">
                {section.items.map((item) => {
                  const active = pathname === item.href
                  const Icon = item.icon
                  return (
                    <Link
                      key={item.href}
                      href={item.href}
                      onClick={onClose}
                      aria-current={active ? 'page' : undefined}
                      className={cn(
                        'group relative flex items-center gap-3 rounded-lg px-3 py-2 text-sm font-medium transition-colors',
                        active
                          ? 'bg-sidebar-accent text-sidebar-foreground'
                          : 'text-muted-foreground hover:bg-sidebar-accent/60 hover:text-sidebar-foreground',
                      )}
                    >
                      {active && (
                        <span className="absolute inset-y-1.5 left-0 w-0.5 rounded-full bg-primary" />
                      )}
                      <Icon
                        className={cn(
                          'size-4 transition-colors',
                          active
                            ? 'text-primary'
                            : 'text-muted-foreground group-hover:text-sidebar-foreground',
                        )}
                      />
                      {item.label}
                    </Link>
                  )
                })}
              </div>
            </div>
          ))}
        </nav>

        <div className="border-t border-sidebar-border p-4">
          <div className="flex items-center justify-between rounded-lg bg-sidebar-accent/50 px-3 py-2.5">
            <div className="flex items-center gap-2">
              {mode === 'replay' ? (
                <>
                  <StatusDot level="warning" />
                  <span className="text-xs font-medium text-sidebar-foreground">{replayStatusLabel(replay.status)}</span>
                </>
              ) : (
                <>
                  <StatusDot level={connToLevel(connection)} />
                  <span className="text-xs font-medium capitalize text-sidebar-foreground">{connection}</span>
                </>
              )}
            </div>
            <span className="tabular text-xs text-muted-foreground">LAP {lap.lap}</span>
          </div>
        </div>
      </aside>
    </>
  )
}
