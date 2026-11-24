# Raspberry Pi Architecture

> **SUPERSEDED / HISTORICAL RECORD — read before relying on this file.**
> Everything below describes an earlier, more granular Raspberry Pi
> Python package (`RaspberryPi/{camera,opencv,track_analysis,inference,
> ai,wing_optimizer,can,lora,telemetry,video,simulation}/`). That package
> layout was later consolidated into a single self-contained script,
> `HARDWARE/raspberry_pi/main.py`, per the project's final packaging
> architecture. This document is kept as a historical record of that
> module's design reasoning (why rule-based over trained ML, the
> hardware-independence pattern, etc.), NOT as a description of the
> CURRENT implementation.
>
> **CURRENT IMPLEMENTATION:** `HARDWARE/raspberry_pi/main.py` is a
> vehicle telemetry/communications gateway ONLY. It decodes CAN/LoRa
> telemetry, forwards it to the Racing Engineer Station backend, and
> requests a recommendation via `GET /api/ai/recommendation` — it never
> computes one itself. The sole AI recommendation generator is
> `SOFTWARE/backend/app/ai_engine.py`'s `generate_recommendation()`, a
> deterministic, rule-based function (explicitly not a trained ML model).
> There is no camera/OpenCV/track-analysis/inference pipeline or
> `wing_optimizer` running on the Pi in the current implementation.
> Camera/OpenCV/computer-vision input to the AI remains PLANNED/FUTURE
> functionality that would feed the Racing Engineer Station AI, not a
> Pi-local recommendation path.

## Module map (historical — see note above)

```
config/settings.py  (mirrors Firmware Configuration's role, tagged tunables)
     |
camera/  (CameraSource: SimulationCamera, FileCamera work with zero hardware;
          PiCameraSource is a documented stub that raises rather than fakes frames)
     |
opencv/pipeline.py  (resize -> ROI -> preprocess -> Canny edges)
     |
track_analysis/  (boundary.py extract_boundary, corner_classifier.py, racing_line.py)
     |
inference/pipeline.py  (glues camera+opencv+track_analysis -> feature dict -> AIModel)
     |
ai/  (AIModel interface; RuleBasedModel is the default, deterministic, no
      trained weights required; ONNXModel is real-inference-shaped but has
      no trained .onnx file for this project and raises clearly)
     |
wing_optimizer/optimizer.py  (InferenceResult -> two CAN frames, the ONLY
                               Pi-side code that touches the CAN boundary)
     |
can/  (CanInterface: MockCanInterface is in-process loopback;
       SocketCanInterface wraps python-can, STUB/NOT HARDWARE VALIDATED)
lora/  (LoRaInterface: MockLoRaBackend loopback; SX1278Backend STUB)
telemetry/  (VehicleTelemetry dataclass, adapters.py -> dashboard
             TelemetryFrame JSON shape, ws_client.py -> existing FastAPI
             /ws/telemetry, no backend changes needed)
video/streaming.py  (VideoStreamer, decoupled from the telemetry socket)
simulation/  (scenarios.py: 10 deterministic fault scenarios; bridge.py:
              ties AI -> wing_optimizer -> shared MockCanInterface bus
              into one testable Python-only pipeline)
```

## Why RuleBasedModel and not a trained model

No labeled training data or trained weights exist for this project yet.
`RuleBasedModel` is deliberately simple and explainable — proportional to
(speed, lateral-g), same physical inputs `Firmware/Control/active_aero.c`'s
independent baseline uses — so a disagreement between the Pi's
recommendation and the VCU's own physics baseline during simulation is a
meaningful signal, not noise from two unrelated heuristics. `ONNXModel`
exists as the real-inference-shaped class the project will grow into, but
calling `.predict()` on it raises `NotImplementedError` rather than
returning fabricated output (Phase 33: do not fake AI).

## The telemetry adapter boundary

`telemetry/adapters.py`'s `to_dashboard_frame()` is the **only** place
field-name/unit translation between the firmware's `VehicleTelemetry`
naming and the dashboard's existing `TelemetryFrame` naming happens. It
does not change either schema — see `Documentation/TelemetryProtocol.md`
(already existed pre-baseline) for the frontend/backend contract this
adapter targets. The VCU state name mismatch (firmware has `ARMED`,
`SELF_TEST`, `SHUTDOWN`; the dashboard's `VcuState` union doesn't) is
resolved by an explicit collapse table in the same file, not by changing
the frontend type.

## Hardware-independence guarantee

Every subpackage's development path (`Simulation*`/`Mock*`/`File*`
classes) has zero hardware imports at module scope — `picamera2`,
`onnxruntime`, `python-can`, and a real serial/SPI SX1278 library are all
imported **lazily, inside the Real* class's methods**, so `pip install -r
requirements.txt` plus `pytest` never requires a Raspberry Pi, camera, CAN
adapter, or LoRa radio to be present. This is exercised directly: the test
suite in this response ran in a plain Linux container with none of that
hardware.

## Status summary

| Module | Status |
|---|---|
| camera (Simulation/File), opencv, track_analysis, ai (RuleBased), inference, can (Mock), lora (Mock), telemetry (adapters), video, wing_optimizer, simulation | IMPLEMENTED — real logic, unit-tested |
| camera (PiCameraSource), can (SocketCanInterface), lora (SX1278Backend), ai (ONNXModel) | STUB — interface exists, raises `NotImplementedError` rather than faking hardware |
| telemetry/ws_client.py | IMPLEMENTED but only SIMULATED end-to-end (connects to a running backend; never driven by a real VCU feed) |
