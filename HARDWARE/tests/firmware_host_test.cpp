/*
 * firmware_host_test.cpp — runs the REAL VCU logic (vcu_logic.h, the same
 * header the ESP32-C6 sketch compiles) on a PC.
 *
 * Two modes:
 *   ./firmware_host_test selftest   internal assertions (state machine,
 *                                   safety, supervisor, motor, servo maths)
 *   ./firmware_host_test rpc        line protocol on stdin/stdout so
 *                                   test_firmware_host.py can compare the
 *                                   firmware codec byte-for-byte with the
 *                                   Raspberry Pi's Python codec.
 *
 * Built and driven by HARDWARE/tests/test_firmware_host.py (needs g++).
 * Manual:  g++ -std=c++17 -Wall -Wextra -Werror -I<sketch dir> firmware_host_test.cpp -o fht
 */
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <string>
#include <sstream>
#include <iostream>
#include <algorithm>

#include "vcu_logic.h"

using namespace vcu;

static int g_fail = 0;
#define CHECK(cond) do { if (!(cond)) { std::printf("FAIL %s:%d %s\n", __FILE__, __LINE__, #cond); ++g_fail; } } while (0)

static std::string hex(const CanFrame &f) {
  char buf[64]; int n = std::snprintf(buf, sizeof buf, "%03X:", f.id);
  for (int i = 0; i < f.dlc; ++i) n += std::snprintf(buf + n, sizeof buf - n, "%02X", f.data[i]);
  return buf;
}
static CanFrame parse(uint16_t id, const std::string &h) {
  CanFrame f; f.id = id; f.dlc = static_cast<uint8_t>(h.size() / 2);
  for (int i = 0; i < f.dlc && i < 8; ++i) f.data[i] = static_cast<uint8_t>(std::strtoul(h.substr(2 * i, 2).c_str(), nullptr, 16));
  return f;
}

// ---------------------------------------------------------------- FW 2.2 (VcuController)
// A healthy car on the bench: fresh sensors, CAN up, Pi heartbeat with the ground link up.
static VcuInputs healthy(uint32_t now) {
  VcuInputs in; in.now_ms = now; in.dt_s = 0.01f;
  in.battery_v = 12.0f; in.battery_a = 0.4f; in.battery_temp_c = 27; in.motor_temp_c = 30;
  in.can_link_ok = true; in.hb_seen = true; in.hb.lora_link_ok = true; in.hb.backend_ok = true; in.hb_age_ms = 50;
  return in;
}
static DriverCommand drv(uint32_t now, int16_t steer, int16_t thr, uint8_t flags) {
  DriverCommand d; d.valid = true; d.rx_ms = now; d.steering_permille = steer; d.throttle_permille = thr; d.flags = flags; return d;
}
/* Run the controller for `ms` with a simple plant: rear wheels follow duty (5 km/h per unit), one pulse
 * per 0.22 m. Returns the last outputs. `mutate` may change the inputs each step. */
static float g_rear = 0, g_dist_acc = 0;   // plant state (reset by each test's new controller)
template <typename F>
static VcuOutputs run(VcuController &c, uint32_t &t, uint32_t ms, F mutate, float *rear_kmh = nullptr) {
  VcuOutputs o; float &rear = g_rear; float &dist_acc = g_dist_acc;
  for (uint32_t k = 0; k < ms / 10; ++k) {
    t += 10;
    VcuInputs in = healthy(t);
    in.wheel_kmh[2] = in.wheel_kmh[3] = rear; in.speed_kmh = rear;
    float d = rear / 3.6f * 0.01f; dist_acc += d; in.rear_dist_m = d;
    if (dist_acc > 0.22f) { in.rear_pulses = 2; dist_acc -= 0.22f; }
    mutate(in);
    uint32_t ev = o.events;
    o = c.step(in);
    o.events |= ev;                         // events from every step of the window
    if (o.request_self_test) c.self_test_done(0);
    float target = o.motor_duty > 0.2f ? o.motor_duty * 5.0f : 0.0f;
    rear += (target - rear) * 0.05f;
  }
  if (rear_kmh) *rear_kmh = rear;
  return o;
}

