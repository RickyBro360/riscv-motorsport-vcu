'use client'

import { createContext, useCallback, useContext, useEffect, useRef, useState, type ReactNode } from 'react'
import { useSettings } from '@/contexts/settings-context'
import { TelemetryService, TelemetryParser, type TelemetryMode } from '@/services/telemetry-stack'
import { TelemetrySocketClient } from '@/services/telemetry-socket'
import { sessionApi, recorderApi, faultsApi, type RecordSnapshot } from '@/services/api'
import { ReplayEngine, type ReplayFrameRecord, type ReplaySnapshot } from '@/services/replay-engine'
import type {
  AiRecommendation,
  ConnectionStatus,
  Fault,
  SensorHealth,
  SystemDiagnostics,
  TelemetryFrame,
  SessionMode,
  SessionState,
  ReplayState,
  ReplayRate,
  SimulationScenario,
  TelemetryLinkState,
} from '@/types/telemetry'

// How often the recorder flushes buffered frames to the backend. Frames
// are still sampled at the full telemetry rate (settings.sampleRate) —
// this only batches the network write so a 10Hz+ stream doesn't turn
// into 10+ HTTP requests/sec. If the backend is unreachable, batches are
// dropped (capped) rather than retried forever — see flushRecorder().
const RECORDER_FLUSH_MS = 1000
const RECORDER_MAX_BUFFER = 200

const HISTORY_SIZE = 300

// A frame older than this while mode === 'live' means the socket is open
// but nothing hardware-originated is actually arriving — the link drops
// from 'live_hardware' back to 'connected' ("waiting for telemetry")
// rather than continuing to claim live data. Generous relative to typical
// telemetry rates (a few Hz to ~20Hz) so normal jitter never flaps the
// badge, but short enough that a stalled Pi/CAN feed is caught quickly.
const LIVE_FRAME_STALE_MS = 3000
const LIVE_FRAME_WATCHDOG_MS = 500
// FW 2.3 (dashboard lag): LIVE frames arrive at up to 20 Hz (MODE A) and in bursts. Each one used
// to trigger its own render of every mounted page (charts with 300 points, the 3D car, gauges).
// Frames are now collected as they arrive and the screen is updated at most this often, on an
// animation frame. Charts still get every frame (history is appended per frame).
const LIVE_UI_MIN_INTERVAL_MS = 100

type ReplayPayload = { ai: AiRecommendation | null; diagnostics: SystemDiagnostics | null }

interface LapInfo { lap: number; lastLapTime: number; currentLapTime: number; position: number }
interface TelemetryContextValue {
  frame: TelemetryFrame | null; history: TelemetryFrame[]; faults: Fault[]; ai: AiRecommendation | null; diagnostics: SystemDiagnostics | null; sensors: SensorHealth[]; lap: LapInfo; connection: ConnectionStatus; running: boolean; toggleRunning: () => void
  mode: TelemetryMode; setMode: (mode: TelemetryMode) => void; session: SessionState; startSession: (mode?: SessionMode) => void; pauseSession: () => void; resumeSession: () => void; stopSession: () => void
  /** Central replay state — the ONE replay clock for the whole app. See
   * services/replay-engine.ts. All replay UI (transport controls, the
   * Replay page's scrubber, Digital Twin, Track Map, Diagnostics) reads
   * this instead of keeping its own playhead. */
  replay: ReplayState
  /** The full frame array currently loaded into the replay engine, sorted
   * by timestamp — static for the duration of a loaded session (only
   * `replay.index` changes during playback). Charts use this to render a
   * scrubbed trace up to the current playhead without keeping a second
   * copy of replay state. Empty when nothing is loaded. */
  replayFrames: TelemetryFrame[]
  /** Load an authoritative recorded session from the backend (SQLite via
   * FastAPI) and switch to replay mode. Returns false if the session has
   * no frames or the backend is unreachable. */
  loadReplaySession: (sessionId: string) => Promise<boolean>
  /** Load the frontend's rolling in-memory buffer as a local, unlabelled
   * replay capture — the lighter-weight offline alternative to a backend
   * session. Does not touch SQLite. */
  loadReplayCapture: (frames: TelemetryFrame[]) => void
  playReplay: () => void
  pauseReplay: () => void
  stopReplay: () => void
  /** Unload the current replay session entirely and return the Replay page
   * to its session-picker state (distinct from stopReplay, which only
   * resets the playhead to the start of the still-loaded session). */
  clearReplay: () => void
  /** Seek by fraction of total duration, 0..1. */
  seekReplay: (fraction: number) => void
  setReplayRate: (rate: ReplayRate) => void
  scenario: SimulationScenario; setScenario: (scenario: SimulationScenario) => void
  /** SIMULATION only: which stored circuit the offline track-run simulator drives. */
  setSimulationTrack: (id: number) => void
  /** SIMULATION only (FW 2.2): a Car-control request to the offline simulator; returns the VCU-style status. */
  simulateCarCommand: (key: number, value: number) => string
  /** Backend-recorded session id once the recorder has confirmed one exists
   * (see startSession). Null while offline or before the create call resolves —
   * the app never blocks on this, it's informational (e.g. "view in Data Logs"). */
  backendSessionId: string | null
  /** True state of the /ws/telemetry connection. 'offline_simulation' whenever
   * mode === 'simulation' (no backend socket open); 'replay' whenever
   * mode === 'replay' — there is deliberately no backend socket open in
   * either case, and replay must never be reported as 'connected'. While
   * mode === 'live': 'connected' means the socket is open but no
   * hardware-originated frame has arrived yet (or has gone stale);
   * 'live_hardware' means a fresh frame is actually being received. Only
   * 'live_hardware' may be presented to the user as live vehicle data. */
  link: TelemetryLinkState
}
const TelemetryContext = createContext<TelemetryContextValue | null>(null)
const initialSession: SessionState = { id: 'SIM-001', mode: 'practice', status: 'running', elapsedS: 0, lap: 1, currentLapS: 0, bestLapS: null }
const initialReplay: ReplayState = { status: 'idle', rate: 1, index: 0, totalFrames: 0, startTimestamp: 0, endTimestamp: 0, currentTimestamp: 0, sessionId: null, sessionSource: null }

