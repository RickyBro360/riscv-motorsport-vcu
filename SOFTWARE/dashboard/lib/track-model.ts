/**
 * The trained track model used by the AI Model page (one implementation for both "AI recommends" and
 * "AI drives").  A small neural network (2 hidden layers of 48) maps
 *
 *     lap position (0..1, as Fourier terms)  +  rear wheel speed   ->   motor duty %, steering deg, straight mode, speed
 *
 * monza-model.json was trained on laps of Monza driven by the VCU's own Demo 2 lap (the real vcu_logic.h on
 * the host, see SOFTWARE/tools/monza_model/). It therefore imitates the car's stored Monza map; it has not
 * learned from a human driver. Steering sign: + = LEFT (the project convention).
 */
import modelJson from './monza-model.json'

export interface TrackModel {
  name: string; track_id: number; track_name: string; hidden: number[]; fourier_k: number; v_norm: number
  outputs: string[]; out_scale: Record<string, number>; lap_length_m: number; duty_floor: number; duty_cap: number
  x_mean: number[]; x_std: number[]; weights: number[][][]; biases: number[][]; n_params: number
  training: Record<string, number | string>
}
export interface ModelOutput { duty: number; steer: number; drs: boolean; speedKmh: number }

export const MONZA_MODEL = modelJson as unknown as TrackModel

export function predict(m: TrackModel, frac: number, speedKmh: number): ModelOutput {
  const f = frac - Math.floor(frac)
  const x: number[] = [f]
  for (let k = 1; k <= m.fourier_k; k++) x.push(Math.sin(2 * Math.PI * f * k))
  for (let k = 1; k <= m.fourier_k; k++) x.push(Math.cos(2 * Math.PI * f * k))
  x.push(speedKmh / m.v_norm)
  let h = x.map((v, i) => (v - m.x_mean[i]) / m.x_std[i])
  const nl = m.weights.length
  for (let l = 0; l < nl; l++) {
    const W = m.weights[l], b = m.biases[l], out = new Array<number>(b.length)
    for (let j = 0; j < b.length; j++) {
      let acc = b[j]
      for (let i = 0; i < h.length; i++) acc += h[i] * W[i][j]
      out[j] = l < nl - 1 ? Math.max(acc, 0) : acc
    }
    h = out
  }
  const get = (name: string) => h[m.outputs.indexOf(name)] * m.out_scale[name]
  const clip = (v: number, lo: number, hi: number) => Math.min(Math.max(v, lo), hi)
  return { duty: clip(get('duty'), m.duty_floor, m.duty_cap), steer: clip(get('steer'), -30, 30), drs: get('drs') > 0.5, speedKmh: Math.max(get('speed'), 0) }
}

/** The model's own lap: its outputs at every position, using its own speed estimate as the speed input. */
export function lapProfile(m: TrackModel, points = 240) {
  let v = 14
  const rows: { x: number; duty: number; steer: number; drs: boolean; speed: number }[] = []
  for (let pass = 0; pass < 2; pass++) {
    rows.length = 0
    for (let i = 0; i < points; i++) {
      const o = predict(m, i / points, v)
      v = o.speedKmh
      rows.push({ x: (i / points) * 100, duty: o.duty, steer: o.steer, drs: o.drs, speed: o.speedKmh })
    }
  }
  return rows
}

/**
 * Lap position without GPS: integrate the rear wheel speed over the model's lap length. Isolated speed
 * spikes are replaced by the recent median so they do not push the position forward.
 */
export class LapPosition {
  frac = 0; laps = 0
  private hist: number[] = []
  private lastMs: number | null = null
  private spikeSince: number | null = null
  constructor(private lapLengthM: number) {}
  reset() { this.frac = 0; this.laps = 0; this.lastMs = null; this.hist = []; this.spikeSince = null }
  /** returns the cleaned speed (km/h) */
  update(nowMs: number, rearKmh: number): number {
    let v = Number.isFinite(rearKmh) && rearKmh > 0 ? rearKmh : 0
    if (this.hist.length >= 3) {
      const s = [...this.hist].sort((a, b) => a - b), med = s[Math.floor(s.length / 2)]
      // a jump far above the recent median is a spike... unless it lasts: after 0.35 s it is the real speed
      if (med > 2 && v > 1.4 * med + 1.0) {
        if (this.spikeSince === null) this.spikeSince = nowMs
        if (nowMs - this.spikeSince < 350) v = med; else { this.hist = []; this.spikeSince = null }
      } else this.spikeSince = null
    }
    this.hist.push(v); if (this.hist.length > 5) this.hist.shift()
    if (this.lastMs !== null) {
      const dt = Math.min(Math.max((nowMs - this.lastMs) / 1000, 0), 0.5)
      this.frac += (v / 3.6) * dt / this.lapLengthM
      while (this.frac >= 1) { this.frac -= 1; this.laps += 1 }
    }
    this.lastMs = nowMs
    return v
  }
  /** follow a position reported by the VCU (its own circuit run) */
  sync(frac: number, nowMs: number) { this.frac = frac - Math.floor(frac); this.lastMs = nowMs }
}
