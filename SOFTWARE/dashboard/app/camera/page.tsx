'use client'

import { useCallback, useEffect, useState } from 'react'
import { Camera, Circle, Download, FileSpreadsheet, ImageDown, Radio, Square, Trash2, Video } from 'lucide-react'
import { PageHeader } from '@/components/common/page-header'
import { Panel } from '@/components/common/panel'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { useTelemetry } from '@/contexts/telemetry-context'
import { formatLapTime } from '@/lib/format'
import { cameraApi, type CameraRelayStatus, type Recording } from '@/services/api'

/*
 * Stage 2d: the ESP32-CAM serves ONE stream client at a time, so the backend is that
 * client. It relays the frames to every dashboard (/api/camera/stream) and records
 * them (MJPEG .avi, plus .mp4 when ffmpeg is available) for download. The browser
 * never connects to the camera directly any more — that would take the one slot.
 */
const URL_KEY = 'esp32cam.streamUrl'

function mb(n: number) { return `${(n / 1e6).toFixed(1)} MB` }

export default function CameraPage() {
  const { frame, backendSessionId } = useTelemetry()
  const [status, setStatus] = useState<CameraRelayStatus | null>(null)
  const [recs, setRecs] = useState<Recording[]>([])
  const [draft, setDraft] = useState(process.env.NEXT_PUBLIC_ESP32_CAM_URL ?? '')
  const [err, setErr] = useState<string | null>(null)
  const [streamKey, setStreamKey] = useState(0)
  const [elapsed, setElapsed] = useState(0)

  useEffect(() => { try { const s = localStorage.getItem(URL_KEY); if (s) setDraft(s) } catch { /* ignore */ } }, [])

  const refresh = useCallback(async () => {
    try {
      const [s, r] = await Promise.all([cameraApi.status(), cameraApi.recordings()])
      setStatus(s); setRecs(r); setErr(null)
    } catch (e) { setErr((e as Error).message) }
  }, [])
  useEffect(() => { refresh(); const id = window.setInterval(refresh, 2000); return () => window.clearInterval(id) }, [refresh])
  useEffect(() => {
    if (!status?.recording) { setElapsed(0); return }
    const id = window.setInterval(() => setElapsed(Date.now() / 1000 - status.recording!.startedAt), 500)
    return () => window.clearInterval(id)
  }, [status?.recording])

  const apply = async () => {
    try {
      await cameraApi.setSource(draft.trim())
      try { localStorage.setItem(URL_KEY, draft.trim()) } catch { /* ignore */ }
      setStreamKey((k) => k + 1)
      refresh()
    } catch (e) { setErr((e as Error).message) }
  }
  const run = async (fn: () => Promise<unknown>) => { try { await fn() } catch (e) { setErr((e as Error).message) } finally { refresh() } }

  const connected = !!status?.connected
  const recording = !!status?.recording
  const tr = frame?.track

  return (
    <div>
      <PageHeader
        title="Live Camera"
        description="ESP32-CAM on the car → Wi-Fi → the engineer-station backend, which relays it here and records it. Video never travels over LoRa. Recordings download as .avi (always) and .mp4 (when ffmpeg is installed), with a telemetry CSV for the same time window."
        actions={<div className="flex items-center gap-2">
          <Badge variant={connected ? 'success' : err ? 'danger' : 'outline'} className="gap-1.5"><Radio className="size-3" />
            {connected ? `ESP32-CAM · ${status?.fps.toFixed(0)} fps` : status?.url ? 'connecting…' : 'no camera source'}</Badge>
          {recording && <Badge variant="danger" className="gap-1.5"><Circle className="size-2.5 fill-current" /> REC {formatLapTime(elapsed)}</Badge>}
        </div>}
      />
      {err && <p className="-mt-3 mb-4 text-xs text-danger">{err}</p>}
      <div className="grid gap-4 xl:grid-cols-[1fr_340px]">
        <Panel title="On-board camera" icon={Camera} bodyClassName="p-0">
          <div className="relative flex aspect-video items-center justify-center overflow-hidden bg-zinc-950">
            {status?.url && (
              // eslint-disable-next-line @next/next/no-img-element -- MJPEG cannot go through next/image
              <img key={streamKey} src={`${cameraApi.streamUrl()}?k=${streamKey}`} alt="ESP32-CAM live stream (relayed)"
                className={`h-full w-full object-contain ${connected ? '' : 'opacity-30'}`} onError={() => window.setTimeout(() => setStreamKey((k) => k + 1), 2000)} />
            )}
            {!connected && (
              <div className="absolute max-w-md px-6 text-center text-muted-foreground">
                <Video className="mx-auto mb-3 size-8 opacity-50" />
                <p className="text-sm">{status?.url ? 'Waiting for frames from the ESP32-CAM' : 'Set the camera URL on the right'}</p>
                {status?.lastError && <p className="mt-1 text-xs">{status.lastError}</p>}
              </div>
            )}
            {/* telemetry overlay — what the engineer sees in the video when it is recorded is the raw camera image; this overlay is live-only */}
            {frame && (
              <div className="absolute bottom-3 left-3 rounded bg-black/60 px-2 py-1 font-mono text-[0.7rem] text-white">
                {frame.speed.toFixed(2)} km/h · duty {frame.throttle.toFixed(0)}% · {tr ? `lap ${tr.lap + 1} ${(tr.pos * 100).toFixed(0)}% · ${tr.straightMode ? 'STRAIGHT MODE' : 'CORNER MODE'}` : 'no run'}
              </div>
            )}
            <span className="absolute left-3 top-3 inline-flex items-center gap-2 rounded bg-black/60 px-2 py-1 font-mono text-[0.65rem] text-white">
              <span className={`size-1.5 rounded-full ${connected ? 'bg-emerald-500' : 'bg-red-500'}`} /> CAM-01 · relay · {status?.viewers ?? 0} viewer(s)
            </span>
          </div>
        </Panel>
        <div className="space-y-4">
          <Panel title="Camera source">
            {status?.announced ? (
              <p className="mb-2 rounded-md border border-success/30 bg-success/10 px-2 py-1.5 text-[0.7rem] text-success">
                The ESP32-CAM announced itself {status.announced.ageS.toFixed(0)} s ago ({status.announced.ip}{status.announced.rssi != null ? `, RSSI ${status.announced.rssi} dBm` : ''}). {status.origin === 'announce' ? 'The relay connected to it automatically.' : 'You pinned a URL by hand; it is kept while it delivers frames.'}
              </p>
            ) : (
              <p className="mb-2 text-[0.7rem] text-muted-foreground">FW 2.2: the camera registers itself with this backend every 10 s (set <code>BACKEND_HOST</code> in the sketch to this laptop&apos;s IP). Until it does, paste its URL here.</p>
            )}
            <label className="text-xs text-muted-foreground" htmlFor="cam-url">ESP32-CAM stream URL (the sketch prints it at boot)</label>
            <div className="mt-1 flex gap-2">
              <input id="cam-url" value={draft} placeholder="http://<esp32-cam-ip>:81/stream" onChange={(e) => setDraft(e.target.value)} onKeyDown={(e) => { if (e.key === 'Enter') apply() }} spellCheck={false}
                className="h-8 min-w-0 flex-1 rounded-md border border-border bg-background px-2 font-mono text-xs outline-none focus:border-primary" />
              <Button size="sm" onClick={apply}>Connect</Button>
            </div>
            <p className="mt-2 text-[0.7rem] text-muted-foreground">Close any browser tab that points straight at the camera — the ESP32-CAM serves one client, and it must be the backend.</p>
            <div className="mt-3 space-y-1 text-xs">
              <div className="flex justify-between"><span className="text-muted-foreground">Frames relayed</span><span className="tabular">{status?.frames ?? 0}</span></div>
              <div className="flex justify-between"><span className="text-muted-foreground">Last frame</span><span className="tabular">{status?.lastFrameAgeS != null ? `${status.lastFrameAgeS.toFixed(1)} s ago` : '—'}</span></div>
              <div className="flex justify-between"><span className="text-muted-foreground">Reconnects</span><span className="tabular">{status?.errors ?? 0}</span></div>
            </div>
          </Panel>
          <Panel title="Record & capture">
            <div className="flex flex-wrap gap-2">
              {!recording
                ? <Button size="sm" onClick={() => run(() => cameraApi.startRecording(backendSessionId))} disabled={!status?.url}><Circle className="size-3.5 fill-current text-danger" /> Start recording</Button>
                : <Button size="sm" variant="outline" onClick={() => run(() => cameraApi.stopRecording())}><Square className="size-3.5" /> Stop</Button>}
              <a href={cameraApi.snapshotUrl()} download><Button size="sm" variant="outline" disabled={!connected}><ImageDown className="size-3.5" /> Snapshot</Button></a>
            </div>
            <p className="mt-2 text-[0.7rem] text-muted-foreground">MP4 conversion: {status?.mp4Available ? 'available (ffmpeg found)' : 'not available — install imageio-ffmpeg in the backend; .avi still works in VLC / Films & TV'}.</p>
          </Panel>
        </div>
      </div>
      <Panel className="mt-4" title={`Recordings (${recs.length})`} icon={Download}>
        {recs.length === 0 ? <p className="text-sm text-muted-foreground">No recordings yet.</p> : (
          <table className="w-full text-sm">
            <thead><tr className="text-left text-xs text-muted-foreground"><th className="py-1 font-normal">Recording</th><th className="font-normal">Length</th><th className="font-normal">Frames</th><th className="font-normal">Size</th><th className="font-normal">Download</th><th /></tr></thead>
            <tbody>{recs.map((r) => (
              <tr key={r.name} className="border-t border-border">
                <td className="py-2 font-mono text-xs">{r.name}{r.inProgress && <Badge variant="danger" className="ml-2">recording</Badge>}<div className="text-[0.65rem] text-muted-foreground">{r.startedAt ? new Date(r.startedAt * 1000).toLocaleString() : ''} {r.width ? `· ${r.width}×${r.height} @ ${r.fps} fps` : ''}</div></td>
                <td className="tabular">{r.durationS ? formatLapTime(r.durationS) : '—'}</td>
                <td className="tabular">{r.frames ?? '—'}</td>
                <td className="tabular">{mb(r.sizeBytes)}</td>
                <td><div className="flex flex-wrap gap-1">
                  {!r.inProgress && r.files.map((f) => <a key={f.name} href={cameraApi.fileUrl(f.name)} download><Button size="sm" variant="outline">.{f.format} <span className="text-muted-foreground">{mb(f.sizeBytes)}</span></Button></a>)}
                  {!r.inProgress && <a href={cameraApi.csvUrl(r.name)} download><Button size="sm" variant="outline"><FileSpreadsheet className="size-3.5" /> telemetry.csv</Button></a>}
                </div></td>
                <td>{!r.inProgress && <Button size="icon" variant="ghost" onClick={() => { if (confirm(`Delete ${r.name}?`)) run(() => cameraApi.remove(r.name)) }}><Trash2 className="size-4" /></Button>}</td>
              </tr>))}</tbody>
          </table>
        )}
      </Panel>
    </div>
  )
}
