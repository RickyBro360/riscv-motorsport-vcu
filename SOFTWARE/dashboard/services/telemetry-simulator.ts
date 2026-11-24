import { TELEMETRY_SCHEMA_VERSION } from '@/types/telemetry'
import type {
  TelemetryFrame,
  AiRecommendation,
  CornerType,
  Fault,
  SystemDiagnostics,
  SensorHealth,
  CanNode,
  LinkHealth,
  WingData,
  VcuState,
  SimulationScenario,
} from '@/types/telemetry'
import { clamp } from '@/lib/format'
import { LAP_LENGTH_M } from '@/constants'

/**
 * Deterministic-ish physically-plausible telemetry generator.
 * Models a single scale car lapping a circuit. A normalised lap
 * position (0..1) drives corner phases which in turn shape speed,
 * steering, IMU and thermal behaviour so every signal correlates.
 *
 * Scenario selection (Phase 4) biases this same physical model rather
 * than swapping in unrelated fake data, so signals stay internally
 * consistent (e.g. OVER_TEMPERATURE still has current draw that tracks
 * throttle, it just starts hot and cools slower).
 */

// Track segments across one lap (fractions of lap distance).
interface Segment {
  end: number
  type: CornerType
  targetSpeed: number // km/h
  steer: number // deg bias
}

const SEGMENTS: Segment[] = [
  { end: 0.12, type: 'Straight', targetSpeed: 62, steer: 0 },
  { end: 0.2, type: 'Braking Zone', targetSpeed: 32, steer: 0 },
  { end: 0.28, type: 'Slow Corner', targetSpeed: 26, steer: -24 },
  { end: 0.4, type: 'Fast Corner', targetSpeed: 50, steer: 14 },
  { end: 0.52, type: 'Straight', targetSpeed: 60, steer: 0 },
  { end: 0.6, type: 'Braking Zone', targetSpeed: 30, steer: 0 },
  { end: 0.68, type: 'Chicane', targetSpeed: 34, steer: 22 },
  { end: 0.76, type: 'Chicane', targetSpeed: 34, steer: -22 },
  { end: 0.88, type: 'Medium Corner', targetSpeed: 44, steer: 18 },
  { end: 1.0, type: 'Straight', targetSpeed: 63, steer: 0 },
]

const WHEEL_RADIUS_M = 0.034 // 1:10 scale tyre

function segmentAt(pos: number): Segment {
  for (const s of SEGMENTS) if (pos <= s.end) return s
  return SEGMENTS[SEGMENTS.length - 1]
}

const FAULT_POOL: Omit<Fault, 'id' | 'timestamp'>[] = [
  { code: 'THR-0x21', message: 'Motor temperature approaching limit', level: 'warning', subsystem: 'Powertrain' },
  { code: 'BMS-0x14', message: 'Cell voltage imbalance detected', level: 'warning', subsystem: 'Battery' },
  { code: 'CAN-0x08', message: 'CAN frame retransmission spike', level: 'warning', subsystem: 'Comms' },
  { code: 'IMU-0x33', message: 'IMU gyro bias drift corrected', level: 'ok', subsystem: 'Sensors' },
  { code: 'LORA-0x02', message: 'LoRa RSSI below threshold', level: 'warning', subsystem: 'Comms' },
  { code: 'PWR-0x41', message: 'Peak current draw exceeded soft limit', level: 'critical', subsystem: 'Powertrain' },
  { code: 'AERO-0x05', message: 'Wing actuator response lag', level: 'warning', subsystem: 'Aero' },
]

const SENSOR_FAULT: Omit<Fault, 'id' | 'timestamp'> = {
  code: 'SNS-0x77',
  message: 'Wing position sensor (AS5600) reporting out-of-range value',
  level: 'critical',
  subsystem: 'Sensors',
}
const COMMS_FAULT: Omit<Fault, 'id' | 'timestamp'> = {
  code: 'LORA-0x0F',
  message: 'LoRa link timeout — telemetry uplink lost',
  level: 'critical',
  subsystem: 'Comms',
}
const ESTOP_FAULT: Omit<Fault, 'id' | 'timestamp'> = {
  code: 'SAFE-0x01',
  message: 'Emergency stop commanded — actuators zeroed, wings at safe fallback',
  level: 'critical',
  subsystem: 'Safety',
}

