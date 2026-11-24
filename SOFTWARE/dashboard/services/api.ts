/**
 * Typed client for the FastAPI backend (backend/app/main.py).
 *
 * Every function here degrades the same way: on network failure, a
 * missing backend, or a non-2xx response, it throws — callers are
 * expected to catch that and fall back to a clear "offline/simulation"
 * UI state rather than crash. See contexts/telemetry-context.tsx for
 * the recorder that calls these best-effort, and app/data-logs and
 * app/replay for the read side.
 */
import type {
  SessionMode,
  TelemetryFrame,
  AiRecommendation,
  SystemDiagnostics,
  Fault,
  VehicleSetup,
} from '@/types/telemetry'

import { backendHttpBase } from '@/lib/backend-url'

const API_BASE = backendHttpBase()

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    headers: { 'Content-Type': 'application/json' },
    // Fail fast rather than hanging the UI if the backend is down.
    signal: AbortSignal.timeout(4000),
    ...init,
  })
  if (!response.ok) {
    const body = await response.text().catch(() => '')
    throw new Error(`API ${init?.method ?? 'GET'} ${path} failed: ${response.status} ${body}`)
  }
  return response.json() as Promise<T>
}

export const restApi = {
  get: <T>(path: string) => request<T>(path),
  post: <T>(path: string, body?: unknown) =>
    request<T>(path, { method: 'POST', body: body === undefined ? undefined : JSON.stringify(body) }),
}

export interface BackendSession {
  id: string
  mode: SessionMode
  status: 'idle' | 'running' | 'paused' | 'completed' | 'aborted'
  track: string | null
  createdAt: number
  startedAt: number | null
  endedAt: number | null
  lapCount: number
  bestLapS: number | null
  frameCount: number
}

export interface BackendSessionDetail extends BackendSession {
  stats: {
    frameCount: number
    faultCount: number
    durationS?: number
    maxSpeed?: number
    avgSpeed?: number
    maxMotorTemp?: number
    maxBatteryTemp?: number
    peakCurrent?: number
    distanceM?: number
  }
}

export interface BackendFrameRecord {
  frame: TelemetryFrame
  ai: AiRecommendation | null
  diagnostics: SystemDiagnostics | null
}

/** Health check used to decide live/offline UI state — cheap, short-timeout. */
export async function pingBackend(): Promise<boolean> {
  try {
    await request('/api/health')
    return true
  } catch {
    return false
  }
}

export const sessionApi = {
  create: (mode: SessionMode, track?: string) =>
    restApi.post<BackendSession>('/api/sessions', { mode, track }),
  list: () => restApi.get<BackendSession[]>('/api/sessions'),
  get: (id: string) => restApi.get<BackendSessionDetail>(`/api/sessions/${id}`),
  stop: (id: string) => restApi.post<BackendSession>(`/api/sessions/${id}/stop`),
  pause: (id: string) => restApi.post<BackendSession>(`/api/sessions/${id}/pause`),
  resume: (id: string) => restApi.post<BackendSession>(`/api/sessions/${id}/resume`),
  frames: (id: string) => restApi.get<BackendFrameRecord[]>(`/api/sessions/${id}/frames`),
  exportJsonUrl: (id: string) => `${API_BASE}/api/sessions/${id}/export.json`,
  exportCsvUrl: (id: string) => `${API_BASE}/api/sessions/${id}/export.csv`,
}

export interface RecordSnapshot {
  frame: TelemetryFrame
  ai?: AiRecommendation | null
  diagnostics?: SystemDiagnostics | null
  faults?: Fault[]
}

export const recorderApi = {
  /** Fire-and-forget batch flush — the recorder in telemetry-context calls
   * this every ~1s while a session is running and recording is enabled. */
  sendBatch: (sessionId: string, frames: RecordSnapshot[]) =>
    restApi.post<{ accepted: number }>(`/api/sessions/${sessionId}/frames`, { frames }),
}

export const configurationApi = {
  save: (setup: VehicleSetup) => restApi.post<{ setup: VehicleSetup; version: number; updatedAt: number }>('/api/config', setup),
  load: () => restApi.get<{ setup: VehicleSetup; version: number; updatedAt: number }>('/api/config'),
}

