/**
 * Analytics calculation layer.
 *
 * Pure functions over an array of recorded frames (plus optional AI/fault
 * records fetched alongside them) — no React, no fetching. Used by
 * app/analytics/page.tsx so metric logic isn't buried inside components
 * and can be unit-tested on its own (see the "session comparison" and
 * "empty/partial telemetry" tests this module is written to satisfy).
 *
 * Every metric here is computed from what's actually in the frames. If a
 * field is missing or there's nothing to average, the metric resolves to
 * `null` and the UI shows "N/A" — nothing here fabricates a plausible
 * number.
 */

import type { AiRecommendation, Fault, SystemDiagnostics, TelemetryFrame, WingData } from '@/types/telemetry'

export interface AnalyticsFrameRecord {
  frame: TelemetryFrame
  ai?: AiRecommendation | null
  diagnostics?: SystemDiagnostics | null
}

export type Metric = number | null

export interface PerformanceMetrics {
  maxSpeed: Metric
  avgSpeed: Metric
  distanceM: Metric
  durationS: Metric
  lapCount: Metric
  bestLapS: Metric
}

export interface PowerMetrics {
  avgVoltage: Metric
  minVoltage: Metric
  maxCurrent: Metric
  avgCurrent: Metric
  energyWh: Metric
  batteryUsedPct: Metric
  efficiencyWhPerKm: Metric
}

export interface ThermalMetrics {
  maxMotorTemp: Metric
  maxBatteryTemp: Metric
  avgMotorTemp: Metric
  avgBatteryTemp: Metric
}

export interface DynamicsMetrics {
  peakLateralG: Metric
  peakLongitudinalG: Metric
  peakYawRate: Metric
  maxRoll: Metric
  maxPitch: Metric
}

export interface AeroMetrics {
  avgFrontLeft: Metric
  avgFrontRight: Metric
  avgRearLeft: Metric
  avgRearRight: Metric
  maxWingAngle: Metric
  wingActivity: Metric // mean absolute delta between consecutive frames, deg
}

export interface ReliabilityMetrics {
  faultCount: Metric
  warningCount: Metric
  criticalCount: Metric
  communicationEvents: Metric
}

export interface AiMetrics {
  recommendationCount: Metric
  categoryCounts: Record<string, number>
  avgConfidence: Metric
}

export interface SessionAnalytics {
  frameCount: number
  performance: PerformanceMetrics
  power: PowerMetrics
  thermal: ThermalMetrics
  dynamics: DynamicsMetrics
  aero: AeroMetrics
  reliability: ReliabilityMetrics
  ai: AiMetrics
}

function avg(values: number[]): Metric {
  return values.length ? values.reduce((sum, v) => sum + v, 0) / values.length : null
}
function max(values: number[]): Metric {
  return values.length ? Math.max(...values) : null
}
function collect<T>(records: AnalyticsFrameRecord[], pick: (f: TelemetryFrame) => T | undefined | null): T[] {
  const out: T[] = []
  for (const r of records) {
    const v = pick(r.frame)
    if (v !== undefined && v !== null && !(typeof v === 'number' && Number.isNaN(v))) out.push(v)
  }
  return out
}
function meanAbsDelta(values: number[]): Metric {
  if (values.length < 2) return values.length === 1 ? 0 : null
  let total = 0
  for (let i = 1; i < values.length; i++) total += Math.abs(values[i] - values[i - 1])
  return total / (values.length - 1)
}

function computePerformance(records: AnalyticsFrameRecord[]): PerformanceMetrics {
  const speeds = collect(records, (f) => f.speed)
  const distances = collect(records, (f) => f.distance)
  const timestamps = records.map((r) => r.frame.timestamp)
  const durationS = timestamps.length > 1 ? (Math.max(...timestamps) - Math.min(...timestamps)) / 1000 : null
  const lapValues = collect(records, (f) => (f as unknown as { lap?: number }).lap)
  return {
    maxSpeed: max(speeds),
    avgSpeed: avg(speeds),
    distanceM: distances.length ? Math.max(...distances) : null,
    durationS,
    lapCount: lapValues.length ? Math.max(...lapValues) : null,
    bestLapS: null, // no per-lap timestamp boundary in TelemetryFrame — honestly unavailable rather than guessed
  }
}