export class TelemetrySimulator {
  private t = 0 // seconds
  private pos = 0 // lap position 0..1
  private speed = 20
  private motorTemp = 42
  private batteryTemp = 30
  private battery = 94
  private voltage = 24.6
  private yaw = 0
  private wheelAngle = 0
  private lapCount = 1
  private lapStart = 0
  private lastLapTime = 78.4
  private faults: Fault[] = []
  private faultAcc = 0
  private energyAcc = 0 // Wh consumed this lap
  private lastEnergyPerLap = 0
  private wing: WingData = { frontLeft: 8, frontRight: 8, rearLeft: 8, rearRight: 8 }
  private vcuState: VcuState = 'POWER_ON'
  private packetLossBase = 0

  private noise(scale: number) {
    return (Math.random() - 0.5) * scale
  }

  /** Advance the VCU boot sequence, then hold in IDLE/DRIVE based on motion. */
  private advanceVcuState(scenario: SimulationScenario, moving: boolean) {
    if (scenario === 'EMERGENCY_STOP') {
      this.vcuState = 'EMERGENCY_STOP'
      return
    }
    if (scenario === 'SENSOR_FAULT' && this.t > 2) {
      this.vcuState = 'FAULT'
      return
    }
    switch (this.vcuState) {
      case 'POWER_ON':
        if (this.t > 0.4) this.vcuState = 'INITIALIZATION'
        break
      case 'INITIALIZATION':
        if (this.t > 1.0) this.vcuState = 'SENSOR_CHECK'
        break
      case 'SENSOR_CHECK':
        if (this.t > 1.6) this.vcuState = 'IDLE'
        break
      case 'IDLE':
        if (moving) this.vcuState = 'DRIVE'
        break
      case 'DRIVE':
        if (!moving) this.vcuState = 'IDLE'
        break
      case 'FAULT':
      case 'EMERGENCY_STOP':
        // Requires an explicit scenario/session reset — the dashboard cannot
        // self-clear a safety state, matching the locked safety spec.
        break
    }
  }

