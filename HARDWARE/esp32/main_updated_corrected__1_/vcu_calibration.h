/*
 * =============================================================================
 * vcu_calibration.h — SERVO + KNOB CALIBRATION. THIS IS THE FILE YOU EDIT BY HAND.
 * =============================================================================
 * Every number here can ALSO be changed live, without re-flashing:
 *   - Dashboard: Vehicle Setup -> "Servo calibration" (each change moves that servo to the
 *     new pulse for 2.5 s so you can see it), then "Save on car".
 *   - Console (115200): `cal` lists everything; `cal steer left 1150`, `cal flap rl max 1520`,
 *     `cal neutral 15`, ... then `cal save`. `cal` also prints a ready-to-paste copy of
 *     this block with your live values.
 *
 * WHICH VALUES WIN AT BOOT (FW 2.3.3 — a re-flash can never make a servo jump)
 *   - SERVO END POINTS (all the *_US pulses): what is saved on the car (`cal save` /
 *     "Save on car") ALWAYS wins. Editing the pulses below does nothing on a car that has
 *     a saved calibration; the boot log says "file differs". To use the file pulses on
 *     purpose: `cal reset` (servos MOVE to the file values) then `cal save`.
 *   - FLAP LAW + KNOB (LOCK, NEUTRAL, SWING, KNOB_*): this file wins whenever you edit it
 *     (fingerprint stored with every save). These only move flaps BETWEEN your saved FLAT
 *     and MAX end points, so they are safe to change.
 *   - Nothing saved on the car yet: everything comes from this file.
 *
 * HOW TO FIND THE NUMBERS (linkages connected, car on the stand)
 *   1. Console: `pulse 0 1500` moves the steering servo to a raw 1500 us. Step it
 *      (`pulse 0 1450`, `pulse 0 1400`, ...) until the wheels are at full LEFT lock without
 *      the servo buzzing against a stop. That number is STEER_LEFT_US. Same for RIGHT and
 *      for straight-ahead (CENTER). `bench off` releases the servo.
 *   2. Flaps: `pulse <1..4> <us>` (1 FL, 2 FR, 3 RL, 4 RR). FLAT = flap at its lowest
 *      downforce position (0 deg). MAX = flap at its highest downforce position (45 deg).
 *      A servo mounted mirror-image just has FLAT > MAX — that is fine, it is how the
 *      left and right flaps end up moving the right way.
 *   3. Put the numbers below (or set them live and `cal save`).
 *
 * Servo pulse limits: 500..2500 us. MG995/MG996R/SG90 are all roughly 1000-2000 us for
 * 90 degrees of horn travel, so 45 degrees of flap is about 500 us of pulse.
 * =============================================================================
 */
#pragma once

// ---------------------------------------------------------------- STEERING SERVO (MG995, servo 0)
#define VCU_CAL_STEER_LEFT_US       1150   // pulse at full LEFT lock
#define VCU_CAL_STEER_CENTER_US     1600   // pulse with the front wheels straight
#define VCU_CAL_STEER_RIGHT_US      2200   // pulse at full RIGHT lock
#define VCU_CAL_STEER_LOCK_DEG      30     // road-wheel angle at full lock (5..45). Knob range and flap law use it.
// 0 = send NO pulses to the steering servo (it stays limp; the knob still sets the steering
// ANGLE that the flaps follow and the dashboard shows). Set 0 while a CONTINUOUS-ROTATION (360 deg)
// servo is fitted: such a servo turns pulses into SPEED, not position, and will wind the steering
// into its stops. Check: `pulse 0 1300` — a positional servo moves and stops, a 360 deg one keeps
// spinning. Set 1 once a normal 180 deg servo is fitted.
#define VCU_CAL_STEER_SERVO_ENABLED 1   // MG996R (180 deg) fitted 2026-11

// ---------------------------------------------------------------- FLAP SERVOS
// FLAT = 0 deg (least downforce), MAX = 45 deg (most downforce).
#define VCU_CAL_FLAP_FL_FLAT_US     1500   // servo 1, front-left  SG90
#define VCU_CAL_FLAP_FL_MAX_US      650
#define VCU_CAL_FLAP_FR_FLAT_US     1500   // servo 2, front-right SG90
#define VCU_CAL_FLAP_FR_MAX_US      1000
#define VCU_CAL_FLAP_RL_FLAT_US     1750   // servo 3, rear-left   MG996R (+ pot feedback)
#define VCU_CAL_FLAP_RL_MAX_US      2000
#define VCU_CAL_FLAP_RR_FLAT_US     1420   // servo 4, rear-right  MG996R (+ pot feedback)
#define VCU_CAL_FLAP_RR_MAX_US      900

// ---------------------------------------------------------------- STEERING -> FLAPS
// Wheels straight: all four flaps at NEUTRAL. Turning RIGHT: the LEFT (outside) flaps rise
// toward NEUTRAL + SWING (more downforce) and the RIGHT (inside) flaps drop toward
// NEUTRAL - SWING (less downforce), in proportion to how far the wheels are turned.
// Turning LEFT is the mirror image. Result is clamped to 0..45 deg.
#define VCU_CAL_FLAP_NEUTRAL_DEG    45     // 0..45   45 = straight: all four flaps at MAX
#define VCU_CAL_FLAP_SWING_DEG      45     // 0..90 at full lock. Above 45 the inside pair is fully flat
                                           // before full lock (90 = flat at half lock).
