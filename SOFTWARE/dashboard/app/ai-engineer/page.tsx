'use client'

import { useCallback, useEffect, useState } from 'react'
import { AlertTriangle, BrainCircuit, Check, CheckCircle2, Clock, Info, Lightbulb, Radio, Send, X, XCircle } from 'lucide-react'
import { PageHeader } from '@/components/common/page-header'
import { Panel } from '@/components/common/panel'
import { SourceTag } from '@/components/common/source-tag'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { CircuitMap } from '@/components/features/circuit-map'
import { useTelemetry } from '@/contexts/telemetry-context'
import { useTrack } from '@/hooks/use-tracks'
import { useSmoothPosition } from '@/hooks/use-smooth-position'
import { effectiveConfig } from '@/lib/tracks'
import { cn } from '@/lib/utils'
import { engineerApi, type EngineerState, type Insight, type Suggestion } from '@/services/api'

const SEV: Record<Insight['severity'], { icon: typeof Info; cls: string }> = {
  info: { icon: Info, cls: 'text-info' },
  warning: { icon: AlertTriangle, cls: 'text-warning' },
  critical: { icon: XCircle, cls: 'text-danger' },
}

const STEPS = ['proposed', 'approved', 'sent', 'on car', 'evaluated'] as const

function stepOf(s: Suggestion): number {
  if (s.status === 'proposed') return 0
  if (s.status === 'applied') return s.outcome?.verdict ? 4 : 3
  if (s.status === 'approved') {
    if (s.commands.some((c) => c.status === 'staged_next_lap')) return 3
    if (s.commands.some((c) => c.status === 'sent')) return 2
    return 1
  }
  return -1
}

function Pipeline({ s }: { s: Suggestion }) {
  const step = stepOf(s)
  const staged = s.commands.some((c) => c.status === 'staged_next_lap')
  return (
    <div className="flex flex-wrap items-center gap-1 text-[0.65rem]">
      {STEPS.map((label, i) => (
        <span key={label} className={cn('rounded px-1.5 py-0.5', i <= step ? 'bg-primary/15 text-primary' : 'bg-secondary text-muted-foreground')}>
          {label === 'on car' && staged && s.status !== 'applied' ? 'staged → next lap' : label}
        </span>
      ))}
    </div>
  )
}

function SuggestionCard({ s, onApprove, onReject, busy }: { s: Suggestion; onApprove?: () => void; onReject?: () => void; busy?: boolean }) {
  const failed = s.status === 'failed' || s.status === 'rejected' || s.status === 'expired'
  return (
    <div className={cn('rounded-lg border p-4', s.status === 'proposed' ? 'border-primary/40 bg-primary/5' : 'border-border bg-card')}>
      <div className="flex items-start justify-between gap-3">
        <div>
          <p className="font-semibold">{s.title}</p>
          <p className="mt-0.5 text-[0.7rem] text-muted-foreground">#{s.id} · {s.kind.replace(/_/g, ' ')} · {new Date(s.created_at * 1000).toLocaleTimeString()}</p>
        </div>
        <Badge variant={s.status === 'applied' ? 'success' : failed ? 'danger' : s.status === 'proposed' ? 'accent' : 'info'}>{s.status}</Badge>
      </div>
      <p className="mt-3 text-sm leading-relaxed">{s.body.rationale}</p>
      <table className="mt-3 w-full text-xs">
        <thead><tr className="text-left text-muted-foreground"><th className="py-1 font-normal">Setting</th><th className="font-normal">Now</th><th className="font-normal">Proposed</th><th className="font-normal">Takes effect</th></tr></thead>
        <tbody>{s.body.changes.map((c) => (
          <tr key={c.key} className="border-t border-border">
            <td className="py-1">{c.label}</td>
            <td className="tabular">{c.from} {c.unit}</td>
            <td className="tabular font-semibold">{c.to} {c.unit}</td>
            <td className="text-muted-foreground">{c.apply === 'staged' ? 'next start/finish' : c.apply === 'idle_only' ? 'when stopped' : 'immediately'}</td>
          </tr>))}</tbody>
      </table>
      <div className="mt-3 grid gap-2 text-xs md:grid-cols-2">
        <p><span className="text-muted-foreground">Expected: </span>{s.body.expectedEffect}</p>
        <p><span className="text-muted-foreground">Risk: </span>{s.body.risk}</p>
      </div>
      {s.status !== 'proposed' && <div className="mt-3"><Pipeline s={s} /></div>}
      {s.commands.length > 0 && (
        <p className="mt-2 text-[0.7rem] text-muted-foreground">VCU: {s.commands.map((c) => `${c.label} → ${c.status.replace(/_/g, ' ')}${c.attempts > 1 ? ` (${c.attempts} tries)` : ''}`).join(' · ')}</p>
      )}
      {s.outcome?.verdict && <p className="mt-2 rounded bg-success/10 px-2 py-1 text-xs text-success">After: {s.outcome.verdict}</p>}
      {s.outcome?.failure && <p className="mt-2 rounded bg-danger/10 px-2 py-1 text-xs text-danger">Not applied: {s.outcome.failure}</p>}
      {s.status === 'proposed' && onApprove && onReject && (
        <div className="mt-4 flex gap-2">
          <Button size="sm" onClick={onApprove} disabled={busy}><Check className="size-3.5" /> Approve — send to car</Button>
          <Button size="sm" variant="outline" onClick={onReject} disabled={busy}><X className="size-3.5" /> Reject</Button>
        </div>
      )}
    </div>
  )
}