static void selftest_fw22() {
  int32_t v;
  // --- config: new keys ---
  {
    ConfigStore cs; ConfigContext idle, run_ctx; run_ctx.run_active = true;
    CHECK(cs.snap.drive_mode == DRIVE_MODE_TRACK);                         // safe default: nothing moves without ARM
    CHECK(cs.write(CFG_KEY_TRACK_RUN, 1, idle, v) == CFG_REJ_STATE);        // map runs need the rollers
    CHECK(cs.write(CFG_KEY_DRIVE_MODE, 2, idle, v) == CFG_REJ_RANGE);
    CHECK(cs.write(CFG_KEY_DRIVE_MODE, DRIVE_MODE_DYNO, run_ctx, v) == CFG_REJ_STATE);
    CHECK(cs.write(CFG_KEY_DRIVE_MODE, DRIVE_MODE_DYNO, idle, v) == CFG_APPLIED && cs.snap.drive_mode == DRIVE_MODE_DYNO);
    CHECK(cs.write(CFG_KEY_TRACK_RUN, 1, idle, v) == CFG_APPLIED && cs.snap.track_run);
    CHECK(cs.write(CFG_KEY_DRIVE_MODE, DRIVE_MODE_TRACK, idle, v) == CFG_APPLIED && !cs.snap.track_run);  // cleared
    CHECK(cs.write(CFG_KEY_RUN_LAPS, 100, idle, v) == CFG_REJ_RANGE);
    CHECK(cs.write(CFG_KEY_RUN_LAPS, 3, run_ctx, v) == CFG_APPLIED && cs.snap.run_laps == 3);
    CHECK(cs.write(CFG_KEY_TC_SLIP_PERMILLE, 20, idle, v) == CFG_REJ_RANGE);
    CHECK(cs.write(CFG_KEY_TC_SLIP_PERMILLE, 0, idle, v) == CFG_APPLIED);
    CHECK(cs.write(CFG_KEY_TC_SLIP_PERMILLE, 300, idle, v) == CFG_APPLIED && cs.snap.tc_slip_permille == 300);
    CHECK(cs.write(CFG_KEY_RUN_CMD, 1, idle, v) == CFG_REJ_UNKNOWN);       // commands never reach the store
    CHECK(cs.write(CFG_KEY_ESTOP, 1, idle, v) == CFG_REJ_UNKNOWN);
    cs.hw_profile = HW_PROFILE_PCB_REV_B;
    CHECK(cs.read(CFG_KEY_RO_HW_PROFILE, v) && v == HW_PROFILE_PCB_REV_B);
    CHECK(cs.read(CFG_KEY_RO_WHEEL_PPR, v) && v == CFG_WHEEL_PULSES_PER_REV);
    CHECK(!cs.read(CFG_KEY_ESTOP, v));
    for (uint8_t i = 0; i < CONFIG_REPORT_KEY_COUNT; ++i) CHECK(cs.read(CONFIG_REPORT_KEYS[i], v));
  }
  // --- 0x148 ---
  {
    VcuModeStatus m{DRIVE_MODE_DYNO, HW_PROFILE_PCB_REV_B, MF_MOTOR_POWER | MF_REMOTE_RUN, 5, 42, -3};
    CanFrame f = canEncodeVcuMode(m);
    CHECK(f.id == 0x148 && f.dlc == 7 && f.data[0] == 0 && f.data[1] == 1 && f.data[2] == 5 && f.data[3] == 5);
    CHECK(f.data[4] == 42 && getI16(&f.data[5]) == -3);
  }
  // --- boot, e-stop command and reset through CONFIG_WRITE key 8 ---
  {
    g_rear = g_dist_acc = 0; VcuController c; c.begin(HW_PROFILE_PCB_REV_B, true, true); c.boot(0);
    CHECK(c.state() == VS_IDLE);
    uint32_t t = 0; auto none = [](VcuInputs &) {};
    VcuOutputs o = run(c, t, 100, none);
    CHECK(o.state == VS_IDLE && !o.motor_power && o.motor_duty == 0.0f);
    CHECK(c.write(CFG_KEY_ESTOP, ESTOP_CMD_TRIGGER, false, t, v) == CFG_APPLIED);
    o = run(c, t, 20, none); CHECK(o.state == VS_EMERGENCY_STOP);
    CHECK(c.write(CFG_KEY_ESTOP, 3, false, t, v) == CFG_REJ_RANGE);
    CHECK(c.write(CFG_KEY_ESTOP, ESTOP_CMD_RESET, false, t, v) == CFG_APPLIED);
    bool asked = false;
    for (int k = 0; k < 5 && !asked; ++k) { VcuInputs in = healthy(t += 10); o = c.step(in); asked = o.request_self_test; }
    CHECK(asked);
    c.self_test_done(0); o = run(c, t, 20, none); CHECK(o.state == VS_IDLE);
  }
  // --- dashboard run: refused in TRACK mode, runs in DYNO, link loss is the deadman ---
  {
    g_rear = g_dist_acc = 0; VcuController c; c.begin(HW_PROFILE_PCB_REV_B, true, false); c.boot(0);
    uint32_t t = 0; auto none = [](VcuInputs &) {};
    run(c, t, 100, none);
    CHECK(c.write(CFG_KEY_RUN_CMD, RUN_CMD_LAPS, false, t, v) == CFG_REJ_STATE);            // TRACK mode
    CHECK(c.write(CFG_KEY_DRIVE_MODE, DRIVE_MODE_DYNO, false, t, v) == CFG_APPLIED);
    VcuOutputs o = run(c, t, 50, none);
    CHECK(o.events & EV_MODE_CHANGED);
    CHECK(c.write(CFG_KEY_RUN_LAPS, 2, false, t, v) == CFG_APPLIED);
    CHECK(c.write(CFG_KEY_RUN_CMD, RUN_CMD_LAPS, false, t, v) == CFG_APPLIED);
    CHECK(c.write(CFG_KEY_DRIVE_MODE, DRIVE_MODE_TRACK, false, t, v) == CFG_REJ_STATE);    // not while running
    CHECK(c.write(CFG_KEY_RUN_CMD, RUN_CMD_DYNO_CAL, false, t, v) == CFG_REJ_STATE);       // one run at a time
    float rear = 0;
    o = run(c, t, 3000, none, &rear);
    CHECK(o.run_state == RUN_BENCH_LAPS && o.remote_run && o.lapping);
    CHECK(o.motor_power);                                       // kill switch on for the run
    CHECK(o.motor_duty > 0.3f && rear > 1.0f);
    CHECK(c.cfg.active_run_cmd == RUN_CMD_LAPS);
    CHECK(c.mode_status(o).flags & MF_REMOTE_RUN);
    // ground link lost: the run stops, power off, COMM_LOSS warning shown for a while
    o = run(c, t, 1500, [](VcuInputs &in) { in.hb.lora_link_ok = false; });
    CHECK(o.run_state == RUN_OFF && !o.motor_power && o.motor_duty == 0.0f);
    CHECK(o.fault_flags & FAULT_BIT_COMM_LOSS); CHECK(o.state == VS_IDLE);
    o = run(c, t, 3500, none); CHECK(!(o.fault_flags & FAULT_BIT_COMM_LOSS));
    // a run cannot start without the link
    CHECK(c.write(CFG_KEY_RUN_CMD, RUN_CMD_LAPS, false, t, v) == CFG_APPLIED);
    o = run(c, t, 100, none); CHECK(o.run_state == RUN_BENCH_LAPS);
    CHECK(c.write(CFG_KEY_RUN_CMD, RUN_CMD_STOP, false, t, v) == CFG_APPLIED);
    o = run(c, t, 20, none); CHECK(o.run_state == RUN_OFF && !o.motor_power);
    o = run(c, t, 50, [](VcuInputs &in) { in.hb_age_ms = 5000; });
    CHECK(c.write(CFG_KEY_RUN_CMD, RUN_CMD_LAPS, false, t, v) == CFG_REJ_STATE);
    // ... but the console (operator at the car) can
    CHECK(c.write(CFG_KEY_RUN_CMD, RUN_CMD_LAPS, true, t, v) == CFG_APPLIED);
    o = run(c, t, 200, [](VcuInputs &in) { in.hb_age_ms = 5000; });
    CHECK(o.run_state == RUN_BENCH_LAPS && !o.remote_run);
    // e-stop from the dashboard ends it at once
    CHECK(c.write(CFG_KEY_ESTOP, ESTOP_CMD_TRIGGER, false, t, v) == CFG_APPLIED);
    o = run(c, t, 10, none);
    CHECK(o.state == VS_EMERGENCY_STOP && o.run_state == RUN_OFF && o.motor_duty == 0.0f && !o.motor_power);
    CHECK(o.flaps[0] == CFG_WING_FAILSAFE_ANGLE_DEG && o.steering_deg == CFG_STEERING_FAILSAFE_DEG);
  }
  // --- laps target: the run stops itself ---
  {
    g_rear = g_dist_acc = 0; VcuController c; c.begin(HW_PROFILE_PROTOTYPE, false, false); c.boot(0);
    uint32_t t = 0; auto none = [](VcuInputs &) {};
    c.write(CFG_KEY_DRIVE_MODE, DRIVE_MODE_DYNO, false, t, v);
    c.write(CFG_KEY_LAP_LENGTH_CM, 500, false, t, v);           // 5 m laps
    c.write(CFG_KEY_RUN_LAPS, 1, false, t, v);
    run(c, t, 50, none);
    CHECK(c.write(CFG_KEY_RUN_CMD, RUN_CMD_LAPS, false, t, v) == CFG_APPLIED);
    VcuOutputs o; bool done = false;
    for (int k = 0; k < 60 && !done; ++k) { o = run(c, t, 500, none); done = o.run_state == RUN_OFF; }
    CHECK(done); CHECK(c.lap_count() >= 1);
    CHECK(!o.motor_power);                                      // prototype: no kill switch fitted
  }
  // --- TRACK mode: driver drives, map off, TC limits wheelspin (FW 2.3: no IMU straight/corner aero) ---
  {
    g_rear = g_dist_acc = 0; VcuController c; c.begin(HW_PROFILE_PCB_REV_B, true, false); c.boot(0);
    uint32_t t = 0;
    run(c, t, 50, [&](VcuInputs &in) { in.driver = drv(in.now_ms, 0, 0, DRV_FLAG_ARM); });
    VcuOutputs o = run(c, t, 400, [&](VcuInputs &in) { in.driver = drv(in.now_ms, 0, 0, DRV_FLAG_ARM); });
    CHECK(o.state == VS_DRIVE && o.motor_power);
    auto straight_line = [&](VcuInputs &in) {
      in.driver = drv(in.now_ms, 0, 1000, DRV_FLAG_ARM);
      in.wheel_kmh[0] = in.wheel_kmh[1] = in.wheel_kmh[2]; in.speed_kmh = in.wheel_kmh[2];
    };
    o = run(c, t, 2000, straight_line);
    CHECK(!o.aero_straight && !(o.track_flags & TF_RUN_ACTIVE));
    CHECK(o.motor_duty > 0.5f && !o.tc_active);
    // no knob fitted: the driver's wheel steers (packet + = RIGHT -> steering - = right); right turn: LEFT flaps up
    o = run(c, t, 600, [&](VcuInputs &in) { straight_line(in); in.driver.steering_permille = 1000; });
    CHECK(o.steer_source == STEER_SRC_DRIVER && o.steering_deg < -25.0f);
    CHECK(o.flaps[AERO_LF] > o.flaps[AERO_RF] + 30.0f && o.flaps[AERO_LR] > o.flaps[AERO_RR] + 30.0f);
    // wheelspin: rears 60 % faster than the fronts -> duty cut
    float duty_before = o.motor_duty;
    o = run(c, t, 600, [&](VcuInputs &in) {
      in.driver = drv(in.now_ms, 0, 1000, DRV_FLAG_ARM);
      in.wheel_kmh[0] = in.wheel_kmh[1] = 4.0f; in.wheel_kmh[2] = in.wheel_kmh[3] = 6.4f; in.speed_kmh = 4.0f;
    });
    CHECK(o.tc_active && o.tc_cut > 0.3f && o.motor_duty < duty_before * 0.8f);
    // TC off -> no cut
    c.write(CFG_KEY_TC_SLIP_PERMILLE, 0, false, t, v);
    o = run(c, t, 2000, [&](VcuInputs &in) {
      in.driver = drv(in.now_ms, 0, 1000, DRV_FLAG_ARM);
      in.wheel_kmh[0] = in.wheel_kmh[1] = 4.0f; in.wheel_kmh[2] = in.wheel_kmh[3] = 6.4f; in.speed_kmh = 4.0f;
    });
    CHECK(!o.tc_active);
    // in TRACK mode a dashboard circuit run is refused even when idle, and the mode cannot change in DRIVE
    CHECK(c.write(CFG_KEY_DRIVE_MODE, DRIVE_MODE_DYNO, false, t, v) == CFG_REJ_STATE);
    // driver lets go of ARM -> IDLE, power off
    o = run(c, t, 50, [&](VcuInputs &in) { in.driver = drv(in.now_ms, 0, 0, 0); });
    CHECK(o.state == VS_IDLE && !o.motor_power && o.motor_duty == 0.0f);
    CHECK(c.write(CFG_KEY_RUN_CMD, RUN_CMD_LAPS, true, t, v) == CFG_REJ_STATE);
    CHECK(c.write(CFG_KEY_RUN_CMD, RUN_CMD_DYNO_CAL, true, t, v) == CFG_REJ_STATE);
  }
  // --- stall while driving: the motor is locked out until the driver lets go ---
  {
    g_rear = g_dist_acc = 0; VcuController c; c.begin(HW_PROFILE_PCB_REV_B, true, false); c.boot(0);
    uint32_t t = 0;
    run(c, t, 450, [&](VcuInputs &in) { in.driver = drv(in.now_ms, 0, 0, DRV_FLAG_ARM); });
    VcuOutputs o = run(c, t, 3500, [&](VcuInputs &in) {
      in.driver = drv(in.now_ms, 0, 1000, DRV_FLAG_ARM); in.rear_pulses = 0; in.wheel_kmh[2] = in.wheel_kmh[3] = 0; });
    CHECK(o.track_flags & TF_STALLED); CHECK(o.motor_duty == 0.0f && !o.motor_power);
    o = run(c, t, 100, [&](VcuInputs &in) { in.driver = drv(in.now_ms, 0, 0, DRV_FLAG_ARM); });
    o = run(c, t, 500, [&](VcuInputs &in) { in.driver = drv(in.now_ms, 0, 1000, DRV_FLAG_ARM); });
    CHECK(o.motor_duty > 0.0f && o.motor_power);
  }
  // --- FW 2.3.12: AI steering (key 27) replaces the knob while fresh, then hands back ---
  {
    g_rear = g_dist_acc = 0; VcuController c; c.begin(HW_PROFILE_PROTOTYPE, false, true); c.boot(0);
    uint32_t t = 0; auto none = [](VcuInputs &) {}; int32_t vv = 0;
    VcuOutputs o = run(c, t, 500, none);
    CHECK(o.state == VS_IDLE && o.steer_source == STEER_SRC_KNOB);
    const float lock = c.cfg.trim.steer_lock_cdeg / 100.0f;
    CHECK(c.write(CFG_KEY_AI_STEER_CMD, 901, false, t, vv) == CFG_REJ_RANGE);
    CHECK(c.write(CFG_KEY_AI_STEER_CMD, 125, false, t, vv) == CFG_APPLIED && vv == 125);      // 12.5 deg LEFT
    o = run(c, t, 800, none);
    CHECK(o.steer_source == STEER_SRC_AI && std::fabs(o.steering_deg - 12.5f) < 0.05f);
    CHECK(o.flaps[AERO_RF] > o.flaps[AERO_LF]);                                               // flaps follow, as with the knob
    { int32_t rep = -1; CHECK(c.cfg.read(CFG_KEY_AI_STEER_CMD, rep) && rep == 125); }
    // kept alive by new values; beyond the lock is clamped
    CHECK(c.write(CFG_KEY_AI_STEER_CMD, -900, false, t, vv) == CFG_APPLIED && vv == -static_cast<int32_t>(lock * 10));
    o = run(c, t, 1000, none);
    CHECK(o.steer_source == STEER_SRC_AI && std::fabs(o.steering_deg + lock) < 0.05f);
    // no new value: back to the knob (which is still at 0)
    o = run(c, t, 2500, none);
    CHECK(o.steer_source == STEER_SRC_KNOB && std::fabs(o.steering_deg) < 0.05f);
    { int32_t rep = -1; CHECK(c.cfg.read(CFG_KEY_AI_STEER_CMD, rep) && rep == 0); }
    // a hand on the knob takes the steering back at once and blocks the AI for a while
    CHECK(c.write(CFG_KEY_AI_STEER_CMD, 100, false, t, vv) == CFG_APPLIED);
    o = run(c, t, 300, none); CHECK(o.steer_source == STEER_SRC_AI);
    o = run(c, t, 10, [](VcuInputs &in) { in.knob_detents = 2; });
    CHECK(o.steer_source == STEER_SRC_KNOB);
    CHECK(c.write(CFG_KEY_AI_STEER_CMD, 100, false, t, vv) == CFG_REJ_STATE);
    o = run(c, t, 800, none); CHECK(o.steer_source == STEER_SRC_KNOB && o.steering_deg < -1.0f);
    o = run(c, t, 2600, none);
    CHECK(c.write(CFG_KEY_AI_STEER_CMD, 100, false, t, vv) == CFG_APPLIED);
    o = run(c, t, 300, none); CHECK(o.steer_source == STEER_SRC_AI);
    // the ground link drops: a remote AI command is dropped with it
    o = run(c, t, 400, [](VcuInputs &in) { in.hb.lora_link_ok = false; });
    CHECK(o.steer_source == STEER_SRC_KNOB);
    CHECK(c.write(CFG_KEY_AI_STEER_CMD, 100, false, t, vv) == CFG_REJ_STATE);
    // e-stop: refused
    o = run(c, t, 300, none);
    CHECK(c.write(CFG_KEY_AI_STEER_CMD, 100, false, t, vv) == CFG_APPLIED);
    CHECK(c.write(CFG_KEY_ESTOP, 1, false, t, vv) == CFG_APPLIED);
    o = run(c, t, 200, none);
    CHECK(o.state == VS_EMERGENCY_STOP && o.steer_source != STEER_SRC_AI);
    CHECK(c.write(CFG_KEY_AI_STEER_CMD, 100, false, t, vv) == CFG_REJ_STATE);
  }
  // ============================== FW 2.3 ==============================
  // --- knob = steering wheel; flaps follow the steering ---
  {
    g_rear = g_dist_acc = 0; VcuController c; c.begin(HW_PROFILE_PROTOTYPE, false, true); c.boot(0);
    uint32_t t = 0; auto none = [](VcuInputs &) {};
    VcuOutputs o = run(c, t, 500, none);
    CHECK(o.state == VS_IDLE && o.steer_source == STEER_SRC_KNOB && std::fabs(o.steering_deg) < 1e-3f);
    const ServoTrim &tr = c.cfg.trim;
    // wheels straight: every flap at NEUTRAL
    for (int i = 0; i < 4; ++i) CHECK(std::fabs(o.flaps[i] - tr.flap_neutral_cdeg / 100.0f) < 0.05f);
    // 4 clicks clockwise = RIGHT 12 deg: left (outside) flaps UP, right (inside) DOWN, in proportion
    o = run(c, t, 10, [](VcuInputs &in) { in.knob_detents = 4; });
    o = run(c, t, 1000, none);
    CHECK(o.knob_pos == 4);
    CHECK(std::fabs(o.steering_deg - (-12.0f)) < 0.01f);
    const float s_frac = 12.0f / (tr.steer_lock_cdeg / 100.0f), n = tr.flap_neutral_cdeg / 100.0f, w = tr.flap_swing_cdeg / 100.0f;
    const float up = clampf(n + s_frac * w, 0.0f, 45.0f), dn = clampf(n - s_frac * w, 0.0f, 45.0f);
    CHECK(std::fabs(o.flaps[AERO_LF] - up) < 0.05f && std::fabs(o.flaps[AERO_LR] - up) < 0.05f);
    CHECK(std::fabs(o.flaps[AERO_RF] - dn) < 0.05f && std::fabs(o.flaps[AERO_RR] - dn) < 0.05f && dn < up);
    // steer left past full lock: clamped at the lock, knob position clamped too (no wind-up)
    o = run(c, t, 10, [](VcuInputs &in) { in.knob_detents = -40; });
    o = run(c, t, 1000, none);
    CHECK(std::fabs(o.steering_deg - tr.steer_lock_cdeg / 100.0f) < 0.01f && o.knob_pos == -10);
    CHECK(o.flaps[AERO_RF] > o.flaps[AERO_LF]);                         // left turn: right flaps up
    o = run(c, t, 10, [](VcuInputs &in) { in.knob_detents = 1; });      // one click back moves at once
    o = run(c, t, 300, none); CHECK(std::fabs(o.steering_deg - 27.0f) < 0.01f);
    // centre command (key 23)
    CHECK(c.write(CFG_KEY_KNOB_CMD, 1, false, t, v) == CFG_APPLIED);
    o = run(c, t, 500, none); CHECK(o.knob_pos == 0 && std::fabs(o.steering_deg) < 1e-3f);
    CHECK(c.write(CFG_KEY_KNOB_CMD, 2, false, t, v) == CFG_REJ_RANGE);
    // knob reversed
    CHECK(c.write(CFG_KEY_KNOB_REVERSE, 1, false, t, v) == CFG_APPLIED);
    o = run(c, t, 10, [](VcuInputs &in) { in.knob_detents = 2; }); o = run(c, t, 300, none);
    CHECK(o.steering_deg > 5.0f);
    // the knob keeps steering in FAULT (the motor is off, the servos are not)
    o = run(c, t, 1500, [](VcuInputs &in) { in.motor_temp_c = 95.0f; });
    CHECK(o.state == VS_FAULT && o.steer_source == STEER_SRC_KNOB && o.steering_deg > 5.0f);
    CHECK(o.flaps[AERO_RF] > o.flaps[AERO_LF]);
    // ... and in E-STOP everything goes to failsafe
    c.write(CFG_KEY_ESTOP, ESTOP_CMD_TRIGGER, false, t, v); o = run(c, t, 20, none);
    CHECK(o.state == VS_EMERGENCY_STOP && o.steering_deg == CFG_STEERING_FAILSAFE_DEG && o.flaps[0] == CFG_WING_FAILSAFE_ANGLE_DEG);
    // no knob fitted: knob clicks ignored
    VcuController noknob; noknob.begin(HW_PROFILE_PROTOTYPE, false, false); noknob.boot(0);
    o = run(noknob, t, 200, [](VcuInputs &in) { in.knob_detents = 5; });
    CHECK(o.knob_pos == 0 && std::fabs(o.steering_deg) < 1e-3f && o.steer_source == STEER_SRC_FAILSAFE);
  }
  // --- steering -> flaps law and pulse maths (unit) ---
  {
    ServoTrim tr = servoTrimDefaults(); float f[4];
    tr.flap_neutral_cdeg = 2000; tr.flap_swing_cdeg = 1500; tr.steer_lock_cdeg = 3000;
    steeringFlaps(tr, 0, f); for (float x : f) CHECK(std::fabs(x - 20.0f) < 1e-4f);
    steeringFlaps(tr, -30, f);                                          // full RIGHT
    CHECK(std::fabs(f[AERO_LF] - 35.0f) < 1e-4f && std::fabs(f[AERO_RF] - 5.0f) < 1e-4f);
    CHECK(std::fabs(f[AERO_LR] - 35.0f) < 1e-4f && std::fabs(f[AERO_RR] - 5.0f) < 1e-4f);
    steeringFlaps(tr, 15, f);                                           // half LEFT
    CHECK(std::fabs(f[AERO_RF] - 27.5f) < 1e-4f && std::fabs(f[AERO_LF] - 12.5f) < 1e-4f);
    tr.flap_swing_cdeg = 4500; steeringFlaps(tr, -30, f); CHECK(f[AERO_LF] == 45.0f && f[AERO_RF] == 0.0f);  // clamped
    tr.steer_left_us = 1100; tr.steer_center_us = 1450; tr.steer_right_us = 1900;   // asymmetric linkage
    CHECK(steerPulseUs(tr, 30) == 1100 && steerPulseUs(tr, 0) == 1450 && steerPulseUs(tr, -30) == 1900);
    CHECK(steerPulseUs(tr, 15) == 1275 && steerPulseUs(tr, -15) == 1675 && steerPulseUs(tr, -99) == 1900);
    tr.flap_flat_us[2] = 1800; tr.flap_max_us[2] = 1300;                // mirror-mounted servo: FLAT > MAX
    CHECK(flapPulseUs(tr, 2, 0) == 1800 && flapPulseUs(tr, 2, 45) == 1300 && flapPulseUs(tr, 2, 22.5f) == 1550);
    CHECK(flapPulseUs(tr, 2, -5) == 1800 && flapPulseUs(tr, 2, NAN) == 1800);
    CHECK(servoTrimValid(servoTrimDefaults()));
    ServoTrim bad = servoTrimDefaults(); bad.flap_max_us[1] = 3000; CHECK(!servoTrimValid(bad));
    ServoTrim d2 = servoTrimDefaults(); CHECK(servoTrimFingerprint(d2) == servoTrimFingerprint(servoTrimDefaults()));
    d2.steer_center_us += 1; CHECK(servoTrimFingerprint(d2) != servoTrimFingerprint(servoTrimDefaults()));
    // FW 2.3.3 default law 45/45: straight = all MAX; knob LEFT -> left pair FLAT, right pair MAX
    ServoTrim a = servoTrimDefaults();
    CHECK(a.flap_neutral_cdeg == 4500 && a.flap_swing_cdeg == 4500);
    steeringFlaps(a, 0, f); for (float x : f) CHECK(x == 45.0f);
    steeringFlaps(a, steerLockDeg(a), f);                               // full LEFT
    CHECK(f[AERO_LF] == 0.0f && f[AERO_LR] == 0.0f && f[AERO_RF] == 45.0f && f[AERO_RR] == 45.0f);
    steeringFlaps(a, -steerLockDeg(a) / 2, f);                          // half RIGHT
    CHECK(f[AERO_LF] == 45.0f && f[AERO_LR] == 45.0f && std::fabs(f[AERO_RF] - 22.5f) < 1e-3f && std::fabs(f[AERO_RR] - 22.5f) < 1e-3f);
    // FW 2.3.3 boot rule: saved pulses ALWAYS win; the law follows the file only when the file's law changed
    ServoTrim file = servoTrimDefaults(), saved = file; bool lf = true;
    saved.flap_flat_us[1] = 1720; saved.flap_max_us[1] = 1210; saved.flap_neutral_cdeg = 2000; saved.flap_swing_cdeg = 2000;
    ServoTrim b = servoTrimBoot(saved, file, servoTrimLawFingerprint(file), lf);        // file law unchanged
    CHECK(!lf && b.flap_flat_us[1] == 1720 && b.flap_neutral_cdeg == 2000);
    b = servoTrimBoot(saved, file, 0, lf);                                               // old save / file law edited
    CHECK(lf && b.flap_flat_us[1] == 1720 && b.flap_max_us[1] == 1210 && b.flap_neutral_cdeg == 4500 && b.flap_swing_cdeg == 4500);
    CHECK(!servoTrimPulsesEqual(saved, file) && servoTrimPulsesEqual(b, saved));
    ServoTrim law2 = file; law2.steer_left_us += 50; CHECK(servoTrimLawFingerprint(law2) == servoTrimLawFingerprint(file));
    law2.flap_swing_cdeg = 3000; CHECK(servoTrimLawFingerprint(law2) != servoTrimLawFingerprint(file));
  }
  // --- calibration keys: range, report, preview, revert ---
  {
    g_rear = g_dist_acc = 0; VcuController c; c.begin(HW_PROFILE_PROTOTYPE, false, true); c.boot(0);
    uint32_t t = 0; auto none = [](VcuInputs &) {};
    run(c, t, 50, none);
    CHECK(c.write(CFG_KEY_STEER_LEFT_US, 400, false, t, v) == CFG_REJ_RANGE);
    CHECK(c.write(CFG_KEY_FLAP_RL_MAX_US, 1620, false, t, v) == CFG_APPLIED && c.cfg.trim.flap_max_us[2] == 1620);
    VcuOutputs o = run(c, t, 100, none);
    CHECK(o.preview_servo == SERVO_LR && o.preview_us == 1620 && o.preview_key == CFG_KEY_FLAP_RL_MAX_US);
    o = run(c, t, VcuController::PREVIEW_MS, none); CHECK(o.preview_servo == -1);           // ends by itself
    CHECK(c.write(CFG_KEY_STEER_CENTER_US, 1480, false, t, v) == CFG_APPLIED);
    o = run(c, t, 20, none); CHECK(o.preview_servo == SERVO_STEERING && o.preview_us == 1480);
    CHECK(c.write(CFG_KEY_FLAP_NEUTRAL_CDEG, 1000, false, t, v) == CFG_APPLIED);           // not a pulse: no preview change
    CHECK(c.write(CFG_KEY_KNOB_CDEG_PER_CLICK, 20, false, t, v) == CFG_REJ_RANGE);
    CHECK(c.write(CFG_KEY_STEER_LOCK_CDEG, 2000, false, t, v) == CFG_APPLIED);
    CHECK(c.cfg.read(CFG_KEY_FLAP_RL_MAX_US, v) && v == 1620);
    CHECK(c.cfg.read(CFG_KEY_STEER_LOCK_CDEG, v) && v == 2000);
    o = run(c, t, 10, [](VcuInputs &in) { in.knob_detents = -20; }); o = run(c, t, 500, none);
    CHECK(std::fabs(o.steering_deg - 20.0f) < 0.01f);                  // new lock clamps the knob
    CHECK(c.write(CFG_KEY_CAL_CMD, CAL_CMD_SAVE, false, t, v) == CFG_APPLIED);
    o = run(c, t, 10, none); CHECK(o.events & EV_CAL_SAVE);
    CHECK(c.write(CFG_KEY_CAL_CMD, CAL_CMD_REVERT, false, t, v) == CFG_APPLIED);
    CHECK(c.cfg.trim.flap_max_us[2] == VCU_CAL_FLAP_RL_MAX_US && c.cfg.trim.steer_lock_cdeg == VCU_CAL_STEER_LOCK_DEG * 100);
    CHECK(c.write(CFG_KEY_CAL_CMD, 3, false, t, v) == CFG_REJ_RANGE);
    CHECK(c.cfg.write(CFG_KEY_CAL_CMD, 1, ConfigContext(), v) == CFG_REJ_UNKNOWN);         // commands never reach the store
  }
  // --- motor from the dashboard (key 9): any drive mode, link = deadman, live duty change ---
  {
    g_rear = g_dist_acc = 0; VcuController c; c.begin(HW_PROFILE_PROTOTYPE, false, true); c.boot(0);
    uint32_t t = 0; auto none = [](VcuInputs &) {};
    run(c, t, 100, none);
    CHECK(c.cfg.snap.drive_mode == DRIVE_MODE_TRACK);                   // works in TRACK mode too
    CHECK(c.write(CFG_KEY_MOTOR_CMD, 1001, false, t, v) == CFG_REJ_RANGE);
    CHECK(c.write(CFG_KEY_MOTOR_CMD, 400, false, t, v) == CFG_APPLIED);
    float rear = 0; VcuOutputs o = run(c, t, 1500, none, &rear);
    CHECK(o.run_state == RUN_BENCH_MOTOR && o.remote_run && std::fabs(o.motor_duty - 0.4f) < 1e-3f && rear > 1.0f);
    CHECK(std::fabs(o.manual_duty - 0.4f) < 1e-4f);
    CHECK(c.cfg.read(CFG_KEY_MOTOR_CMD, v) && v == 400);
    // steering still from the knob while the motor runs
    o = run(c, t, 10, [](VcuInputs &in) { in.knob_detents = 3; }); o = run(c, t, 300, none);
    CHECK(o.steer_source == STEER_SRC_KNOB && o.steering_deg < -8.0f && o.motor_duty > 0.3f);
    // a new value changes the duty at once (no stop / start), capped by the duty cap
    CHECK(c.write(CFG_KEY_MOTOR_CMD, 700, false, t, v) == CFG_APPLIED);
    o = run(c, t, 500, none); CHECK(std::fabs(o.motor_duty - 0.7f) < 1e-3f && o.run_state == RUN_BENCH_MOTOR);
    CHECK(c.write(CFG_KEY_MOTOR_CMD, 1000, false, t, v) == CFG_APPLIED);
    o = run(c, t, 500, none); CHECK(std::fabs(o.motor_duty - c.cfg.active.duty_cap_permille / 1000.0f) < 1e-3f);
    // no 120 s console timeout for the dashboard motor (the link is its deadman)
    o = run(c, t, 125000, none); CHECK(o.run_state == RUN_BENCH_MOTOR && o.motor_duty > 0.5f);
    // ground link lost: stops, COMM_LOSS warning, and it does NOT restart by itself
    o = run(c, t, 1500, [](VcuInputs &in) { in.hb.lora_link_ok = false; });
    CHECK(o.run_state == RUN_OFF && o.motor_duty == 0.0f && (o.fault_flags & FAULT_BIT_COMM_LOSS) && o.state == VS_IDLE);
    o = run(c, t, 500, none); CHECK(o.run_state == RUN_OFF && o.motor_duty == 0.0f);
    CHECK(c.cfg.read(CFG_KEY_MOTOR_CMD, v) && v == 0);
    // refused without the link, accepted from the console (operator at the car), which times out at 120 s
    o = run(c, t, 50, [](VcuInputs &in) { in.hb_age_ms = 5000; });
    CHECK(c.write(CFG_KEY_MOTOR_CMD, 300, false, t, v) == CFG_REJ_STATE);
    CHECK(c.write(CFG_KEY_MOTOR_CMD, 300, true, t, v) == CFG_APPLIED);
    o = run(c, t, 1000, [](VcuInputs &in) { in.hb_age_ms = 5000; }); CHECK(o.run_state == RUN_BENCH_MOTOR && !o.remote_run);
    o = run(c, t, 121000, [](VcuInputs &in) { in.hb_age_ms = 5000; });
    CHECK(o.run_state == RUN_OFF && (o.events & EV_RUN_MOTOR_TIMEOUT));
    // stop command (link back first)
    run(c, t, 50, none);
    CHECK(c.write(CFG_KEY_MOTOR_CMD, 500, false, t, v) == CFG_APPLIED); o = run(c, t, 500, none);
    CHECK(o.motor_duty > 0.4f);
    CHECK(c.write(CFG_KEY_MOTOR_CMD, 0, false, t, v) == CFG_APPLIED); o = run(c, t, 20, none);
    CHECK(o.run_state == RUN_OFF && o.motor_duty == 0.0f);
    // a real fault (motor over-temperature) stops it; it is refused until the fault clears
    CHECK(c.write(CFG_KEY_MOTOR_CMD, 500, false, t, v) == CFG_APPLIED); o = run(c, t, 300, none);
    o = run(c, t, 1200, [](VcuInputs &in) { in.motor_temp_c = 95.0f; });
    CHECK(o.state == VS_FAULT && o.run_state == RUN_OFF && o.motor_duty == 0.0f);
    CHECK(c.write(CFG_KEY_MOTOR_CMD, 500, false, t, v) == CFG_REJ_STATE);
    o = run(c, t, 1200, none); CHECK(o.state == VS_IDLE);              // clears itself 1 s after the cause
    CHECK(c.write(CFG_KEY_MOTOR_CMD, 500, false, t, v) == CFG_APPLIED);
    // E-STOP stops it and it stays stopped after the reset
    o = run(c, t, 300, none); CHECK(o.motor_duty > 0.0f);
    c.write(CFG_KEY_ESTOP, ESTOP_CMD_TRIGGER, false, t, v); o = run(c, t, 20, none);
    CHECK(o.state == VS_EMERGENCY_STOP && o.motor_duty == 0.0f && o.run_state == RUN_OFF);
    CHECK(c.write(CFG_KEY_MOTOR_CMD, 500, false, t, v) == CFG_REJ_STATE);
    c.write(CFG_KEY_ESTOP, ESTOP_CMD_RESET, false, t, v); o = run(c, t, 100, none);
    CHECK(o.state == VS_IDLE && o.motor_duty == 0.0f);
  }
  // --- FW 2.3.3: "straight = all MAX, inside pair to FLAT" law, and DRS ---
  {
    g_rear = g_dist_acc = 0; VcuController c; c.begin(HW_PROFILE_PROTOTYPE, false, true); c.boot(0);
    uint32_t t = 0; auto none = [](VcuInputs &) {};
    CHECK(c.write(CFG_KEY_FLAP_NEUTRAL_CDEG, 4500, false, t, v) == CFG_APPLIED);
    CHECK(c.write(CFG_KEY_FLAP_SWING_CDEG, 4500, false, t, v) == CFG_APPLIED);
    CHECK(c.write(CFG_KEY_FLAP_SWING_CDEG, 9001, false, t, v) == CFG_REJ_RANGE);
    VcuOutputs o = run(c, t, 1000, none);
    for (int i = 0; i < 4; ++i) CHECK(std::fabs(o.flaps[i] - 45.0f) < 0.05f);          // straight: all MAX
    o = run(c, t, 10, [](VcuInputs &in) { in.knob_detents = -10; });                    // full LEFT
    o = run(c, t, 1000, none);
    CHECK(o.steering_deg > 29.0f);
    CHECK(o.flaps[AERO_LF] < 0.05f && o.flaps[AERO_LR] < 0.05f);                        // left (inside) FLAT
    CHECK(o.flaps[AERO_RF] > 44.9f && o.flaps[AERO_RR] > 44.9f);                        // right (outside) MAX
    o = run(c, t, 10, [](VcuInputs &in) { in.knob_detents = 15; });                     // to 5 clicks RIGHT
    o = run(c, t, 1000, none);
    CHECK(o.flaps[AERO_LF] > 44.9f && std::fabs(o.flaps[AERO_RF] - 22.5f) < 0.05f);     // half right: right flaps half
    // swing 90: inside pair already FLAT at half lock
    CHECK(c.write(CFG_KEY_FLAP_SWING_CDEG, 9000, false, t, v) == CFG_APPLIED);
    o = run(c, t, 1000, none); CHECK(o.flaps[AERO_RF] < 0.05f && o.flaps[AERO_LF] > 44.9f);
    CHECK(c.cfg.read(CFG_KEY_DRS_CMD, v) && v == 0);
    // DRS: all FLAT for 5 s whatever the steering, then all MAX for 1 s, then the law
    CHECK(c.write(CFG_KEY_DRS_CMD, 2, false, t, v) == CFG_REJ_RANGE);
    CHECK(c.write(CFG_KEY_DRS_CMD, 1, false, t, v) == CFG_APPLIED);
    o = run(c, t, 600, none);
    CHECK(o.drs_phase == 1 && o.aero_straight && (o.events & EV_DRS_OPEN));
    for (int i = 0; i < 4; ++i) CHECK(o.flaps[i] < 0.05f);
    CHECK(c.cfg.read(CFG_KEY_DRS_CMD, v) && v == 1);
    o = run(c, t, 2000, [](VcuInputs &in) { in.knob_detents = (in.now_ms % 200 == 0) ? 1 : 0; });   // steering during DRS
    CHECK(o.drs_phase == 1); for (int i = 0; i < 4; ++i) CHECK(o.flaps[i] < 0.05f);
    o = run(c, t, 2600, none);                                                           // t = 5.2 s
    CHECK(o.drs_phase == 2 && !o.aero_straight && (o.events & EV_DRS_HOLD));
    o = run(c, t, 500, none);                                                            // t = 5.7 s (flaps slew 120 deg/s)
    CHECK(o.drs_phase == 2); for (int i = 0; i < 4; ++i) CHECK(o.flaps[i] > 44.9f);
    o = run(c, t, 500, none);                                                            // t = 6.2 s: the law again
    CHECK(o.drs_phase == 0 && (o.events & EV_DRS_END));
    o = run(c, t, 500, none);
    CHECK(o.flaps[AERO_RF] < 0.05f || o.flaps[AERO_LF] < 0.05f);                         // still turned: inside FLAT
    // pressing again restarts; 0 cancels at once
    c.write(CFG_KEY_DRS_CMD, 1, false, t, v); o = run(c, t, 300, none); CHECK(o.drs_phase == 1);
    c.write(CFG_KEY_DRS_CMD, 0, false, t, v); o = run(c, t, 20, none); CHECK(o.drs_phase == 0);
    // FW 2.3.4: wheel speed estimated from duty
    {
      DutySpeedModel m; CHECK(std::fabs(DutySpeedModel::vmaxKmh() - 16.96f) < 0.05f);
      for (int i = 0; i < 400; ++i) m.update(0.5f, 0.01f);
      CHECK(std::fabs(m.v_kmh - 0.5f * DutySpeedModel::vmaxKmh()) < 0.05f);
      for (int i = 0; i < 600; ++i) m.update(0.15f, 0.01f);                              // below the 20 % dead band
      CHECK(m.v_kmh == 0.0f);
    }
    // FW 2.3.4: demo sequence script
    {
      CHECK(demoSequence(0, 0.22f, 0.6f).duty == 0.22f && demoSequence(3000, 0.22f, 0.6f).duty > 0.4f);
      CHECK(demoSequence(6000, 0.22f, 0.6f).steer_frac > 0.5f && demoSequence(8000, 0.22f, 0.6f).steer_frac < -0.5f);
      CHECK(demoSequence(10000, 0.22f, 0.6f).steer_frac == 0.0f);
      CHECK(demoSequence(14000, 0.22f, 0.6f).drs && demoSequence(14000, 0.22f, 0.6f).duty == 0.6f);
      CHECK(!demoSequence(20999, 0.22f, 0.6f).done && demoSequence(21000, 0.22f, 0.6f).done);
      for (uint32_t k = 0; k <= 21000; k += 50) CHECK(demoSequence(k, 0.22f, 0.6f).duty >= 0.22f);   // never in the dead band
    }
    // ... and the VCU running it: motor, steering, the flap law and the normal DRS timer
    CHECK(c.write(CFG_KEY_FLAP_SWING_CDEG, 4500, false, t, v) == CFG_APPLIED);
    c.set_speed_from_duty(true);
    CHECK(c.cfg.read(CFG_KEY_RO_SPEED_SOURCE, v) && v == 1);
    CHECK(c.write(CFG_KEY_DEMO_CMD, 3, true, t, v) == CFG_REJ_RANGE);
    CHECK(c.write(CFG_KEY_DEMO_CMD, 1, true, t, v) == CFG_APPLIED);
    o = run(c, t, 3000, none);                                                           // 3 s: cruising straight
    CHECK(o.run_state == RUN_DEMO && o.motor_duty > 0.2f && o.model_speed_kmh > 3.0f && std::fabs(o.steering_deg) < 0.5f);
    CHECK(c.cfg.read(CFG_KEY_DEMO_CMD, v) && v == 1);
    CHECK(c.write(CFG_KEY_MOTOR_CMD, 500, true, t, v) == CFG_REJ_STATE);                 // no duty change mid-demo
    o = run(c, t, 3500, none);                                                           // 6.5 s: LEFT
    CHECK(o.steering_deg > 20.0f && o.flaps[AERO_LF] < 15.0f && o.flaps[AERO_RF] > 44.9f && o.motor_duty > 0.2f);
    o = run(c, t, 2000, none);                                                           // 8.5 s: RIGHT
    CHECK(o.steering_deg < -20.0f && o.flaps[AERO_RF] < 15.0f && o.flaps[AERO_LF] > 44.9f);
    o = run(c, t, 6000, none);                                                           // 14.5 s: top speed + DRS
    CHECK(o.drs_phase == 1 && std::fabs(o.steering_deg) < 0.5f && o.motor_duty > 0.4f);
    for (int i = 0; i < 4; ++i) CHECK(o.flaps[i] < 0.05f);
    o = run(c, t, 4200, none);                                                           // 18.7 s: DRS hold, all MAX
    CHECK(o.drs_phase == 2 && o.run_state == RUN_DEMO);
    o = run(c, t, 3000, none);                                                           // 21.7 s: finished
    CHECK(o.run_state == RUN_OFF && o.motor_duty == 0.0f && o.drs_phase == 0);
    CHECK(c.cfg.read(CFG_KEY_DEMO_CMD, v) && v == 0);
    CHECK(c.write(CFG_KEY_DEMO_CMD, 1, true, t, v) == CFG_APPLIED); o = run(c, t, 500, none);
    CHECK(c.write(CFG_KEY_DEMO_CMD, 0, true, t, v) == CFG_APPLIED); o = run(c, t, 50, none); CHECK(o.run_state == RUN_OFF);
    // FW 2.3.8 demo 2: one lap of Monza scaled to under 40 s, drive mode restored afterwards
    {
      const uint8_t mode0 = c.cfg.snap.drive_mode;
      CHECK(c.write(CFG_KEY_DEMO_CMD, 2, true, t, v) == CFG_APPLIED);
      CHECK(c.cfg.snap.track_id == TRACK_DEFAULT_ID && c.cfg.snap.drive_mode == DRIVE_MODE_DYNO);
      o = run(c, t, 1000, none);
      CHECK(o.run_state == RUN_BENCH_LAPS && o.track_frac > 0.005f && o.track_frac < 0.2f && o.motor_duty > 0.2f);
      CHECK(c.cfg.read(CFG_KEY_DEMO_CMD, v) && v == 2);
      // FW 2.3.9: full lock both ways in the chicanes, seconds at half lock in the long corners,
      // duty never above the demo-lap top and never in the dead band, clear slow-down
      const float lk = steerLockDeg(c.cfg.trim);
      float lap_s = 1.0f, half_s = 0, smax = 0, smin = 0, dmax = 0, dmin = 1, vmax = 0, vmin = 99; bool full_l = false, full_r = false;
      while (lap_s < 60.0f) {
        o = run(c, t, 50, none); lap_s += 0.05f; if (o.run_state == RUN_OFF) break;
        if (o.steering_deg > 0.95f * lk) full_l = true;
        if (o.steering_deg < -0.95f * lk) full_r = true;
        if (std::fabs(std::fabs(o.steering_deg) - 0.5f * lk) < 0.08f * lk) half_s += 0.05f;
        smax = std::max(smax, o.steering_deg); smin = std::min(smin, o.steering_deg);
        dmax = std::max(dmax, o.motor_duty); dmin = std::min(dmin, o.motor_duty);
        vmax = std::max(vmax, o.model_speed_kmh); vmin = std::min(vmin, o.model_speed_kmh);
      }
      std::printf("demo 2 lap %.2f s, %.1f m, half-lock %.1f s, duty %.2f..%.2f, speed %.1f..%.1f km/h, steer %.1f..%.1f\n", lap_s,
                  c.cfg.snap.lap_length_cm / 100.0f, half_s, dmin, dmax, vmin, vmax, smin, smax);
      CHECK(lap_s > 25.0f && lap_s < 40.0f && full_l && full_r && half_s > 3.0f);
      CHECK(dmax <= 0.80f + 1e-3f && dmin >= 0.2f && vmax < 14.0f && vmin < 0.5f * vmax);
      o = run(c, t, 100, none);
      CHECK(o.run_state == RUN_OFF && o.motor_duty == 0.0f && c.cfg.snap.drive_mode == mode0);
      CHECK(c.cfg.read(CFG_KEY_DEMO_CMD, v) && v == 0);
      // FW 2.3.10: the same demo lap on EVERY circuit (key 26 = 100 + id): under 40 s, full lock somewhere
      CHECK(c.write(CFG_KEY_DEMO_CMD, 100 + TRACK_COUNT, true, t, v) == CFG_REJ_RANGE);
      for (int id = 0; id < TRACK_COUNT; ++id) {
        CHECK(c.write(CFG_KEY_DEMO_CMD, 100 + id, true, t, v) == CFG_APPLIED);
        CHECK(c.cfg.snap.track_id == id);
        float ls = 0, sm = 0, half = 0; const float lk2 = steerLockDeg(c.cfg.trim);
        while (ls < 60.0f) {
          o = run(c, t, 50, none); ls += 0.05f; if (o.run_state == RUN_OFF) break;
          sm = std::max(sm, std::fabs(o.steering_deg));
          if (std::fabs(o.steering_deg) > 0.4f * lk2 && std::fabs(o.steering_deg) < 0.9f * lk2) half += 0.05f;
        }
        std::printf("  demo lap %2d %-18s %.1f s  %.0f m  max steer %.1f  partial-lock %.1f s\n", id, TRACKS[id].name, ls,
                    c.cfg.snap.lap_length_cm / 100.0f, sm, half);
        CHECK(ls > 25.0f && ls < 40.0f && sm > 0.95f * lk2);
        o = run(c, t, 100, none); CHECK(o.run_state == RUN_OFF && c.cfg.snap.drive_mode == mode0);
      }
      CHECK(c.write(CFG_KEY_DEMO_CMD, 2, true, t, v) == CFG_APPLIED); o = run(c, t, 1000, none);
      CHECK(c.write(CFG_KEY_DEMO_CMD, 0, true, t, v) == CFG_APPLIED); o = run(c, t, 100, none);
      CHECK(o.run_state == RUN_OFF && c.cfg.snap.drive_mode == mode0);
    }
    c.set_speed_from_duty(false);
    // works in FAULT (flaps still live), refused in E-STOP, cancelled by an E-STOP
    o = run(c, t, 1500, [](VcuInputs &in) { in.motor_temp_c = 95.0f; }); CHECK(o.state == VS_FAULT);
    CHECK(c.write(CFG_KEY_DRS_CMD, 1, false, t, v) == CFG_APPLIED);
    o = run(c, t, 600, [](VcuInputs &in) { in.motor_temp_c = 95.0f; });
    CHECK(o.drs_phase == 1 && o.flaps[0] < 0.05f);
    c.write(CFG_KEY_ESTOP, ESTOP_CMD_TRIGGER, false, t, v); o = run(c, t, 20, none);
    CHECK(o.state == VS_EMERGENCY_STOP && o.drs_phase == 0);
    CHECK(c.write(CFG_KEY_DRS_CMD, 1, false, t, v) == CFG_REJ_STATE);
  }
  // --- only real faults stop the car ---
  {
    // a failed self-test item is a WARNING: the car is IDLE and the motor may run
    g_rear = g_dist_acc = 0; VcuController c; c.begin(HW_PROFILE_PROTOTYPE, false, true); c.boot(0x08 /* DS18B20 */);
    uint32_t t = 0; auto none = [](VcuInputs &) {};
    VcuOutputs o = run(c, t, 100, none);
    CHECK(o.state == VS_IDLE && (o.fault_flags & FAULT_BIT_SELF_TEST_FAILED) && o.worst == SEV_WARNING && o.permits_drive);
    CHECK(c.write(CFG_KEY_MOTOR_CMD, 300, false, t, v) == CFG_APPLIED);
    // IMU / INA260 timeouts are warnings: the motor keeps running
    o = run(c, t, 600, [](VcuInputs &in) { in.imu_age_ms = 5000; in.power_age_ms = 5000; });
    CHECK(o.state == VS_IDLE && (o.fault_flags & FAULT_BIT_SENSOR_TIMEOUT) && o.motor_duty > 0.2f);
    // FW 2.3.1: a stalled task (software watchdog) is a WARNING — the ESP32 task watchdog handles real hangs
    o = run(c, t, 300, [](VcuInputs &in) { in.watchdog_ok = false; });
    CHECK(o.state == VS_IDLE && (o.fault_flags & FAULT_BIT_WATCHDOG_TIMEOUT) && o.worst == SEV_WARNING && o.motor_duty > 0.2f);
    CHECK(c.write(CFG_KEY_MOTOR_CMD, 0, false, t, v) == CFG_APPLIED);
    // over-current is a FAULT (motor off), not an E-STOP
    o = run(c, t, 300, [](VcuInputs &in) { in.battery_a = 20.0f; });
    CHECK(o.state == VS_FAULT && (o.fault_flags & FAULT_BIT_OVERCURRENT));
    o = run(c, t, 1200, none); CHECK(o.state == VS_IDLE);
  }
}

