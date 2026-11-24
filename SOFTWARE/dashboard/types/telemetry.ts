export type ConnectionStatus = 'connected' | 'degraded' | 'disconnected'

/** State of the frontend's real link to FastAPI's /ws/telemetry socket.
 * 'offline_simulation' is a distinct, explicit state — not a degraded form
 * of 'connected' — for whenever telemetry mode is 'simulation'/'replay',
 * i.e. no backend WebSocket is expected to be open at all. */
// 'connected' = the /ws/telemetry socket is open but no TelemetryFrame has
// arrived yet (or the last one has gone stale) — "waiting for telemetry".
// 'live_hardware' = the socket is open AND a frame has arrived within the
// freshness window (see LIVE_FRAME_STALE_MS in contexts/telemetry-context.tsx).
// Only 'live_hardware' may ever be shown to the user as live vehicle data;
// a bare open socket is never enough to claim that.
export type TelemetryLinkState =
  | 'connecting'
  | 'connected'
  | 'live_hardware'
  | 'reconnecting'
  | 'disconnected'
  | 'offline_simulation'
  | 'replay'

export type HealthLevel = 'ok' | 'warning' | 'critical'

export type CornerType =
  | 'Straight'
  | 'Braking Zone'
  | 'Slow Corner'
  | 'Medium Corner'
  | 'Fast Corner'
  | 'Chicane'

export interface ImuData {
  roll: number // deg
  pitch: number // deg
  yaw: number // deg
}

// Four independently actuated aero flaps — the locked spec's asymmetric
// active-aero research objective requires front/rear AND left/right to be
// controllable independently, so this is a 4-channel struct rather than a
// single left/right pair.
export interface WingData {
  frontLeft: number // deg
  frontRight: number // deg
  rearLeft: number // deg
  rearRight: number // deg
}

// VCU operating state machine (Phase 5). The website only displays this —
// the real safety authority lives on the future ESP32-C6 VCU firmware.
export type VcuState =
  | 'POWER_ON'
  | 'INITIALIZATION'
  | 'SENSOR_CHECK'
  | 'IDLE'
  | 'DRIVE'
  | 'FAULT'
  | 'EMERGENCY_STOP'

// Selectable deterministic test scenarios for the simulator (Phase 4).
export type SimulationScenario =
  | 'TRACK_RUN'
  | 'NORMAL_DRIVING'
  | 'HIGH_LOAD'
  | 'CORNERING'
  | 'LOW_BATTERY'
  | 'OVER_TEMPERATURE'
  | 'COMMUNICATION_LOSS'
  | 'SENSOR_FAULT'
  | 'EMERGENCY_STOP'

// Telemetry protocol version. Bump this — and add a TelemetryFrameV2 type
// alongside (not instead of) this one — whenever a field is added, removed,
// or changes meaning/units, per Documentation/TelemetryProtocol.md. Old
// recorded sessions may predate this field entirely; readers must treat a
// missing schemaVersion as 1, never reject the frame.
export const TELEMETRY_SCHEMA_VERSION = 1 as const

export interface TelemetryFrame {
  schemaVersion: number // protocol version this frame was produced under (see TELEMETRY_SCHEMA_VERSION)
  timestamp: number
  speed: number // km/h
  voltage: number // V
  current: number // A (pack)
  motorCurrent: number // A
  motorTemp: number // °C
  batteryTemp: number // °C
  battery: number // %
  imu: ImuData
  steering: number // deg (-30..30)
  wing: WingData // actual measured flap angles (position sensors)
  wingTarget: WingData // VCU-accepted setpoint the servos are chasing
  throttle: number // %
  brake: number // %
  gForceLat: number // g
  gForceLong: number // g
  rpm: number
  distance: number // m into lap
  wheelAngle: number // rad — accumulated wheel rotation for the digital twin
  vcuState: VcuState
  // ── Stage 2d additive keys (TelemetryProtocol.md §4b). Optional: frames
  // recorded before Stage 2d, and the Stage-2 simulator, do not carry them. ──
  wheels?: { fl: number; fr: number; rl: number; rr: number } // km/h, hall sensors
  gyroZ?: number // deg/s yaw rate
  vibrationMg?: number // RMS milli-g (VCU VibrationMeter)
  odometerM?: number | null // total dyno wheel travel (not carried over LoRa)
  aeroMode?: number // 0 baseline, 1 AI-assist, 2 failsafe hold
  lastAiReason?: string // VCU verdict on the last AI message
  track?: TrackTelemetry
  runState?: 'off' | 'track_drive' | 'bench_laps' | 'bench_motor' | 'dyno_cal' | string
  dynoCal?: 'idle' | 'find_start' | 'hold_cap' | 'done' | 'failed' | string
  vcuConfig?: Record<string, number | boolean> | null // VCU tunables as reported by CONFIG_REPORT
  configAcks?: { key: number; status: number; value: number }[]
  vcu?: VcuModeTelemetry // FW 2.2 (CAN 0x148): drive mode, hardware, kill switch, TC, knob
  demoFeed?: boolean // sent by backend/tools/demo_car.py — not the car
  simulated?: boolean // recorded from the dashboard's own offline simulation
  derived?: {
    powerW?: number
    energyWh?: number
    whPerKm?: number
    socCoulombPct?: number
    packResistanceOhm?: number
    slipRatio?: number
    secondsToMotorLimit?: number | null
    secondsToBatteryLimit?: number | null
  }
}

