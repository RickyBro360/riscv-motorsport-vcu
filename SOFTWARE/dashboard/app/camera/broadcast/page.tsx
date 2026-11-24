'use client'

import { useEffect, useRef, useState } from 'react'
import { Camera, Circle, Radio, Video, VideoOff } from 'lucide-react'
import { PageHeader } from '@/components/common/page-header'
import { Panel } from '@/components/common/panel'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { CameraBroadcastClient, type CameraConnectionState } from '@/services/camera-signal'

/**
 * ON-BOARD PHONE page. Open this URL on the phone that's mounted on the
 * car (any modern mobile browser — Safari on iOS, Chrome on Android).
 *
 * Two independent things happen here, exactly per the target architecture:
 *   1. LIVE: the camera stream is published over WebRTC straight to the
 *      dashboard/Racing Engineer Station (peer-to-peer, via /ws/camera-signal
 *      for signaling only — no video touches the backend).
 *   2. LOCAL RECORDING: MediaRecorder captures the same camera stream and
 *      saves it to the phone's own storage (browser download) so a full
 *      copy exists even if the live link drops. This never claims to be
 *      "recorded by the dashboard" — it's a phone-local file, full stop.
 * Losing the live link does not stop or corrupt the local recording, and
 * vice versa — they use two independent MediaStream consumers off the
 * same getUserMedia() capture.
 *
 * This page is the "clearly identified test-stream method" too: any
 * laptop/phone with a webcam can open it to exercise the exact same code
 * path the real on-board phone will use.
 */
