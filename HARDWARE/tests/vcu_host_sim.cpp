/*
 * vcu_host_sim.cpp — a VIRTUAL CAR for the PC: the REAL VCU control logic (vcu_logic.h
 * VcuController, the same code the ESP32-C6 runs) plus a small plant model, talking CAN
 * frames over stdin/stdout. HARDWARE/tests/virtual_car.py puts those frames on a
 * python-can virtual bus, so the real Raspberry Pi gateway, ground bridge, backend and
 * dashboard can run end to end with no hardware.
 *
 * Line protocol (stdin -> stdout):
 *   RX <id hex> <data hex>        a frame from the Pi (0x118 driver, 0x160/161 AI, 0x180 write, 0x190 heartbeat)
 *   STEP <now_ms>                 run one 10 ms control step; prints "TX <id> <data>" lines, then "END"
 *   SET <name> <value>            plant settings: dyno 0|1 (rollers turn only the rears), grip <0..1>
 *   QUIT
 * The CAN transmit schedule mirrors the sketch's canTask (10/20/50/100/200 ms).
 *
 * What it does NOT model: sensor noise, ISR timing, real servo torque, the 12 V adapter's
 * behaviour under load, radio range. It proves the logic and the protocol, not the hardware.
 *
 * Build: g++ -std=c++17 -O1 -I<sketch dir> vcu_host_sim.cpp -o vcu_host_sim [-DSIM_HW_PROFILE=1]
 */
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <iostream>
#include <sstream>
#include <string>

#include "vcu_logic.h"

using namespace vcu;

#ifndef SIM_HW_PROFILE
#define SIM_HW_PROFILE 1   // 1 = PCB rev B (kill switch fitted), 0 = prototype
#endif

static void tx(const CanFrame &f) {
  std::printf("TX %03X ", f.id);
  for (int i = 0; i < f.dlc; ++i) std::printf("%02X", f.data[i]);
  std::printf("\n");
}

static CanFrame parse(const std::string &id, const std::string &hex) {
  CanFrame f; f.id = static_cast<uint16_t>(std::strtoul(id.c_str(), nullptr, 16));
  f.dlc = static_cast<uint8_t>(std::min<size_t>(8, hex.size() / 2));
  for (int i = 0; i < f.dlc; ++i) f.data[i] = static_cast<uint8_t>(std::strtoul(hex.substr(2 * i, 2).c_str(), nullptr, 16));
  return f;
}

struct Plant {
  bool dyno = true;            // on the rollers: fronts do not turn
  float grip = 0.85f;          // track mode: above this fraction of max acceleration the rears spin
  float rear_kmh = 0, front_kmh = 0;
  float flaps_meas[4] = {0, 0, 0, 0};
  float motor_temp = 29, batt_temp = 27;
  float lat_g = 0, yaw_dps = 0;
  double dist_rl = 0, dist_rr = 0;
  uint32_t pulses_rl = 0, pulses_rr = 0;

  static constexpr float VMAX_KMH = 4.4f;   // rear-wheel speed at 100 % duty on the rollers (cf. demo_car.py)
  static constexpr float START_DUTY = 0.25f;
  static constexpr float WHEELBASE_M = 0.36f;

  void step(const VcuOutputs &o, float dt) {
    float duty = o.motor_power || SIM_HW_PROFILE == 0 ? o.motor_duty : 0.0f;
    float v_target = duty > START_DUTY ? duty * VMAX_KMH * (dyno ? 1.0f : 3.0f) : 0.0f;   // floor: lighter load
    float tau = o.motor_braking ? 0.15f : 0.35f;
    float prev = rear_kmh;
    rear_kmh += (v_target - rear_kmh) * std::min(1.0f, dt / tau);
    if (dyno) {
      front_kmh = 0;
    } else {
      // the car (= the free-rolling fronts) follows the driven rears only as fast as grip allows;
      // asked for more, the rears overspeed (wheelspin) until the car catches up
      (void)prev;
      float max_accel = 6.0f * grip;                            // km/h per s
      float want = (rear_kmh - front_kmh) / std::max(dt, 1e-3f);
      front_kmh += std::min(std::max(want, -12.0f), max_accel) * dt;
      front_kmh = std::max(0.0f, std::min(front_kmh, rear_kmh));
    }
    // cornering from the steering servo (track mode only; the fronts do not steer the car on the rig)
    float v = front_kmh / 3.6f;
    float steer = o.steering_deg * 3.14159265f / 180.0f;
    float curvature = dyno ? 0.0f : std::tan(steer) / WHEELBASE_M;
    lat_g = v * v * curvature / 9.81f;
    yaw_dps = v * curvature * 57.2958f;
    // odometry pulses (1 per rev, 70 mm tyre)
    double circ = 3.14159265 * CFG_WHEEL_DIAMETER_M / CFG_WHEEL_PULSES_PER_REV;
    double d = rear_kmh / 3.6 * dt;
    dist_rl += d; dist_rr += d;
    while (dist_rl >= circ) { dist_rl -= circ; ++pulses_rl; }
    while (dist_rr >= circ) { dist_rr -= circ; ++pulses_rr; }
    for (int i = 0; i < 4; ++i) {
      float lag = i >= 2 ? 0.85f : 1.0f;
      float step = 300.0f * lag * dt;
      flaps_meas[i] += std::max(-step, std::min(step, o.flaps[i] - flaps_meas[i]));
    }
    float current = 0.18f + 1.55f * duty;
    motor_temp += (current * current * 0.012f - (motor_temp - 27) * 0.004f) * dt;
  }
  float current(const VcuOutputs &o) const {
    float duty = o.motor_power || SIM_HW_PROFILE == 0 ? o.motor_duty : 0.0f;
    return 0.18f + 1.55f * duty;
  }
};

