# System Architecture

> **CURRENT IMPLEMENTATION NOTE:** The diagram below (and the folder
> names inside it) is carried forward from an earlier documentation pass
> and predates the project's final packaging consolidation. In the
> CURRENTLY IMPLEMENTED system, the Raspberry Pi (`HARDWARE/raspberry_pi/main.py`)
> is a **vehicle telemetry/communications gateway only** — it does not
> run OpenCV, track analysis, or AI inference, and does not generate wing
> recommendations locally. The sole AI recommendation generator is
> `SOFTWARE/backend/app/ai_engine.py`'s `generate_recommendation()`, a
> deterministic, rule-based function (not a trained ML model) running at
> the Racing Engineer Station. The Pi requests a recommendation from that
> backend over HTTP and relays it to the VCU over CAN — it never computes
> one itself. See `Documentation/RaspberryPiArchitecture.md` for the
> corresponding note on the Pi-side module breakdown below, which
> describes a PLANNED/earlier on-Pi AI path that was not carried into the
> current implementation. Camera/OpenCV/track-analysis remain a
> PLANNED/FUTURE input to the Racing Engineer Station AI, not a current
> Pi-local capability.

## Locked hardware architecture (unchanged by this pass)

```
Racing Engineer Station (Web Dashboard: Next.js + FastAPI + SQLite + WebSocket)
        ^
        | LoRa telemetry (Firmware/LoRa <-> RaspberryPi/lora)
        |
Raspberry Pi 5 (Vehicle Telemetry/Communications Gateway — CURRENT: HARDWARE/raspberry_pi/main.py)
  - Pi Camera V3 -> OpenCV -> Track Analysis -> AI -> Wing Recommendation
    [PLANNED/FUTURE — not part of the current gateway; see note above.
     CURRENT: Pi relays telemetry to the Racing Engineer Station backend
     and requests a recommendation from ai_engine.py's
     generate_recommendation() over HTTP.]
        |
        | CAN / UART — AI recommendations ONLY, never actuator commands
        v
ESP32-C6 RISC-V VCU (Firmware/)
  - HAL, Sensor Manager, Vehicle State Estimator, Control Manager,
    Active Aero Controller, Safety Manager, State Machine, Scheduler,
    CAN Manager, LoRa Manager, Diagnostics, Data Logger, Configuration, Watchdog
        |
   +----+----+-----------------+
   |         |                 |
Sensors   CAN / LoRa       Actuators
   |                            |
   +- ICM42688P IMU             +- Motor
   +- AS5600 steering encoder   +- Steering servo
   +- 4x AS5600 wing encoders   +- LF/RF/LR/RR Aero servos
   +- 4x Hall wheel sensors
   +- INA226 current
   +- ADS1115 battery voltage
   +- DS18B20 temperature
```

This diagram is unchanged from the locked project specification — nothing
in this pass altered the hardware architecture, replaced the ESP32-C6 or
Raspberry Pi 5, removed CAN/LoRa, or let AI address an actuator directly.

## Software repository structure

```
Major Project (this repo)/
├── racing-engineer-suite/   [PRE-EXISTING, UNCHANGED] Next.js dashboard
├── backend/                 [PRE-EXISTING, UNCHANGED] FastAPI + SQLite
├── Firmware/                [NEW] ESP32-C6 VCU, C, ESP-IDF-shaped
│   ├── main/, HAL/, Drivers/, Sensors/, Control/, Safety/, CAN/, LoRa/,
│   │   Scheduler/, Diagnostics/, Logger/, Configuration/, StateMachine/,
│   │   Tests/, Common/ (CRC + shared telemetry struct)
├── RaspberryPi/              [NEW, at the time this pass was written]
│   ├── config/, camera/, opencv/, track_analysis/, ai/, inference/,
│   │   telemetry/, can/, lora/, video/, wing_optimizer/, simulation/, tests/
│   (SUPERSEDED — this granular package layout, including any on-Pi `ai/`
│    or `wing_optimizer/` module, was later consolidated into the single
│    self-contained `HARDWARE/raspberry_pi/main.py` gateway script, which
│    performs no AI inference itself; see the note at the top of this
│    file and `Documentation/RaspberryPiArchitecture.md`.)
└── Documentation/            [EXTENDED] architecture + protocol + status docs
```

`DigitalTwin/`, `AI/`, and `Testing/` from the target structure in the
project brief are not separate top-level folders here: Digital Twin
already lives inside `racing-engineer-suite/app/digital-twin/` (frontend
consumer) with no firmware/Pi-side "Digital Twin" module of its own to
add; "AI" is `RaspberryPi/ai/` + `Firmware/Control/active_aero.c` (split
by where each half of the AI pipeline actually runs, not duplicated into
a third location); "Testing" is `Firmware/Tests/` + `RaspberryPi/tests/` +
`backend/tests/` (kept next to the code they test rather than centralized,
matching the pattern the pre-existing repo already used for
`backend/tests/`).

## Canonical telemetry contract

`Firmware/Common/vehicle_telemetry.h`'s `vehicle_telemetry_t` is the
vehicle-side canonical struct (Phase 2). It is a superset of the
dashboard's pre-existing `TelemetryFrame` (racing-engineer-suite/types/telemetry.ts)
plus raw per-wheel/per-wing/comms-health fields the vehicle needs
internally. `RaspberryPi/telemetry/adapters.py`'s `to_dashboard_frame()`
is the single, explicit adapter between the two — no field was renamed or
reinterpreted on the dashboard side to accommodate it. See
`Documentation/TelemetryProtocol.md` (pre-existing) for the dashboard's
own contract and `Documentation/RaspberryPiArchitecture.md` for the
adapter's mapping table (including the VCU-state collapse note).

See CANProtocol.md and LoRaProtocol.md for the two vehicle-external wire
protocols, both separate from the WebSocket JSON contract above.
