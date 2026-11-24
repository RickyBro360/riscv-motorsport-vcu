/**
 * Chart and gauge scales from the hardware actually on the car (Stage 2d),
 * not from a generic RC car:
 *
 *   supply      12 V / 2.2 A adapter (bench) or 3S LiPo — INA260, ±15 A sensor
 *   motors      2x Johnson on one L298N (forward only). RPM rating unknown (no label):
 *               the speed axis comes from `dyno cal` (dynoVmaxCkmh) when the VCU has
 *               reported it, else from the fastest speed seen, never from a guess.
 *   wheels      70 mm tyre (ASSUMED, 1:10 of a 2026 F1 tyre), 1 magnet per wheel
 *   flaps       0..45 deg (MG996R LR/RR with feedback, SG90 LF/RF open loop)
 *   steering    ±30 deg, MG995 servo (open loop)
 *   IMU         MPU6050 ±4 g range; on the dyno the chassis does not corner, so
 *               measured lateral g is ~0 and the modelled value is shown next to it
 *   temps       DS18B20, motor limit 80 °C, battery limit 60 °C (vcu_logic.h)
 */
import type { TelemetryFrame } from '@/types/telemetry'

export type PowerSource = 'adapter' | 'lipo'

export const SUPPLY = {
  adapter: { label: '12 V / 2.2 A adapter', limitA: 2.2, derateA: 2.0, vMin: 9, vMax: 13, iMax: 3, pMax: 30 },
  lipo: { label: '3S LiPo', limitA: 14, derateA: 8, vMin: 9, vMax: 13, iMax: 15, pMax: 160 },
} as const

export const MOTOR_LIMIT_C = 80
export const BATTERY_LIMIT_C = 60
export const WHEEL_DIAMETER_M = 0.07

export function speedMaxKmh(frame: TelemetryFrame | null, history: TelemetryFrame[]): number {
  const cal = Number(frame?.vcuConfig?.dynoVmaxCkmh ?? 0) / 100
  const seen = history.reduce((m, f) => Math.max(m, f.speed, f.wheels?.rl ?? 0, f.wheels?.rr ?? 0), 0)
  const base = cal > 0.2 ? cal * 1.15 : Math.max(seen * 1.25, 2)
  return Math.ceil(base * 2) / 2
}

export function wheelRpm(kmh: number, diameterM: number = WHEEL_DIAMETER_M): number {
  return ((kmh / 3.6) / (Math.PI * diameterM)) * 60
}
