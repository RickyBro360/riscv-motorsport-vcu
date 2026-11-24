'use client'

import { useState, type ReactNode } from 'react'
import { Sidebar } from './sidebar'
import { TopBar } from './top-bar'
import { StatusBar } from './status-bar'
import { AppErrorBoundary } from '@/components/common/error-boundary'

export function AppShell({ children }: { children: ReactNode }) {
  const [mobileOpen, setMobileOpen] = useState(false)

  return (
    <div className="flex h-screen w-full overflow-hidden bg-background">
      <Sidebar mobileOpen={mobileOpen} onClose={() => setMobileOpen(false)} />
      <div className="flex min-w-0 flex-1 flex-col">
        <TopBar onMenu={() => setMobileOpen(true)} />
        <main className="scrollbar-thin flex-1 overflow-y-auto">
          <div className="mx-auto max-w-[1600px] p-4 sm:p-6"><AppErrorBoundary>{children}</AppErrorBoundary></div>
        </main>
        <StatusBar />
      </div>
    </div>
  )
}