export default function CameraBroadcastPage() {
  const videoRef = useRef<HTMLVideoElement>(null)
  const streamRef = useRef<MediaStream | null>(null)
  const broadcastRef = useRef<CameraBroadcastClient | null>(null)
  const recorderRef = useRef<MediaRecorder | null>(null)
  const chunksRef = useRef<Blob[]>([])

  const [captureState, setCaptureState] = useState<'idle' | 'requesting' | 'active' | 'denied'>('idle')
  const [linkState, setLinkState] = useState<CameraConnectionState>('idle')
  const [recording, setRecording] = useState(false)
  const [savedClips, setSavedClips] = useState<string[]>([])

  useEffect(() => {
    return () => {
      broadcastRef.current?.stop()
      recorderRef.current?.stop()
      streamRef.current?.getTracks().forEach((track) => track.stop())
    }
  }, [])

  async function startCamera() {
    setCaptureState('requesting')
    try {
      const stream = await navigator.mediaDevices.getUserMedia({
        video: { facingMode: 'environment' },
        audio: true,
      })
      streamRef.current = stream
      if (videoRef.current) videoRef.current.srcObject = stream
      setCaptureState('active')

      const broadcast = new CameraBroadcastClient({ onState: setLinkState })
      broadcastRef.current = broadcast
      await broadcast.publish(stream)
    } catch {
      setCaptureState('denied')
    }
  }

  function stopCamera() {
    broadcastRef.current?.stop()
    broadcastRef.current = null
    if (recording) stopRecording()
    streamRef.current?.getTracks().forEach((track) => track.stop())
    streamRef.current = null
    if (videoRef.current) videoRef.current.srcObject = null
    setCaptureState('idle')
    setLinkState('idle')
  }

  function startRecording() {
    if (!streamRef.current || typeof MediaRecorder === 'undefined') return
    const mimeType = MediaRecorder.isTypeSupported('video/webm;codecs=vp9')
      ? 'video/webm;codecs=vp9'
      : 'video/webm'
    const recorder = new MediaRecorder(streamRef.current, { mimeType })
    chunksRef.current = []
    recorder.ondataavailable = (event) => {
      if (event.data.size > 0) chunksRef.current.push(event.data)
    }
    recorder.onstop = () => {
      const blob = new Blob(chunksRef.current, { type: mimeType })
      const url = URL.createObjectURL(blob)
      const filename = `lap-${new Date().toISOString().replace(/[:.]/g, '-')}.webm`
      // Local-storage save = a browser download to the phone's own
      // filesystem. This is the phone saving its own file — the dashboard
      // never sees or stores this blob.
      const a = document.createElement('a')
      a.href = url
      a.download = filename
      a.click()
      URL.revokeObjectURL(url)
      setSavedClips((prev) => [filename, ...prev])
    }
    recorder.start()
    recorderRef.current = recorder
    setRecording(true)
  }

  function stopRecording() {
    recorderRef.current?.stop()
    recorderRef.current = null
    setRecording(false)
  }

  const linkBadgeMap: Record<CameraConnectionState, { label: string; variant: 'success' | 'warning' | 'outline' | 'danger' }> = {
    idle: { label: 'Not publishing', variant: 'outline' },
    connecting: { label: 'Connecting…', variant: 'warning' },
    connected: { label: 'Live to Engineer Station', variant: 'success' },
    disconnected: { label: 'Link disconnected', variant: 'danger' },
    error: { label: 'Link error', variant: 'danger' },
  }
  const linkBadge = linkBadgeMap[linkState]

  return (
    <div>
      <PageHeader
        title="On-Board Phone — Camera Publisher"
        description="Open this page on the phone mounted on the car. Streams live to the dashboard and records locally, independently."
        actions={<Badge variant={linkBadge.variant} className="gap-1.5"><Radio className="size-3" /> {linkBadge.label}</Badge>}
      />
      <div className="grid gap-4 xl:grid-cols-[1fr_320px]">
        <Panel title="Phone camera" icon={Camera} bodyClassName="p-0">
          <div className="relative flex aspect-video items-center justify-center overflow-hidden bg-zinc-950">
            <video ref={videoRef} autoPlay playsInline muted className="h-full w-full object-cover" />
            {captureState !== 'active' && (
              <div className="absolute inset-0 flex items-center justify-center bg-zinc-950 text-center text-muted-foreground">
                <div>
                  {captureState === 'denied' ? (
                    <VideoOff className="mx-auto mb-3 size-8 opacity-50" />
                  ) : (
                    <Video className="mx-auto mb-3 size-8 opacity-50" />
                  )}
                  <p className="text-sm">
                    {captureState === 'denied'
                      ? 'Camera permission denied or unavailable'
                      : captureState === 'requesting'
                        ? 'Requesting camera access…'
                        : 'Camera not started'}
                  </p>
                </div>
              </div>
            )}
            {recording && (
              <span className="absolute left-4 top-4 inline-flex items-center gap-2 rounded bg-black/60 px-2 py-1 font-mono text-[0.65rem] text-white">
                <span className="size-1.5 animate-pulse rounded-full bg-red-500" /> RECORDING (local)
              </span>
            )}
          </div>
        </Panel>
        <div className="space-y-4">
          <Panel title="Controls">
            <div className="grid grid-cols-1 gap-2">
              {captureState === 'active' ? (
                <Button variant="outline" onClick={stopCamera}>Stop camera &amp; live link</Button>
              ) : (
                <Button onClick={startCamera} disabled={captureState === 'requesting'}>Start camera</Button>
              )}
              <Button
                variant={recording ? 'default' : 'outline'}
                onClick={() => (recording ? stopRecording() : startRecording())}
                disabled={captureState !== 'active'}
              >
                <Circle className="size-3.5" /> {recording ? 'Stop local recording' : 'Start local recording'}
              </Button>
            </div>
            <p className="mt-3 text-xs leading-relaxed text-muted-foreground">
              Live streaming and local recording are independent — stopping
              one does not affect the other. Local recording saves a .webm
              file to this phone&apos;s own downloads; the dashboard never
              receives or stores it.
            </p>
          </Panel>
          {savedClips.length > 0 && (
            <Panel title="Saved to this phone">
              <ul className="space-y-1 text-xs text-muted-foreground">
                {savedClips.map((name) => (
                  <li key={name} className="font-mono">{name}</li>
                ))}
              </ul>
            </Panel>
          )}
        </div>
      </div>
    </div>
  )
}
