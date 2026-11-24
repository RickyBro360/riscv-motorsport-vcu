'use client'

import { useEffect, useState } from 'react'
import { AlertTriangle, CloudDownload, History, ShieldAlert } from 'lucide-react'
import { PageHeader } from '@/components/common/page-header'
import { Panel } from '@/components/common/panel'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { firmwareApi, type FirmwareDevice, type OtaJob } from '@/services/api'

export default function OtaPage() {
  const [jobs, setJobs] = useState<OtaJob[]>([])
  const [devices, setDevices] = useState<FirmwareDevice[]>([])
  const [note, setNote] = useState('')
  const [error, setError] = useState(false)
  const [staging, setStaging] = useState(false)

  const refresh = () => {
    Promise.all([firmwareApi.listJobs(), firmwareApi.listDevices()])
      .then(([jobsRes, devicesRes]) => {
        setJobs(jobsRes.jobs)
        setNote(jobsRes.note)
        setDevices(devicesRes.devices)
        setError(false)
      })
      .catch(() => setError(true))
  }
  useEffect(refresh, [])

  const staged = devices.find((d) => d.status === 'update-available')

  const stageUpdate = async () => {
    if (!staged) return
    setStaging(true)
    try {
      await firmwareApi.stage(staged.id)
      refresh()
    } catch {
      setError(true)
    } finally {
      setStaging(false)
    }
  }

  return <div><PageHeader title="OTA Updates" description="Development-only OTA workflow. No signed update channel or physical device exists yet — every job below is a local simulation." actions={<Badge variant="outline"><ShieldAlert className="size-3.5" /> SIMULATED — no hardware connected</Badge>} /><div className="grid gap-4 lg:grid-cols-3">{error && <div className="lg:col-span-3 flex items-center gap-2 rounded-lg border border-danger/30 bg-danger/10 px-4 py-2 text-xs text-danger"><AlertTriangle className="size-3.5" /> Backend unreachable — OTA state can't be loaded.</div>}<Panel className="lg:col-span-2" title="Release channel"><div className="flex flex-col gap-4 rounded-lg border border-border bg-secondary/40 p-4 sm:flex-row sm:items-center sm:justify-between">{staged ? <><div><p className="font-medium">{staged.name} firmware</p><p className="mt-1 text-xs text-muted-foreground">{staged.current} → {staged.latest} · dev package</p><div className="mt-3 flex flex-wrap gap-2"><Badge variant="outline">Not device-signed</Badge><Badge variant="outline">Simulation only</Badge></div></div><Button onClick={stageUpdate} disabled={staging}><CloudDownload className="size-4" /> {staging ? 'Staging…' : 'Stage update'}</Button></> : <p className="text-sm text-muted-foreground">No update currently staged.</p>}</div></Panel><Panel title="Reality check"><ul className="space-y-3 text-sm">{['No ESP32-C6/ESC/BMS hardware connected', 'No signed package verification pipeline', 'Nothing here is flashed to a real device', 'Jobs reset when the backend restarts'].map((check) => <li key={check} className="flex items-center gap-2 text-muted-foreground"><AlertTriangle className="size-4 text-warning" /> {check}</li>)}</ul></Panel></div><Panel className="mt-4" title="Simulation job history" icon={History} bodyClassName="p-0"><div className="divide-y divide-border">{jobs.map((job) => <div key={job.id} className="flex flex-col gap-2 p-4 sm:flex-row sm:items-center sm:justify-between"><div><p className="font-medium">{job.device} <span className="ml-2 tabular text-xs text-muted-foreground">{job.id}</span></p><p className="text-xs text-muted-foreground">{job.version} · {new Date(job.startedAt * 1000).toLocaleString()}</p></div><div className="flex items-center gap-3 text-xs"><span className="tabular text-muted-foreground">{job.durationS ? `${job.durationS}s` : 'instant (simulated)'}</span><Badge variant={job.status === 'success' ? 'success' : job.status === 'rolled-back' ? 'warning' : 'danger'}>{job.status}</Badge></div></div>)}{!jobs.length && !error && <p className="p-10 text-center text-sm text-muted-foreground">No OTA jobs yet — stage an update above to generate one.</p>}</div></Panel><p className="mt-3 text-xs text-muted-foreground">{note}</p></div>
}