/** FW 2.2 — how the VCU is operating (CAN 0x148). Fields the LoRa link does not carry are
 * null (MODE B carries the drive mode and the motor-power bit in every packet). */
export interface VcuModeTelemetry {
  driveMode: 'dyno' | 'track' | null
  hwProfile: 'prototype' | 'pcb_rev_b' | null
  killSwitchFitted: boolean | null
  motorPower: boolean | null // kill switch (BTS7004) on; null when no kill switch is fitted
  remoteRun: boolean | null // the active run was started from the dashboard
  tcActive: boolean | null
  tcCutPct: number | null
  groundLinkOk: boolean | null // the VCU sees the Pi heartbeat with the ground link up
  aeroStraight: boolean | null
  encoderFitted: boolean | null
  knob: number | null
  runLaps: number | null
}

/** Where the VCU says the car is on the stored circuit (CAN 0x145/0x146). The
 * VCU moves the flaps and the steering from this same number, so drawing the
 * car here keeps the dashboard in sync with the physical actuators. */
export interface TrackTelemetry {
  id: number
  pos: number // lap fraction 0..1
  lap: number // completed laps in this run
  currentLapMs: number
  lastLapMs: number
  profileDutyPct: number | null
  latGModel: number | null // modelled full-scale lateral g (null over LoRa)
  runActive: boolean
  straightMode: boolean
  brakingZone: boolean
  configPending: boolean
  benchRun: boolean
  sector: number // 0..2
  stalled: boolean
}

// Explicit alias so code/docs can refer to the exact wire version by name,
// matching the locked spec's "TelemetryFrameV1 / TelemetryFrameV2" example.
// TelemetryFrame always means "whatever the current version is" — prefer it
// in day-to-day code; use TelemetryFrameV1 when a version-specific type is
// actually needed (e.g. a migration function converting V1 -> V2 later).
export type TelemetryFrameV1 = TelemetryFrame

export interface Fault {
  id: string
  code: string
  message: string
  level: HealthLevel
  timestamp: number
  subsystem: string
}

export interface AiRecommendation {
  corner: CornerType
  grip: number // 0..1
  recommendedWing: WingData
  recommendedSpeed: number // km/h target for the current phase
  confidence: number // 0..1
  efficiency: number // 0..100 aero/energy efficiency score
  racingLine: string
  note: string
  // null when the backend has not recorded enough movement to measure them:
  // these are integrated from pack voltage x current (backend
  // app/services/energy.py), never estimated from a formula.
  energyPerLap: number | null // Wh consumed per lap, measured
  predictedRangeLaps: number | null // laps remaining at the measured consumption
  whPerKm?: number | null // measured consumption
  energyMeasured?: boolean // false = the fields above are null, not estimated
  warnings: string[]
  // --- Racing Engineer AI layer additions (smallest compatible addition —
  // see SOFTWARE/backend/app/ai_engine.py). Optional so older recorded
  // sessions / any sender that predates this still validates. ---
  type?: 'DRIVER_ADVICE' | 'AERO_ADVICE' | 'SAFETY_ADVICE'
  timestamp?: number
  reason?: string
  target?: string
  validityMs?: number
  validUntil?: number
  source?: string
  videoAvailable?: boolean
  videoUsed?: boolean
}