function computePower(records: AnalyticsFrameRecord[]): PowerMetrics {
  const voltages = collect(records, (f) => f.voltage)
  const currents = collect(records, (f) => f.current)
  const batteries = collect(records, (f) => f.battery)
  const timestamps = records.map((r) => r.frame.timestamp)
  const durationH = timestamps.length > 1 ? (Math.max(...timestamps) - Math.min(...timestamps)) / 1000 / 3600 : null
  // Energy: trapezoidal integration of instantaneous power (V*A) over time.
  let energyWh: Metric = null
  const sorted = [...records].sort((a, b) => a.frame.timestamp - b.frame.timestamp)
  if (sorted.length > 1) {
    let wh = 0
    for (let i = 1; i < sorted.length; i++) {
      const a = sorted[i - 1].frame
      const b = sorted[i].frame
      const dtH = (b.timestamp - a.timestamp) / 1000 / 3600
      if (dtH <= 0) continue
      const pA = a.voltage * a.current
      const pB = b.voltage * b.current
      wh += ((pA + pB) / 2) * dtH
    }
    energyWh = wh
  }
  const batteryUsedPct = batteries.length ? batteries[0] - batteries[batteries.length - 1] : null
  const distances = collect(records, (f) => f.distance)
  const distanceKm = distances.length ? (Math.max(...distances) - Math.min(...distances)) / 1000 : null
  const efficiencyWhPerKm = energyWh !== null && distanceKm && distanceKm > 0 ? energyWh / distanceKm : null
  void durationH
  return {
    avgVoltage: avg(voltages),
    minVoltage: voltages.length ? Math.min(...voltages) : null,
    maxCurrent: max(currents),
    avgCurrent: avg(currents),
    energyWh,
    batteryUsedPct,
    efficiencyWhPerKm,
  }
}

function computeThermal(records: AnalyticsFrameRecord[]): ThermalMetrics {
  const motor = collect(records, (f) => f.motorTemp)
  const batt = collect(records, (f) => f.batteryTemp)
  return {
    maxMotorTemp: max(motor),
    maxBatteryTemp: max(batt),
    avgMotorTemp: avg(motor),
    avgBatteryTemp: avg(batt),
  }
}

function computeDynamics(records: AnalyticsFrameRecord[]): DynamicsMetrics {
  const lat = collect(records, (f) => f.gForceLat)
  const lon = collect(records, (f) => f.gForceLong)
  const yaw = collect(records, (f) => f.imu?.yaw)
  const roll = collect(records, (f) => f.imu?.roll)
  const pitch = collect(records, (f) => f.imu?.pitch)
  return {
    peakLateralG: lat.length ? Math.max(...lat.map(Math.abs)) : null,
    peakLongitudinalG: lon.length ? Math.max(...lon.map(Math.abs)) : null,
    peakYawRate: yaw.length ? Math.max(...yaw.map(Math.abs)) : null,
    maxRoll: roll.length ? Math.max(...roll.map(Math.abs)) : null,
    maxPitch: pitch.length ? Math.max(...pitch.map(Math.abs)) : null,
  }
}

function computeAero(records: AnalyticsFrameRecord[]): AeroMetrics {
  const wings = collect(records, (f) => f.wing).filter(Boolean) as WingData[]
  const fl = wings.map((w) => w.frontLeft)
  const fr = wings.map((w) => w.frontRight)
  const rl = wings.map((w) => w.rearLeft)
  const rr = wings.map((w) => w.rearRight)
  const allAngles = [...fl, ...fr, ...rl, ...rr]
  const avgAll = wings.map((w) => (w.frontLeft + w.frontRight + w.rearLeft + w.rearRight) / 4)
  return {
    avgFrontLeft: avg(fl),
    avgFrontRight: avg(fr),
    avgRearLeft: avg(rl),
    avgRearRight: avg(rr),
    maxWingAngle: allAngles.length ? Math.max(...allAngles.map(Math.abs)) : null,
    wingActivity: meanAbsDelta(avgAll),
  }
}

function computeReliability(records: AnalyticsFrameRecord[], sessionFaults: Fault[]): ReliabilityMetrics {
  const warnings = sessionFaults.filter((f) => f.level === 'warning').length
  const criticals = sessionFaults.filter((f) => f.level === 'critical').length
  const commEvents = sessionFaults.filter((f) => f.subsystem?.toLowerCase().includes('can') || f.subsystem?.toLowerCase().includes('lora') || f.subsystem?.toLowerCase().includes('comm')).length
  return {
    faultCount: sessionFaults.length,
    warningCount: warnings,
    criticalCount: criticals,
    communicationEvents: commEvents,
  }
}

function computeAi(records: AnalyticsFrameRecord[]): AiMetrics {
  const recs = records.map((r) => r.ai).filter((a): a is AiRecommendation => !!a)
  const categoryCounts: Record<string, number> = {}
  for (const r of recs) {
    const key = r.corner ?? 'Unknown'
    categoryCounts[key] = (categoryCounts[key] ?? 0) + 1
  }
  const confidences = recs.map((r) => r.confidence).filter((c) => typeof c === 'number')
  return {
    recommendationCount: recs.length ? recs.length : null,
    categoryCounts,
    avgConfidence: avg(confidences),
  }
}

export function computeSessionAnalytics(records: AnalyticsFrameRecord[], sessionFaults: Fault[] = []): SessionAnalytics {
  return {
    frameCount: records.length,
    performance: computePerformance(records),
    power: computePower(records),
    thermal: computeThermal(records),
    dynamics: computeDynamics(records),
    aero: computeAero(records),
    reliability: computeReliability(records, sessionFaults),
    ai: computeAi(records),
  }
}

/** Formats a Metric for display — the single place "N/A" is decided, so
 * no page independently invents a fallback string for a missing value. */
export function fmtMetric(value: Metric, digits = 1, unit = ''): string {
  if (value === null || value === undefined || Number.isNaN(value)) return 'N/A'
  return `${value.toFixed(digits)}${unit ? ` ${unit}` : ''}`
}
