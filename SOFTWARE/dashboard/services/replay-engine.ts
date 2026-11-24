/**
 * Timestamp-driven replay engine.
 *
 * This is the ONE replay clock for the whole app (Phase 4/5 of the
 * integration task). It does not know about React, Digital Twin, charts,
 * or the Track Map — it just owns a sorted frame list and a playback
 * position in session time, and calls `onUpdate` whenever that position
 * changes. `contexts/telemetry-context.tsx` is the only consumer: it
 * pushes the emitted frame into the same central telemetry state that
 * LIVE and SIMULATION write to, so every other page (Digital Twin, Track
 * Map, Diagnostics, charts) stays a passive consumer of `frame` and never
 * needs its own replay timer.
 *
 * Design notes:
 * - Playback position is tracked as `playbackTimeMs`, elapsed session time
 *   since the first frame's timestamp — NOT an array index incremented on
 *   a fixed interval. Index is derived from playback time, not the other
 *   way around, which is what makes seek/rate-change/pause exact.
 * - A single requestAnimationFrame loop drives playback; play()/pause()/
 *   stop() start and cancel it, so there is never more than one running
 *   timer for this engine, and dispose() guarantees it is cancelled on
 *   unmount.
 * - Frame timestamps are assumed to be in the same unit the backend
 *   already persists them in (milliseconds — see backend/app/routers/
 *   sessions.py _compute_stats, which divides a timestamp delta by 1000
 *   to get seconds).
 */

import type { TelemetryFrame } from '@/types/telemetry'

export type ReplayStatus = 'idle' | 'playing' | 'paused' | 'stopped'
export type ReplayRate = 0.25 | 0.5 | 1 | 2 | 4 | 10

export interface ReplayFrameRecord<T = unknown> {
  frame: TelemetryFrame
  /** Everything besides the frame that travelled with this recorded
   * sample (AI recommendation, diagnostics, etc.) — kept as a single
   * opaque payload so the engine itself stays agnostic to its shape;
   * the caller (telemetry-context) knows the real type. */
  payload: T
}

export interface ReplaySnapshot<T = unknown> {
  record: ReplayFrameRecord<T> | null
  index: number
  totalFrames: number
  startTimestamp: number
  endTimestamp: number
  currentTimestamp: number
  status: ReplayStatus
  rate: ReplayRate
}

export class ReplayEngine<T = unknown> {
  private frames: ReplayFrameRecord<T>[] = []
  private status: ReplayStatus = 'idle'
  private rate: ReplayRate = 1
  private index = 0
  private playbackTimeMs = 0
  private rafId: number | null = null
  private lastWallClockMs = 0

  constructor(private readonly onUpdate: (snapshot: ReplaySnapshot<T>) => void) {}

  /** Load a new (unsorted-safe) frame set and reset playback to the start. */
  load(records: ReplayFrameRecord<T>[]): void {
    this.stopLoop()
    this.frames = [...records].sort((a, b) => a.frame.timestamp - b.frame.timestamp)
    this.index = 0
    this.playbackTimeMs = 0
    this.status = 'idle'
    this.emit()
  }

  clear(): void {
    this.stopLoop()
    this.frames = []
    this.index = 0
    this.playbackTimeMs = 0
    this.status = 'idle'
    this.emit()
  }

  play(): void {
    if (this.frames.length === 0) return
    if (this.status === 'stopped' || this.atEnd()) {
      this.playbackTimeMs = 0
      this.index = 0
    }
    this.status = 'playing'
    this.lastWallClockMs = typeof performance !== 'undefined' ? performance.now() : Date.now()
    this.startLoop()
    this.emit()
  }

  pause(): void {
    if (this.status !== 'playing') return
    this.status = 'paused'
    this.stopLoop()
    this.emit()
  }

  stop(): void {
    this.status = this.frames.length ? 'stopped' : 'idle'
    this.stopLoop()
    this.playbackTimeMs = 0
    this.index = 0
    this.emit()
  }

  setRate(rate: ReplayRate): void {
    this.rate = rate
    this.emit()
  }

  /** Seek by fraction of total duration, 0..1. */
  seekFraction(fraction: number): void {
    const clamped = Math.max(0, Math.min(1, fraction))
    this.playbackTimeMs = clamped * this.durationMs()
    this.updateIndexForTime()
    if (this.status === 'stopped' || this.status === 'idle') this.status = 'paused'
    this.emit()
  }

  seekTimestamp(timestampMs: number): void {
    this.playbackTimeMs = Math.max(0, Math.min(this.durationMs(), timestampMs - this.startTs()))
    this.updateIndexForTime()
    if (this.status === 'stopped' || this.status === 'idle') this.status = 'paused'
    this.emit()
  }

  dispose(): void {
    this.stopLoop()
  }

  getSnapshot(): ReplaySnapshot<T> {
    return this.buildSnapshot()
  }

  // ── internals ──────────────────────────────────────────────────
  private startTs(): number {
    return this.frames[0]?.frame.timestamp ?? 0
  }
  private endTs(): number {
    return this.frames[this.frames.length - 1]?.frame.timestamp ?? 0
  }
  private durationMs(): number {
    return Math.max(0, this.endTs() - this.startTs())
  }
  private atEnd(): boolean {
    return this.frames.length > 0 && this.index >= this.frames.length - 1
  }

  private updateIndexForTime(): void {
    if (this.frames.length === 0) {
      this.index = 0
      return
    }
    const targetTs = this.startTs() + this.playbackTimeMs
    // Linear scan is fine at realistic session sizes (10 Hz sessions of a
    // few minutes = low thousands of frames); a binary search would be a
    // premature optimisation here.
    let i = 0
    while (i < this.frames.length - 1 && this.frames[i + 1].frame.timestamp <= targetTs) i++
    this.index = i
  }

  private startLoop(): void {
    if (this.rafId != null) return
    const tick = (now: number) => {
      const dtWall = now - this.lastWallClockMs
      this.lastWallClockMs = now
      if (this.status === 'playing') {
        this.playbackTimeMs += dtWall * this.rate
        if (this.playbackTimeMs >= this.durationMs()) {
          this.playbackTimeMs = this.durationMs()
          this.updateIndexForTime()
          this.status = 'paused' // end-of-session: hold on the last frame
          this.emit()
          this.stopLoop()
          return
        }
        this.updateIndexForTime()
        this.emit()
      }
      this.rafId = requestAnimationFrame(tick)
    }
    this.rafId = requestAnimationFrame(tick)
  }

  private stopLoop(): void {
    if (this.rafId != null) {
      cancelAnimationFrame(this.rafId)
      this.rafId = null
    }
  }

  private buildSnapshot(): ReplaySnapshot<T> {
    return {
      record: this.frames[this.index] ?? null,
      index: this.index,
      totalFrames: this.frames.length,
      startTimestamp: this.startTs(),
      endTimestamp: this.endTs(),
      currentTimestamp: this.frames[this.index]?.frame.timestamp ?? this.startTs(),
      status: this.status,
      rate: this.rate,
    }
  }

  private emit(): void {
    this.onUpdate(this.buildSnapshot())
  }
}