export interface SensorHealth {
  id: string
  name: string
  level: HealthLevel
  value: string
  subsystem: string
}

export interface CanNode {
  id: string // hex CAN ID
  name: string
  level: HealthLevel
  loadPct: number // bus load contribution
  lastSeenMs: number // ms since last frame
}

export interface LinkHealth {
  rssi: number // dBm
  snr: number // dB
  spreadingFactor: number
  bandwidthKhz: number
  txPowerDbm: number
}

export interface SystemDiagnostics {
  can: ConnectionStatus
  lora: ConnectionStatus
  packetLoss: number // %
  cpu: number // %
  memory: number // %
  watchdog: HealthLevel
  ping: number // ms
  packetRate: number // Hz
  refreshRate: number // Hz
  uptimeS: number // seconds since VCU boot
  temperatureC: number // SoC die temperature
  canNodes: CanNode[]
  loraLink: LinkHealth
}

export interface SessionLog {
  id: string
  name: string
  track: string
  date: string
  duration: string
  laps: number
  bestLap: string
  topSpeed: number
  avgSpeed: number
  faults: number
  status: 'Completed' | 'Aborted' | 'Testing'
}

// ── Vehicle setup ────────────────────────────────────────────────
export interface PidGains {
  p: number
  i: number
  d: number
}

export interface VehicleSetup {
  // aero
  wingAngle: number
  adaptiveAero: boolean
  brakeBias: number
  steeringRate: number
  // energy
  ersMap: number
  regen: number
  tractionControl: boolean
  // control loops
  steeringPid: PidGains
  motorPid: PidGains
  // servo calibration
  steeringCenterUs: number
  steeringMinUs: number
  steeringMaxUs: number
  wingCenterUs: number
  // comms
  canBitrateKbps: number
  loraSpreadingFactor: number
  telemetryRateHz: number
  // battery
  cellCount: number
  capacityMah: number
  cutoffVoltage: number
}

// ── Firmware / OTA ───────────────────────────────────────────────
export interface FirmwareDevice {
  id: string
  name: string
  chip: string
  current: string
  latest: string
  status: 'up-to-date' | 'update-available' | 'updating' | 'offline'
}

export interface OtaJob {
  id: string
  device: string
  version: string
  date: string
  status: 'success' | 'failed' | 'rolled-back'
  durationS: number
}

// ── Analytics ────────────────────────────────────────────────────
export interface LapRecord {
  lap: number
  time: number // seconds
  topSpeed: number
  avgSpeed: number
  energy: number // Wh
  motorPeak: number // °C
}

export type SessionMode = 'practice' | 'qualifying' | 'race' | 'testing'
export type SessionStatus = 'idle' | 'running' | 'paused' | 'completed' | 'aborted'
export type CameraPreset = 'top' | 'front' | 'rear' | 'driver'

export interface VehicleState {
  frame: TelemetryFrame | null
  mode: 'simulation' | 'replay' | 'live'
  session: SessionState
}

export interface SessionState {
  id: string
  mode: SessionMode
  status: SessionStatus
  elapsedS: number
  lap: number
  currentLapS: number
  bestLapS: number | null
}

export type ReplayStatus = 'idle' | 'playing' | 'paused' | 'stopped'
export type ReplayRate = 0.25 | 0.5 | 1 | 2 | 4 | 10

/** Central replay state, driven by the single ReplayEngine instance owned
 * by TelemetryProvider (see services/replay-engine.ts). `status` is the
 * source of truth for the REPLAY / REPLAYING / PAUSED / STOPPED indicator
 * required by the locked spec — it is never derived from the live-link
 * ConnectionStatus, which stays honest about there being no WebSocket
 * involved in replay at all. */
export interface ReplayState {
  status: ReplayStatus
  rate: ReplayRate
  index: number
  totalFrames: number
  startTimestamp: number
  endTimestamp: number
  currentTimestamp: number
  /** Backend session id when replaying recorded data, or a synthetic id
   * for a local rolling-buffer capture. Null when nothing is loaded. */
  sessionId: string | null
  sessionSource: 'backend' | 'capture' | null
}

export interface TrackState {
  name: string
  lapProgress: number
  sector: 1 | 2 | 3
  sectorTimes: [number, number, number]
  position: [number, number]
  racingLine: [number, number][]
}