// FW 2.3.3 default = "straight: all MAX; turning: the INSIDE pair (front + rear) drops toward FLAT,
// the OUTSIDE pair stays MAX": NEUTRAL 45, SWING 45 (inside flat at full lock; 90 = flat at half lock).
// Knob LEFT -> left flaps go flat, right flaps stay max. Knob RIGHT -> the mirror image.
// If ONE flap moves the wrong way (rises when it should drop), its FLAT/MAX are swapped:
// `cal flap <fl|fr|rl|rr> swap`, check, `cal save`.

// ---------------------------------------------------------------- DRS (dashboard button, CONFIG key 25)
// Press: all four flaps FLAT for OPEN_MS (whatever the steering does), then all MAX for HOLD_MS,
// then back to following the steering. (Timings only — not part of the saved calibration.)
#define VCU_CAL_DRS_OPEN_MS         5000
#define VCU_CAL_DRS_HOLD_MS         1000

// ---- FW 2.3.4 --------------------------------------------------------------------------------
// Wheel speed ESTIMATED from the motor duty (the hobby hall modules read too badly to show).
// 1 = estimate (dashboard labels it "estimated from duty"; the pulse-based stall watchdog is OFF,
//     because it needs trustworthy wheel pulses). 0 = hall sensors, as before.
#define VCU_CAL_SPEED_FROM_DUTY     1
#define VCU_CAL_MOTOR_MAX_RPM       1000   // Johnson geared motor, free speed at 100 % duty
#define VCU_CAL_WHEEL_DIAMETER_MM   90     // MEASURE the tyre and set this (user: 80-100 mm)
#define VCU_CAL_MOTOR_MIN_DUTY_PCT  20     // below this the motor does not turn: estimate = 0
// FW 2.3.5 — SIMULATED sensor behaviour on top of the duty estimate (exploration only; the numbers
// are NOT measurements, do not present them as hall-sensor data):
// DISPLAY ONLY: the motor PWM, the odometer and the VCU's logic never see the jitter or the spikes.
//   SIM_NOISE 1          each rear wheel gets its own slow jitter of +/- JITTER_PCT and, every
//                        SPIKE_MIN_S..SPIKE_MAX_S seconds, a short spike of 1.5x..3.5x the speed.
//   REPORT_AS_SENSOR 1   the VCU reports speed source 0, so the dashboard shows its original
//                        "measured" labels instead of "estimated from motor duty".
// The odometer, lap position and the VCU's own logic keep using the clean estimate.
#define VCU_CAL_SPEED_SIM_NOISE         1
#define VCU_CAL_SPEED_JITTER_PCT        10
#define VCU_CAL_SPEED_SPIKE_MIN_S       6
#define VCU_CAL_SPEED_SPIKE_MAX_S       14
#define VCU_CAL_SPEED_SPIKE_MS          200    // FW 2.3.6: how long a spike lasts; it also shows on the vehicle speed
#define VCU_CAL_SPEED_REPORT_AS_SENSOR  1
// Press the KY-040 knob = DRS. Wire the encoder's SW pin to J12 pin 3, the FRONT-RIGHT wheel-sensor signal
// (ESP32-C6 GPIO2, classic stand-in D26): that input is free once the front halls are not used.
// The switch connects SW to GND when pressed; the internal pull-up is enabled. 0 = off.
#define VCU_CAL_KNOB_BUTTON_DRS     1
// Demo 2 (FW 2.3.8): one auto-driven lap of Monza, scaled so the lap takes about this long.
#define VCU_CAL_DEMO_LAP_SECONDS    34
// FW 2.3.9 demo-lap shaping: highest duty on the straights (%), and the steering it shows:
// tight corners (chicanes) = full lock, long corners = HALF_LOCK_PCT of lock, smoothed.
#define VCU_CAL_DEMO_LAP_TOP_DUTY_PCT   80
#define VCU_CAL_DEMO_LAP_HALF_LOCK_PCT  50
// IMU: 1 = if no mounting correction is saved and the board is clearly not flat at power-up
// (more than ~30 deg off), level it automatically (same as typing `imu level`). Car must be on its wheels.
#define VCU_CAL_IMU_AUTO_LEVEL      1
// Demo corner sequence (dashboard "Play demo" / console `demo`): highest duty it uses, in %.
#define VCU_CAL_DEMO_TOP_DUTY_PCT   100

// ---------------------------------------------------------------- KNOB (KY-040 = steering wheel)
#define VCU_CAL_KNOB_DEG_PER_CLICK  3      // road-wheel degrees per click (0.5..10). 3 deg x 10 clicks = full lock
#define VCU_CAL_KNOB_REVERSE        0      // 1 if turning the knob clockwise steers LEFT
#define VCU_CAL_KNOB_COUNTS_PER_CLICK 2    // hardware: quadrature counts per detent. Measured 2 on this
                                           // KY-040 (FW 2.2.7). If one click moves the wheels two steps, set 4.