export const faultsApi = {
  list: (sessionId?: string) => restApi.get<Fault[]>(`/api/faults${sessionId ? `?session_id=${sessionId}` : ''}`),
}

export interface HardwareStatus {
  mode: 'SIMULATED' | string
  connected?: boolean
  raspberryPiConnected?: boolean
  modelLoaded?: boolean
  resolution?: string | null
  fps?: number | null
  latencyMs?: number | null
  inferenceLatencyMs?: number | null
  recording?: boolean
  source?: string
  note?: string
}

/** Honest hardware-presence contracts (Phase 12/18 of the project spec):
 * these NEVER report a real device as connected until the backend is
 * actually talking to a Raspberry Pi / camera over CAN, UART, or a video
 * gateway. Until then they always resolve to mode: 'SIMULATED'. */
export const hardwareApi = {
  cameraStatus: () => restApi.get<HardwareStatus>('/api/camera/status'),
  aiStatus: () => restApi.get<HardwareStatus>('/api/ai/status'),
}

/** Racing Engineer AI layer — the Engineer Station's current recommendation,
 * computed deterministically from the latest telemetry (see
 * SOFTWARE/backend/app/ai_engine.py). `available: false` is a normal,
 * expected state (no telemetry yet / AI idle), not an error — callers
 * should treat it as "nothing to show right now", not fall back to a
 * fabricated value. */
export interface AiRecommendationResponse extends Partial<AiRecommendation> {
  available: boolean
  sessionId?: string | null
}

export const aiApi = {
  recommendation: () => restApi.get<AiRecommendationResponse>('/api/ai/recommendation'),
}

export interface FirmwareDevice {
  id: string
  name: string
  chip: string
  current: string
  latest: string
  status: 'up-to-date' | 'update-available' | 'updating' | 'offline'
  connected: boolean
}

export interface OtaJob {
  id: string
  device: string
  version: string
  startedAt: number
  status: 'success' | 'failed' | 'rolled-back'
  durationS: number
  simulated: boolean
}

/** Dev-only, in-memory firmware/OTA contract (Phase 14): every response is
 * explicitly `mode: 'SIMULATED'` because no ESP32-C6/ESC/BMS/aero-servo
 * hardware is connected. Nothing here ever flashes a real device. */
export const firmwareApi = {
  listDevices: () => restApi.get<{ mode: string; note: string; devices: FirmwareDevice[] }>('/api/firmware/devices'),
  stage: (deviceId: string) => restApi.post<{ mode: string; job: OtaJob; note: string }>(`/api/firmware/devices/${deviceId}/stage`),
  listJobs: () => restApi.get<{ mode: string; jobs: OtaJob[]; note: string }>('/api/firmware/jobs'),
}

/** WebSocket telemetry path — used for cross-tab/multi-viewer live fan-out.
 * Returns null during SSR or if WebSocket isn't available in this runtime. */
export function createTelemetrySocket(path = '/ws/telemetry'): WebSocket | null {
  if (typeof WebSocket === 'undefined') return null
  return new WebSocket(`${API_BASE.replace(/^http/, 'ws')}${path}`)
}

// ════════════════════════════════════════════════════════════════════
// Stage 2d — circuit maps, AI engineer desk, VCU tunables, camera relay
// ════════════════════════════════════════════════════════════════════
export interface Insight {
  id: string
  severity: 'info' | 'warning' | 'critical'
  category: string
  title: string
  detail: string
  metrics: Record<string, unknown>
}

export interface ChangeSpec {
  key: number
  name: string
  label: string
  unit: string
  from: number
  to: number
  wire: number
  apply: string
}

export interface VehicleCommand {
  id: number
  created_at: number
  key: number
  name: string
  label: string
  value: number
  displayValue: number
  unit: string
  status: string
  attempts: number
  origin: string
  acked_at: number | null
}