export function TelemetryProvider({ children }: { children: ReactNode }) {
  const { settings } = useSettings()
  const serviceRef = useRef<TelemetryService | null>(null)
  if (!serviceRef.current) serviceRef.current = new TelemetryService()
  const historyRef = useRef<TelemetryFrame[]>([])
  const [mode, setMode] = useState<TelemetryMode>('simulation')
  const [frame, setFrame] = useState<TelemetryFrame | null>(null)
  const [history, setHistory] = useState<TelemetryFrame[]>([])
  const [faults, setFaults] = useState<Fault[]>([])
  const [ai, setAi] = useState<AiRecommendation | null>(null)
  const [diagnostics, setDiagnostics] = useState<SystemDiagnostics | null>(null)
  const [sensors, setSensors] = useState<SensorHealth[]>([])
  const [running, setRunning] = useState(true)
  const [session, setSession] = useState<SessionState>(initialSession)
  const [scenario, setScenario] = useState<SimulationScenario>('TRACK_RUN')
  const [backendSessionId, setBackendSessionId] = useState<string | null>(null)
  const backendSessionRef = useRef<string | null>(null)
  const recordBufferRef = useRef<RecordSnapshot[]>([])
  const [link, setLink] = useState<TelemetryLinkState>('offline_simulation')
  // Settings read inside the socket's onFrame callback below, which is
  // created once and must not go stale — mirrored into a ref rather than
  // recreating the (stable) socket client every time settings change.
  const settingsRef = useRef(settings)
  useEffect(() => { settingsRef.current = settings }, [settings])

  // Wall-clock time (ms) the last LIVE frame was applied. null whenever no
  // hardware-originated frame has arrived since the socket last opened.
  // Read by the freshness watchdog below to decide 'live_hardware' vs
  // 'connected'. Never touched by simulation/replay.
  const lastFrameAtRef = useRef<number | null>(null)
  const frameParserRef = useRef(new TelemetryParser())
  // FW 2.3: coalesced LIVE updates (see LIVE_UI_MIN_INTERVAL_MS)
  const liveRef = useRef<{
    frame: TelemetryFrame | null; faults: Fault[] | null; ai: AiRecommendation | null
    diagnostics: SystemDiagnostics | null; frames: number; scheduled: boolean; lastFlush: number
  }>({ frame: null, faults: null, ai: null, diagnostics: null, frames: 0, scheduled: false, lastFlush: 0 })

  // ── Replay: one engine instance, owned here, feeding the same `frame`/
  // `ai`/`diagnostics`/`faults` state that LIVE and SIMULATION write to.
  // See services/replay-engine.ts — no other page creates a timer.
  const [replay, setReplay] = useState<ReplayState>(initialReplay)
  const [replayFrames, setReplayFrames] = useState<TelemetryFrame[]>([])
  const [sessionFaultsAll, setSessionFaultsAll] = useState<Fault[]>([])
  const replayEngineRef = useRef<ReplayEngine<ReplayPayload> | null>(null)
  if (!replayEngineRef.current) {
    replayEngineRef.current = new ReplayEngine<ReplayPayload>((snapshot: ReplaySnapshot<ReplayPayload>) => {
      setReplay((current) => ({
        status: snapshot.status,
        rate: snapshot.rate,
        index: snapshot.index,
        totalFrames: snapshot.totalFrames,
        startTimestamp: snapshot.startTimestamp,
        endTimestamp: snapshot.endTimestamp,
        currentTimestamp: snapshot.currentTimestamp,
        sessionId: current.sessionId,
        sessionSource: current.sessionSource,
      }))
      if (snapshot.record) {
        // Authoritative for replay mode: this frame becomes the ONLY thing
        // that updates `frame`/`ai`/`diagnostics` while mode === 'replay' —
        // see the mode-gated sample-interval effect below, which refuses to
        // run at all during replay so nothing else can overwrite this.
        setFrame(snapshot.record.frame)
        setAi(snapshot.record.payload?.ai ?? null)
        setDiagnostics(snapshot.record.payload?.diagnostics ?? null)
      }
    })
  }
  useEffect(() => () => replayEngineRef.current?.dispose(), [])

  // Historical faults up to the current replay position — Diagnostics/
  // events during replay should reflect what had already happened at
  // that point in the recording, not the full session's fault list and
  // never a live fault overwriting it (see loadReplaySession/mode gating).
  useEffect(() => {
    if (mode !== 'replay') return
    if (sessionFaultsAll.length === 0) { setFaults([]); return }
    setFaults(sessionFaultsAll.filter((f) => f.timestamp <= replay.currentTimestamp))
  }, [mode, sessionFaultsAll, replay.currentTimestamp])

  // mode is read inside socket callbacks created once below; mirror into a
  // ref so those closures see the current value without recreating the
  // (stable) socket client every mode change.
  const modeRef = useRef(mode)
  useEffect(() => { modeRef.current = mode }, [mode])

  // FW 2.3: apply the newest coalesced LIVE frame to React state (stable: refs + state setters only)
  const flushLive = useCallback(() => {
    const q = liveRef.current
    q.scheduled = false
    if (modeRef.current !== 'live' || !q.frame) return
    q.lastFlush = performance.now()
    const dt = q.frames / settingsRef.current.sampleRate
    q.frames = 0
    setLink('live_hardware')
    setFrame(q.frame)
    if (settingsRef.current.dataRecording) setHistory(historyRef.current)
    if (q.faults) { setFaults(q.faults); q.faults = null }
    if (q.ai) { setAi(q.ai); q.ai = null }
    if (q.diagnostics) { setDiagnostics({ ...q.diagnostics, refreshRate: settingsRef.current.sampleRate }); q.diagnostics = null }
    setSession((current) => ({ ...current, elapsedS: current.elapsedS + dt, currentLapS: current.currentLapS + dt }))
  }, [])


  // The one WebSocket client for /ws/telemetry, created once. See
  // services/telemetry-socket.ts — it owns connect/reconnect/state.
  const socketRef = useRef<TelemetrySocketClient | null>(null)
  if (!socketRef.current) {
    socketRef.current = new TelemetrySocketClient({
      onState: (state) => {
        if (modeRef.current !== 'live') return
        // A fresh socket connection has no frames yet — always start at
        // 'connected' ("waiting for telemetry"), never inherit a stale
        // 'live_hardware' from before a reconnect. onFrame is the only
        // place link is allowed to advance to 'live_hardware'.
        lastFrameAtRef.current = null
        setLink(state)
      },
      onFrame: ({ snapshot }) => {
        if (modeRef.current !== 'live') return // replay/simulation own state exclusively while active
        const parsed = snapshot as Partial<{
          frame: unknown
          ai: AiRecommendation | null
          diagnostics: SystemDiagnostics | null
          faults: Fault[]
        }> | null
        // The one and only place an inbound LIVE frame is validated —
        // matches TelemetrySnapshotIn on the backend (frame.timestamp/
        // frame.speed required, everything else forward-compatible).
        let frame: TelemetryFrame
        try {
          frame = frameParserRef.current.parse(parsed?.frame)
        } catch {
          return // malformed frame from the gateway — drop it, keep showing the last good one
        }
        lastFrameAtRef.current = Date.now()
        historyRef.current = [...historyRef.current, frame].slice(-HISTORY_SIZE)
        const q = liveRef.current
        q.frame = frame; q.frames += 1
        if (parsed?.faults) q.faults = parsed.faults
        if (parsed?.ai) q.ai = parsed.ai
        if (parsed?.diagnostics) q.diagnostics = parsed.diagnostics
        if (!q.scheduled) {
          q.scheduled = true
          const wait = Math.max(0, LIVE_UI_MIN_INTERVAL_MS - (performance.now() - q.lastFlush))
          window.setTimeout(() => window.requestAnimationFrame(flushLive), wait)
        }
      },
      onError: () => {
        // Malformed backend message or a rejected frame — non-fatal, the
        // dashboard just keeps showing the last good frame until the next
        // valid one arrives.
      },
    })
  }

  // Own the socket's lifecycle: only ever open while mode === 'live', and
  // only once a backend session exists to attach frames to (the backend
  // requires a real sessionId — see record_batch in backend/app/services/recording.py).
  // This is the single place LIVE vs OFFLINE_SIMULATION/REPLAY is decided;
  // no page independently opens a socket or claims a connection state.
  useEffect(() => {
    if (mode === 'replay') {
      socketRef.current!.disconnect()
      setLink('replay')
      return
    }
    if (mode !== 'live') {
      socketRef.current!.disconnect()
      setLink('offline_simulation')
      return
    }
    if (!backendSessionId) {
      setLink('connecting') // waiting on sessionApi.create() from changeMode below
      return
    }
    socketRef.current!.connect(backendSessionId)
  }, [mode, backendSessionId])

  useEffect(() => () => socketRef.current?.disconnect(), [])

  // Freshness watchdog: while LIVE, periodically check whether the last
  // applied frame has gone stale and, if so, downgrade the badge from
  // 'live_hardware' back to 'connected'. This is what makes "socket open"
  // and "telemetry actually arriving" two different, honestly-reported
  // states — see LIVE_FRAME_STALE_MS above and the onFrame/onState
  // handlers, which are the only places link can move the other direction.
  useEffect(() => {
    if (mode !== 'live') return
    const id = window.setInterval(() => {
      const last = lastFrameAtRef.current
      if (last === null) return
      if (Date.now() - last > LIVE_FRAME_STALE_MS) {
        lastFrameAtRef.current = null
        setLink((current) => (current === 'live_hardware' ? 'connected' : current))
      }
    }, LIVE_FRAME_WATCHDOG_MS)
    return () => window.clearInterval(id)
  }, [mode])

  // Mode ownership: only ONE source may write `frame`/`ai`/`diagnostics` at
  // a time. LIVE is driven exclusively by the socket's onFrame handler
  // above — hardware-originated frames only, nothing local. REPLAY is
  // driven by the engine's onUpdate above. This interval belongs to
  // SIMULATION only: it explicitly refuses to run at all for 'replay' AND
  // 'live', so nothing on this dashboard can ever manufacture a frame that
  // reaches the screen while claiming to be live vehicle data.
  useEffect(() => {
    if (mode === 'replay' || mode === 'live') return
    if (!running || session.status !== 'running') return
    const rate = settings.sampleRate
    const id = window.setInterval(() => {
      const dt = 1 / rate
      const snapshot = serviceRef.current!.next(mode, dt, scenario)
      const nextHistory = [...historyRef.current, snapshot.frame].slice(-HISTORY_SIZE)
      historyRef.current = nextHistory
      if (settings.dataRecording) setHistory(nextHistory)
      setFrame(snapshot.frame); setFaults(snapshot.faults); setAi(snapshot.ai); setDiagnostics({ ...snapshot.diagnostics, refreshRate: rate }); setSensors(snapshot.sensors)
      setSession((current) => ({ ...current, elapsedS: current.elapsedS + dt, currentLapS: current.currentLapS + dt }))
      // Buffer for the backend recorder (flushed on its own interval below).
      // This interval only ever runs for mode === 'simulation' (see the
      // early-return above), so this check is a defensive no-op today —
      // kept explicit so the recorder can never accidentally buffer a
      // fabricated frame if this effect is ever extended.
      if (mode === 'simulation' && settings.dataRecording && backendSessionRef.current) {
        // marked so the backend never treats it as the car's latest state (FW 2.2: its AI talks to the real car)
        recordBufferRef.current.push({ frame: { ...snapshot.frame, simulated: true }, ai: snapshot.ai, diagnostics: snapshot.diagnostics, faults: snapshot.faults })
        if (recordBufferRef.current.length > RECORDER_MAX_BUFFER) recordBufferRef.current.shift()
      }
    }, Math.max(20, Math.round(1000 / rate)))
    return () => window.clearInterval(id)
  }, [mode, running, session.status, settings.dataRecording, settings.sampleRate, scenario])

  // Flush the recorder buffer to the backend on its own cadence, independent
  // of sample rate. Best-effort: a failed flush drops that batch rather than
  // retrying forever, so an unreachable backend can't grow unbounded memory
  // or pile up requests once it comes back — see RECORDER_MAX_BUFFER for the
  // ceiling while offline.
  useEffect(() => {
    const id = window.setInterval(() => {
      const sid = backendSessionRef.current
      if (!sid || recordBufferRef.current.length === 0) return
      const batch = recordBufferRef.current
      recordBufferRef.current = []
      recorderApi.sendBatch(sid, batch).catch(() => {
        // Backend unreachable or rejected the batch — drop it silently.
        // This is telemetry recording, not a user action; there is no
        // dialog to show, and the live dashboard already reflects the
        // frame regardless of whether it was persisted.
      })
    }, RECORDER_FLUSH_MS)
    return () => window.clearInterval(id)
  }, [])

  const startSession = useCallback((nextMode: SessionMode = 'practice') => {
    serviceRef.current!.reset('simulation')
    historyRef.current = []
    recordBufferRef.current = []
    setHistory([])
    const localId = `SIM-${Date.now().toString().slice(-4)}`
    setSession({ ...initialSession, id: localId, mode: nextMode, status: 'running' })
    setRunning(true)
    backendSessionRef.current = null
    setBackendSessionId(null)
    sessionApi
      .create(nextMode)
      .then((created) => { backendSessionRef.current = created.id; setBackendSessionId(created.id) })
      .catch(() => { /* backend offline — session stays local-only, dashboard keeps working */ })
  }, [])
  const stopSession = useCallback(() => {
    setSession((value) => ({ ...value, status: 'completed' }))
    const sid = backendSessionRef.current
    if (sid) sessionApi.stop(sid).catch(() => {})
  }, [])
  const pauseSession = useCallback(() => setSession((value) => ({ ...value, status: 'paused' })), [])
  const resumeSession = useCallback(() => { setRunning(true); setSession((value) => ({ ...value, status: 'running' })) }, [])

  // ── Replay controls ──────────────────────────────────────────────
  const loadReplaySession = useCallback(async (sessionId: string): Promise<boolean> => {
    try {
      const [records, sessionFaults] = await Promise.all([
        sessionApi.frames(sessionId),
        faultsApi.list(sessionId).catch(() => [] as Fault[]),
      ])
      if (records.length === 0) return false
      const replayRecords: ReplayFrameRecord<ReplayPayload>[] = records.map((r) => ({
        frame: r.frame,
        payload: { ai: r.ai ?? null, diagnostics: r.diagnostics ?? null },
      }))
      setSessionFaultsAll(sessionFaults.map((f) => ({ ...f, id: String(f.id) })))
      setMode('replay')
      // Per-sensor health isn't part of the recorded schema (see
      // backend/app/models.py TelemetrySnapshotIn — no sensors field),
      // so it can't be replayed honestly. Clear rather than show stale
      // simulation-mode readings under a replayed frame.
      setSensors([])
      setReplay((current) => ({ ...current, sessionId, sessionSource: 'backend' }))
      setReplayFrames(replayRecords.map((r) => r.frame).sort((a, b) => a.timestamp - b.timestamp))
      replayEngineRef.current!.load(replayRecords)
      return true
    } catch {
      return false
    }
  }, [])

  const loadReplayCapture = useCallback((frames: TelemetryFrame[]) => {
    if (frames.length === 0) return
    const replayRecords: ReplayFrameRecord<ReplayPayload>[] = frames.map((frame) => ({ frame, payload: { ai: null, diagnostics: null } }))
    setSessionFaultsAll([])
    setMode('replay')
    setSensors([])
    const localId = `CAPTURE-${Date.now().toString().slice(-6)}`
    setReplay((current) => ({ ...current, sessionId: localId, sessionSource: 'capture' }))
    setReplayFrames([...frames].sort((a, b) => a.timestamp - b.timestamp))
    replayEngineRef.current!.load(replayRecords)
  }, [])

  const playReplay = useCallback(() => replayEngineRef.current!.play(), [])
  const pauseReplay = useCallback(() => replayEngineRef.current!.pause(), [])
  const stopReplay = useCallback(() => replayEngineRef.current!.stop(), [])
  const clearReplay = useCallback(() => {
    replayEngineRef.current!.clear()
    setReplayFrames([])
    setSessionFaultsAll([])
    setReplay((current) => ({ ...current, sessionId: null, sessionSource: null }))
  }, [])
  const seekReplay = useCallback((fraction: number) => replayEngineRef.current!.seekFraction(fraction), [])
  const setReplayRate = useCallback((rate: ReplayRate) => replayEngineRef.current!.setRate(rate), [])

  // Leaving replay mode: pause the engine's loop (position is preserved,
  // so coming back to Replay resumes where it was) rather than letting it
  // keep running in the background while another mode owns the dashboard.
  const prevModeRef = useRef(mode)
  useEffect(() => {
    if (prevModeRef.current === 'replay' && mode !== 'replay') {
      replayEngineRef.current?.pause()
    }
    prevModeRef.current = mode
  }, [mode])

  const changeScenario = useCallback((next: SimulationScenario) => {
    serviceRef.current!.reset('simulation')
    historyRef.current = []
    setHistory([])
    setScenario(next)
  }, [])
  const changeMode = useCallback((next: TelemetryMode) => {
    setMode(next)
    // The backend requires an existing sessionId to attach frames to (see
    // record_batch). Entering LIVE mode without one yet just bootstraps one
    // silently — same call startSession() makes — rather than blocking the
    // mode switch or resetting whatever session/history is already active.
    if (next === 'live' && !backendSessionRef.current) {
      sessionApi
        .create(session.mode)
        .then((created) => { backendSessionRef.current = created.id; setBackendSessionId(created.id) })
        .catch(() => {
          // No backend reachable — the link-lifecycle effect above will
          // keep showing 'connecting' until a session id shows up, which
          // is accurate: we genuinely can't go live yet.
        })
    }
  }, [session.mode])
  // Stage 2d: when the VCU reports a track position, THAT is the lap position (the
  // physical flaps/steering follow the same number). Legacy frames fall back.
  const currentPosition = frame?.track ? frame.track.pos : frame ? (frame.distance % 420) / 420 : 0
  const lapInfo: LapInfo = frame?.track
    ? { lap: frame.track.lap + 1, lastLapTime: frame.track.lastLapMs / 1000, currentLapTime: frame.track.currentLapMs / 1000, position: currentPosition }
    : { lap: session.lap, lastLapTime: session.bestLapS ?? 0, currentLapTime: session.currentLapS, position: currentPosition }
  // 'connection' (the coarse ConnectionStatus badge used across the app —
  // top bar, sidebar, dashboard, settings) is now derived from the REAL
  // socket link state instead of the simulator's fabricated packetLoss
  // number. That number used to say "connected" even with FastAPI
  // stopped; deriving it from `link` means every consumer of `connection`
  // is honest for free, without each page needing to know about `link`.
  const connection: ConnectionStatus =
    link === 'live_hardware'
      ? 'connected'
      : link === 'connected' || link === 'connecting' || link === 'reconnecting'
        ? 'degraded'
        : 'disconnected'

  return <TelemetryContext.Provider value={{ frame, history, faults, ai, diagnostics, sensors, lap: lapInfo, connection, link, running, toggleRunning: () => { setRunning((value) => !value); setSession((value) => ({ ...value, status: value.status === 'running' ? 'paused' : 'running' })) }, mode, setMode: changeMode, session, startSession, pauseSession, resumeSession, stopSession, replay, replayFrames, loadReplaySession, loadReplayCapture, playReplay, pauseReplay, stopReplay, clearReplay, seekReplay, setReplayRate, scenario, setScenario: changeScenario, setSimulationTrack: (id: number) => serviceRef.current!.setSimulationTrack(id), simulateCarCommand: (key: number, value: number) => (mode === 'simulation' ? serviceRef.current!.simulationCommand(key, value, scenario) : 'rejected_state'), backendSessionId }}>{children}</TelemetryContext.Provider>
}
export function useTelemetry() { const ctx = useContext(TelemetryContext); if (!ctx) throw new Error('useTelemetry must be used within TelemetryProvider'); return ctx }
