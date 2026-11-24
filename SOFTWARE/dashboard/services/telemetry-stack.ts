import type {
  AiRecommendation,
  Fault,
  SensorHealth,
  SystemDiagnostics,
  TelemetryFrame,
  SimulationScenario,
} from '@/types/telemetry'
import { TelemetrySimulator } from '@/services/telemetry-simulator'
import { TrackRunSimulator } from '@/services/track-run-simulator'

export type TelemetryMode = 'simulation' | 'replay' | 'live'

export interface TelemetrySnapshot {
  frame: TelemetryFrame
  faults: Fault[]
  ai: AiRecommendation
  diagnostics: SystemDiagnostics
  sensors: SensorHealth[]
}

export interface TelemetrySource {
  next(dt: number, scenario?: SimulationScenario): TelemetrySnapshot
  reset(): void
}

export class TelemetryParser {
  parse(input: unknown): TelemetryFrame {
    if (!input || typeof input !== 'object') throw new Error('Invalid telemetry frame')
    const value = input as Partial<TelemetryFrame>
    if (typeof value.timestamp !== 'number' || typeof value.speed !== 'number') throw new Error('Telemetry frame is missing required fields')
    // Sessions recorded before schemaVersion existed have no such field on
    // disk — default them to 1 (the only version that has ever existed)
    // rather than surfacing `undefined` to every consumer.
    if (typeof value.schemaVersion !== 'number') value.schemaVersion = 1
    return value as TelemetryFrame
  }
}

export class SimulationTelemetrySource implements TelemetrySource {
  private simulator = new TelemetrySimulator()
  private trackRun = new TrackRunSimulator()
  next(dt: number, scenario: SimulationScenario = 'TRACK_RUN') {
    if (scenario === 'TRACK_RUN') {
      // Stage 2d: the car on the dyno driving a stored circuit (same logic as the VCU)
      const frame = this.trackRun.step(dt)
      return {
        frame,
        faults: this.trackRun.getFaults(),
        ai: this.trackRun.getRecommendation(frame),
        diagnostics: this.trackRun.getDiagnostics(),
        sensors: this.trackRun.getSensorHealth(frame),
      }
    }
    // the Stage-2 driving scenarios are free driving: FW 2.2 calls that TRACK mode
    const frame = { ...this.simulator.step(dt, scenario), vcu: TRACK_MODE_VCU }
    return {
      frame,
      faults: this.simulator.getFaults(),
      ai: this.simulator.getRecommendation(frame),
      diagnostics: this.simulator.getDiagnostics(frame),
      sensors: this.simulator.getSensorHealth(frame),
    }
  }
  reset() { this.simulator = new TelemetrySimulator(); this.trackRun = new TrackRunSimulator() }
  setTrack(id: number) { this.trackRun.setTrack(id) }
  /** FW 2.2 car-control requests in Simulation Mode (only the dyno track-run scenario has a run to control). */
  command(key: number, value: number, scenario: SimulationScenario): string {
    return scenario === 'TRACK_RUN' ? this.trackRun.command(key, value) : 'rejected_state'
  }
}

const TRACK_MODE_VCU: NonNullable<TelemetryFrame['vcu']> = {
  driveMode: 'track', hwProfile: 'pcb_rev_b', killSwitchFitted: true, motorPower: true, remoteRun: false,
  tcActive: false, tcCutPct: 0, groundLinkOk: true, aeroStraight: null, encoderFitted: true, knob: 0, runLaps: 0,
}

export class ReplayTelemetrySource implements TelemetrySource {
  private index = 0
  constructor(private readonly frames: TelemetryFrame[] = []) {}
  next(_dt: number): TelemetrySnapshot {
    const frame = this.frames[Math.min(this.index++, Math.max(0, this.frames.length - 1))]
    if (!frame) return new SimulationTelemetrySource().next(0.1)
    return {
      frame,
      faults: [],
      ai: {
        corner: 'Straight' as const,
        grip: 0.86,
        recommendedWing: frame.wing,
        recommendedSpeed: frame.speed,
        confidence: 0.9,
        efficiency: 84,
        racingLine: 'Replay capture',
        note: 'Replay frame',
        energyPerLap: 42,
        predictedRangeLaps: 8,
        warnings: [],
      },
      diagnostics: {
        can: 'connected' as const,
        lora: 'connected' as const,
        packetLoss: 0.3,
        cpu: 28,
        memory: 41,
        watchdog: 'ok' as const,
        ping: 18,
        packetRate: 10,
        refreshRate: 10,
        uptimeS: 0,
        temperatureC: 42,
        canNodes: [],
        loraLink: { rssi: -61, snr: 8.4, spreadingFactor: 7, bandwidthKhz: 125, txPowerDbm: 17 },
      },
      sensors: [],
    }
  }
  reset() { this.index = 0 }
}

// NOTE: there is deliberately no LiveTelemetrySource / TelemetryService
// entry for 'live' mode. LIVE telemetry is never generated locally — it
// only ever reaches the dashboard as an inbound WebSocket frame, parsed by
// TelemetryParser and applied directly in contexts/telemetry-context.tsx.
// TelemetryService.next() below only ever runs for 'simulation' and
// 'replay', both of which are explicitly non-live, clearly-labelled modes.

export class TelemetryService {
  private sources = {
    simulation: new SimulationTelemetrySource(),
    replay: new ReplayTelemetrySource(),
  }
  setReplayFrames(frames: TelemetryFrame[]) { this.sources.replay = new ReplayTelemetrySource(frames) }
  setSimulationTrack(id: number) { this.sources.simulation.setTrack(id) }
  simulationCommand(key: number, value: number, scenario: SimulationScenario) { return this.sources.simulation.command(key, value, scenario) }
  next(mode: 'simulation' | 'replay', dt: number, scenario: SimulationScenario = 'TRACK_RUN') {
    return mode === 'simulation' ? this.sources.simulation.next(dt, scenario) : this.sources.replay.next(dt)
  }
  reset(mode: 'simulation' | 'replay') { this.sources[mode].reset() }
}