// ---------------------------------------------------------------- selftest
static void selftest() {
  // --- state machine: forbidden shortcuts, exhaustively over all events ---
  for (int e = 0; e <= VE_SHUTDOWN_REQUEST; ++e) {
    VehicleState n = stateTransition(VS_FAULT, static_cast<VehicleEvent>(e));
    CHECK(n != VS_ARMED && n != VS_DRIVE);
    n = stateTransition(VS_EMERGENCY_STOP, static_cast<VehicleEvent>(e));
    CHECK(n != VS_IDLE && n != VS_ARMED && n != VS_DRIVE);
    for (int s = 0; s <= VS_SHUTDOWN; ++s) {
      VehicleState from = static_cast<VehicleState>(s);
      VehicleState to = stateTransition(from, static_cast<VehicleEvent>(e));
      if (to == VS_DRIVE) CHECK(from == VS_ARMED || from == VS_DRIVE);  // DRIVE only via ARMED
      if (from == VS_SHUTDOWN) CHECK(to == VS_SHUTDOWN);
    }
  }
  CHECK(stateTransition(VS_DRIVE, VE_ESTOP_REQUEST) == VS_EMERGENCY_STOP);
  CHECK(stateTransition(VS_EMERGENCY_STOP, VE_ESTOP_RESET_REQUEST) == VS_INIT);
  CHECK(stateTransition(VS_FAULT, VE_FAULT_CLEARED) == VS_IDLE);

  // --- supervisor: arm needs fresh driver link + neutral throttle held ---
  {
    Supervisor sup; sup.force(VS_IDLE);
    SupervisorInputs in; in.driver_fresh = true; in.arm_requested = true; in.throttle_permille = 600;
    in.now_ms = 0; sup.step(in); CHECK(sup.state() == VS_IDLE);          // not neutral -> refuse
    in.throttle_permille = 0; sup.step(in); CHECK(sup.state() == VS_ARMED);
    in.now_ms = 100; sup.step(in); CHECK(sup.state() == VS_ARMED);       // neutral hold not yet met
    in.now_ms = 400; sup.step(in); CHECK(sup.state() == VS_DRIVE);
    in.driver_fresh = false; in.now_ms = 500; sup.step(in); CHECK(sup.state() == VS_DRIVE);  // safety handles comm loss
    in.arm_requested = false; sup.step(in); CHECK(sup.state() == VS_IDLE);
    // e-stop latches; reset only with ARM released
    in.arm_requested = true; in.estop_requested = true; sup.step(in); CHECK(sup.state() == VS_EMERGENCY_STOP);
    in.estop_requested = false; in.estop_reset_requested = true; sup.step(in); CHECK(sup.state() == VS_EMERGENCY_STOP);
    in.arm_requested = false; sup.step(in); CHECK(sup.state() == VS_INIT);
  }
  // fault clears only after hold time, never to ARMED
  {
    Supervisor sup; sup.force(VS_DRIVE);
    SupervisorInputs in; in.safety.worst = SEV_FAULT; in.now_ms = 0; sup.step(in); CHECK(sup.state() == VS_FAULT);
    in.safety.worst = SEV_NONE; in.now_ms = 10; sup.step(in); CHECK(sup.state() == VS_FAULT);
    in.now_ms = 10 + CFG_FAULT_CLEAR_HOLD_MS; sup.step(in); CHECK(sup.state() == VS_IDLE);
  }

  // --- safety manager ---
  {
    SafetyManager sm; SafetyInputs in; in.state = VS_DRIVE; in.battery_current_a = 40.0f;
    in.now_ms = 0; CHECK((sm.evaluate(in).fault_flags & FAULT_BIT_OVERCURRENT) == 0);  // spike ignored
    in.now_ms = CFG_OVERCURRENT_PERSIST_MS; SafetyResult r = sm.evaluate(in);
    CHECK((r.fault_flags & FAULT_BIT_OVERCURRENT) != 0); CHECK(r.worst == SEV_FAULT); CHECK(!r.permits_drive);
  }
  {
    SafetyManager sm; SafetyInputs in; in.state = VS_DRIVE; in.driver_link_ok = false;
    SafetyResult r = sm.evaluate(in);
    CHECK((r.fault_flags & FAULT_BIT_COMM_LOSS) != 0); CHECK(r.worst == SEV_FAULT); CHECK(!r.aero_failsafe);
    in.state = VS_IDLE; r = sm.evaluate(in); CHECK((r.fault_flags & FAULT_BIT_COMM_LOSS) == 0);
    in.aero_pos_error = true; r = sm.evaluate(in); CHECK(!r.aero_failsafe); CHECK(r.permits_drive);  // warning only
    in.aero_pos_error = false; in.battery_voltage_v = 0.1f; r = sm.evaluate(in);  // disconnected divider
    CHECK((r.fault_flags & FAULT_BIT_INVALID_SENSOR) != 0); CHECK((r.fault_flags & FAULT_BIT_BATTERY_UNDERVOLT) == 0);
    CHECK(r.worst == SEV_WARNING && r.permits_drive);             // FW 2.3: implausible reading = warning
  }
  // 3S LiPo under 9.9 V for >= 2 s = FAULT (FW 2.3; was critical)
  {
    SafetyManager sm; SafetyInputs in; in.state = VS_DRIVE; in.battery_voltage_v = 9.5f;
    in.now_ms = 1000; sm.evaluate(in); in.now_ms = 2999; CHECK((sm.evaluate(in).fault_flags & FAULT_BIT_BATTERY_UNDERVOLT) == 0);
    in.now_ms = 3000; CHECK((sm.evaluate(in).fault_flags & FAULT_BIT_BATTERY_UNDERVOLT) != 0);
  }

  // --- active aero: failsafe immediate, rate limit otherwise ---
  {
    ActiveAeroController aero(0.0f); AeroInputs in; in.speed_kmh = 50; in.state = VS_DRIVE; in.dt_s = 0.01f;
    AeroOutput o = aero.compute(in, nullptr, 0);
    CHECK(std::fabs(o.desired[0] - 42.0f) < 1e-4f); CHECK(std::fabs(o.target[0] - 1.2f) < 1e-3f);  // 120 dps * 10 ms
    in.state = VS_FAULT; o = aero.compute(in, nullptr, 10); CHECK(o.target[0] > CFG_WING_FAILSAFE_ANGLE_DEG);  // FW 2.3
    in.state = VS_EMERGENCY_STOP; o = aero.compute(in, nullptr, 20); CHECK(o.target[0] == CFG_WING_FAILSAFE_ANGLE_DEG);
  }
  {  // asymmetric: +lat_g raises right flaps, lowers left, capped
    float out[4]; aeroBaseline(30.0f, 0.5f, CFG_AERO_LATERAL_G_GAIN_DEG, out);
    CHECK(out[AERO_RF] > out[AERO_LF]); CHECK(std::fabs((out[AERO_RF] - out[AERO_LF]) - 8.0f) < 1e-4f);
    aeroBaseline(30.0f, 5.0f, CFG_AERO_LATERAL_G_GAIN_DEG, out);
    CHECK(std::fabs((out[AERO_RF] - out[AERO_LF]) - 2 * CFG_AERO_LATERAL_MAX_DEG) < 1e-4f);
  }

  // --- AI pair assembler ---
  {
    AiRecAssembler as; CanFrame a, b; AiWingRecommendation r;
    canEncodeAiRecommendation(5, 10, 11, 12, 13, 0.9f, a, b);
    CHECK(!as.feed(a, 100, r)); CHECK(as.feed(b, 150, r)); CHECK(r.valid && r.sequence == 5 && r.timestamp_ms == 150);
    CHECK(std::fabs(r.rr - 13.0f) < 1e-3f);
    CHECK(!as.feed(a, 200, r)); b.data[0] = 6; CHECK(!as.feed(b, 210, r)); CHECK(as.rejected_pairs == 1);
    canEncodeAiRecommendation(7, 10, 11, 12, 13, 0.9f, a, b);
    CHECK(!as.feed(a, 300, r)); CHECK(!as.feed(b, 300 + CFG_AI_PAIR_WINDOW_MS + 1, r));  // too late
  }

  // --- motor / BTS7960 ---
  {
    HBridgeOut h = hbridgeMap(0.5f, true); CHECK(!h.lpwm_high && std::fabs(h.rpwm_duty - 0.5f) < 1e-6f);
    h = hbridgeMap(-0.25f, true); CHECK(h.lpwm_high && std::fabs(h.rpwm_duty - 0.75f) < 1e-6f);
    h = hbridgeMap(NAN, true); CHECK(!h.enable);
    MotorController m;
    float d = m.update(1000, true, 0, 12.0f, 0, 0.1f); CHECK(std::fabs(d - CFG_MOTOR_RAMP_PER_S * 0.1f) < 1e-4f);
    for (int i = 0; i < 50; ++i) d = m.update(1000, true, 20, 12.0f, 0, 0.1f);
    CHECK(std::fabs(d - CFG_MOTOR_MAX_DUTY) < 1e-4f);
    d = m.update(-800, true, 20, 12.0f, 0, 0.01f); CHECK(d == 0.0f && m.braking());   // brake while rolling
    d = m.update(1000, false, 0, 12.0f, 0, 0.1f); CHECK(d == 0.0f);                    // not allowed -> 0
    d = m.update(30, true, 0, 12.0f, 0, 0.1f); CHECK(d == 0.0f);                       // inside neutral band
    for (int i = 0; i < 50; ++i) d = m.update(1000, true, 20, 3.2f * 3, 0, 0.1f);    // undervolt derate
    CHECK(d < CFG_MOTOR_MAX_DUTY * 0.31f);
  }

  // --- servo maths ---
  {
    ServoCal c{0.0f, 45.0f, 1000, 2000, 400, 3600, true};
    CHECK(servoAngleToPulse(c, 0) == 1000); CHECK(servoAngleToPulse(c, 45) == 2000);
    CHECK(servoAngleToPulse(c, 22.5f) == 1500); CHECK(servoAngleToPulse(c, 99) == 2000);
    CHECK(std::fabs(servoFeedbackToAngle(c, 2000) - 22.5f) < 1e-3f);
    c.fb_calibrated = false; CHECK(std::isnan(servoFeedbackToAngle(c, 2000)));
    PositionMonitor pm; CHECK(!pm.update(10, 30, 0)); CHECK(pm.update(10, 30, CFG_SERVO_POS_ERR_PERSIST_MS));
    PositionMonitor pm2; CHECK(!pm2.update(10, NAN, 0)); CHECK(!pm2.update(10, NAN, 10000));  // uncalibrated never faults
  }

  // --- driver command freshness + decode range check ---
  {
    CanFrame f; f.id = CAN_ID_DRIVER_COMMAND; f.dlc = 6; f.data[0] = 1; putI16(&f.data[1], 1500);
    DriverCommand c; CHECK(!canDecodeDriverCommand(f, 0, c));
    putI16(&f.data[1], -200); putI16(&f.data[3], 300); f.data[5] = DRV_FLAG_ARM;
    CHECK(canDecodeDriverCommand(f, 1000, c)); CHECK(driverCommandFresh(c, 1000 + CFG_DRIVER_CMD_TIMEOUT_MS));
    CHECK(!driverCommandFresh(c, 1001 + CFG_DRIVER_CMD_TIMEOUT_MS));
  }
  // --- derived metrics: slip ratio ---
  {
    CHECK(slipRatioPermille(20, 20, 20, 20) == 0);            // no slip
    CHECK(slipRatioPermille(0, 0, 0, 0) == 0);                // stationary: not meaningful
    CHECK(slipRatioPermille(20, 20, 22, 22) == 100);          // rear 10 % faster = wheelspin
    CHECK(slipRatioPermille(20, 20, 18, 18) == -100);         // rear slower = lock-up
    CHECK(slipRatioPermille(1, 1, 1.5f, 1.5f) == 0);          // below the speed floor
    CHECK(slipRatioPermille(NAN, 20, 20, 20) == 0);
    CHECK(slipRatioPermille(10, 10, 100, 100) == 2000);       // clamped at +200 %
  }
  // --- derived metrics: thermal time-to-limit ---
  {
    ThermalEstimator te;
    CHECK(te.update(40.0f, 80.0f, 0) == THERMAL_NO_LIMIT);    // first sample: no trend yet
    uint16_t last = THERMAL_NO_LIMIT;
    for (uint32_t t = 1000; t <= 200000; t += 1000)           // +0.1 C/s toward an 80 C limit
      last = te.update(40.0f + t / 10000.0f, 80.0f, t);
    CHECK(te.rate_c_per_s() > 0.05f && te.rate_c_per_s() < 0.15f);
    CHECK(last != THERMAL_NO_LIMIT && last < 400);            // roughly (80 - T) / 0.1
    ThermalEstimator steady;
    for (uint32_t t = 0; t <= 60000; t += 1000) steady.update(30.0f, 80.0f, t);
    CHECK(steady.update(30.0f, 80.0f, 61000) == THERMAL_NO_LIMIT);  // not warming
    ThermalEstimator over;
    over.update(95.0f, 80.0f, 0);
    CHECK(over.update(95.0f, 80.0f, 1000) == 0);              // already past the limit
  }

  // --- wheel speed ---
  CHECK(std::fabs(wheelSpeedKmhFromPeriod(100000, 0.064f, 1) - 0.064f * 3.14159265f * 10 * 3.6f) < 1e-3f);
  CHECK(wheelSpeedKmhFromPeriod(0, 0.064f, 1) == 0.0f);

  // ======================= Stage 2d =======================
  // --- L298N forward-only motor ---
  {
    MotorController m(false);
    float d = 0;
    for (int i = 0; i < 50; ++i) d = m.update(1000, true, 5, 12.0f, 0, 0.1f);
    CHECK(std::fabs(d - CFG_MOTOR_MAX_DUTY) < 1e-4f);
    d = m.update(-800, true, 5, 12.0f, 0, 0.01f); CHECK(d == 0.0f);          // no reverse, no brake: coast
    d = m.update(-800, true, 0, 12.0f, 0, 0.01f); CHECK(d == 0.0f);          // stationary: still no reverse
    m.set_limits(0.5f, 2.0f);
    for (int i = 0; i < 50; ++i) d = m.update(1000, true, 5, 12.0f, 0, 0.1f);
    CHECK(std::fabs(d - 0.5f) < 1e-4f);
    for (int i = 0; i < 50; ++i) d = m.update(1000, true, 5, 12.0f, 4.0f, 0.1f);  // 2x over the 2 A limit
    CHECK(std::fabs(d - 0.25f) < 1e-3f); CHECK(m.derating());
    CHECK(l298nDuty(-0.3f, true) == 0.0f); CHECK(l298nDuty(0.4f, false) == 0.0f);
    CHECK(std::fabs(l298nDuty(0.4f, true) - 0.4f) < 1e-6f); CHECK(l298nDuty(NAN, true) == 0.0f);
  }
  // --- aero: map baseline override keeps the AI blend limit and the rate limit ---
  {
    ActiveAeroController aero(0.0f); AeroInputs in; in.state = VS_IDLE; in.dt_s = 1.0f; in.speed_kmh = 0;
    in.baseline_override = true; in.baseline[0] = 30; in.baseline[1] = 30; in.baseline[2] = 5; in.baseline[3] = 5;
    AeroOutput o = aero.compute(in, nullptr, 0);
    CHECK(o.desired[0] == 30.0f && o.desired[2] == 5.0f);      // speed below 8 km/h does NOT force failsafe
    CHECK(std::fabs(o.target[0] - 12.0f) < 1e-3f);             // 120 dps * dt clamp 0.1 s
    aero.set_rate_dps(40.0f); o = aero.compute(in, nullptr, 0);
    CHECK(std::fabs(o.target[0] - 16.0f) < 1e-3f);
    AiWingRecommendation r; r.valid = true; r.confidence = 0.9f; r.timestamp_ms = 100;
    r.lf = 0; r.rf = 45; r.lr = 45; r.rr = 45; in.mode = AERO_MODE_AI_ASSIST;
    o = aero.compute(in, &r, 150);
    CHECK(o.ai_applied); CHECK(std::fabs(o.desired[0] - 10.0f) < 1e-4f); CHECK(std::fabs(o.desired[1] - 45.0f) < 1e-4f);
    CHECK(std::fabs(o.desired[2] - 25.0f) < 1e-4f);            // +20 deg max nudge
    in.force_failsafe = true; o = aero.compute(in, &r, 150); CHECK(o.target[0] == CFG_WING_FAILSAFE_ANGLE_DEG);
  }
  // --- config store ---
  {
    ConfigStore cs; ConfigContext idle, run, drive; run.lap_running = true; drive.in_drive = true; drive.lap_running = true;
    int32_t v;
    CHECK(cs.write(CFG_KEY_CORNER_FRONT_CDEG, 3300, idle, v) == CFG_APPLIED); CHECK(cs.active.corner_front_cdeg == 3300);
    CHECK(cs.write(CFG_KEY_CORNER_FRONT_CDEG, 4600, idle, v) == CFG_REJ_RANGE); CHECK(cs.active.corner_front_cdeg == 3300);
    CHECK(cs.write(CFG_KEY_CORNER_FRONT_CDEG, 2500, run, v) == CFG_STAGED);
    CHECK(cs.active.corner_front_cdeg == 3300 && cs.pending);
    CHECK(cs.write(CFG_KEY_STRAIGHT_FRONT_CDEG, 800, run, v) == CFG_STAGED);  // second staged change accumulates
    CHECK(cs.commitAtLapBoundary()); CHECK(!cs.pending);
    CHECK(cs.active.corner_front_cdeg == 2500 && cs.active.straight_front_cdeg == 800);
    CHECK(!cs.commitAtLapBoundary());
    CHECK(cs.write(CFG_KEY_TRACK_ID, 3, run, v) == CFG_REJ_STATE);
    CHECK(cs.write(CFG_KEY_TRACK_ID, TRACK_COUNT, idle, v) == CFG_REJ_RANGE);
    CHECK(cs.write(CFG_KEY_TRACK_ID, 3, idle, v) == CFG_APPLIED && cs.snap.track_id == 3);
    CHECK(cs.write(CFG_KEY_AERO_MODE, 0, drive, v) == CFG_REJ_STATE);
    CHECK(cs.write(CFG_KEY_TRACK_RUN, 1, drive, v) == CFG_REJ_STATE);
    CHECK(cs.write(CFG_KEY_TRACK_RUN, 0, drive, v) == CFG_APPLIED);            // disabling is always allowed
    CHECK(cs.write(CFG_KEY_DUTY_FLOOR_PERMILLE, 900, idle, v) == CFG_REJ_RANGE);  // > 800
    CHECK(cs.write(CFG_KEY_DUTY_FLOOR_PERMILLE, 700, idle, v) == CFG_APPLIED);
    CHECK(cs.write(CFG_KEY_DUTY_CAP_PERMILLE, 600, idle, v) == CFG_REJ_RANGE);    // cap must stay above floor
    CHECK(cs.write(CFG_KEY_CURRENT_LIMIT_CA, 150, drive, v) == CFG_APPLIED);      // protection: immediate
    CHECK(cs.active.current_limit_ca == 150);
    CHECK(cs.write(77, 1, idle, v) == CFG_REJ_UNKNOWN);
    CHECK(cs.read(CFG_KEY_RO_TRACK_COUNT, v) && v == TRACK_COUNT);
    for (uint8_t i = 0; i < CONFIG_REPORT_KEY_COUNT; ++i) CHECK(cs.read(CONFIG_REPORT_KEYS[i], v));
  }
  // --- track runner ---
  {
    CHECK(TRACK_COUNT >= 20); CHECK(TRACK_DEFAULT_ID < TRACK_COUNT);
    CHECK(std::string(TRACKS[TRACK_DEFAULT_ID].code) == "ITA");
    TrackRunner tr; Tunables tun;
    tr.start(TRACK_DEFAULT_ID, 60.0f, 1000);
    CHECK(tr.fraction() == 0.0f);
    TrackSample smp; TrackCommand c = tr.command(tun, 1000, &smp);
    CHECK(smp.straight);                                       // Monza: start/finish is on the main straight
    CHECK(c.straight_front);
    CHECK(std::fabs(c.flaps[AERO_LF] - 5.0f) < 1e-3f);
    CHECK(c.duty > 0.35f && c.duty <= 1.0f + 1e-6f);
    for (int i = 0; i < 100; ++i) tr.advance(0.5f, 1000 + i * 100);   // 50 m
    CHECK(tr.lap == 0); CHECK(std::fabs(tr.lap_dist_m - 50.0f) < 1e-3f);
    tr.advance(10.5f, 20000); CHECK(tr.lap == 1); CHECK(tr.lap_completed_event);
    CHECK(tr.last_lap_ms == 19000); CHECK(tr.best_lap_ms == 19000); CHECK(std::fabs(tr.lap_dist_m - 0.5f) < 1e-3f);
    tr.advance(0.1f, 20010); CHECK(!tr.lap_completed_event);
    CHECK(tr.odometer_m > 60.55 && tr.odometer_m < 60.65);
    tr.stop(); float before = tr.lap_dist_m; tr.advance(5.0f, 21000); CHECK(tr.lap_dist_m == before);  // odometer only
    // somewhere on the lap Monza must steer both ways, slow down, and enter corner mode
    tr.start(TRACK_DEFAULT_ID, 100.0f, 0);
    float min_st = 0, max_st = 0, min_duty = 1; bool corner = false;
    for (int i = 0; i < 1000; ++i) {
      tr.advance(0.1f, i * 10); TrackCommand k = tr.command(tun, i * 10);
      min_st = std::min(min_st, k.steer_deg); max_st = std::max(max_st, k.steer_deg);
      min_duty = std::min(min_duty, k.duty); corner |= !k.straight_front;
      CHECK(k.duty >= tun.duty_floor_permille / 1000.0f - 1e-4f);
      CHECK(k.steer_deg >= -CFG_STEERING_MAX_DEG && k.steer_deg <= CFG_STEERING_MAX_DEG);
    }
    CHECK(min_st < -5.0f && max_st > 5.0f); CHECK(min_duty < 0.5f); CHECK(corner);
    // rear flaps lag the front by the stagger delay
    Tunables t2; TrackRunner tr2; tr2.start(TRACK_DEFAULT_ID, 100.0f, 0);
    TrackCommand k0 = tr2.command(t2, 0); CHECK(k0.straight_front && !k0.straight_rear);  // rear starts in corner mode
    TrackCommand k1 = tr2.command(t2, TrackRunner::REAR_STAGGER_MS - 1); CHECK(!k1.straight_rear);
    TrackCommand k2 = tr2.command(t2, TrackRunner::REAR_STAGGER_MS); CHECK(k2.straight_rear);
    // closing straight mode earlier shrinks the zone
    Tunables early; early.straight_close_early_m = 300;
    int n_norm = 0, n_early = 0;
    for (int i = 0; i < 2000; ++i) {
      n_norm += trackSample(TRACK_DEFAULT_ID, i / 2000.0f, t2).straight;
      n_early += trackSample(TRACK_DEFAULT_ID, i / 2000.0f, early).straight;
    }
    CHECK(n_early < n_norm);
  }
  // --- stall + dyno calibration + vibration ---
  {
    StallDetector sd; CHECK(!sd.update(0.5f, false, 0)); CHECK(!sd.update(0.5f, false, StallDetector::HOLD_MS - 1));
    CHECK(sd.update(0.5f, false, StallDetector::HOLD_MS));
    StallDetector sd2; sd2.update(0.5f, false, 0); CHECK(!sd2.update(0.5f, true, 2000));
    CHECK(!sd2.update(0.5f, false, 2000 + StallDetector::HOLD_MS - 1));      // a pulse resets the timer
    StallDetector sd3; sd3.update(0.5f, false, 0); CHECK(!sd3.update(0.1f, false, 5000));  // low duty: not a stall
    DynoCalibrator dc; dc.begin(0.8f, 0);
    uint32_t t = 0; float duty = dc.update(0, t);
    while (dc.active() && t < 30000) {
      t += 10;
      float rear = duty >= 0.30f - 1e-4f ? duty * 5.0f : 0.0f;   // wheels break away at 30 % duty, 5 km/h per unit duty
      duty = dc.update(rear, t);
    }
    CHECK(dc.phase == DynoCalibrator::DONE);
    CHECK(std::fabs(dc.start_duty - 0.30f) < 0.051f); CHECK(std::fabs(dc.vmax_kmh - 4.0f) < 0.01f);
    DynoCalibrator stuck; stuck.begin(0.8f, 0); t = 0;
    while (stuck.active() && t < 30000) { t += 10; stuck.update(0, t); }
    CHECK(stuck.phase == DynoCalibrator::FAILED);
    VibrationMeter vm; float r = 0;
    for (int i = 0; i < 500; ++i) r = vm.update(0, 0, 1.0f);
    CHECK(r < 1.0f);
    for (int i = 0; i < 500; ++i) r = vm.update(0, 0, 1.0f + 0.1f * ((i % 2) ? 1 : -1));
    CHECK(r > 60.0f && r < 140.0f);
  }
  // --- new frames ---
  {
    CanFrame f = canEncodeTrackPosition(0.5f, 3, 12, TF_RUN_ACTIVE | TF_STRAIGHT_MODE, 0.62f, -1.5f);
    CHECK(f.dlc == 8 && getU16(&f.data[0]) == 32768 && getU16(&f.data[2]) == 3 && f.data[4] == 12);
    CHECK(f.data[6] == 62 && static_cast<int8_t>(f.data[7]) == -30);
    f = canEncodeConfigAck(17, CFG_STAGED, 700); CHECK(f.dlc == 6 && f.data[1] == 1 && getU32(&f.data[2]) == 700);
    f = canEncodeConfigReport(15, -50, true); CHECK(static_cast<int32_t>(getU32(&f.data[1])) == -50 && f.data[5] == 1);
    f = canEncodeDyno(123.456, 42.4f, RUN_BENCH_LAPS, 0); CHECK(getU32(&f.data[0]) == 12345 && getU16(&f.data[4]) == 42);
    f = canEncodeVehicleDerived2(10, 20, 30, 55.5f); CHECK(f.dlc == 8 && getU16(&f.data[6]) == 56);
    uint8_t k; int32_t v; CHECK(canDecodeConfigWrite(canEncodeConfigWrite(16, -7), k, v) && k == 16 && v == -7);
  }

  selftest_fw22();
  std::printf("SELFTEST %s failures=%d\n", g_fail ? "FAILED" : "PASSED", g_fail);
}

