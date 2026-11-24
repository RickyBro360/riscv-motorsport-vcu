'use client'

import { Pause, Play, Square } from 'lucide-react'
import { useTelemetry } from '@/contexts/telemetry-context'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import type { SessionMode, SimulationScenario } from '@/types/telemetry'

const SCENARIOS: { value: SimulationScenario; label: string }[] = [
  { value: 'TRACK_RUN', label: 'Dyno track run (Stage 2d)' },
  { value: 'NORMAL_DRIVING', label: 'Normal Driving' },
  { value: 'HIGH_LOAD', label: 'High Load' },
  { value: 'CORNERING', label: 'Cornering' },
  { value: 'LOW_BATTERY', label: 'Low Battery' },
  { value: 'OVER_TEMPERATURE', label: 'Over Temperature' },
  { value: 'COMMUNICATION_LOSS', label: 'Communication Loss' },
  { value: 'SENSOR_FAULT', label: 'Sensor Fault' },
  { value: 'EMERGENCY_STOP', label: 'Emergency Stop' },
]

export function SessionControls() {
  const { session, startSession, pauseSession, resumeSession, stopSession, mode, scenario, setScenario } = useTelemetry()
  const elapsed = `${Math.floor(session.elapsedS / 60).toString().padStart(2, '0')}:${Math.floor(session.elapsedS % 60).toString().padStart(2, '0')}`
  return <div className="flex flex-wrap items-center gap-2"><Badge variant={session.status === 'running' ? 'success' : session.status === 'paused' ? 'warning' : 'outline'}>{session.mode} · {elapsed}</Badge>{session.status === 'running' ? <Button variant="outline" size="sm" onClick={pauseSession}><Pause className="size-3.5" /> Pause</Button> : <Button variant="outline" size="sm" onClick={resumeSession}><Play className="size-3.5" /> Resume</Button>}<Button variant="outline" size="sm" onClick={stopSession} disabled={session.status === 'completed'}><Square className="size-3.5" /> Stop</Button><select aria-label="Session type" value={session.mode} onChange={(event) => startSession(event.target.value as SessionMode)} className="h-8 rounded-md border border-border bg-background px-2 text-xs"><option value="practice">Practice</option><option value="qualifying">Qualifying</option><option value="race">Race</option><option value="testing">Testing</option></select>{mode === 'simulation' && <select aria-label="Simulation scenario" value={scenario} onChange={(event) => setScenario(event.target.value as SimulationScenario)} className="h-8 rounded-md border border-border bg-background px-2 text-xs" title="Test scenario">{SCENARIOS.map((s) => <option key={s.value} value={s.value}>{s.label}</option>)}</select>}</div>
}