  step(dt = 0.1, scenario: SimulationScenario = 'NORMAL_DRIVING'): TelemetryFrame {
    this.t += dt

    if (scenario === 'EMERGENCY_STOP') {
      this.advanceVcuState(scenario, false)
      this.speed = clamp(this.speed - 120 * dt, 0, 66)
      this.wing = { frontLeft: 4, frontRight: 4, rearLeft: 4, rearRight: 4 } // safe fallback
      const wingTarget = { ...this.wing }
      if (this.faultAcc <= 0) {
        this.faults.unshift({ ...ESTOP_FAULT, id: `${Date.now()}-estop`, timestamp: Date.now() })
        this.faultAcc = 1
      }
      return this.frameFrom({ throttle: 0, braking: 100, steering: 0, roll: 0, pitch: 0, longG: 0, motorCurrent: 0, current: 0, rpm: 0, wing: this.wing, wingTarget })
    }

    const seg = segmentAt(this.pos)
    const cornerLock = scenario === 'CORNERING'

    // approach segment target speed smoothly
    const scenarioSpeedScale = scenario === 'HIGH_LOAD' ? 1.05 : 1
    const targetSpeed = cornerLock ? clamp(seg.targetSpeed, 20, 40) : seg.targetSpeed * scenarioSpeedScale
    const accel = targetSpeed > this.speed ? 0.55 : 1.1
    this.speed += (targetSpeed - this.speed) * accel * dt + this.noise(0.4)
    this.speed = clamp(this.speed, 0, 66)

    // advance lap position based on speed (km/h -> m/s)
    const mps = this.speed / 3.6
    this.pos += (mps * dt) / LAP_LENGTH_M
    this.wheelAngle = (this.wheelAngle + (mps / WHEEL_RADIUS_M) * dt) % (Math.PI * 2)
    if (this.pos >= 1) {
      this.pos -= 1
      this.lapCount += 1
      this.lastLapTime = this.t - this.lapStart
      this.lapStart = this.t
      this.lastEnergyPerLap = this.energyAcc
      this.energyAcc = 0
    }

    const throttle = clamp((targetSpeed / 66) * 100 + this.noise(6), 0, 100)
    const braking = seg.type === 'Braking Zone' ? clamp(70 + this.noise(20), 0, 100) : clamp(this.noise(6), 0, 100)

    // steering follows segment bias with easing + micro corrections; CORNERING
    // scenario forces continuous alternating steering input for aero testing.
    const steerBias = cornerLock ? 20 * Math.sin(this.t * 0.9) : seg.steer
    const steerTarget = steerBias + this.noise(3)
    const steering = clamp(steerTarget, -30, 30)

    // IMU: roll from lateral load, pitch from accel/brake
    const latG = (Math.abs(steering) / 30) * (this.speed / 55) * 1.4
    const roll = clamp(-Math.sign(steering) * latG * 9 + this.noise(1.5), -22, 22)
    const longG = ((targetSpeed - this.speed) / 30) * 1.2
    const pitch = clamp(-longG * 6 + this.noise(1), -14, 14)
    this.yaw = (this.yaw + steering * dt * 2.4) % 360
    if (this.yaw < 0) this.yaw += 360

    // thermal model — heat rises with current, cools slowly
    const load = throttle / 100
    const loadGain = scenario === 'HIGH_LOAD' ? 2.2 : 1.4
    const coolGain = scenario === 'OVER_TEMPERATURE' ? 0.006 : 0.02
    this.motorTemp += (load * loadGain - (this.motorTemp - 40) * coolGain) * dt * 6 + this.noise(0.15)
    this.motorTemp = clamp(this.motorTemp, 35, 98)
    this.batteryTemp += (load * (loadGain * 0.5) - (this.batteryTemp - 28) * (coolGain * 0.75)) * dt * 6 + this.noise(0.1)
    this.batteryTemp = clamp(this.batteryTemp, 26, 72)
    if (scenario === 'OVER_TEMPERATURE' && this.t < 1) {
      this.motorTemp = 82
      this.batteryTemp = 58
    }

    // electrical
    const currentGain = scenario === 'HIGH_LOAD' ? 1.3 : 1
    const motorCurrent = clamp(load * 78 * currentGain + this.noise(5), 0, 92)
    const current = clamp(motorCurrent + 8 + this.noise(4), 0, 120)
    if (scenario === 'LOW_BATTERY' && this.t < dt * 1.5) this.battery = 12
    this.battery = clamp(this.battery - current * dt * 0.0016, 0, 100)
    this.voltage = clamp(25.2 - (100 - this.battery) * 0.06 - current * 0.012 + this.noise(0.05), 18, 25.2)
    // energy integration (Wh) — P = V*I, dt in seconds
    this.energyAcc += (this.voltage * current * dt) / 3600

    const rpm = Math.round((this.speed / 66) * 42000 + this.noise(400))

    // Active aero: independent per-flap setpoints. Cornering drives an
    // asymmetric loading split (outside flaps gain angle, inside flaps
    // shed it) which is the project's core research signal; rear flaps
    // run a higher base angle than front for aero balance.
    const cornerFactor = clamp(Math.abs(steering) / 30 + (60 - this.speed) / 60, 0, 1)
    const baseFront = 6 + cornerFactor * 24
    const baseRear = 10 + cornerFactor * 32
    const asym = (steering / 30) * cornerFactor * 10 // + means right-hand load bias
    const wingTarget: WingData = {
      frontLeft: clamp(baseFront - asym, 0, 45),
      frontRight: clamp(baseFront + asym, 0, 45),
      rearLeft: clamp(baseRear - asym * 1.3, 0, 45),
      rearRight: clamp(baseRear + asym * 1.3, 0, 45),
    }
    // servos slew toward target rather than snapping — gives a real
    // actual-vs-target gap for the Active Aero page to visualise.
    const slew = clamp(dt * 60, 0, 1)
    this.wing = {
      frontLeft: this.wing.frontLeft + (wingTarget.frontLeft - this.wing.frontLeft) * slew + this.noise(0.3),
      frontRight: this.wing.frontRight + (wingTarget.frontRight - this.wing.frontRight) * slew + this.noise(0.3),
      rearLeft: this.wing.rearLeft + (wingTarget.rearLeft - this.wing.rearLeft) * slew + this.noise(0.3),
      rearRight: this.wing.rearRight + (wingTarget.rearRight - this.wing.rearRight) * slew + this.noise(0.3),
    }

    // occasional faults — forced pool for COMMUNICATION_LOSS / SENSOR_FAULT
    this.faultAcc += dt
    const forcedFault =
      scenario === 'COMMUNICATION_LOSS' ? COMMS_FAULT : scenario === 'SENSOR_FAULT' ? SENSOR_FAULT : null
    if (forcedFault && this.faults[0]?.code !== forcedFault.code) {
      this.faults.unshift({ ...forcedFault, id: `${Date.now()}-forced`, timestamp: Date.now() })
      this.faults = this.faults.slice(0, 12)
    } else if (!forcedFault && this.faultAcc > 6 && Math.random() < 0.12) {
      this.faultAcc = 0
      const pick = FAULT_POOL[Math.floor(Math.random() * FAULT_POOL.length)]
      this.faults.unshift({
        ...pick,
        id: `${Date.now()}-${Math.random().toString(36).slice(2, 6)}`,
        timestamp: Date.now(),
      })
      this.faults = this.faults.slice(0, 12)
    }

    this.packetLossBase = scenario === 'COMMUNICATION_LOSS' ? 7.5 : 0
    this.advanceVcuState(scenario, this.speed > 1)

    return this.frameFrom({ throttle, braking, steering, roll, pitch, longG, motorCurrent, current, rpm, wing: this.wing, wingTarget })
  }

