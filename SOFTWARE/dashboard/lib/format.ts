import type { ConnectionStatus, HealthLevel, TelemetryLinkState, VcuState, WingData } from '@/types/telemetry'

export function fmt(value: number, digits = 0): string {
  return value.toLocaleString('en-US', {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  })
}

export function clamp(value: number, min: number, max: number): number {
  return Math.min(Math.max(value, min), max)
}

/** Map a value within a range to a 0..100 percentage. */
export function toPct(value: number, min: number, max: number): number {
  if (max === min) return 0
  return clamp(((value - min) / (max - min)) * 100, 0, 100)
}

export function levelFromLimit(
  value: number,
  warn: number,
  max: number,
  invert = false,
): HealthLevel {
  if (invert) {
    // low is bad (e.g. battery %)
    if (value <= warn * 0.5) return 'critical'
    if (value <= warn) return 'warning'
    return 'ok'
  }
  if (value >= max) return 'critical'
  if (value >= warn) return 'warning'
  return 'ok'
}

export const LEVEL_TEXT: Record<HealthLevel, string> = {
  ok: 'text-success',
  warning: 'text-warning',
  critical: 'text-danger',
}

export const LEVEL_BG: Record<HealthLevel, string> = {
  ok: 'bg-success',
  warning: 'bg-warning',
  critical: 'bg-danger',
}

export const LEVEL_LABEL: Record<HealthLevel, string> = {
  ok: 'Nominal',
  warning: 'Warning',
  critical: 'Critical',
}

export function formatClock(date: Date): string {
  return date.toLocaleTimeString('en-GB', {
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
  })
}

export function formatLapTime(seconds: number): string {
  const m = Math.floor(seconds / 60)
  const s = Math.floor(seconds % 60)
  const ms = Math.floor((seconds % 1) * 1000)
  return `${m}:${s.toString().padStart(2, '0')}.${ms.toString().padStart(3, '0')}`
}

/** Human-readable uptime / duration, e.g. "1h 04m" or "12m 30s". */
export function formatDuration(seconds: number): string {
  const h = Math.floor(seconds / 3600)
  const m = Math.floor((seconds % 3600) / 60)
  const s = Math.floor(seconds % 60)
  if (h > 0) return `${h}h ${m.toString().padStart(2, '0')}m`
  if (m > 0) return `${m}m ${s.toString().padStart(2, '0')}s`
  return `${s}s`
}

/** Map a link/connection status onto a health level for status dots. */
export function connToLevel(status: ConnectionStatus): HealthLevel {
  return status === 'connected' ? 'ok' : status === 'degraded' ? 'warning' : 'critical'
}

/** Human label for the real /ws/telemetry link state — used wherever the
 * UI needs to be explicit about what's actually arriving over the socket.
 * 'connected' (socket open, no fresh frame) is deliberately labelled as
 * still WAITING — only 'live_hardware' claims real vehicle data is on
 * screen. See contexts/telemetry-context.tsx for how the two are told
 * apart (lastFrame freshness), and Documentation/TelemetryProtocol.md for
 * the Raspberry Pi gateway -> FastAPI -> WebSocket -> dashboard path. */
export const LINK_LABEL: Record<TelemetryLinkState, string> = {
  connected: 'Connected · Waiting for Telemetry',
  live_hardware: 'Live · Hardware',
  connecting: 'Connecting…',
  reconnecting: 'Reconnecting…',
  disconnected: 'Disconnected',
  offline_simulation: 'Offline / Simulation',
  replay: 'Replay',
}

export function linkToLevel(link: TelemetryLinkState): HealthLevel {
  if (link === 'live_hardware') return 'ok'
  if (link === 'connected' || link === 'connecting' || link === 'reconnecting') return 'warning'
  if (link === 'replay') return 'warning'
  return 'critical'
}

/** REPLAY / REPLAYING / PAUSED / STOPPED — the exact status text required
 * wherever replay mode is shown, so it's never confused with a live
 * connection state. */
export function replayStatusLabel(status: 'idle' | 'playing' | 'paused' | 'stopped'): string {
  if (status === 'playing') return 'REPLAYING'
  if (status === 'paused') return 'PAUSED'
  if (status === 'stopped') return 'STOPPED'
  return 'REPLAY'
}

/** Mean angle across all four independent aero flaps — for compact readouts. */
export function avgWing(w: WingData): number {
  return (w.frontLeft + w.frontRight + w.rearLeft + w.rearRight) / 4
}

/** How asymmetric the current aero trim is (max channel spread). Useful to
 * surface the project's core asymmetric-loading research signal at a glance. */
export function wingAsymmetry(w: WingData): number {
  const vals = [w.frontLeft, w.frontRight, w.rearLeft, w.rearRight]
  return Math.max(...vals) - Math.min(...vals)
}

export const VCU_STATE_LABEL: Record<VcuState, string> = {
  POWER_ON: 'Power On',
  INITIALIZATION: 'Initializing',
  SENSOR_CHECK: 'Sensor Check',
  IDLE: 'Idle',
  DRIVE: 'Drive',
  FAULT: 'Fault',
  EMERGENCY_STOP: 'Emergency Stop',
}

export function vcuStateLevel(state: VcuState): HealthLevel {
  if (state === 'FAULT' || state === 'EMERGENCY_STOP') return 'critical'
  if (state === 'POWER_ON' || state === 'INITIALIZATION' || state === 'SENSOR_CHECK') return 'warning'
  return 'ok'
}

// ── Unit conversion ──────────────────────────────────────────────
export type UnitSystem = 'metric' | 'imperial'

export const kmhToMph = (v: number) => v * 0.621371
export const celsiusToFahrenheit = (v: number) => v * 1.8 + 32

export function convertSpeed(kmh: number, system: UnitSystem): number {
  return system === 'imperial' ? kmhToMph(kmh) : kmh
}

export function convertTemp(celsius: number, system: UnitSystem): number {
  return system === 'imperial' ? celsiusToFahrenheit(celsius) : celsius
}

export const speedUnit = (system: UnitSystem) => (system === 'imperial' ? 'mph' : 'km/h')
export const tempUnit = (system: UnitSystem) => (system === 'imperial' ? '°F' : '°C')