// --------------------------------------------------------------------- rpc
/* operator>> cannot parse "nan"/"inf" (the stream just fails and leaves the
 * remaining values unset), so values that may be non-finite go via strtof. */
static float F(std::istream &is) { std::string t; is >> t; return std::strtof(t.c_str(), nullptr); }

static void rpc() {
  std::string line;
  while (std::getline(std::cin, line)) {
    std::istringstream is(line);
    std::string cmd; is >> cmd;
    if (cmd == "ai_encode") {
      int seq; float lf, rf, lr, rr, conf; is >> seq >> lf >> rf >> lr >> rr >> conf;
      CanFrame a, b; canEncodeAiRecommendation(static_cast<uint8_t>(seq), lf, rf, lr, rr, conf, a, b);
      std::cout << hex(a) << " " << hex(b) << "\n";
    } else if (cmd == "ai_decode") {  // two hex payloads -> assembled values
      std::string ha, hb; is >> ha >> hb;
      AiRecAssembler as; AiWingRecommendation r;
      as.feed(parse(CAN_ID_AI_RECOMMENDATION_A, ha), 1000, r);
      bool ok = as.feed(parse(CAN_ID_AI_RECOMMENDATION_B, hb), 1000, r);
      std::printf("%d %.4f %.4f %.4f %.4f %.6f %u\n", ok ? 1 : 0, r.lf, r.rf, r.lr, r.rr, r.confidence, r.sequence);
    } else if (cmd == "enc") {
      std::string what; is >> what;
      if (what == "state") { int s; unsigned long fl; is >> s >> fl; std::cout << hex(canEncodeVehicleState(s, fl)) << "\n"; }
      else if (what == "wheel") { float a, b, c, d; is >> a >> b >> c >> d; std::cout << hex(canEncodeWheelSpeed(a, b, c, d)) << "\n"; }
      else if (what == "driver_inputs") { float a, b, c; is >> a >> b >> c; std::cout << hex(canEncodeDriverInputs(a, b, c)) << "\n"; }
      else if (what == "accel") { float a, b, c; is >> a >> b >> c; std::cout << hex(canEncodeImuAccel(a, b, c)) << "\n"; }
      else if (what == "gyro") { float a, b, c; is >> a >> b >> c; std::cout << hex(canEncodeImuGyro(a, b, c)) << "\n"; }
      else if (what == "attitude") { float a, b, c; is >> a >> b >> c; std::cout << hex(canEncodeImuAttitude(a, b, c)) << "\n"; }
      else if (what == "battery") { float a, b; is >> a >> b; std::cout << hex(canEncodeBattery(a, b)) << "\n"; }
      else if (what == "temperature") { float a, b, c; is >> a >> b >> c; std::cout << hex(canEncodeTemperature(a, b, c)) << "\n"; }
      else if (what == "aero_target" || what == "aero_measured") {
        float d[4]; is >> d[0] >> d[1] >> d[2] >> d[3];
        std::cout << hex(canEncodeAero(what == "aero_target" ? CAN_ID_AERO_TARGET : CAN_ID_AERO_MEASURED, d)) << "\n";
      }
      else if (what == "derived") { int sl, tm, tb; is >> sl >> tm >> tb; std::cout << hex(canEncodeVehicleDerived((int16_t)sl, (uint16_t)tm, (uint16_t)tb)) << "\n"; }
      else if (what == "track_position") { float fr, du, lg; int lap, tr, fl; is >> fr >> lap >> tr >> fl >> du >> lg;
        std::cout << hex(canEncodeTrackPosition(fr, (uint16_t)lap, (uint8_t)tr, (uint8_t)fl, du, lg)) << "\n"; }
      else if (what == "lap_timing") { unsigned long a, b; is >> a >> b; std::cout << hex(canEncodeLapTiming(a, b)) << "\n"; }
      else if (what == "dyno") { double od; float vib; int rs, cp; is >> od >> vib >> rs >> cp;
        std::cout << hex(canEncodeDyno(od, vib, (uint8_t)rs, (uint8_t)cp)) << "\n"; }
      else if (what == "config_ack") { int k, st; long v; is >> k >> st >> v; std::cout << hex(canEncodeConfigAck((uint8_t)k, (uint8_t)st, (int32_t)v)) << "\n"; }
      else if (what == "config_report") { int k, p; long v; is >> k >> v >> p; std::cout << hex(canEncodeConfigReport((uint8_t)k, (int32_t)v, p != 0)) << "\n"; }
      else if (what == "derived2") { int sl, tm, tb; float vib; is >> sl >> tm >> tb >> vib;
        std::cout << hex(canEncodeVehicleDerived2((int16_t)sl, (uint16_t)tm, (uint16_t)tb, vib)) << "\n"; }
      else if (what == "vcu_mode") { int m, hw, fl, laps, tc, knob; is >> m >> hw >> fl >> laps >> tc >> knob;
        VcuModeStatus st{(uint8_t)m, (uint8_t)hw, (uint8_t)fl, (uint8_t)laps, (uint8_t)tc, (int16_t)knob};
        std::cout << hex(canEncodeVcuMode(st)) << "\n"; }
      else if (what == "config_write") { int k; long v; is >> k >> v; std::cout << hex(canEncodeConfigWrite((uint8_t)k, (int32_t)v)) << "\n"; }
      else if (what == "fault_event") { unsigned long fl; int sev, st; is >> fl >> sev >> st; std::cout << hex(canEncodeFaultEvent(fl, sev, st)) << "\n"; }
      else if (what == "vcu_status") {
        int m, r, d, c, u, o, p; is >> m >> r >> d >> c >> u >> o >> p;
        VcuStatus s{(uint8_t)m, (uint8_t)r, (uint8_t)d, (uint8_t)c, (uint16_t)u, (uint8_t)o, (uint8_t)p};
        std::cout << hex(canEncodeVcuStatus(s)) << "\n";
      } else std::cout << "ERR unknown enc\n";
    } else if (cmd == "slip") {
      float fl = F(is), fr = F(is), rl = F(is), rr = F(is);
      std::printf("%d\n", slipRatioPermille(fl, fr, rl, rr));
    } else if (cmd == "dec_driver") {
      std::string h; is >> h; DriverCommand c;
      bool ok = canDecodeDriverCommand(parse(CAN_ID_DRIVER_COMMAND, h), 5, c);
      std::printf("%d %u %d %d %u\n", ok ? 1 : 0, c.sequence, c.steering_permille, c.throttle_permille, c.flags);
    } else if (cmd == "dec_hb") {
      std::string h; is >> h; PiHeartbeat hb{};
      bool ok = canDecodePiHeartbeat(parse(CAN_ID_PI_HEARTBEAT, h), 5, hb);
      std::printf("%d %u %d %d\n", ok ? 1 : 0, hb.counter, hb.lora_link_ok ? 1 : 0, hb.backend_ok ? 1 : 0);
    } else if (cmd == "dec_cfg") {
      std::string h; is >> h; uint8_t k = 0; int32_t v = 0;
      bool ok = canDecodeConfigWrite(parse(CAN_ID_CONFIG_WRITE, h), k, v);
      std::printf("%d %u %d\n", ok ? 1 : 0, k, v);
    } else if (cmd == "ai_check") {  // valid conf ts now lf rf lr rr
      int valid; unsigned long ts, now;
      is >> valid; float conf = F(is); is >> ts >> now;
      float lf = F(is), rf = F(is), lr = F(is), rr = F(is);
      AiWingRecommendation r; r.valid = valid; r.confidence = conf; r.timestamp_ms = ts;
      r.lf = lf; r.rf = rf; r.lr = lr; r.rr = rr;
      std::printf("%s\n", aiReasonName(aiRecommendationCheck(r, now)));
    } else if (cmd == "aero") {  // speed state(0 drive/1 fault/2 estop) valid conf ts now lf rf lr rr
      float speed, conf, lf, rf, lr, rr; int st, valid; unsigned long ts, now;
      is >> speed >> st >> valid >> conf >> ts >> now >> lf >> rf >> lr >> rr;
      ActiveAeroController aero(0.0f);  // lateral gain 0 == vcu_sim's simplified model
      AeroInputs in; in.speed_kmh = speed; in.state = st == 0 ? VS_DRIVE : (st == 1 ? VS_FAULT : VS_EMERGENCY_STOP);
      AiWingRecommendation r; r.valid = valid; r.confidence = conf; r.timestamp_ms = ts;
      r.lf = lf; r.rf = rf; r.lr = lr; r.rr = rr;
      AeroOutput o = aero.compute(in, &r, now);
      std::printf("%d %.4f %.4f %.4f %.4f\n", o.ai_applied ? 1 : 0, o.desired[0], o.desired[1], o.desired[2], o.desired[3]);
    } else if (cmd == "track_sample") {  // track frac -> steer speed_frac latg straight braking sector
      int tr; float fr; is >> tr >> fr; Tunables tun; TrackSample t = trackSample((uint8_t)tr, fr, tun);
      std::printf("%.4f %.4f %.4f %d %d %u\n", t.steer_deg, t.speed_frac, t.latg_model, t.straight ? 1 : 0,
                  t.braking ? 1 : 0, t.sector);
    } else if (cmd == "track_info") {
      int tr; is >> tr; const TrackInfo &t = TRACKS[tr];
      std::printf("%s %lu %u %lu\n", t.code, (unsigned long)t.length_m, t.vmax_kmh, (unsigned long)t.model_lap_ms);
    } else if (!cmd.empty()) {
      std::cout << "ERR unknown " << cmd << "\n";
    }
    std::cout.flush();
  }
}

int main(int argc, char **argv) {
  if (argc > 1 && std::strcmp(argv[1], "rpc") == 0) { rpc(); return 0; }
  selftest();
  return g_fail ? 1 : 0;
}
