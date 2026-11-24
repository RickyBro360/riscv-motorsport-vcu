import type { TelemetryLinkState } from '@/types/telemetry'

import { backendWsBase } from '@/lib/backend-url'

const WS_BASE = backendWsBase()

const RECONNECT_DELAY_MS = 2000
const MAX_RECONNECT_DELAY_MS = 10000

export interface TelemetrySocketFrameMessage {
  sessionId: string
  snapshot: unknown
}

export interface TelemetrySocketHandlers {
  onState: (state: TelemetryLinkState) => void
  onFrame: (message: TelemetrySocketFrameMessage) => void
  /** Malformed JSON from the backend, or an {type:"error"} reply to a sent frame. Non-fatal. */
  onError?: (detail: string) => void
}

/**
 * Single authoritative WebSocket client for /ws/telemetry.
 *
 * One instance is created and owned by TelemetryProvider
 * (contexts/telemetry-context.tsx) — pages never open their own socket,
 * so there is exactly one live connection and one place connection state
 * is decided, no matter how many dashboard pages are mounted.
 *
 * Guarantees:
 * - connect() is idempotent for the same sessionId (no duplicate sockets)
 * - automatic reconnect with capped backoff on unexpected close
 * - no reconnect after an explicit disconnect() (mode switch / unmount)
 * - onState always reflects reality: it is never told 'connected' unless
 *   the browser's WebSocket 'open' event actually fired
 */
export class TelemetrySocketClient {
  private ws: WebSocket | null = null
  private closedByUser = true
  private hasAttemptedConnect = false
  private reconnectTimer: number | null = null
  private reconnectDelay = RECONNECT_DELAY_MS
  private sessionId: string | null = null

  constructor(private readonly handlers: TelemetrySocketHandlers) {}

  connect(sessionId: string): void {
    if (
      this.sessionId === sessionId &&
      this.ws &&
      (this.ws.readyState === WebSocket.OPEN || this.ws.readyState === WebSocket.CONNECTING)
    ) {
      return // already connecting/connected for this exact session — no duplicate socket
    }
    this.teardownSocket()
    if (this.reconnectTimer !== null) {
      window.clearTimeout(this.reconnectTimer)
      this.reconnectTimer = null
    }
    this.closedByUser = false
    this.sessionId = sessionId
    this.reconnectDelay = RECONNECT_DELAY_MS
    this.hasAttemptedConnect = false
    this.open()
  }

  send(sessionId: string, snapshot: unknown): boolean {
    if (!this.ws || this.ws.readyState !== WebSocket.OPEN || this.sessionId !== sessionId) return false
    try {
      this.ws.send(JSON.stringify({ type: 'frame', sessionId, snapshot }))
      return true
    } catch {
      return false
    }
  }

  disconnect(): void {
    this.closedByUser = true
    this.sessionId = null
    if (this.reconnectTimer !== null) {
      window.clearTimeout(this.reconnectTimer)
      this.reconnectTimer = null
    }
    this.reconnectDelay = RECONNECT_DELAY_MS
    this.teardownSocket()
  }

  private open(): void {
    if (typeof WebSocket === 'undefined') return // SSR guard
    this.handlers.onState(this.hasAttemptedConnect ? 'reconnecting' : 'connecting')
    this.hasAttemptedConnect = true

    const socket = new WebSocket(`${WS_BASE}/ws/telemetry`)
    this.ws = socket

    socket.onopen = () => {
      if (this.ws !== socket) return
      this.reconnectDelay = RECONNECT_DELAY_MS
      this.handlers.onState('connected')
    }

    socket.onmessage = (event: MessageEvent) => {
      if (this.ws !== socket) return
      let message: unknown
      try {
        message = JSON.parse(event.data)
      } catch {
        this.handlers.onError?.('Received malformed telemetry message from backend')
        return
      }
      const parsed = message as { type?: string; sessionId?: string; snapshot?: unknown; detail?: string }
      if (parsed.type === 'frame' && parsed.snapshot && this.sessionId) {
        this.handlers.onFrame({ sessionId: parsed.sessionId ?? this.sessionId, snapshot: parsed.snapshot })
      } else if (parsed.type === 'error') {
        this.handlers.onError?.(parsed.detail ?? 'Backend rejected the last telemetry frame')
      }
      // type === 'connected' is just the initial ack; onopen already set state.
    }

    socket.onclose = () => {
      if (this.ws !== socket) return // a newer socket already superseded this one
      this.ws = null
      if (this.closedByUser) {
        this.handlers.onState('disconnected')
        return
      }
      this.scheduleReconnect()
    }

    socket.onerror = () => {
      // The browser always follows an error with a close event, which is
      // where reconnect/disconnected handling actually happens — nothing
      // else to do here.
    }
  }

  private scheduleReconnect(): void {
    this.handlers.onState('reconnecting')
    if (this.reconnectTimer !== null) return
    this.reconnectTimer = window.setTimeout(() => {
      this.reconnectTimer = null
      this.reconnectDelay = Math.min(this.reconnectDelay * 1.5, MAX_RECONNECT_DELAY_MS)
      if (!this.closedByUser && this.sessionId) this.open()
    }, this.reconnectDelay)
  }

  private teardownSocket(): void {
    if (!this.ws) return
    const socket = this.ws
    this.ws = null
    socket.onopen = null
    socket.onmessage = null
    socket.onclose = null
    socket.onerror = null
    try {
      socket.close()
    } catch {
      // already closed/closing — fine
    }
  }
}