  private frameFrom(v: {
    throttle: number
    braking: number
    steering: number
    roll: number
    pitch: number
    longG: number
    motorCurrent: number
    current: number
    rpm: number
    wing: WingData
    wingTarget: WingData
  }): TelemetryFrame {
    return {
      schemaVersion: TELEMETRY_SCHEMA_VERSION,
      timestamp: Date.now(),
      speed: this.speed,
      voltage: this.voltage,
      current: v.current,
      motorCurrent: v.motorCurrent,
      motorTemp: this.motorTemp,
      batteryTemp: this.batteryTemp,
      battery: this.battery,
      imu: { roll: v.roll, pitch: v.pitch, yaw: this.yaw },
      steering: v.steering,
      wing: v.wing,
      wingTarget: v.wingTarget,
      throttle: v.throttle,
      brake: v.braking,
      gForceLat: (Math.abs(v.steering) / 30) * (this.speed / 55) * 1.4 * Math.sign(-v.steering || 1),
      gForceLong: v.longG,
      rpm: v.rpm,
      distance: this.pos * LAP_LENGTH_M,
      wheelAngle: this.wheelAngle,
      vcuState: this.vcuState,
    }
  }

  getLapInfo() {
    return {
      lap: this.lapCount,
      lastLapTime: this.lastLapTime,
      currentLapTime: this.t - this.lapStart,
      position: this.pos,
    }
  }

  getFaults(): Fault[] {
    return this.faults
  }

