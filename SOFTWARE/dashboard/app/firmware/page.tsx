'use client'

import { useEffect, useMemo, useState } from 'react'
import { AlertTriangle, CheckCircle2, Cpu, Download, RefreshCw, Search } from 'lucide-react'
import { PageHeader } from '@/components/common/page-header'
import { Panel } from '@/components/common/panel'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { firmwareApi, type FirmwareDevice } from '@/services/api'

export default function FirmwarePage() {
  const [devices, setDevices] = useState<FirmwareDevice[]>([])
  const [query, setQuery] = useState('')
  const [note, setNote] = useState('')
  const [error, setError] = useState(false)
  const [staging, setStaging] = useState<string | null>(null)

  const refresh = () => {
    firmwareApi
      .listDevices()
      .then((result) => { setDevices(result.devices); setNote(result.note); setError(false) })
      .catch(() => setError(true))
  }
  useEffect(refresh, [])

  const filtered = useMemo(() => devices.filter((device) => `${device.name} ${device.chip}`.toLowerCase().includes(query.toLowerCase())), [devices, query])

  const stage = async (id: string) => {
    setStaging(id)
    try {
      await firmwareApi.stage(id)
      refresh()
    } catch {
      setError(true)
    } finally {
      setStaging(null)
    }
  }

  return <div><PageHeader title="Firmware Manager" description="Development interface for controller firmware. No physical boards are connected yet — this manages a local simulation, not real devices." actions={<Badge variant="outline"><Cpu className="size-3.5" /> SIMULATED · {devices.filter((d) => d.status === 'up-to-date').length}/{devices.length} up to date</Badge>} /><Panel bodyClassName="p-0"><div className="flex flex-col gap-3 border-b border-border p-4 sm:flex-row sm:items-center sm:justify-between"><div className="relative w-full sm:max-w-xs"><Search className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" /><input value={query} onChange={(event) => setQuery(event.target.value)} aria-label="Search firmware devices" placeholder="Search devices" className="h-9 w-full rounded-md border border-border bg-background pl-9 pr-3 text-sm outline-none focus:border-primary/60" /></div><Button variant="outline" size="sm" onClick={refresh}><RefreshCw className="size-3.5" /> Refresh</Button></div>{error && <div className="flex items-center gap-2 border-b border-border bg-danger/10 px-4 py-2 text-xs text-danger"><AlertTriangle className="size-3.5" /> Backend unreachable — firmware state can't be loaded.</div>}<div className="divide-y divide-border">{filtered.map((device) => <div key={device.id} className="flex flex-col gap-4 p-4 sm:flex-row sm:items-center sm:justify-between"><div className="flex items-center gap-3"><div className="flex size-9 items-center justify-center rounded-lg bg-secondary text-muted-foreground"><Cpu className="size-4" /></div><div><p className="font-medium">{device.name}</p><p className="text-xs text-muted-foreground">{device.chip} · {device.id.toUpperCase()}</p></div></div><div className="flex flex-wrap items-center gap-3 text-xs"><span className="tabular text-muted-foreground">{device.current} → {device.latest}</span><DeviceStatus status={device.status} />{device.status === 'update-available' && <Button size="sm" onClick={() => stage(device.id)} disabled={staging === device.id}>{staging === device.id ? 'Staging…' : <><Download className="size-3.5" /> Stage update</>}</Button>}</div></div>)}{!filtered.length && !error && <p className="p-10 text-center text-sm text-muted-foreground">No firmware devices match this search.</p>}</div></Panel><p className="mt-3 text-xs text-muted-foreground">{note}</p></div>
}

function DeviceStatus({ status }: { status: FirmwareDevice['status'] }) { if (status === 'up-to-date') return <Badge variant="success"><CheckCircle2 className="size-3" /> Up to date</Badge>; if (status === 'update-available') return <Badge variant="warning">Update available</Badge>; if (status === 'updating') return <Badge variant="info">Updating</Badge>; return <Badge variant="outline">Not connected</Badge> }
