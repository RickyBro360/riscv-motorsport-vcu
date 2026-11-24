/**
 * WebRTC signaling client for the on-board-phone camera feed.
 *
 * Video is peer-to-peer between the phone's browser and the dashboard's
 * browser — it never goes through the FastAPI backend. This client only
 * exchanges small SDP offer/answer + ICE candidate JSON messages over
 * /ws/camera-signal (see SOFTWARE/backend/app/camera_signal.py) so the two
 * sides can find each other, then hands off to a real RTCPeerConnection.
 *
 * This is intentionally separate from services/telemetry-socket.ts — the
 * camera feed and the CAN/LoRa telemetry path share nothing (different
 * socket, different transport, different failure modes) per the target
 * architecture's "video is a separate Internet/high-bandwidth path".
 *
 * Free public STUN is used for NAT traversal (no TURN server configured).
 * This is a known limitation: some cellular NATs require a TURN relay to
 * complete the connection. See CameraBroadcastClient/CameraViewerClient
 * doc comments below.
 */

import { backendWsBase } from '@/lib/backend-url'

const WS_BASE = backendWsBase()

const ICE_SERVERS: RTCIceServer[] = [{ urls: 'stun:stun.l.google.com:19302' }]

export type CameraConnectionState = 'idle' | 'connecting' | 'connected' | 'disconnected' | 'error'

type SignalMessage =
  | { type: 'offer'; sdp: string }
  | { type: 'answer'; sdp: string }
  | { type: 'ice-candidate'; candidate: RTCIceCandidateInit }
  | { type: 'phone-connected' }
  | { type: 'phone-disconnected' }

function openSignalSocket(role: 'phone' | 'viewer'): WebSocket {
  return new WebSocket(`${WS_BASE}/ws/camera-signal?role=${role}`)
}

/**
 * Dashboard side. Waits for the phone to publish, answers its offer,
 * and surfaces the resulting MediaStream (or connection state changes)
 * to the caller. One instance = one <video> element.
 */
export class CameraViewerClient {
  private ws: WebSocket | null = null
  private pc: RTCPeerConnection | null = null
  private closedByUser = false

  constructor(
    private readonly handlers: {
      onState: (state: CameraConnectionState) => void
      onStream: (stream: MediaStream | null) => void
    }
  ) {}

  connect(): void {
    if (typeof WebSocket === 'undefined') return // SSR guard
    this.closedByUser = false
    this.handlers.onState('connecting')
    const ws = openSignalSocket('viewer')
    this.ws = ws

    ws.onmessage = async (event) => {
      let message: SignalMessage
      try {
        message = JSON.parse(event.data)
      } catch {
        return
      }
      if (message.type === 'offer') {
        await this.handleOffer(message.sdp)
      } else if (message.type === 'ice-candidate') {
        try {
          await this.pc?.addIceCandidate(message.candidate)
        } catch {
          // Candidate arriving before remote description is set is
          // expected sometimes; harmless to drop.
        }
      } else if (message.type === 'phone-disconnected') {
        this.teardownPeerConnection()
        this.handlers.onState('disconnected')
        this.handlers.onStream(null)
      }
      // 'phone-connected' just means "expect an offer soon" — nothing to
      // do until it actually arrives.
    }

    ws.onclose = () => {
      if (this.closedByUser) return
      this.handlers.onState('disconnected')
      this.handlers.onStream(null)
    }

    ws.onerror = () => {
      this.handlers.onState('error')
    }
  }

  disconnect(): void {
    this.closedByUser = true
    this.teardownPeerConnection()
    this.ws?.close()
    this.ws = null
    this.handlers.onState('idle')
    this.handlers.onStream(null)
  }

  private async handleOffer(sdp: string): Promise<void> {
    this.teardownPeerConnection()
    const pc = new RTCPeerConnection({ iceServers: ICE_SERVERS })
    this.pc = pc

    pc.ontrack = (event) => {
      this.handlers.onStream(event.streams[0] ?? null)
      this.handlers.onState('connected')
    }
    pc.onicecandidate = (event) => {
      if (event.candidate) {
        this.send({ type: 'ice-candidate', candidate: event.candidate.toJSON() })
      }
    }
    pc.onconnectionstatechange = () => {
      if (pc.connectionState === 'failed' || pc.connectionState === 'closed') {
        this.handlers.onState('disconnected')
        this.handlers.onStream(null)
      }
    }

    await pc.setRemoteDescription({ type: 'offer', sdp })
    const answer = await pc.createAnswer()
    await pc.setLocalDescription(answer)
    this.send({ type: 'answer', sdp: answer.sdp ?? '' })
  }

