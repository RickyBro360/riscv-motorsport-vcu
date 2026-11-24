/**
 * TRACK_RUN simulation — an offline stand-in for the car on the dyno running a
 * stored circuit (Stage 2d). It runs the SAME logic as the VCU's TrackRunner
 * (vcu_logic.h): odometry -> lap fraction -> map steering, map speed -> motor duty,
 * straight/corner-mode flaps with the rear pair staggered, rate-limited servos.
 * The electrical and thermal numbers are shaped to the bench hardware (12 V / 2.2 A
 * adapter, two small Johnson motors on an L298N) so the dashboard scales can be
 * checked without the car. Everything it produces is labelled SIMULATION by the
 * telemetry context — it never reaches the screen as live data.
 */
import { TELEMETRY_SCHEMA_VERSION } from '@/types/telemetry'
import type { AiRecommendation, Fault, SensorHealth, SystemDiagnostics, TelemetryFrame, WingData } from '@/types/telemetry'
import { VCU_DEFAULTS, cornerNear, flapsFor, loadTracks, nextEvent, sampleAt, wrap01, type TrackDoc, type TrackDef } from '@/lib/tracks'

// FW 2.3.7: same model as the VCU (vcu_calibration.h): 1000 rpm Johnson motor, 90 mm wheel = 17.0 km/h at 100 %,
// nothing below 20 % duty.
const SIM_VMAX_KMH = (1000 / 60) * Math.PI * 0.09 * 3.6
const SIM_START_DUTY = 0.2
const REAR_STAGGER_S = 0.15
const LOOKAHEAD_S = 0.35

let tracksDoc: TrackDoc | null = null
loadTracks().then((d) => { tracksDoc = d }).catch(() => {})

export class TrackRunSimulator {
  private t = 0
  private pos = 0
  private lap = 0
  private lapStart = 0
  private lastLapMs = 0
  private odometer = 0
  private speed = 0 // km/h rear
  private steer = 0
  private wing: WingData = { frontLeft: 0, frontRight: 0, rearLeft: 0, rearRight: 0 }
  private target: WingData = { frontLeft: 0, frontRight: 0, rearLeft: 0, rearRight: 0 }
  private rearStraight = false
  private rearChangeAt: number | null = null
  private motorTemp = 29
  private yaw = 0
  private cfg: Record<string, number> = { ...VCU_DEFAULTS, dynoVmaxCkmh: Math.round(SIM_VMAX_KMH * 100), dynoStartPermille: SIM_START_DUTY * 1000 }
  private trackId: number | null = null
  // FW 2.2: the VCU's operating state, driven by the Car control panel (same rules as VcuController)
  private driveMode: 'dyno' | 'track' = 'dyno'
  private runActive = true            // the simulation starts with a circuit run, as before
  private estop = false
  private runLaps = 0
  private lapAtStart = 0

  /** Car-control requests in Simulation Mode. Returns the status the VCU would ACK. */
  command(key: number, value: number): string {
    switch (key) {
      case 5:
        if (value !== 0 && value !== 1) return 'rejected_range'
        if (this.runActive) return 'rejected_state'
        this.driveMode = value === 1 ? 'track' : 'dyno'
        return 'applied'
      case 6:
        if (value === 0) { this.runActive = false; return 'applied' }
        if (value !== 1 && value !== 2) return 'rejected_range'
        if (this.driveMode !== 'dyno' || this.estop || this.runActive) return 'rejected_state'
        this.runActive = true; this.lapAtStart = this.lap
        return 'applied'
      case 7:
        if (value < 0 || value > 99) return 'rejected_range'
        this.runLaps = value
        return 'applied'
      case 8:
        if (value === 1) { this.estop = true; this.runActive = false; return 'applied' }
        if (value === 2) { this.estop = false; return 'applied' }
        return 'rejected_range'
      case 26:                                    // FW 2.3.4 demo sequence (not simulated here)
        return value === 0 || value === 1 || value === 2 || (value >= 100 && value < 200) ? (this.estop && value !== 0 ? 'rejected_state' : 'applied') : 'rejected_range'
      case 25:                                    // FW 2.3.3 DRS (the flaps are not simulated here)
        if (value !== 0 && value !== 1) return 'rejected_range'
        return this.estop && value === 1 ? 'rejected_state' : 'applied'
      default:
        return 'rejected_unknown_key'
    }
  }

  track(): TrackDef | null {
    if (!tracksDoc) return null
    const id = this.trackId ?? tracksDoc.defaultTrackId
    return tracksDoc.tracks[id] ?? null
  }

  setTrack(id: number) { this.trackId = id; this.pos = 0; this.lap = 0; this.lapStart = this.t }

