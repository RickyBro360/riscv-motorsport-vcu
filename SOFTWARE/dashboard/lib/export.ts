import type { TelemetryFrame } from '@/types/telemetry'

/** Trigger a client-side download of a text blob. */
export function downloadFile(filename: string, content: string, mime = 'text/csv') {
  const blob = new Blob([content], { type: `${mime};charset=utf-8;` })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  document.body.appendChild(a)
  a.click()
  document.body.removeChild(a)
  URL.revokeObjectURL(url)
}

const TELEMETRY_COLUMNS: { key: string; get: (f: TelemetryFrame) => number | string }[] = [
  { key: 'timestamp', get: (f) => f.timestamp },
  { key: 'speed_kmh', get: (f) => f.speed.toFixed(2) },
  { key: 'rpm', get: (f) => f.rpm },
  { key: 'voltage_v', get: (f) => f.voltage.toFixed(3) },
  { key: 'pack_current_a', get: (f) => f.current.toFixed(2) },
  { key: 'motor_current_a', get: (f) => f.motorCurrent.toFixed(2) },
  { key: 'motor_temp_c', get: (f) => f.motorTemp.toFixed(2) },
  { key: 'battery_temp_c', get: (f) => f.batteryTemp.toFixed(2) },
  { key: 'battery_pct', get: (f) => f.battery.toFixed(1) },
  { key: 'roll_deg', get: (f) => f.imu.roll.toFixed(2) },
  { key: 'pitch_deg', get: (f) => f.imu.pitch.toFixed(2) },
  { key: 'yaw_deg', get: (f) => f.imu.yaw.toFixed(2) },
  { key: 'steering_deg', get: (f) => f.steering.toFixed(2) },
  { key: 'throttle_pct', get: (f) => f.throttle.toFixed(1) },
  { key: 'brake_pct', get: (f) => f.brake.toFixed(1) },
  { key: 'wing_fl_deg', get: (f) => f.wing.frontLeft.toFixed(2) },
  { key: 'wing_fr_deg', get: (f) => f.wing.frontRight.toFixed(2) },
  { key: 'wing_rl_deg', get: (f) => f.wing.rearLeft.toFixed(2) },
  { key: 'wing_rr_deg', get: (f) => f.wing.rearRight.toFixed(2) },
  { key: 'wing_fl_target_deg', get: (f) => f.wingTarget.frontLeft.toFixed(2) },
  { key: 'wing_fr_target_deg', get: (f) => f.wingTarget.frontRight.toFixed(2) },
  { key: 'wing_rl_target_deg', get: (f) => f.wingTarget.rearLeft.toFixed(2) },
  { key: 'wing_rr_target_deg', get: (f) => f.wingTarget.rearRight.toFixed(2) },
  { key: 'vcu_state', get: (f) => f.vcuState },
  { key: 'glat_g', get: (f) => f.gForceLat.toFixed(3) },
  { key: 'glong_g', get: (f) => f.gForceLong.toFixed(3) },
]

/** Serialise telemetry frames into a CSV string with a header row. */
export function framesToCsv(frames: TelemetryFrame[]): string {
  const header = TELEMETRY_COLUMNS.map((c) => c.key).join(',')
  const rows = frames.map((f) => TELEMETRY_COLUMNS.map((c) => c.get(f)).join(','))
  return [header, ...rows].join('\n')
}

/** Generic array-of-objects to CSV. */
export function rowsToCsv<T extends object>(rows: T[], columns: (keyof T)[]): string {
  const header = columns.map(String).join(',')
  const body = rows.map((r) =>
    columns
      .map((c) => {
        const v = r[c]
        const s = String(v ?? '')
        return s.includes(',') ? `"${s}"` : s
      })
      .join(','),
  )
  return [header, ...body].join('\n')
}
