# Changelog

Summary only. Full notes and hardware re-test lists are in `SOFTWARE/Documentation/ChangeLog_FW*.md`.
The CAN protocol is still version 2; new CONFIG keys are additive. The Pi `main.py`, `bridge.py`, backend and dashboard must be updated together.

## FW 2.3.11 – 2.3.12 · exploration (virtual car only)
- AI steering request (CONFIG key 27): replaces the knob while fresh values arrive, refused in FAULT / E-STOP; the knob takes back control at once.
- Dashboard **AI Model** tab: a small network trained on the VCU's own Monza circuit run. "AI recommends" shows its suggestions; "AI drives" sends them over `/ws/ai-command` (off by default).
- IMU level / nose / flip now saved to flash.

## FW 2.3.4 – 2.3.7 · exploration
- Rear wheel speed computed from motor duty (`VCU_CAL_SPEED_FROM_DUTY`), because the hobby hall sensors read badly.
- Knob press = DRS. VCU-run demo corner sequence (key 26).
- Digital twin rebuilt from the chassis geometry.
- 2.3.5 / 2.3.6 add **simulated** display noise to that computed speed (`VCU_CAL_SPEED_SIM_NOISE`). Display only.
- Motor duty cap default 100 %.

## FW 2.3.3 · final demo, 18 Nov 2026
- Asymmetric aero: straight = all four flaps at maximum; turning, the inside pair drops toward flat in proportion to steering.
- DRS (key 25): all flat 5 s, all max 1 s, then back to the law.
- Steering servo replaced with a 180° MG996R; full calibration on the car.

## FW 2.3.0
- KY-040 knob is the steering wheel (PCNT hardware counter). Flaps follow steering.
- Motor from the dashboard (key 9) with the ground link as deadman.
- Fewer stops: only E-STOP latches; other faults clear themselves.
- Servo calibration file `vcu_calibration.h` + live calibration from the dashboard.
- Dashboard lag fixed (SQLite writes off the event loop, WAL, concurrent broadcast).

## FW 2.2.x
- Drive modes DYNO / TRACK, dashboard run control and E-STOP, traction control, stall watchdog.
- One firmware for two boards: `vcu_hw_profile.h` selects the prototype PCB or PCB rev B.
- Virtual car: real `vcu_logic.h` behind the real Pi gateway on a virtual CAN bus.
- Classic-ESP32 stand-in pin map after the ESP32-C6 was damaged (since replaced).

## FW 2.1.x · Stage 2d
- Dyno track runs on 24 circuits, AI engineer with approval, circuit-map dashboard, camera recording.

## Stage 2c
- First end-to-end chain on hardware: VCU → CAN → Pi → LoRa → ground ESP32 → backend → dashboard.
