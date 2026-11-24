import {
  LayoutDashboard,
  Activity,
  Boxes,
  BrainCircuit,
  Stethoscope,
  History,
  Database,
  Settings2,
  SlidersHorizontal,
  LineChart,
  Cpu,
  DownloadCloud,
  Camera,
  Map,
  Bot,
} from 'lucide-react'
import type { VehicleSetup } from '@/types/telemetry'
import type { LucideIcon } from 'lucide-react'

export const PROJECT_NAME = 'Racing Engineer Suite'
export const VEHICLE_ID = 'RV-VCU-01'
export const FIRMWARE_VERSION = 'v2.4.1'
export const TELEMETRY_PROTOCOL_REV = 7
export const TELEMETRY_INTERVAL_MS = 100

/** Sidebar navigation, grouped into logical sections. */
export const NAV_SECTIONS = [
  {
    label: 'Operations',
    items: [
      { label: 'Dashboard', href: '/', icon: LayoutDashboard },
      { label: 'Live Telemetry', href: '/live-telemetry', icon: Activity },
      { label: 'Live Camera', href: '/camera', icon: Camera },
      { label: 'Track Map', href: '/track-map', icon: Map },
      { label: 'Digital Twin', href: '/digital-twin', icon: Boxes },
      { label: 'AI Engineer', href: '/ai-engineer', icon: BrainCircuit },
      { label: 'AI Model', href: '/ai-model', icon: Bot },
    ],
  },
  {
    label: 'Analysis',
    items: [
      { label: 'Diagnostics', href: '/diagnostics', icon: Stethoscope },
      { label: 'Analytics', href: '/analytics', icon: LineChart },
      { label: 'Replay', href: '/replay', icon: History },
      { label: 'Data Logs', href: '/data-logs', icon: Database },
    ],
  },
  {
    label: 'Vehicle',
    items: [
      { label: 'Vehicle Setup', href: '/vehicle-setup', icon: SlidersHorizontal },
      { label: 'Firmware Manager', href: '/firmware', icon: Cpu },
      { label: 'OTA Updates', href: '/ota', icon: DownloadCloud },
      { label: 'Settings', href: '/settings', icon: Settings2 },
    ],
  },
] as const

/** Flat list of all nav items — used for title lookup and iteration. */
export const NAV_ITEMS: { label: string; href: string; icon: LucideIcon }[] = NAV_SECTIONS.flatMap((section) => [...section.items])

// Vehicle operating limits used across gauges and warnings — Stage 2d: the REAL
// hardware (12 V / 2.2 A adapter or 3S LiPo, 2x Johnson on an L298N, 70 mm tyres,
// DS18B20 limits from vcu_logic.h). See lib/scales.ts for the per-source detail.
export const LIMITS = {
  speed: { min: 0, max: 8, warn: 7 }, // km/h wheel speed on the dyno (auto-rescaled from `dyno cal` on charts)
  voltage: { min: 9.0, max: 13.0, warn: 10.5 }, // 12 V adapter / 3S LiPo; VCU derates below 10.5 V
  current: { min: 0, max: 3, warn: 2.0 }, // adapter rated 2.2 A; VCU derates above 2.0 A (tunable)
  motorCurrent: { min: 0, max: 3, warn: 2.0 }, // = pack current (no motor current sensor in the BOM)
  motorTemp: { min: 15, max: 80, warn: 65 }, // CFG_MOTOR_OVERTEMP_C
  batteryTemp: { min: 15, max: 60, warn: 50 }, // CFG_BATTERY_OVERTEMP_C
  battery: { min: 0, max: 100, warn: 20 },
  steering: { min: -30, max: 30 },
  wing: { min: 0, max: 45 },
  rpm: { min: 0, max: 600, warn: 520 }, // wheel rpm at ~8 km/h on a 70 mm tyre
} as const

/** Semantic chart palette — single source of truth for all traces. */
export const CHART_COLORS = {
  primary: 'var(--color-primary)',
  info: 'var(--color-info)',
  success: 'var(--color-success)',
  warning: 'var(--color-warning)',
  purple: 'var(--color-chart-5)',
} as const

export const CHART_WINDOW = 80 // samples visible in live charts

export const TRACK_NAME = 'Monza' // default circuit; the live name comes from the VCU's track id
export const TOTAL_LAPS = 24
export const LAP_LENGTH_M = 420 // legacy Stage-2 simulator only

export const DEFAULT_SETUP: VehicleSetup = {
  wingAngle: 22,
  adaptiveAero: true,
  brakeBias: 54,
  steeringRate: 65,
  ersMap: 3,
  regen: 40,
  tractionControl: true,
  steeringPid: { p: 2.4, i: 0.12, d: 0.045 },
  motorPid: { p: 1.8, i: 0.35, d: 0.02 },
  steeringCenterUs: 1500,
  steeringMinUs: 1000,
  steeringMaxUs: 2000,
  wingCenterUs: 1480,
  canBitrateKbps: 500,
  loraSpreadingFactor: 9,
  telemetryRateHz: 10,
  cellCount: 6,
  capacityMah: 5200,
  cutoffVoltage: 18.0,
}
