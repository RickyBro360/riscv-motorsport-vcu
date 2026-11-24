# AI Engineer — how it works (Stage 2d)

Code: `SOFTWARE/backend/app/ai_engineer.py` (engine), `app/twin.py` (digital twin),
`app/vcu_config.py` (tunables), `app/services/vehicle_commands.py` (outbox),
`app/routers/engineer.py` / `vehicle.py` (API), dashboard `app/ai-engineer/page.tsx`.

**What it is:** deterministic rules plus a digital-twin optimiser. There is no trained
model, and the UI says so. Every conclusion carries the numbers it was drawn from.

## 1. Authority — unchanged from the locked architecture

```
AI proposes ──► operator approves ──► backend outbox ──► gateway (Pi or ground bridge)
   ──► CAN 0x180 CONFIG_WRITE ──► VCU ConfigStore validates (bounds + state rules)
   ──► applies now, or stages to the next start/finish crossing ──► CAN 0x181 CONFIG_ACK
   ──► back over LoRa/Wi-Fi ──► backend marks the command applied / staged / rejected
```

- The AI never writes to the outbox. Only an approval does.
- The VCU re-checks every value. The backend only pre-checks the same bounds so that it can refuse a bad value immediately.
- Safety thresholds are not tunable.
- The live aero messages (`AI_RECOMMENDATION`) are accepted only in AI-assist mode, only when fresh (< 500 ms) and confident (≥ 0.55), and only within ±20° of the VCU's own map baseline.
- A lost ACK is recovered from the VCU's `CONFIG_REPORT` round robin. An un-ACKed write is resent every 3 s, up to 6 tries, and then marked **expired**.

## 2. Live look-ahead aero (position-based, about 5 Hz)

1. Rate on the map = rear wheel speed ÷ dyno lap length.
2. Predicted position = VCU position + rate × look-ahead (default 0.35 s: LoRa hop, VCU staleness margin and servo start).
3. At the predicted position it applies the VCU's own flap formula: straight or corner angles, with the asymmetric split from the modelled lateral g.
4. The result: before a braking zone the AI already calls corner mode, and the flaps start rising before the map's own switch.
5. Confidence is 0.9 in steady sections and 0.72 across a transition.

## 3. Inferences (last 30 s of the session)

| Insight | Data | Why it matters |
|---|---|---|
| Where the car is | VCU position + circuit map | Corner number and radius, next straight/corner event, modelled speed and g |
| Supply headroom | 90th-percentile current vs the adapter's 2.2 A, lowest voltage | The adapter trips before the VCU's own over-current check |
| Supply + wiring resistance | slope of V against I | High R means a bad lead, fuse holder or terminal |
| Motor thermal trend | °C/min, time to 80 °C | Avoids a mid-run over-temperature fault |
| Flap tracking (rear LR/RR, the MG996R pair with feedback) | steady error and response time vs the VCU target | Binding linkage, stale calibration, slow servo |
| Rear motor balance | RL vs RR on one shared PWM | Brush/bearing/roller drag on one motor |
| Front wheels idle | FL/FR ≈ 0 | The rig does not turn the fronts: speed comes from the rear, slip is unmeasurable |
| Vibration vs speed | VCU RMS mg at high vs low speed | Wheel/roller imbalance, loose mount; it also corrupts the IMU |
| LoRa link | packet loss, RSSI | Late look-ahead messages get dropped by the VCU as stale |
| AI acceptance | the VCU's verdicts | Shows the authority chain working (or aero mode set to baseline) |

## 4. Proposals

| Kind | Fires when | Change | Evaluated by |
|---|---|---|---|
| `duty_cap_supply` | adapter 90th-percentile current > 88 % of its rating, or voltage < 10.8 V | duty cap −12 % | 90th-percentile current and dyno lap, before vs after |
| `duty_cap_thermal` | projected < 10 min to 80 °C | duty cap −15 % | motor temperature trend |
| `duty_floor` | stall watchdog tripped, or floor < `dyno cal` start duty + 3 % | floor = start + 5 % | — |
| `close_early_servo_lag` | the map advances further during a flap transition (measured on the rear MG996R pair, else slew-limited) than the current close-early offset | close straight mode that many full-scale metres earlier | twin lap cost is quoted |
| `aero_optimise` | the twin finds ≥ 0.1 s/lap from different flap angles (196-point grid) | straight/corner angles, front/rear | twin lap, before vs after |
| `lap_length` | dyno lap > 150 s or < 30 s | resize so a lap takes ~75 s | dyno lap |

- There is only one open proposal per kind.
- A rejected kind is not re-proposed for 2 minutes.
- An unanswered proposal expires after 5 minutes if its condition has cleared.

## 5. Digital twin (`app/twin.py`)

- It is a quasi-steady-state point-mass lap: forward and backward passes, a friction circle and constant power.
- It uses illustrative 2026-style numbers: 800 kg, 620 kW, μ 1.8.
- The aero map uses the mean flap angle θ:
  - Downforce **saturates**: ClA = 1.78 + 3.37(1 − e^(−θ/18°)).
  - Drag **keeps rising**: CdA = 0.923 + 0.004θ + 0.00028θ².
  - Both pass through the reference lap points used by `build_tracks.py`.
- Because downforce saturates while drag keeps rising, the best corner-mode angle differs by circuit. Across the 2026 calendar it comes out between 25/35° and 45/45°. That trade-off is what `aero_optimise` searches.
- **What it cannot do:**
  - Asymmetric flaps are a yaw tool, and a point mass has no yaw.
  - The rollers do not feel downforce, so the dyno lap time does not change when the flaps change. The twin lap time does.