int main() {
  std::ios::sync_with_stdio(false);
  VcuController ctrl;
  ctrl.begin(SIM_HW_PROFILE, SIM_HW_PROFILE == 1, true);
  ctrl.boot(0);
  Plant plant;
  AiRecAssembler assembler;
  DriverCommand driver;
  PiHeartbeat hb{0, false, false, 0};
  bool hb_seen = false;
  AiWingRecommendation ai;
  uint32_t last_rx_ms = 0, now = 0, tick = 0, prev_now = 0;
  uint32_t prev_pulses = 0;
  uint8_t last_ai_reason = AI_NONE_RECEIVED;
  uint32_t prev_flags = 0xFFFFFFFFu;
  VehicleState prev_state = VS_BOOT;
  struct Pending { uint8_t key; int32_t value; };
  Pending pending[16]; int n_pending = 0;
  ThermalEstimator mt, bt;
  VibrationMeter vib;
  uint8_t report_idx = 0;
  float last_current = 0.18f;
  int32_t knob_clicks = 0;

  std::string line;
  while (std::getline(std::cin, line)) {
    std::istringstream is(line);
    std::string cmd; is >> cmd;
    if (cmd == "QUIT") break;
    if (cmd == "SET") {
      std::string k; float v; is >> k >> v;
      if (k == "dyno") plant.dyno = v != 0;
      else if (k == "grip") plant.grip = v;
      else if (k == "speedmodel") ctrl.set_speed_from_duty(v != 0);   // as VCU_CAL_SPEED_FROM_DUTY 1 on the car
      else if (k == "knob") knob_clicks += static_cast<int32_t>(v);   // FW 2.3: turn the steering knob
      continue;
    }
    if (cmd == "RX") {
      std::string id, hex; is >> id >> hex;
      CanFrame f = parse(id, hex);
      last_rx_ms = now;
      DriverCommand dc; PiHeartbeat h; AiWingRecommendation rec; uint8_t key; int32_t value;
      if (canDecodeDriverCommand(f, now, dc)) driver = dc;
      else if (canDecodePiHeartbeat(f, now, h)) { hb = h; hb_seen = true; }
      else if (assembler.feed(f, now, rec)) ai = rec;
      else if (canDecodeConfigWrite(f, key, value) && n_pending < 16) pending[n_pending++] = {key, value};
      continue;
    }
    if (cmd != "STEP") continue;
    unsigned long t; is >> t; now = static_cast<uint32_t>(t);
    float dt = prev_now ? (now - prev_now) / 1000.0f : 0.01f;
    prev_now = now;
    ++tick;

    // writes / commands, in order, each answered with CONFIG_ACK (as the sketch's control task)
    for (int i = 0; i < n_pending; ++i) {
      int32_t out = pending[i].value;
      ConfigStatus r = ctrl.write(pending[i].key, pending[i].value, false, now, out);
      tx(canEncodeConfigAck(pending[i].key, r, out));
    }
    n_pending = 0;

    VcuInputs in;
    in.now_ms = now; in.dt_s = dt;
    in.wheel_kmh[0] = in.wheel_kmh[1] = plant.front_kmh;
    in.wheel_kmh[2] = plant.rear_kmh; in.wheel_kmh[3] = plant.rear_kmh * 0.985f;
    in.speed_kmh = plant.front_kmh >= 0.3f ? plant.front_kmh : (in.wheel_kmh[2] + in.wheel_kmh[3]) * 0.5f;
    in.accel_g[0] = 0; in.accel_g[1] = plant.lat_g; in.accel_g[2] = 1;
    in.gyro_dps[2] = plant.yaw_dps;
    in.battery_a = last_current;
    in.battery_v = 12.15f - 0.42f * in.battery_a;
    in.battery_temp_c = plant.batt_temp; in.motor_temp_c = plant.motor_temp;
    in.imu_age_ms = 5; in.power_age_ms = 20; in.temp_age_ms = 500;
    in.fb_deg[SERVO_LR] = plant.flaps_meas[2]; in.fb_deg[SERVO_RR] = plant.flaps_meas[3];
    uint32_t pulses = plant.pulses_rl + plant.pulses_rr;
    in.rear_pulses = pulses - prev_pulses; prev_pulses = pulses;
    in.rear_dist_m = plant.rear_kmh / 3.6f * dt;
    in.driver = driver;
    in.can_link_ok = last_rx_ms && now - last_rx_ms < CFG_CAN_RX_TIMEOUT_MS;
    in.hb_seen = hb_seen; in.hb = hb; in.hb_age_ms = hb_seen ? now - hb.rx_ms : 0xFFFFFFFFu;
    in.ai = ai;
    in.knob_detents = knob_clicks; knob_clicks = 0;
    VcuOutputs o = ctrl.step(in);
    if (o.request_self_test) ctrl.self_test_done(0);
    if (o.new_ai_rec) last_ai_reason = o.ai_reason;
    plant.step(o, dt);
    if (std::isfinite(o.model_speed_kmh)) {    // speedmodel on: report the duty-model speed, as the sketch does
      in.wheel_kmh[0] = in.wheel_kmh[1] = 0; in.wheel_kmh[2] = in.wheel_kmh[3] = o.model_speed_kmh; in.speed_kmh = o.model_speed_kmh;
    }
    float cur = plant.current(o);
    last_current = cur;
    float volts = 12.15f - 0.42f * cur;
    float vib_mg = vib.update(0, plant.lat_g, 1.0f + 0.01f * plant.rear_kmh * std::sin(now * 0.05f));

    if (o.fault_flags != prev_flags || o.state != prev_state) {
      tx(canEncodeFaultEvent(o.fault_flags, o.worst, o.state));
      prev_flags = o.fault_flags; prev_state = o.state;
    }
    // ---- the sketch's canTask schedule ----
    tx(canEncodeImuAccel(in.accel_g[0], in.accel_g[1], in.accel_g[2]));
    tx(canEncodeImuGyro(0, 0, plant.yaw_dps));
    if (tick % 2 == 0) {
      tx(canEncodeVehicleState(o.state, o.fault_flags));
      tx(canEncodeWheelSpeed(in.wheel_kmh[0], in.wheel_kmh[1], in.wheel_kmh[2], in.wheel_kmh[3]));
      tx(canEncodeDriverInputs(o.steering_deg, o.motor_duty * 100.0f, o.motor_braking ? 100.0f : 0.0f));
      tx(canEncodeImuAttitude(0, 0, 0));
      tx(canEncodeAero(CAN_ID_AERO_TARGET, o.flaps));
      float meas[4] = {o.flaps[0], o.flaps[1], plant.flaps_meas[2], plant.flaps_meas[3]};
      tx(canEncodeAero(CAN_ID_AERO_MEASURED, meas));
    }
    if (tick % 5 == 0) {
      tx(canEncodeBattery(volts, cur));
      tx(canEncodeTrackPosition(o.track_frac, o.lap, ctrl.cfg.snap.track_id, o.track_flags, o.profile_duty, o.latg_model));
    }
    if (tick % 20 == 0 || o.lap_event) tx(canEncodeLapTiming(o.last_lap_ms, o.cur_lap_ms));
    if (tick % 10 == 0) {
      tx(canEncodeTemperature(plant.motor_temp, plant.batt_temp, cur));
      VcuStatus vs{static_cast<uint8_t>(ctrl.cfg.snap.aero_mode), last_ai_reason,
                   static_cast<uint8_t>(driverCommandFresh(driver, now)), 0x18, static_cast<uint16_t>(now / 1000), 0,
                   CAN_PROTOCOL_VERSION};
      tx(canEncodeVcuStatus(vs));
      tx(canEncodeVcuMode(ctrl.mode_status(o)));
      tx(canEncodeVehicleDerived2(slipRatioPermille(in.wheel_kmh[0], in.wheel_kmh[1], in.wheel_kmh[2], in.wheel_kmh[3]),
                                  mt.update(plant.motor_temp, CFG_MOTOR_OVERTEMP_C, now),
                                  bt.update(plant.batt_temp, CFG_BATTERY_OVERTEMP_C, now), vib_mg));
      tx(canEncodeDyno(o.odometer_m, vib_mg, o.run_state, o.cal_phase));
      uint8_t k = CONFIG_REPORT_KEYS[report_idx++ % CONFIG_REPORT_KEY_COUNT];
      int32_t v = 0;
      if (ctrl.cfg.read(k, v)) tx(canEncodeConfigReport(k, v, ctrl.cfg.pending));
    }
    std::printf("END\n");
    std::fflush(stdout);
  }
  return 0;
}