  step(dt: number): TelemetryFrame {
    this.t += dt
    const t = this.track()
    const cfg = this.cfg
    let duty = 0
    let straight = false
    let braking = false
    let sector = 0
    let latG = 0
    if (t && this.runActive) {
      const s = sampleAt(t, this.pos)
      const vmaxTrack = t.model.vmaxKmh
      const cap = cfg.dutyCapPermille / 1000
      const floor = cfg.dutyFloorPermille / 1000
      duty = floor + (cap - floor) * Math.max(0, Math.min(1, s.speedKmh / vmaxTrack))
      const f = flapsFor(t, this.pos, cfg)
      straight = f.straight
      braking = s.braking
      sector = s.sector
      latG = s.latG
      // rear pair follows the front after REAR_STAGGER_S (VCU TrackRunner)
      if (f.straight !== this.rearStraight) {
        if (this.rearChangeAt === null) this.rearChangeAt = this.t
        if (this.t - this.rearChangeAt >= REAR_STAGGER_S) { this.rearStraight = f.straight; this.rearChangeAt = null }
      } else this.rearChangeAt = null
      const rear = (this.rearStraight ? cfg.straightRearCdeg : cfg.cornerRearCdeg) / 100
      const lat = this.rearStraight ? 0 : Math.max(-10, Math.min(10, (latG * cfg.asymGainCdegPerG) / 100))
      this.target = { ...f.wing, rearLeft: Math.max(0, rear - lat), rearRight: Math.min(45, rear + lat) }
      const steerTarget = Math.max(-30, Math.min(30, s.steerDeg * cfg.steerGainPermille / 1000))
      this.steer += Math.max(-300 * dt, Math.min(300 * dt, steerTarget - this.steer))
    } else {
      // IDLE / E-STOP: motor off, flaps and steering to failsafe
      this.target = { frontLeft: 0, frontRight: 0, rearLeft: 0, rearRight: 0 }
      this.steer += Math.max(-300 * dt, Math.min(300 * dt, -this.steer))
    }
    // flaps: VCU slew limit; the rear MG996R (the ones with feedback) lag a little behind the command
    const slew = cfg.wingSlewDps * dt
    let moving = 0
    for (const k of ['frontLeft', 'frontRight', 'rearLeft', 'rearRight'] as const) {
      const d = this.target[k] - this.wing[k]
      if (Math.abs(d) > 0.3) moving++
      const lag = k.startsWith('rear') ? 0.85 : 1
      this.wing[k] += Math.max(-slew * lag, Math.min(slew * lag, d))
    }
    // motor: wheels break away above the start duty, first-order speed response
    const vTarget = duty >= SIM_START_DUTY ? duty * SIM_VMAX_KMH : 0
    this.speed += (vTarget - this.speed) * Math.min(1, dt / 0.35)
    const ms = this.speed / 3.6
    const dist = ms * dt
    this.odometer += dist
    if (t && this.runActive) {
      this.pos += dist / (cfg.lapLengthCm / 100)
      if (this.pos >= 1) {
        this.pos -= 1
        this.lap += 1
        this.lastLapMs = Math.round((this.t - this.lapStart) * 1000)
        this.lapStart = this.t
        if (this.runLaps && this.lap - this.lapAtStart >= this.runLaps) this.runActive = false
      }
    }
    const runOn = !!t && this.runActive
    // electrical: motors ~ duty, servo transients, adapter sag
    const current = 0.18 + 1.55 * duty * (0.9 + 0.1 * Math.sin(this.t * 3.1)) + 0.12 * moving + 0.02 * (Math.random() - 0.5)
    const voltage = 12.15 - 0.42 * current + 0.01 * (Math.random() - 0.5)
    this.motorTemp += (current * current * 0.012 - (this.motorTemp - 27) * 0.004) * dt
    this.yaw = (this.yaw + 0.3 * (Math.random() - 0.5)) % 360
    const vib = 18 + this.speed * 14 + 6 * Math.random()
    const rear = this.speed + 0.02 * (Math.random() - 0.5)
    const fr: TelemetryFrame = {
      schemaVersion: TELEMETRY_SCHEMA_VERSION,
      timestamp: Math.round(this.t * 1000),
      speed: rear,
      voltage,
      current,
      motorCurrent: current,
      motorTemp: this.motorTemp,
      batteryTemp: 27 + (this.motorTemp - 27) * 0.1,
      battery: 100,
      imu: { roll: 0.2 * Math.sin(this.t), pitch: -0.4 + 0.1 * Math.sin(this.t * 0.7), yaw: this.yaw },
      steering: this.steer,
      wing: { ...this.wing },
      // like the VCU: the open-loop front SG90s report their (rate-limited) command as "measured"
      wingTarget: { ...this.target, frontLeft: this.wing.frontLeft, frontRight: this.wing.frontRight },
      throttle: duty * 100,
      brake: 0,
      gForceLat: (vib / 1000) * (Math.random() - 0.5),
      gForceLong: (vib / 1000) * (Math.random() - 0.5),
      rpm: (ms / (Math.PI * 0.07)) * 60,
      distance: this.odometer,
      wheelAngle: (this.odometer / 0.035) % (Math.PI * 2),
      vcuState: this.estop ? 'EMERGENCY_STOP' : 'IDLE',
      wheels: { fl: 0, fr: 0, rl: rear, rr: rear * 0.985 },
      gyroZ: 0.2 * (Math.random() - 0.5),
      vibrationMg: vib,
      odometerM: this.odometer,
      aeroMode: 1,
      lastAiReason: 'accepted',
      track: {
        id: t?.id ?? 12, pos: wrap01(this.pos), lap: this.lap,
        currentLapMs: Math.round((this.t - this.lapStart) * 1000), lastLapMs: this.lastLapMs,
        profileDutyPct: duty * 100, latGModel: latG, runActive: runOn, straightMode: straight,
        brakingZone: braking, configPending: false, benchRun: true, sector, stalled: false,
      },
      runState: runOn ? 'bench_laps' : 'off',
      dynoCal: 'done',
      vcuConfig: { ...cfg, trackId: t?.id ?? 12, pending: false, driveMode: this.driveMode === 'track' ? 1 : 0, runLaps: this.runLaps, hwProfile: 1 } as Record<string, number | boolean>,
      configAcks: [],
      vcu: {
        driveMode: this.driveMode, hwProfile: 'pcb_rev_b', killSwitchFitted: true, motorPower: runOn && !this.estop,
        remoteRun: runOn, tcActive: false, tcCutPct: 0, groundLinkOk: true, aeroStraight: runOn && straight,
        encoderFitted: true, knob: 0, runLaps: this.runLaps,
      },
      derived: { powerW: voltage * current },
    }
    return fr
  }

