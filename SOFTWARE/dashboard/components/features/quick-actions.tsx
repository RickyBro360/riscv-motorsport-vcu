'use client'

import { Zap, Radio, RotateCcw, Download, Flag, Wrench } from 'lucide-react'
import { Panel } from '@/components/common/panel'

const ACTIONS = [
  { label: 'Arm VCU', icon: Zap },
  { label: 'Ping Vehicle', icon: Radio },
  { label: 'Reset Session', icon: RotateCcw },
  { label: 'Export Log', icon: Download },
  { label: 'Flag Lap', icon: Flag },
  { label: 'Safe Mode', icon: Wrench },
]

export function QuickActions() {
  return (
    <Panel title="Quick Actions" icon={Zap} className="h-full">
      <div className="grid grid-cols-2 gap-2.5">
        {ACTIONS.map((a) => {
          const Icon = a.icon
          return (
            <button
              key={a.label}
              className="flex flex-col items-start gap-2 rounded-lg border border-border bg-secondary/40 p-3 text-left transition-colors hover:border-primary/40 hover:bg-secondary"
            >
              <Icon className="size-4 text-primary" />
              <span className="text-xs font-medium">{a.label}</span>
            </button>
          )
        })}
      </div>
    </Panel>
  )
}