export default function AiEngineerPage() {
  const { frame, ai, mode } = useTelemetry()
  const [state, setState] = useState<EngineerState | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const cfg = effectiveConfig(frame?.vcuConfig)
  const track = useTrack(frame?.track?.id ?? cfg.trackId)
  const rear = frame?.wheels ? (frame.wheels.rl + frame.wheels.rr) / 2 : frame?.speed ?? 0
  const pos = useSmoothPosition(frame?.track?.pos, rear, cfg.lapLengthCm / 100, !!frame?.track?.runActive)

  const poll = useCallback(async () => {
    try { setState(await engineerApi.state()); setError(null) } catch (e) { setError((e as Error).message) }
  }, [])
  useEffect(() => { poll(); const id = window.setInterval(poll, 1500); return () => window.clearInterval(id) }, [poll])

  const act = async (fn: () => Promise<unknown>) => {
    setBusy(true)
    try { await fn() } catch (e) { setError((e as Error).message) } finally { setBusy(false); poll() }
  }

  const live = state?.live
  const proposed = state?.suggestions.filter((s) => s.status === 'proposed') ?? []
  const active = state?.suggestions.filter((s) => s.status === 'approved' || (s.status === 'applied' && !s.outcome?.verdict)) ?? []
  const history = state?.suggestions.filter((s) => !proposed.includes(s) && !active.includes(s)) ?? []
  const insights = [...(state?.insights ?? [])].sort((a, b) => ['critical', 'warning', 'info'].indexOf(a.severity) - ['critical', 'warning', 'info'].indexOf(b.severity))
  const aheadPos = live ? live.posAhead : null
  const reason = live?.reason ?? (mode !== 'live' ? ai?.reason : null)
  const wing = live?.recommendedWing ?? (mode !== 'live' ? ai?.recommendedWing : null)

  return (
    <div>
      <PageHeader
        title="AI Engineer"
        description="Watches the car's position on the circuit and its sensors, explains what it sees, and proposes changes. It can only propose: you approve, the VCU validates every value against its own limits, staged changes take effect at the next start/finish line, and 'applied' is shown only after the VCU acknowledges. Deterministic rules plus a digital-twin optimiser — no trained model."
        actions={<div className="flex items-center gap-2">
          <Badge variant="outline"><BrainCircuit className="size-3" /> rules + twin</Badge>
          {state?.settings && <Badge variant="outline">{state.settings.powerSource === 'adapter' ? '12 V / 2.2 A adapter' : '3S LiPo'}</Badge>}
          <Badge variant={error ? 'danger' : 'success'}>{error ? 'backend unreachable' : 'engineer station online'}</Badge>
        </div>}
      />
      {error && <p className="mb-4 text-xs text-danger">{error}{mode !== 'live' ? ' — proposals need the backend; the look-ahead below is from the offline simulator.' : ''}</p>}

      <div className="grid gap-4 xl:grid-cols-[1.1fr_1fr]">
        <Panel title="Live aero call (look-ahead)" icon={Radio} action={<SourceTag source={mode === 'live' ? 'derived' : 'simulated'} />}>
          <div className="grid gap-4 md:grid-cols-[1fr_200px]">
            <div className="aspect-[1.3] rounded-md bg-secondary/20 p-2">
              {track && <CircuitMap track={track} pos={frame?.track ? pos : null} aheadPos={aheadPos} cfg={cfg} straightMode={frame?.track?.straightMode} compact showCorners={false} />}
            </div>
            <div className="space-y-2 text-sm">
              <p className="leading-snug">{reason ?? 'No run active — the AI has no position to look ahead from.'}</p>
              {wing && <div className="grid grid-cols-2 gap-1 font-mono text-xs">
                <span>LF {wing.frontLeft.toFixed(1)}°</span><span>RF {wing.frontRight.toFixed(1)}°</span>
                <span>LR {wing.rearLeft.toFixed(1)}°</span><span>RR {wing.rearRight.toFixed(1)}°</span>
              </div>}
              {live && <p className="text-xs text-muted-foreground">Confidence {(live.confidence * 100).toFixed(0)} % · look-ahead {live.lookaheadS.toFixed(2)} s · {live.trackCode}</p>}
              <p className="text-xs text-muted-foreground">VCU verdict: <span className="font-medium text-foreground">{frame?.lastAiReason?.replace(/_/g, ' ') ?? '—'}</span> · aero mode {['baseline', 'AI-assist', 'failsafe'][frame?.aeroMode ?? 1]}</p>
              <p className="text-[0.7rem] text-muted-foreground">In AI-assist the VCU blends this within ±20° of its own map; in baseline it ignores it.</p>
            </div>
          </div>
        </Panel>

        <Panel title="What the data shows" icon={Lightbulb} action={<span className="text-xs text-muted-foreground">{state?.frameCount ?? 0} frames · last 30 s</span>}>
          <div className="max-h-[360px] space-y-2 overflow-y-auto pr-1">
            {insights.length === 0 && <p className="text-sm text-muted-foreground">{state?.reason ?? 'Waiting for telemetry…'}</p>}
            {insights.map((i) => {
              const S = SEV[i.severity]
              return <div key={i.id} className="flex gap-2 rounded-md border border-border p-2">
                <S.icon className={cn('mt-0.5 size-4 shrink-0', S.cls)} />
                <div><p className="text-sm font-medium">{i.title}</p><p className="text-xs text-muted-foreground">{i.detail}</p></div>
              </div>
            })}
          </div>
        </Panel>
      </div>

      <div className="mt-4 grid gap-4 xl:grid-cols-2">
        <Panel title={`Proposals waiting for you (${proposed.length})`} icon={Send}>
          <div className="space-y-3">
            {proposed.length === 0 && <p className="text-sm text-muted-foreground">Nothing to propose right now. Proposals appear when the data supports a change the VCU can make.</p>}
            {proposed.map((s) => <SuggestionCard key={s.id} s={s} busy={busy}
              onApprove={() => act(() => engineerApi.approve(s.id))} onReject={() => act(() => engineerApi.reject(s.id))} />)}
          </div>
        </Panel>
        <Panel title="On their way to the car" icon={Clock}>
          <div className="space-y-3">
            {active.length === 0 && <p className="text-sm text-muted-foreground">Approved changes show their progress here: queued → sent over LoRa → VCU ACK → applied at the line → evaluated.</p>}
            {active.map((s) => <SuggestionCard key={s.id} s={s} />)}
          </div>
        </Panel>
      </div>

      <div className="mt-4 grid gap-4 xl:grid-cols-[1.4fr_1fr]">
        <Panel title="Decision history" icon={CheckCircle2}>
          <div className="max-h-[420px] space-y-3 overflow-y-auto pr-1">
            {history.length === 0 && <p className="text-sm text-muted-foreground">No decisions yet.</p>}
            {history.map((s) => <SuggestionCard key={s.id} s={s} />)}
          </div>
        </Panel>
        <Panel title="Commands to the VCU" icon={Send}>
          <table className="w-full text-xs">
            <thead><tr className="text-left text-muted-foreground"><th className="py-1 font-normal">#</th><th className="font-normal">Setting</th><th className="font-normal">Value</th><th className="font-normal">From</th><th className="font-normal">Status</th></tr></thead>
            <tbody>{(state?.commands ?? []).map((c) => (
              <tr key={c.id} className="border-t border-border">
                <td className="py-1 tabular">{c.id}</td><td>{c.label}</td><td className="tabular">{c.displayValue} {c.unit}</td>
                <td className="text-muted-foreground">{c.origin}</td>
                <td className={cn(c.status === 'applied' ? 'text-success' : c.status.startsWith('rejected') || c.status === 'expired' ? 'text-danger' : 'text-warning')}>{c.status.replace(/_/g, ' ')}</td>
              </tr>))}</tbody>
          </table>
        </Panel>
      </div>
    </div>
  )
}