  getRecommendation(frame: TelemetryFrame): AiRecommendation {
    const t = this.track()
    if (!t || !frame.track) {
      return { corner: 'Straight', grip: 1, recommendedWing: frame.wing, recommendedSpeed: 0, confidence: 0, efficiency: 0,
        racingLine: '', note: 'Loading circuit maps…', energyPerLap: null, predictedRangeLaps: null, warnings: [] }
    }
    const rate = (frame.speed / 3.6) / (this.cfg.lapLengthCm / 100)
    const ahead = wrap01(frame.track.pos + rate * LOOKAHEAD_S)
    const f = flapsFor(t, ahead, this.cfg)
    const c = cornerNear(t, ahead)
    const ev = nextEvent(t, frame.track.pos, this.cfg.straightOpenDelayM, this.cfg.straightCloseEarlyM)
    return {
      corner: f.straight ? 'Straight' : c ? 'Medium Corner' : 'Fast Corner',
      grip: Math.max(0, 1 - Math.abs(sampleAt(t, ahead).latG) / 6),
      recommendedWing: f.wing,
      recommendedSpeed: sampleAt(t, ahead).speedKmh,
      confidence: f.straight === frame.track.straightMode ? 0.9 : 0.72,
      efficiency: f.straight ? 100 : 70,
      racingLine: 'follow the map',
      note: 'SIMULATION — position-aware look-ahead on the stored circuit map (same rule as the backend AI engineer).',
      energyPerLap: null,
      predictedRangeLaps: null,
      warnings: [],
      type: 'AERO_ADVICE',
      reason: ev ? `${ev.type} in ${ev.inM.toFixed(0)} m (full scale)${c ? ` — T${c.n}` : ''}` : 'straight-mode zone',
      source: 'dashboard_simulation',
    }
  }

  getDiagnostics(): SystemDiagnostics {
    return {
      can: 'connected', lora: 'connected', packetLoss: 1.5, cpu: 21, memory: 38, watchdog: 'ok', ping: 45,
      packetRate: 5, refreshRate: 5, uptimeS: this.t, temperatureC: 47, canNodes: [],
      loraLink: { rssi: -58, snr: 9.5, spreadingFactor: 7, bandwidthKhz: 250, txPowerDbm: 10 },
    }
  }

  getFaults(): Fault[] { return [] }

  getSensorHealth(frame: TelemetryFrame): SensorHealth[] {
    return [
      { id: 'imu', name: 'MPU6050 IMU', level: 'ok', value: `${(frame.vibrationMg ?? 0).toFixed(0)} mg RMS`, subsystem: 'Sensors' },
      { id: 'ina', name: 'INA260 power', level: frame.current > 2.0 ? 'warning' : 'ok', value: `${frame.voltage.toFixed(2)} V / ${frame.current.toFixed(2)} A`, subsystem: 'Power' },
      { id: 'hall', name: 'Hall wheel speed ×4', level: 'ok', value: `RL ${(frame.wheels?.rl ?? 0).toFixed(2)} km/h`, subsystem: 'Sensors' },
      { id: 'ds', name: 'DS18B20 ×2', level: frame.motorTemp > 65 ? 'warning' : 'ok', value: `motor ${frame.motorTemp.toFixed(1)} °C`, subsystem: 'Thermal' },
      { id: 'fb', name: 'MG996R feedback LR/RR', level: 'ok', value: `${frame.wing.rearLeft.toFixed(1)}° / ${frame.wing.rearRight.toFixed(1)}°`, subsystem: 'Aero' },
    ]
  }
}