  private send(message: SignalMessage): void {
    if (this.ws && this.ws.readyState === WebSocket.OPEN) {
      this.ws.send(JSON.stringify(message))
    }
  }

  private teardownPeerConnection(): void {
    if (this.pc) {
      this.pc.ontrack = null
      this.pc.onicecandidate = null
      this.pc.onconnectionstatechange = null
      this.pc.close()
      this.pc = null
    }
  }
}

/**
 * Phone side (loaded from /camera/broadcast). Captures the phone's camera,
 * publishes it as a WebRTC offer to any listening viewer(s), and reports
 * connection state so the broadcast page can show it plainly. Local
 * recording (MediaRecorder -> phone storage) is handled separately by the
 * broadcast page itself — it is independent of this publish path, exactly
 * per the "these are independent" requirement.
 */
export class CameraBroadcastClient {
  private ws: WebSocket | null = null
  private pc: RTCPeerConnection | null = null
  private closedByUser = false

  constructor(
    private readonly handlers: {
      onState: (state: CameraConnectionState) => void
    }
  ) {}

  async publish(stream: MediaStream): Promise<void> {
    if (typeof WebSocket === 'undefined') return // SSR guard
    this.closedByUser = false
    this.handlers.onState('connecting')
    const ws = openSignalSocket('phone')
    this.ws = ws

    ws.onopen = async () => {
      await this.startOfferIfViewerPresent(stream)
    }

    ws.onmessage = async (event) => {
      let message: SignalMessage
      try {
        message = JSON.parse(event.data)
      } catch {
        return
      }
      if (message.type === 'answer') {
        try {
          await this.pc?.setRemoteDescription({ type: 'answer', sdp: message.sdp })
        } catch {
          this.handlers.onState('error')
        }
      } else if (message.type === 'ice-candidate') {
        try {
          await this.pc?.addIceCandidate(message.candidate)
        } catch {
          // Fine to drop late/duplicate candidates.
        }
      }
    }

    ws.onclose = () => {
      if (this.closedByUser) return
      this.handlers.onState('disconnected')
    }

    ws.onerror = () => {
      this.handlers.onState('error')
    }
  }

  stop(): void {
    this.closedByUser = true
    if (this.pc) {
      this.pc.ontrack = null
      this.pc.onicecandidate = null
      this.pc.onconnectionstatechange = null
      this.pc.close()
      this.pc = null
    }
    this.ws?.close()
    this.ws = null
    this.handlers.onState('idle')
  }

  // A fresh offer is created immediately on connect. Any viewer (existing
  // or one that joins later) triggers the backend to send "phone-connected"
  // to itself on join and gets relayed this same offer via a re-send below
  // for viewers that join after the socket opened.
  private async startOfferIfViewerPresent(stream: MediaStream): Promise<void> {
    this.teardownPeerConnection()
    const pc = new RTCPeerConnection({ iceServers: ICE_SERVERS })
    this.pc = pc
    stream.getTracks().forEach((track) => pc.addTrack(track, stream))

    pc.onicecandidate = (event) => {
      if (event.candidate) {
        this.send({ type: 'ice-candidate', candidate: event.candidate.toJSON() })
      }
    }
    pc.onconnectionstatechange = () => {
      if (pc.connectionState === 'connected') {
        this.handlers.onState('connected')
      } else if (pc.connectionState === 'failed' || pc.connectionState === 'closed') {
        this.handlers.onState('disconnected')
      }
    }

    const offer = await pc.createOffer()
    await pc.setLocalDescription(offer)
    this.send({ type: 'offer', sdp: offer.sdp ?? '' })
  }

  private send(message: SignalMessage): void {
    if (this.ws && this.ws.readyState === WebSocket.OPEN) {
      this.ws.send(JSON.stringify(message))
    }
  }

  private teardownPeerConnection(): void {
    if (this.pc) {
      this.pc.close()
      this.pc = null
    }
  }
}
