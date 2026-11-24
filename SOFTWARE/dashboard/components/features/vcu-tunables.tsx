'use client'

import { useCallback, useEffect, useState } from 'react'
import { Cpu, Send } from 'lucide-react'
import { Panel } from '@/components/common/panel'
import { SourceTag } from '@/components/common/source-tag'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { cn } from '@/lib/utils'
import { vehicleApi, type ConfigKeyInfo, type VehicleCommand } from '@/services/api'

/**
 * The VCU's real tunables (vcu_logic.h ConfigStore): what the car reports it is using,
 * the bounds it will accept, and a way for the operator to change one. A change is a
 * CONFIG_WRITE — the VCU validates it and ACKs; this table shows the ACK, not a hope.
 */
export function VcuTunables() {
  const [keys, setKeys] = useState<ConfigKeyInfo[]>([])
  const [reported, setReported] = useState(false)
  const [pending, setPending] = useState(false)
  const [cmds, setCmds] = useState<VehicleCommand[]>([])
  const [draft, setDraft] = useState<Record<string, string>>({})
  const [msg, setMsg] = useState<string | null>(null)

  const refresh = useCallback(async () => {
    try {
      const [c, k] = await Promise.all([vehicleApi.config(), vehicleApi.commands()])
      // run / e-stop / motor live in Car control; servo end points in Servo calibration (FW 2.3)
      setKeys(c.keys.filter((x) => x.apply !== 'command' && x.apply !== 'calibration')); setReported(c.reported); setPending(c.pending); setCmds(k)
    } catch (e) { setMsg((e as Error).message) }
  }, [])
  useEffect(() => { refresh(); const id = window.setInterval(refresh, 2000); return () => window.clearInterval(id) }, [refresh])

  const send = async (k: ConfigKeyInfo) => {
    const v = Number(draft[k.name])
    if (!Number.isFinite(v)) return
    try {
      const c = await vehicleApi.write(k.name, v)
      setMsg(`#${c.id}: ${c.label} → ${c.displayValue} ${c.unit} queued for the VCU`)
      setDraft((d) => ({ ...d, [k.name]: '' }))
      refresh()
    } catch (e) { setMsg((e as Error).message.replace(/^API POST \S+ failed: \d+ /, '')) }
  }

  const lastFor = (key: number) => cmds.find((c) => c.key === key)

  return (
    <Panel title="VCU tunables (live on the car)" icon={Cpu}
      action={<div className="flex items-center gap-2"><SourceTag source="vcu" />
        <Badge variant={reported ? 'success' : 'outline'}>{reported ? 'reported by the VCU' : 'firmware defaults (car not reporting)'}</Badge>
        {pending && <Badge variant="warning">staged → next start/finish</Badge>}</div>}>
      {msg && <p className="mb-3 text-xs text-muted-foreground">{msg}</p>}
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead><tr className="text-left text-xs text-muted-foreground">
            <th className="py-1 font-normal">Setting</th><th className="font-normal">On the car</th><th className="font-normal">Range</th>
            <th className="font-normal">Applies</th><th className="font-normal">Change</th><th className="font-normal">Last write</th></tr></thead>
          <tbody>{keys.map((k) => {
            const last = lastFor(k.key)
            return (
              <tr key={k.key} className="border-t border-border align-top">
                <td className="py-2"><div>{k.label}</div><div className="text-[0.65rem] text-muted-foreground">{k.description}</div></td>
                <td className="tabular whitespace-nowrap">{k.value} {k.unit}</td>
                <td className="tabular whitespace-nowrap text-xs text-muted-foreground">{k.apply === 'readonly' ? 'measured' : `${k.min} … ${k.max}`}</td>
                <td className="text-xs text-muted-foreground">{k.apply.replace('_', ' ')}</td>
                <td>{k.apply !== 'readonly' && (
                  <div className="flex gap-1">
                    <input value={draft[k.name] ?? ''} onChange={(e) => setDraft((d) => ({ ...d, [k.name]: e.target.value }))}
                      onKeyDown={(e) => { if (e.key === 'Enter') send(k) }} placeholder={String(k.value)}
                      className="h-7 w-20 rounded border border-border bg-background px-1.5 text-xs" />
                    <Button size="sm" variant="outline" onClick={() => send(k)} disabled={!draft[k.name]}><Send className="size-3" /></Button>
                  </div>)}</td>
                <td className={cn('text-xs', !last ? 'text-muted-foreground' : last.status === 'applied' ? 'text-success' : last.status.startsWith('rejected') || last.status === 'expired' ? 'text-danger' : 'text-warning')}>
                  {last ? `${last.displayValue} ${last.unit}: ${last.status.replace(/_/g, ' ')}` : '—'}</td>
              </tr>)
          })}</tbody>
        </table>
      </div>
      <p className="mt-2 text-[0.7rem] text-muted-foreground">Safety thresholds (over-temperature, over-current, undervoltage, timeouts, e-stop) are deliberately not in this list: nothing off the car can change them.</p>
    </Panel>
  )
}
