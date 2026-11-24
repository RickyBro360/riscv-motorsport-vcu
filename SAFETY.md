# Safety

Every rule here comes from something that went wrong, or nearly did, while building this car.

## Before every power-up

1. **Meter-check the 12 V side.** With the power off, check that nothing on the 12 V side (adapter, INA260 IN+/IN-, motor driver 12 V terminal) touches VSYS, 3V3 or any MCU pin. About one second of 12 V on the logic side killed an ESP32-C6 VCU on 2026-11-12.
2. **Set the buck to 5.0–5.2 V with no load connected,** before any servo or the VSYS jumper (J17) goes on.
3. **Fit antennas on both Ra-02 radios** before power-up. Transmitting without an antenna can damage the PA.
4. **Car on the rollers or strapped down,** with nothing touching the flaps or the steering linkage.

## USB and back-feed

A laptop was destroyed by back-feed through the VCU's USB port. Connect USB to the car **only** when one of these is true:

- the 12 V adapter is unplugged, **or**
- J17 (5 V → VSYS) is removed, **or**
- the cable goes through a USB isolator.

Never connect anything to the module's VBUS pin.

## Power

- Three metal-gear servos can stall at about **8 A at 5 V** together. Use a buck rated **≥ 5 A** and keep the 470–1000 µF bulk capacitor on the servo rail.
- The **12 V / 2.2 A adapter is for the dyno demo only**. The VCU derates motor duty above 2.0 A. Driving on the floor needs a 3S LiPo or a 5–10 A supply.
- If you move to a LiPo: fuse it, use a LiPo-safe bag for charging, and never leave it charging unattended.
- `R6` (4.7 kΩ pull-down on MOTOR_PWM) keeps the motor off while the MCU resets. Don't remove it.

## Software safety rules (do not weaken)

- The VCU is the **only** actuator authority. The Pi and the laptop send requests; the VCU checks confidence, age and range and may refuse them.
- Remote runs use the ground link as a **deadman**: an old heartbeat or a lost link stops the motor.
- E-STOP latches; everything else is a fault or a warning that clears itself.
- The AI command link (`/ws/ai-command`) is **off by default** and exists for exploration on the virtual car. It contradicts the "AI only recommends" rule; enable it (`VCU_AI_COMMAND_LINK=1`) only on the dyno with someone at the E-STOP.

## Workshop

- Cut sunboard with a blade. **Never use a soldering iron or a hot nail to make holes:** hot PVC gives off hydrochloric-acid fumes.
- Cyanoacrylate (743) and Araldite: ventilate, wear gloves, keep acetone away from the sunboard.