export interface Suggestion {
  id: number
  kind: string
  created_at: number
  updated_at: number
  status: 'proposed' | 'approved' | 'applied' | 'rejected' | 'failed' | 'expired'
  track_id: number | null
  title: string
  body: {
    rationale: string
    changes: ChangeSpec[]
    expectedEffect: string
    risk: string
    evidence: Record<string, unknown>
  }
  outcome: { before?: Record<string, unknown>; after?: Record<string, unknown>; verdict?: string; failure?: string; appliedAt?: number } | null
  commands: VehicleCommand[]
}

export interface LiveAero {
  reason: string
  confidence: number
  recommendedWing: { frontLeft: number; frontRight: number; rearLeft: number; rearRight: number }
  corner: string
  pos: number
  posAhead: number
  lookaheadS: number
  straightModeAhead: boolean
  transitionAhead: boolean
  trackCode: string
}

export interface EngineerState {
  available: boolean
  reason?: string
  running?: boolean
  trackId?: number
  trackCode?: string
  config?: Record<string, number>
  configPending?: boolean
  settings: { powerSource: 'adapter' | 'lipo'; adapterLimitA: number; lookaheadS: number }
  insights: Insight[]
  live: LiveAero | null
  suggestions: Suggestion[]
  commands: VehicleCommand[]
  engine?: string
  frameCount?: number
}

export interface ConfigKeyInfo {
  key: number
  name: string
  label: string
  unit: string
  value: number
  wire: number
  reported: boolean
  min: number
  max: number
  apply: string
  aiTunable: boolean
  description: string
}

export interface TwinState {
  trackId: number
  trackCode: string
  pos: number
  lapTimeS: number
  sectorsS: number[]
  defaultsLapTimeS: number
  vmaxKmh: number
  speedKmh: number[]
  forces: { twinSpeedKmh: number; downforceN: number; dragN: number; clA: number; cdA: number; latG: number; longG: number }
  corner: { n: number; dir: string; radiusM: number } | null
  nextEvent: { type: string; inM: number } | null
}

export interface LapRow {
  lap: number
  trackId: number
  lapTimeS?: number
  twinLapS?: number
  energyWh: number
  peakCurrentA: number
  minVoltageV: number | null
  maxMotorTempC: number | null
  config?: Record<string, number>
}

export interface CameraRelayStatus {
  url: string | null
  origin?: 'operator' | 'announce' | 'env' | null
  announced?: { url: string; ip?: string | null; rssi?: number | null; name?: string | null; ageS: number } | null
  connected: boolean
  frames: number
  errors: number
  lastError: string | null
  fps: number
  lastFrameAgeS: number | null
  viewers: number
  recording: { name: string; startedAt: number } | null
  mp4Available: boolean
}

export interface Recording {
  name: string
  startedAt?: number
  endedAt?: number
  frames?: number
  durationS?: number
  fps?: number
  width?: number
  height?: number
  sizeBytes: number
  files: { name: string; format: 'avi' | 'mp4'; sizeBytes: number }[]
  inProgress: boolean
}

export const engineerApi = {
  state: () => restApi.get<EngineerState>('/api/ai/engineer'),
  approve: (id: number) => restApi.post<Suggestion>(`/api/ai/suggestions/${id}/approve`),
  reject: (id: number) => restApi.post<Suggestion>(`/api/ai/suggestions/${id}/reject`),
}

/** FW 2.2 — GET /api/vehicle/status */
export interface VehicleStatus {
  reporting: boolean
  demoFeed: boolean
  driveMode: 'dyno' | 'track' | null
  hwProfile: 'prototype' | 'pcb_rev_b' | null
  vcuState: string | null
  runState: string
  remoteRun: boolean | null
  motorPower: boolean | null
  killSwitchFitted: boolean | null
  tcActive: boolean | null
  runLaps: number | null
  lap: number | null
  runActive: boolean
  canStartRun: boolean
  startBlockedBy: string[]
  // FW 2.3
  motorRunning: boolean
  motorDutyPct: number | null
  motorCommandPct: number | null
  canRunMotor: boolean
  motorBlockedBy: string[]
  steeringDeg: number | null
  knob: number | null
  demoRunning?: boolean; speedEstimated?: boolean   // FW 2.3.4
  drsPhase: number | null      // FW 2.3.3: 0 off, 1 all FLAT, 2 all MAX hold
  lastCommands: {
    mode: VehicleCommand | null; run: VehicleCommand | null; estop: VehicleCommand | null
    motor: VehicleCommand | null; knob: VehicleCommand | null; calibration: VehicleCommand | null
    drs: VehicleCommand | null; demo?: VehicleCommand | null
  }
}

