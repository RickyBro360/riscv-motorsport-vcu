# Racing Engineer Suite — Software Architecture Status

Final-integration-pass snapshot. Locked hardware architecture (ESP32-C6 VCU →
Raspberry Pi → Dashboard) is unchanged; this document states, subsystem by
subsystem, what the **current software** actually does, so no claim here
implies hardware that doesn't exist yet.

Status key: **IMPLEMENTED** (real, working software, no hardware needed to
demo it) · **SIMULATED** (working software, but the data it shows/acts on is
synthetic pending hardware) · **HARDWARE-DEPENDENT** (interface exists in
software; the real behavior requires physical ESP32-C6/Raspberry Pi hardware
that is not yet connected).

| # | Subsystem | Status | Notes |
|---|---|---|---|
| 1 | Frontend (Next.js) | IMPLEMENTED | App Router, all 12 dashboard pages present and wired to the shared `TelemetryContext`. |
| 2 | FastAPI backend | IMPLEMENTED | Sessions, frames, faults, config, AI/camera status stubs, firmware/OTA stubs, `/ws/telemetry`. |
| 3 | SQLite persistence | IMPLEMENTED | `sessions`, `telemetry_frames`, `faults`, `configuration` tables; `CREATE TABLE IF NOT EXISTS` only — startup never drops data. |
| 4 | Telemetry (central state) | IMPLEMENTED | One `TelemetryContext` owns `frame`/`ai`/`diagnostics`/`faults`; exactly one of {simulator interval, WebSocket, replay engine} is permitted to write it at a time, gated on `mode`. |
| 5 | WebSocket (`/ws/telemetry`) | IMPLEMENTED (round trip) / SIMULATED (payload) | The socket, reconnect handling, and broadcast fan-out are real. Until an ESP32-C6/Raspberry Pi gateway exists, the frame *content* sent over it in LIVE mode still originates from the frontend's own simulator acting as a stand-in gateway — now labelled **"Connected · Simulated Live Fallback"** in the UI rather than plain "Connected". |
| 6 | Simulation | IMPLEMENTED | `TelemetrySimulator` drives SIMULATION mode and the LIVE-mode fallback gateway described above. |
| 7 | Sessions | IMPLEMENTED | Create/stop/pause/resume, persisted to SQLite, survive backend/frontend restarts. |
| 8 | Replay | IMPLEMENTED | `ReplayEngine` is the single replay clock (rAF-driven, time-based not tick-based); loads real session frames from SQLite via `sessionApi.frames`. |
| 9 | Analytics | IMPLEMENTED | `lib/analytics.ts` computes every metric from actual recorded frames; missing data renders `N/A`, nothing is fabricated. |
| 10 | Digital Twin | IMPLEMENTED (consumer) / SIMULATED (values in sim/live-fallback) | Reads only `frame`/`ai` from the central context — no independent simulator or timer of its own. Four aero channels (LF/RF/LR/RR) are independent fields on `WingData`. |
| 11 | Diagnostics | IMPLEMENTED | Faults carry session/timestamp/severity/subsystem; during replay, faults are filtered to `timestamp <= replay.currentTimestamp`, so events appear only once the playhead reaches them. |
| 12 | Track Map | IMPLEMENTED (replay path) / SIMULATED (position source) | No GPS/vision positioning hardware exists, so vehicle position is always synthetic (derived from lap distance), honestly labelled "Simulated position" / "Replay position" — never invented for a session that wasn't recorded with it. |
| 13 | AI interface (Racing Engineer Station backend AI — `SOFTWARE/backend/app/ai_engine.py`'s `generate_recommendation()`; **not** Raspberry Pi-generated) | SIMULATED | `/api/ai/status` explicitly returns `mode: 'SIMULATED'`; the AI Engineer page labels itself "Simulation AI (rule-based)". The Raspberry Pi is a telemetry/communications gateway only and requests this recommendation over HTTP — it does not run AI inference itself. Recommendations are shown as advisory only — see §14. |
| 14 | AI recommendation vs. actuator state | IMPLEMENTED (separation) | AI recommendation, VCU-approved target, and actual wing angle are kept as distinct fields; the Digital Twin explicitly captions the AI figure "simulated, never commands the actuator directly." |
| 15 | Camera interface | SIMULATED / NOT CONNECTED | `/api/camera/status` is a stub; camera page shows "Not connected (simulated)" unless a real feed is wired up. |
| 16 | Firmware interface | SIMULATED | Local-only device list and staging workflow; no ESP32-C6 boards connected. |
| 17 | OTA interface | SIMULATED | Local job history only; explicitly labelled "no signed update channel or physical device exists yet." |
| 18 | CAN / LoRa | SIMULATED | Status bar shows `(SIMULATED)` suffix on both, driven by the same diagnostics object as everything else — no separate hardware poll. |

## Mode ownership (unchanged by this pass, verified)

```
mode === 'simulation' → SimulationTelemetrySource / setInterval owns frame/ai/diagnostics
mode === 'live'       → TelemetrySocketClient (onFrame) owns them; the interval only *sends*, never applies locally
mode === 'replay'     → ReplayEngine.onUpdate owns them; the simulation interval refuses to run at all
```

Switching modes: the socket disconnects outside `live`, the replay engine
pauses (not destroyed — position preserved) outside `replay`, and the
simulation interval is gated off during `replay`. No page opens a second
WebSocket, timer, or replay clock.

## Change made in this pass

The one dishonest-labeling gap found: LIVE mode's "Connected" badge did not
disclose that, absent real ESP32-C6/Raspberry Pi hardware, the frame content
is still generated by the frontend simulator before being round-tripped
through the real WebSocket/FastAPI/SQLite path. Fixed by relabeling that
state **"Connected · Simulated Live Fallback"** everywhere `LINK_LABEL` is
used (top bar, Live Telemetry page), and updating the Live Telemetry page's
description to say so explicitly. No behavior, timers, or data flow changed
— label only.

## Remaining hardware-dependent work (out of scope for a software pass)

- ESP32-C6 firmware (sensors, control, safety, CAN, LoRa, actuators)
- Real CAN/LoRa transport
- Raspberry Pi camera capture and OpenCV video processing as a PLANNED/FUTURE
  additional input feeding the Racing Engineer Station AI (`ai_engine.py`) —
  the Pi is a telemetry/communications gateway and is not planned to run AI
  inference itself; the VCU remains the sole actuator authority regardless
- Real GPS/vision-based track positioning
- Real ESP32-C6 OTA flashing and signed firmware packages
- On-vehicle validation of AI recommendations by the VCU