  getRecommendation(frame: TelemetryFrame): AiRecommendation {
    const seg = segmentAt(this.pos)
    const grip = clamp(0.72 + (55 - frame.batteryTemp) * 0.003 - Math.abs(frame.gForceLat) * 0.04 + this.noise(0.02), 0.4, 0.98)
    const cornerFactor = clamp(Math.abs(frame.steering) / 30 + (60 - frame.speed) / 60, 0, 1)
    const asym = (frame.steering / 30) * cornerFactor * 10
    const baseFront = 6 + cornerFactor * 24
    const baseRear = 10 + cornerFactor * 32
    const recWing: WingData = {
      frontLeft: clamp(baseFront - asym, 0, 45),
      frontRight: clamp(baseFront + asym, 0, 45),
      rearLeft: clamp(baseRear - asym * 1.3, 0, 45),
      rearRight: clamp(baseRear + asym * 1.3, 0, 45),
    }
    const avgActual = (frame.wing.frontLeft + frame.wing.frontRight + frame.wing.rearLeft + frame.wing.rearRight) / 4
    const avgRec = (recWing.frontLeft + recWing.frontRight + recWing.rearLeft + recWing.rearRight) / 4
    const confidence = clamp(0.82 + this.noise(0.06) - (1 - grip) * 0.2, 0.5, 0.99)
    const efficiency = clamp(88 - Math.abs(avgActual - avgRec) * 1.4 - (frame.motorTemp - 60) * 0.3 + this.noise(1), 40, 99)
    const recommendedSpeed = clamp(seg.targetSpeed * (0.9 + grip * 0.12), 20, 66)

    // energy estimate — use last completed lap if available, else project
    const energyPerLap = this.lastEnergyPerLap > 0 ? this.lastEnergyPerLap : this.energyAcc * 4
    const packWh = (frame.voltage * 5.2) // 5200 mAh 6S pack nominal capacity
    const remainingWh = packWh * (frame.battery / 100)
    const predictedRangeLaps = energyPerLap > 0 ? clamp(remainingWh / energyPerLap, 0, 99) : 0

    const lines: Record<CornerType, string> = {
      Straight: 'Hold centre line, minimise wing drag',
      'Braking Zone': 'Late apex, trail-brake into entry',
      'Slow Corner': 'Tight geometric line, early throttle',
      'Medium Corner': 'Sweep wide entry, clip apex',
      'Fast Corner': 'Carry speed, shallow arc',
      Chicane: 'Sacrifice entry for straight-line exit',
    }

    const warnings: string[] = []
    if (frame.motorTemp > 78) warnings.push('Motor thermal derating imminent — ease corner-exit throttle')
    if (Math.abs(avgActual - avgRec) > 6) warnings.push('Wing trim off optimal for current load')
    if (frame.battery < 25) warnings.push('Pack SoC low — switch to conservation ERS map')
    if (grip < 0.55) warnings.push('Low grip estimate — reduce entry speed')

    return {
      corner: seg.type,
      grip,
      recommendedWing: recWing,
      recommendedSpeed,
      confidence,
      efficiency,
      racingLine: lines[seg.type],
      note: efficiency > 82 ? 'Setup within optimal window' : 'Wing trim suboptimal for current corner load',
      energyPerLap,
      predictedRangeLaps,
      warnings,
    }
  }