/** FW 2.3 — GET /api/vehicle/calibration */
export interface CalibrationState {
  reported: boolean
  keys: ConfigKeyInfo[]
  header: string
  lastCommand: VehicleCommand | null
}

export const vehicleApi = {
  status: () => restApi.get<VehicleStatus>('/api/vehicle/status'),
  setMode: (mode: 'dyno' | 'track') => restApi.post<VehicleCommand>('/api/vehicle/mode', { mode }),
  run: (action: 'start' | 'stop' | 'dyno_cal', laps?: number) =>
    restApi.post<VehicleCommand>('/api/vehicle/run', laps === undefined ? { action } : { action, laps }),
  estop: (action: 'trigger' | 'reset') => restApi.post<VehicleCommand>('/api/vehicle/estop', { action }),
  // FW 2.3
  motor: (dutyPct: number) => restApi.post<VehicleCommand>('/api/vehicle/motor', { dutyPct }),
  steeringCenter: () => restApi.post<VehicleCommand>('/api/vehicle/steering/center'),
  demo: (action: 'start' | 'stop' | 'lap' = 'start', trackId?: number) => restApi.post<VehicleCommand>('/api/vehicle/demo', trackId === undefined ? { action } : { action, trackId }),
  drs: (action: 'open' | 'cancel' = 'open') => restApi.post<VehicleCommand>('/api/vehicle/drs', { action }),
  calibration: () => restApi.get<CalibrationState>('/api/vehicle/calibration'),
  calibrationAction: (action: 'save' | 'revert') => restApi.post<VehicleCommand>('/api/vehicle/calibration', { action }),
  config: () => restApi.get<{ reported: boolean; pending: boolean; keys: ConfigKeyInfo[] }>('/api/vehicle/config'),
  write: (name: string, value: number) => restApi.post<VehicleCommand>('/api/vehicle/config', { name, value }),
  selectTrack: (trackId: number) => restApi.post<VehicleCommand>('/api/vehicle/track', { trackId }),
  commands: () => restApi.get<VehicleCommand[]>('/api/vehicle/commands'),
  twin: () => restApi.get<TwinState>('/api/twin'),
  laps: () => restApi.get<{ sessionId: string | null; laps: LapRow[]; bestLapS: number | null }>('/api/laps'),
  station: () => restApi.get<EngineerState['settings']>('/api/station/settings'),
  setStation: (s: Partial<EngineerState['settings']>) => restApi.post<EngineerState['settings']>('/api/station/settings', s),
}

export const cameraApi = {
  status: () => restApi.get<CameraRelayStatus>('/api/camera/relay'),
  setSource: (url: string) => restApi.post<CameraRelayStatus>('/api/camera/source', { url }),
  startRecording: (sessionId?: string | null) => restApi.post<{ name: string }>('/api/camera/record/start', { sessionId: sessionId ?? null }),
  stopRecording: () => restApi.post<Recording>('/api/camera/record/stop'),
  recordings: () => restApi.get<Recording[]>('/api/camera/recordings'),
  remove: (name: string) => request<{ deleted: string }>(`/api/camera/recordings/${encodeURIComponent(name)}`, { method: 'DELETE' }),
  streamUrl: () => `${API_BASE}/api/camera/stream`,
  snapshotUrl: () => `${API_BASE}/api/camera/snapshot`,
  fileUrl: (name: string) => `${API_BASE}/api/camera/recordings/${encodeURIComponent(name)}`,
  csvUrl: (name: string) => `${API_BASE}/api/camera/recordings/${encodeURIComponent(name)}/telemetry.csv`,
}
