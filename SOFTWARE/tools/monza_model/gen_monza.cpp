// Training data for the Monza model: the real VcuController (vcu_logic.h) driving a circuit run of Monza
// (track 12) on the dyno with the duty-based speed model, logged every 50 ms.
#include "vcu_logic.h"
#include <cstdio>
using namespace vcu;
int main(int argc, char **argv) {
  int laps = argc > 1 ? atoi(argv[1]) : 12;
  VcuController c; c.begin(HW_PROFILE_PROTOTYPE, false, true); c.set_speed_from_duty(true); c.boot(0);
  uint32_t t = 0; int32_t v = 0; VcuOutputs o;
  auto step = [&]() { t += 10; VcuInputs in; in.now_ms = t; in.dt_s = 0.01f; in.battery_v = 12.0f; in.battery_a = 0.4f;
    in.battery_temp_c = 27; in.motor_temp_c = 30; in.can_link_ok = true; in.hb_seen = true; in.hb.lora_link_ok = true;
    in.hb.backend_ok = true; in.hb_age_ms = 50; o = c.step(in); };
  for (int i = 0; i < 100; ++i) step();
  if (c.write(CFG_KEY_DRIVE_MODE, DRIVE_MODE_DYNO, false, t, v) != CFG_APPLIED) return 2;
  if (c.write(CFG_KEY_TRACK_ID, 12, false, t, v) != CFG_APPLIED) return 3;
  if (c.write(CFG_KEY_RUN_LAPS, laps, false, t, v) != CFG_APPLIED) return 4;
  for (int i = 0; i < 50; ++i) step();
  if (c.write(CFG_KEY_RUN_CMD, RUN_CMD_LAPS, false, t, v) != CFG_APPLIED) return 5;
  std::printf("t,lap,frac,speed_kmh,duty_pct,steer_deg,straight,flap_rl,flap_rr,lap_len_m\n");
  int k = 0;
  for (;;) {
    step();
    if (!(o.track_flags & TF_RUN_ACTIVE) && t > 3000) break;
    if (t > 2000000) break;
    int32_t ll = 0; c.cfg.read(CFG_KEY_LAP_LENGTH_CM, ll);
    if (++k % 5 == 0)
      std::printf("%.2f,%u,%.5f,%.3f,%.2f,%.2f,%d,%.1f,%.1f,%.2f\n", t / 1000.0, o.lap, o.track_frac, o.model_speed_kmh,
                  o.motor_duty * 100.0f, o.steering_deg, (o.track_flags & TF_STRAIGHT_MODE) ? 1 : 0, o.flaps[AERO_LR], o.flaps[AERO_RR], ll / 100.0);
  }
  return 0;
}