  getDiagnostics(frame: TelemetryFrame): SystemDiagnostics {
    const packetLoss = clamp(this.packetLossBase + 0.4 + Math.max(0, frame.speed - 55) * 0.05 + this.noise(0.2), 0, 12)
    const canDegraded = packetLoss > 3

    const canNodes: CanNode[] = [
      { id: '0x100', name: 'VCU Core (RISC-V)', level: 'ok', loadPct: clamp(18 + this.noise(3), 5, 40), lastSeenMs: clamp(2 + this.noise(2), 0, 20) },
      { id: '0x201', name: 'Motor ESC', level: frame.motorTemp > 78 ? 'warning' : 'ok', loadPct: clamp(24 + frame.throttle * 0.08, 5, 60), lastSeenMs: clamp(3 + this.noise(2), 0, 20) },
      { id: '0x310', name: 'BMS Gateway', level: frame.batteryTemp > 55 ? 'warning' : 'ok', loadPct: clamp(12 + this.noise(3), 5, 30), lastSeenMs: clamp(4 + this.noise(3), 0, 25) },
      { id: '0x420', name: 'IMU / AHRS', level: 'ok', loadPct: clamp(9 + this.noise(2), 3, 20), lastSeenMs: clamp(2 + this.noise(1), 0, 12) },
      { id: '0x501', name: 'Aero Servo Ctrl', level: 'ok', loadPct: clamp(7 + this.noise(2), 2, 18), lastSeenMs: clamp(5 + this.noise(3), 0, 30) },
    ]

    const loraLink: LinkHealth = {
      rssi: clamp(-62 - packetLoss * 6 + this.noise(3), -120, -40),
      snr: clamp(9 - packetLoss * 1.2 + this.noise(0.6), -5, 12),
      spreadingFactor: 9,
      bandwidthKhz: 125,
      txPowerDbm: 17,
    }

    return {
      can: canDegraded ? 'degraded' : 'connected',
      lora: packetLoss > 4.5 ? (packetLoss > 8 ? 'disconnected' : 'degraded') : 'connected',
      packetLoss,
      cpu: clamp(38 + frame.throttle * 0.25 + this.noise(4), 10, 95),
      memory: clamp(52 + this.noise(3), 30, 88),
      watchdog: packetLoss > 4.5 ? 'warning' : 'ok',
      ping: clamp(18 + packetLoss * 6 + this.noise(4), 8, 120),
      packetRate: clamp(50 - packetLoss * 2 + this.noise(1), 30, 52),
      refreshRate: 10,
      uptimeS: this.t + 3600, // simulate a running session
      temperatureC: clamp(46 + frame.throttle * 0.12 + this.noise(1.5), 35, 82),
      canNodes,
      loraLink,
    }
  }

  getSensorHealth(frame: TelemetryFrame): SensorHealth[] {
    const t = (v: number) => `${v.toFixed(1)}`
    return [
      { id: 'imu', name: 'IMU (6-axis)', level: 'ok', value: `${t(frame.imu.roll)}° roll`, subsystem: 'Sensors' },
      { id: 'wheel', name: 'Wheel Speed', level: 'ok', value: `${frame.speed.toFixed(0)} km/h`, subsystem: 'Sensors' },
      {
        id: 'motor-temp',
        name: 'Motor Thermistor',
        level: frame.motorTemp > 78 ? 'critical' : frame.motorTemp > 68 ? 'warning' : 'ok',
        value: `${t(frame.motorTemp)}°C`,
        subsystem: 'Powertrain',
      },
      {
        id: 'batt-temp',
        name: 'Pack Thermistor',
        level: frame.batteryTemp > 55 ? 'warning' : 'ok',
        value: `${t(frame.batteryTemp)}°C`,
        subsystem: 'Battery',
      },
      { id: 'current', name: 'Current Shunt', level: frame.current > 95 ? 'warning' : 'ok', value: `${t(frame.current)} A`, subsystem: 'Battery' },
      { id: 'steer', name: 'Steering Encoder', level: 'ok', value: `${t(frame.steering)}°`, subsystem: 'Chassis' },
      { id: 'wing-fl', name: 'Front-Left Wing Servo', level: 'ok', value: `${t(frame.wing.frontLeft)}°`, subsystem: 'Aero' },
      { id: 'wing-fr', name: 'Front-Right Wing Servo', level: 'ok', value: `${t(frame.wing.frontRight)}°`, subsystem: 'Aero' },
      { id: 'wing-rl', name: 'Rear-Left Wing Servo', level: 'ok', value: `${t(frame.wing.rearLeft)}°`, subsystem: 'Aero' },
      { id: 'wing-rr', name: 'Rear-Right Wing Servo', level: 'ok', value: `${t(frame.wing.rearRight)}°`, subsystem: 'Aero' },
      { id: 'gps', name: 'GPS / Positioning', level: 'ok', value: 'Lock 3D', subsystem: 'Sensors' },
      { id: 'volt', name: 'Voltage Sense', level: frame.voltage < 19.5 ? 'warning' : 'ok', value: `${t(frame.voltage)} V`, subsystem: 'Battery' },
    ]
  }
}
