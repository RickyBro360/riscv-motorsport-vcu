/*
 * =============================================================================
 * main_updated_corrected__1_.ino — ESP32-C6 RISC-V Vehicle Control Unit (VCU)
 *   (FW 2.2.1+: also builds for a classic ESP32 DevKit as a TEMPORARY stand-in)
 * =============================================================================
 * Project : AI-Enabled RISC-V Based Motorsport Electronics Platform with Active
 *           Aerodynamics, Telemetry and Digital Twin — 1:10 Formula-style RC car
 * Target  : SmartElex ESP32-C6 Pico (ESP32-C6-MINI-1, RISC-V), Arduino-ESP32 core 3.1 .. 3.3
 *           (FW 2.2 compile-checked on core 3.3.2 with Arduino's own ctags)
 *           FW 2.2.1 stand-in: classic ESP32 DevKit (ESP-WROOM-32) jumpered into the prototype
 *           board's U1 socket. The pin map follows the board selected in the IDE
 *           (vcu_hw_profile.h). Not RISC-V: temporary until the C6 is replaced.
 * Stage   : 2e / FW 2.3 — the KY-040 knob is the STEERING WHEEL, the four flaps follow the
 *           steering (outside pair up, inside pair down), the motor runs from the dashboard,
 *           every servo end-point is in vcu_calibration.h AND live-editable (dashboard / console),
 *           only real faults stop the motor. FW 2.2 kept: dyno/track drive modes, circuit runs,
 *           e-stop from the dashboard, the two hardware profiles.
 *           Stage 2c was the last hardware-tested build (4 halls, 2 MG996R + feedback, IMU,
 *           DS18B20). See SOFTWARE/Documentation/ChangeLog_FW2.2.md for what changed and the
 *           re-test list.
 *
 * ARCHITECTURE (locked)
 *   Sensors -> sensor task -> shared state -> control task
 *     control task = VcuController::step() in vcu_logic.h (hardware-free, also run on the PC
 *     by HARDWARE/tests): safety -> state machine -> runs / steering / motor / aero.
 *   CAN task <-> Raspberry Pi 4B gateway (telemetry out; AI recommendation, driver command,
 *     config/commands, heartbeat in). THE VCU IS THE ONLY ACTUATOR AUTHORITY: every
 *     Pi/laptop input is validated here first. The dashboard can REQUEST a run or an
 *     e-stop; the VCU decides.
 *
 * FILES IN THIS SKETCH FOLDER
 *   main_updated_corrected__1_.ino  hardware I/O, RTOS tasks, console (this file)
 *   vcu_hw_profile.h                pins + drivers for the prototype / PCB rev B
 *   vcu_calibration.h               FW 2.3: SERVO END-POINTS, steering->flap law, knob. EDIT THIS ONE.
 *   vcu_logic.h                     hardware-free logic (also compiled on PC by
 *                                   HARDWARE/tests/firmware_host_test.cpp and vcu_host_sim.cpp)
 *   track_maps.h                    GENERATED circuit maps (SOFTWARE/tracks/build_tracks.py)
 *
 * BUILD (Arduino IDE or arduino-cli)
 *   Board: "ESP32C6 Dev Module", Partition: Default, no external libraries.
 *   USB CDC On Boot: ENABLED (the SmartElex board's USB-C is the chip's native USB). The
 *   sketch refuses to compile otherwise: GPIO16/17 (UART0) carry a servo / the motor PWM.
 *   arduino-cli compile --fqbn esp32:esp32:esp32c6:CDCOnBoot=cdc <this folder>
 *   Classic ESP32 stand-in: Board "ESP32 Dev Module" (console = the DevKit's USB/CP2102 on
 *   UART0, GPIO1/3, which the VCU does not use). Hold BOOT if the upload stalls at
 *   "Connecting...".   arduino-cli compile --fqbn esp32:esp32:esp32 <this folder>
 *
 * HARDWARE-VALIDATION STATUS: compiled for ESP32-C6 and (2.2.1) the classic ESP32; the control
 *   logic passes the host tests.
 * NOT yet run on the physical car. Every [PLACEHOLDER] must be bench-verified.
 * =============================================================================
 */

#include <Arduino.h>
#include <algorithm>
#include <Preferences.h>
#include <Wire.h>

#include "driver/twai.h"
#include "esp_task_wdt.h"
#include "soc/soc_caps.h"
#include "esp_system.h"   // esp_reset_reason() (FW 2.2.3 boot diagnostics)
#include "vcu_hw_profile.h"   // BEFORE vcu_logic.h: it sets the wheel pulses-per-rev and debounce
#include "vcu_logic.h"

using namespace vcu;

// =============================================================================
// SECTION 0: BUILD OPTIONS
// =============================================================================
// Transport to the Raspberry Pi. TWAI = real CAN (production).
// WIFI_UDP = the Stage-1 development substitute (kept, NOT deleted) — packet
// format [id_lo, id_hi, dlc, data x8], same as raspberry_pi/main.py WifiCanInterface.
#define VCU_TRANSPORT_TWAI 1
#define VCU_TRANSPORT_WIFI_UDP 0

// 1 = read real sensors. 0 = deterministic sample waveform (bench demo with a
// bare devkit; identical to raspberry_pi/main.py SampleTelemetrySource).
#define VCU_SENSORS_REAL 1

// ADS1115 fitted? 0 = not fitted (neither build has it). Battery voltage then comes from the
// INA260 bus-voltage register, and steering / front-flap feedback is not wired (open loop).
#define VCU_HAVE_ADS1115 0

// Servo set (both builds):
//                                        ESP32-C6                         classic ESP32 stand-in
//   0 STEER  MG995  (open loop)          GPIO18                           GPIO18
//   1 LF     SG90   (open loop)          GPIO19 prototype / GPIO16 rev B  GPIO19
//   2 RF     SG90   (open loop)          GPIO20                           GPIO23
//   3 LR     MG996R + pot feedback        GPIO21, wiper -> divider -> GPIO0 GPIO27, wiper -> GPIO36 (VP)
//   4 RR     MG996R + pot feedback        GPIO8,  wiper -> divider -> GPIO1 GPIO14, wiper -> GPIO39 (VN)
// Which pot-feedback wires are physically connected (bit i = servo i). If the rear MG996R
// wiper wires are NOT plugged in, set 0x00 or the VCU fails self-test (bit 6) and stays in FAULT.
#if VCU_HAVE_ADS1115
#define VCU_FB_WIRED_MASK 0x1F
#else
#define VCU_FB_WIRED_MASK 0x18  // LR + RR (the two rear MG996R). 0x08 = LR only, 0x00 = no wiper wires.
#endif

// INA260 power monitor in the +12 V line (off-board on both builds). 0 = not fitted: then no
// supply voltage/current, and DRIVE / runs are refused (no power sensor = no motor).
#define VCU_HAVE_INA260 1

// Both motor drivers are forward only: the direction is fixed in hardware.
#define VCU_MOTOR_FORWARD_ONLY 1
#if !VCU_MOTOR_FORWARD_ONLY
#error "Both hardware profiles are forward-only (direction is strapped / fixed by the half-bridge wiring)"
#endif

// GPIO16 (U0TXD) and GPIO17 (U0RXD) carry a servo / the motor PWM, so the console MUST be on
// the native USB port, never on UART0.
// (ESP32-C6 only. On the classic ESP32 the console is UART0 on GPIO1/3 via the DevKit's
// USB-UART chip; no VCU signal uses those pins.)
#if !VCU_MCU_CLASSIC_ESP32 && (!defined(ARDUINO_USB_CDC_ON_BOOT) || !ARDUINO_USB_CDC_ON_BOOT)
#error "Arduino IDE: Tools > USB CDC On Boot > Enabled (GPIO16/17 are used by the VCU, not by Serial)"
#endif

#if VCU_TRANSPORT_WIFI_UDP
#include <WiFi.h>
#include <WiFiUdp.h>
static const char *WIFI_SSID = "CTRL";           // bench transport only (VCU_TRANSPORT_WIFI_UDP 1)
static const char *WIFI_PASSWORD = "SET_ME";     // do NOT push a real password to a public repo
static const char *PI_IP = "10.49.218.178";     // match raspberry_pi/main.py THIS_PI_IP
static const uint16_t PI_PORT = 5005;            // Pi listens (ESP32 -> Pi)
static const uint16_t ESP32_LISTEN_PORT = 5006;  // ESP32 listens (Pi -> ESP32)
#endif

// =============================================================================
// SECTION 1: PIN ASSIGNMENT — from vcu_hw_profile.h
// AUTHORITATIVE tables: SOFTWARE/Documentation/VCU_final_connections.txt (prototype) and
// SOFTWARE/Documentation/PCB_revB_connections.md (PCB rev B).
// =============================================================================
static const int PIN_FB_LR = VCU_PIN_FB_LR;
static const int PIN_FB_RR = VCU_PIN_FB_RR;
static const int PIN_CAN_TX = VCU_PIN_CAN_TX;
static const int PIN_CAN_RX = VCU_PIN_CAN_RX;
static const int PIN_I2C_SDA = VCU_PIN_I2C_SDA;
static const int PIN_I2C_SCL = VCU_PIN_I2C_SCL;
static const int PIN_WHEEL_FL = VCU_PIN_WHEEL_FL;
static const int PIN_KNOB_SW = VCU_PIN_WHEEL_FR;   // KY-040 SW on J12 pin 3 (front-right wheel-sensor input)
static const int PIN_WHEEL_FR = VCU_PIN_WHEEL_FR;
static const int PIN_WHEEL_RL = VCU_PIN_WHEEL_RL;
static const int PIN_WHEEL_RR = VCU_PIN_WHEEL_RR;
static const int PIN_ONEWIRE = VCU_PIN_ONEWIRE;
static const int PIN_MOTOR_PWM = VCU_PIN_MOTOR_PWM;
static const int PIN_MOTOR_EN = VCU_PIN_MOTOR_EN;      // -1 = no kill switch (prototype)
static const int PIN_SERVO_STEER = VCU_PIN_SERVO_STEER;
static const int PIN_SERVO_LF = VCU_PIN_SERVO_LF;
static const int PIN_SERVO_RF = VCU_PIN_SERVO_RF;
static const int PIN_SERVO_LR = VCU_PIN_SERVO_LR;
static const int PIN_SERVO_RR = VCU_PIN_SERVO_RR;
static const int PIN_ENC_CLK = VCU_PIN_ENC_CLK;
static const int PIN_ENC_DT = VCU_PIN_ENC_DT;

#if VCU_MCU_CLASSIC_ESP32
// Classic ESP32: GPIO34-39 are input-only, GPIO6-11 are the SPI flash, GPIO1/3 the console,
// GPIO0/2/5/12/15 strapping pins. Catch a bad edit of the pin map at compile time.
// (Macros, not functions: the Arduino IDE inserts its generated prototypes before the FIRST
//  function in the sketch, which must stay below the struct definitions.)
#define vcuInputOnly(p) ((p) >= 34 && (p) <= 39)
#define vcuForbidden(p) (((p) >= 6 && (p) <= 11) || (p) == 1 || (p) == 3 || (p) == 0 || (p) == 2 || (p) == 5 || (p) == 12 || (p) == 15)
static_assert(!vcuInputOnly(VCU_PIN_SERVO_STEER) && !vcuInputOnly(VCU_PIN_SERVO_LF) && !vcuInputOnly(VCU_PIN_SERVO_RF) &&
              !vcuInputOnly(VCU_PIN_SERVO_LR) && !vcuInputOnly(VCU_PIN_SERVO_RR) && !vcuInputOnly(VCU_PIN_MOTOR_PWM) &&
              !vcuInputOnly(VCU_PIN_CAN_TX) && !vcuInputOnly(VCU_PIN_ONEWIRE) && !vcuInputOnly(VCU_PIN_I2C_SDA) &&
              !vcuInputOnly(VCU_PIN_I2C_SCL),
              "classic ESP32: an output/bidirectional signal is on an input-only pin (GPIO34-39)");
static_assert(!vcuInputOnly(VCU_PIN_WHEEL_FL) && !vcuInputOnly(VCU_PIN_WHEEL_FR) && !vcuInputOnly(VCU_PIN_WHEEL_RL) &&
              !vcuInputOnly(VCU_PIN_WHEEL_RR),
              "classic ESP32: hall inputs need INPUT_PULLUP, which GPIO34-39 do not have");
static_assert(!vcuForbidden(VCU_PIN_FB_LR) && !vcuForbidden(VCU_PIN_FB_RR) && !vcuForbidden(VCU_PIN_CAN_TX) &&
              !vcuForbidden(VCU_PIN_CAN_RX) && !vcuForbidden(VCU_PIN_I2C_SDA) && !vcuForbidden(VCU_PIN_I2C_SCL) &&
              !vcuForbidden(VCU_PIN_WHEEL_FL) && !vcuForbidden(VCU_PIN_WHEEL_FR) && !vcuForbidden(VCU_PIN_WHEEL_RL) &&
              !vcuForbidden(VCU_PIN_WHEEL_RR) && !vcuForbidden(VCU_PIN_ONEWIRE) && !vcuForbidden(VCU_PIN_MOTOR_PWM) &&
              !vcuForbidden(VCU_PIN_SERVO_STEER) && !vcuForbidden(VCU_PIN_SERVO_LF) && !vcuForbidden(VCU_PIN_SERVO_RF) &&
              !vcuForbidden(VCU_PIN_SERVO_LR) && !vcuForbidden(VCU_PIN_SERVO_RR) && !vcuForbidden(VCU_PIN_ENC_CLK) &&
              !vcuForbidden(VCU_PIN_ENC_DT),
              "classic ESP32: a VCU signal is on a flash, console or strapping pin");
#endif
#if VCU_MCU_CLASSIC_ESP32
#define VCU_STACK_SCALE 2
#else
#define VCU_STACK_SCALE 1
#endif
static TaskHandle_t g_task_can = nullptr, g_task_sensor = nullptr, g_task_control = nullptr;

static const int SERVO_PINS[SERVO_COUNT] = {PIN_SERVO_STEER, PIN_SERVO_LF, PIN_SERVO_RF, PIN_SERVO_LR, PIN_SERVO_RR};
// Feedback source per servo: an ESP32 ADC pin, or an ADS1115 channel (-1 = not this source).
static const int FB_ADC_PINS[SERVO_COUNT] = {-1, -1, -1, PIN_FB_LR, PIN_FB_RR};
static const int FB_ADS_CH[SERVO_COUNT] = {1, 2, 3, -1, -1};
static const char *SERVO_NAMES[SERVO_COUNT] = {"STEER", "LF", "RF", "LR", "RR"};
[[maybe_unused]] static const char *SERVO_MODELS[SERVO_COUNT] = {"MG995", "SG90", "SG90", "MG996R", "MG996R"};

// =============================================================================
// SECTION 2: HARDWARE CONSTANTS
// =============================================================================
static const uint32_t SERVO_PWM_HZ = 50;
static const uint8_t SERVO_PWM_BITS = 14;
static const uint32_t MOTOR_PWM_HZ = VCU_MOTOR_PWM_HZ;
static const uint8_t MOTOR_PWM_BITS = 10;

static const uint8_t I2C_ADDR_MPU6050 = 0x68;
static const uint8_t I2C_ADDR_INA260 = 0x40;
static const uint8_t I2C_ADDR_ADS1115 = 0x48;

// INA260 (TI) — integrated 2 milliohm shunt. Fixed scaling: current 1.25 mA/bit, bus 1.25 mV/bit.
// Its continuous rating is about 15 A; CFG_BATTERY_OVERCURRENT_A is set to match the sensor.
static const float INA260_CURRENT_LSB_A = 0.00125f;
static const float INA260_BUS_LSB_V = 0.00125f;

// ADS1115 AIN0 battery divider [PLACEHOLDER]: 10k (top) / 2.7k (bottom) -> 12.6 V => 2.68 V
static const float BATTERY_DIVIDER_RATIO = (10.0f + 2.7f) / 2.7f;

// IMU mounting remap [PLACEHOLDER]: vehicle axis = sign * sensor axis[index]
// Default: sensor X forward, Y left, Z up. Track-mode aero and traction control use these
// axes, so check `status` with the car tilted / turned before driving.
static const int IMU_AXIS_MAP[3] = {0, 1, 2};
static const float IMU_AXIS_SIGN[3] = {1.0f, 1.0f, 1.0f};

// Potentiometer FEEDBACK calibration (rear MG996R only): ADC reading at 0 deg and at 45 deg.
// FW 2.3: the PULSES no longer live here — they are the ServoTrim in g_ctrl.cfg.trim (defaults in
// vcu_calibration.h). The pulse fields below are unused. The feedback ends are captured
// automatically when a rear flap's FLAT / MAX pulse is set live (see the preview code), or by hand
// with `cal fb <3|4> min|max`. NVS keys "sv<i>" (unchanged layout).
static ServoCal g_cal[SERVO_COUNT] = {
    //  angle_min, angle_max, pulse_min, pulse_max (unused), fb_min, fb_max, calibrated
    {-CFG_STEERING_MAX_DEG, CFG_STEERING_MAX_DEG, 1150, 1850, 0, 0, false},  // steering, MG995 (no feedback)
    {CFG_WING_ANGLE_MIN_DEG, CFG_WING_ANGLE_MAX_DEG, 1000, 1500, 0, 0, false},  // LF, SG90 (no feedback)
    {CFG_WING_ANGLE_MIN_DEG, CFG_WING_ANGLE_MAX_DEG, 1000, 1500, 0, 0, false},  // RF, SG90 (no feedback)
    {CFG_WING_ANGLE_MIN_DEG, CFG_WING_ANGLE_MAX_DEG, 1000, 1500, 0, 0, false},  // LR, MG996R + feedback
    {CFG_WING_ANGLE_MIN_DEG, CFG_WING_ANGLE_MAX_DEG, 1000, 1500, 0, 0, false},  // RR, MG996R + feedback
};
static const char *g_trim_source = "vcu_calibration.h";   // where the live servo calibration came from at boot
static bool g_trim_file_differs = false;   // FW 2.3.3: pulses in vcu_calibration.h != the ones saved on the car

// =============================================================================
// SECTION 3: SHARED STATE (guarded by g_mutex; tasks copy in/out)
// =============================================================================
struct SensorData {
  float accel_g[3] = {0, 0, 1};
  float gyro_dps[3] = {0, 0, 0};
  Attitude att;
  uint32_t imu_ms = 0;
  bool imu_ok = false;

  float wheel_kmh[4] = {0, 0, 0, 0};  // FL, FR, RL, RR
  float speed_kmh = 0;               // front (undriven) average when the fronts turn, else rear
  float vib_mg = 0;                  // vibration RMS (VibrationMeter)

  float battery_v = 0, battery_a = 0, ina_bus_v = 0;
  uint32_t power_ms = 0;

  float battery_temp_c = NAN, motor_temp_c = NAN;
  uint32_t temp_ms = 0;
  uint8_t ds_count = 0;

  int32_t fb_raw[SERVO_COUNT] = {0, 0, 0, 0, 0};  // mV (ESP ADC) or ADS1115 counts x0.125 mV
  float fb_deg[SERVO_COUNT] = {NAN, NAN, NAN, NAN, NAN};
};

struct ControlData {
  VcuOutputs out;                    // the last VcuController::step() result
  VcuModeStatus mode{};              // for CAN 0x148
  AeroMode aero_mode = AERO_MODE_AI_ASSIST;
  AiReason last_ai_reason = AI_NONE_RECEIVED;
  uint32_t ai_accepted = 0, ai_rejected = 0;
  uint8_t loop_overruns = 0;
  bool lap_event = false;            // set at a start/finish crossing, cleared by the CAN task
};

struct CommsData {
  DriverCommand driver;
  AiWingRecommendation ai;
  PiHeartbeat hb{0, false, false, 0};
  bool hb_seen = false;
  uint32_t last_rx_ms = 0;
  uint32_t rx_frames = 0, tx_frames = 0, tx_errors = 0, bus_off_count = 0;
  bool estop_request_local = false;
  bool estop_reset_local = false;
};

// Writes and commands for the controller (CAN 0x180 and the console). The control task
// executes them in order and queues the CONFIG_ACK for the CAN task.
struct PendingCmd {
  uint8_t kind;          // CMD_WRITE: key/value · CMD_BENCH: console bench run
  uint8_t key;
  int32_t value;
  bool from_console;
  uint8_t run_type;      // CMD_BENCH
  float duty;
  uint16_t laps;
};
enum : uint8_t { CMD_WRITE = 0, CMD_BENCH = 1 };
struct PendingAck { uint8_t key, status; int32_t value; };

static SemaphoreHandle_t g_mutex;
static SemaphoreHandle_t g_nvs_mutex;        // Preferences: console (loop task) + control task
static SensorData g_sensors;
static ControlData g_control;
static CommsData g_comms;
static Preferences g_prefs;
static VcuController g_ctrl;         // owned by the control task after setup()
static ConfigStore g_cfg;            // read-only mirror of g_ctrl.cfg for the CAN task / console (g_mutex)
static PendingCmd g_cmdq[8];
static uint8_t g_cmdq_n = 0;
static PendingAck g_ackq[8];
static uint8_t g_ackq_n = 0;
static volatile bool g_sweep_on = false;
static volatile uint32_t g_sweep_period_ms = 3000;

// bench servo override (console `servo <id> <deg>` / `pulse <id> <us>`), honoured ONLY in
// IDLE/FAULT with no run. g_bench_raw_us[i] > 0 = raw pulse (calibration jog), else the angle.
static volatile bool g_bench_override = false;
static float g_bench_angle[SERVO_COUNT] = {0, 0, 0, 0, 0};
static uint16_t g_bench_raw_us[SERVO_COUNT] = {0, 0, 0, 0, 0};
// FW 2.3.2: a bench jog no longer holds the servos for ever — turning the knob, or 60 s without a
// new `pulse` / `servo` command, gives them back to the knob.
static volatile uint32_t g_bench_last_ms = 0;
#define VCU_BENCH_IDLE_RELEASE_MS 60000
// FW 2.3: one task at a time on each bus. The e-stop reset re-runs the self-test (I2C init +
// 1-Wire search) in the CONTROL task while the SENSOR task is reading the same buses.
static SemaphoreHandle_t g_i2c_mutex;        // recursive
static SemaphoreHandle_t g_ow_mutex;
static int8_t g_temp_role_battery = 0, g_temp_role_motor = 1;  // DS18B20 index -> role

// software watchdog heartbeats
static volatile uint32_t g_hb_sensor = 0, g_hb_control = 0, g_hb_can = 0;
// FW 2.3.1 diagnostics (`diag`): where the time goes in each task, and what failed. Maxima since
// boot or `diag reset`. Written by one task each, read by the console: plain volatile is enough.
struct DiagData {
  volatile uint32_t sens_loop_max_us, sens_imu_max_us, sens_ina_max_us, sens_adc_max_us, sens_ds_max_us;
  volatile uint32_t sens_gap_max_ms;            // longest time between two sensor-task loops
  volatile uint32_t imu_fail, ina_fail, ds_fail, i2c_restarts;
  volatile uint32_t can_gap_max_ms, ctrl_gap_max_ms;
  volatile uint32_t wd_trips, wd_sensor_age_ms, wd_can_age_ms;   // last trip: which task was silent, how long
  volatile uint32_t sens_stall_section;         // section that was running in the longest sensor loop
};
static DiagData g_diag = {};
static const char *DIAG_SECTIONS[] = {"-", "IMU", "INA260", "ADC", "DS18B20"};

#define LOCK() xSemaphoreTake(g_mutex, portMAX_DELAY)
#define UNLOCK() xSemaphoreGive(g_mutex)
#define I2C_LOCK() xSemaphoreTakeRecursive(g_i2c_mutex, portMAX_DELAY)
#define I2C_UNLOCK() xSemaphoreGiveRecursive(g_i2c_mutex)
#define OW_LOCK() xSemaphoreTake(g_ow_mutex, portMAX_DELAY)
#define OW_UNLOCK() xSemaphoreGive(g_ow_mutex)
// FW 2.2.10: classic ESP32 I2C at 100 kHz (400 kHz gave IMU/INA260 read failures next to the
// servo wiring); the C6 keeps 400 kHz.
#if VCU_MCU_CLASSIC_ESP32
#define VCU_I2C_HZ 100000
#else
#define VCU_I2C_HZ 400000
#endif
// FW 2.3 knob: the PCNT hardware counter on both chips (FW 2.2.8 fix: polling missed clicks).
#if VCU_HAVE_ENCODER && defined(SOC_PCNT_SUPPORTED) && SOC_PCNT_SUPPORTED
#define VCU_KNOB_PCNT 1
#include "driver/pulse_cnt.h"
static pcnt_unit_handle_t g_knob_unit = nullptr;
#else
#define VCU_KNOB_PCNT 0
#endif

// ---------------------------------------------------------------------------------
// ARDUINO IDE NOTE: the IDE auto-generates a prototype for every function in this .ino
// and inserts them all just before the FIRST function definition in the file. Every
// type used in a function signature must therefore be declared ABOVE this point.
// ---------------------------------------------------------------------------------
// True when servo i has a feedback wire that this build can actually read.
static inline bool fbWired(int i) {
  if (!((VCU_FB_WIRED_MASK >> i) & 1)) return false;
  if (FB_ADC_PINS[i] >= 0) return true;  // on-chip ADC
  return VCU_HAVE_ADS1115 != 0;           // ADS1115 channel
}

static bool queueCmd(const PendingCmd &c) {
  bool ok;
  LOCK();
  ok = g_cmdq_n < (sizeof(g_cmdq) / sizeof(g_cmdq[0]));
  if (ok) g_cmdq[g_cmdq_n++] = c;
  UNLOCK();
  return ok;
}

// =============================================================================
// SECTION 4: I2C DRIVERS (register level, no external libraries)
// =============================================================================
static bool i2cWrite8(uint8_t addr, uint8_t reg, uint8_t val) {
  I2C_LOCK();
  Wire.beginTransmission(addr); Wire.write(reg); Wire.write(val);
  bool ok = Wire.endTransmission() == 0;
  I2C_UNLOCK();
  return ok;
}
static bool i2cWrite16(uint8_t addr, uint8_t reg, uint16_t val) {  // big-endian (INA226/ADS1115)
  I2C_LOCK();
  Wire.beginTransmission(addr); Wire.write(reg); Wire.write(val >> 8); Wire.write(val & 0xFF);
  bool ok = Wire.endTransmission() == 0;
  I2C_UNLOCK();
  return ok;
}
static bool i2cRead(uint8_t addr, uint8_t reg, uint8_t *buf, size_t n) {
  I2C_LOCK();
  bool ok = false;
  Wire.beginTransmission(addr); Wire.write(reg);
  if (Wire.endTransmission(false) == 0 && Wire.requestFrom(addr, (uint8_t)n) == n) {
    for (size_t i = 0; i < n; ++i) buf[i] = Wire.read();
    ok = true;
  }
  I2C_UNLOCK();
  return ok;
}
/* FW 2.3: a bus that keeps failing (SDA held low by a glitch, servo ground noise) is restarted
 * instead of reporting stale data forever. */
static void i2cRestartBus() {
  I2C_LOCK();
  Wire.end();
  delay(2);
  Wire.begin(PIN_I2C_SDA, PIN_I2C_SCL, VCU_I2C_HZ);
  Wire.setTimeOut(5);
  I2C_UNLOCK();
}
static bool i2cRead16(uint8_t addr, uint8_t reg, uint16_t &out) {
  uint8_t b[2];
  if (!i2cRead(addr, reg, b, 2)) return false;
  out = (uint16_t)(b[0] << 8 | b[1]);
  return true;
}

// ---- MPU6050 -----------------------------------------------------------------
static float g_gyro_bias[3] = {0, 0, 0};

static bool mpu6050Init() {
  uint8_t who = 0;
  if (!i2cRead(I2C_ADDR_MPU6050, 0x75, &who, 1)) return false;
  // Genuine = 0x68. Common clones report 0x70/0x72/0x98 — accepted with a warning.
  if (who != 0x68) Serial.printf("[MPU6050] WHO_AM_I=0x%02X (clone?) — continuing\n", who);
  bool ok = i2cWrite8(I2C_ADDR_MPU6050, 0x6B, 0x01);  // wake, PLL on gyro X
  delay(50);
  ok &= i2cWrite8(I2C_ADDR_MPU6050, 0x1A, 0x03);      // DLPF ~44 Hz
  ok &= i2cWrite8(I2C_ADDR_MPU6050, 0x19, 0x04);      // 200 Hz sample rate
  ok &= i2cWrite8(I2C_ADDR_MPU6050, 0x1B, 0x08);      // gyro +/-500 dps
  ok &= i2cWrite8(I2C_ADDR_MPU6050, 0x1C, 0x08);      // accel +/-4 g
  return ok;
}

// FW 2.3.8: mounting rotation (sensor frame -> car frame: x forward, y left, z up). Set with the console
// commands `imu level` (car flat) and `imu nose` (nose lifted), stored in NVS "imurot". Identity = as mounted.
static bool g_cap100_pending = false;   // FW 2.3.11: cap migration to be saved once NVS is writable
static float g_imu_R[3][3] = {{1, 0, 0}, {0, 1, 0}, {0, 0, 1}};
static void imuRotate(float v[3]) {
  float r[3];
  for (int i = 0; i < 3; ++i) r[i] = g_imu_R[i][0] * v[0] + g_imu_R[i][1] * v[1] + g_imu_R[i][2] * v[2];
  memcpy(v, r, sizeof(r));
}

static GyroStillTrim g_gyro_trim;                // FW 2.3.13: zero-rate trim learned while the car is still (sensor task only)
static volatile bool g_yaw_zero = false;         // FW 2.3.13: `imu zero` -> heading back to 0
static volatile bool g_att_reset = false;     // FW 2.3.9: restart the attitude filter (and yaw) after a mounting change
/* d = rotation taking the unit vector (x,y,z) onto +z (Rodrigues, axis (y,-x,0)). */
static void imuLevelMatrix(float x, float y, float z, float d[3][3]) {
  const float id[3][3] = {{1, 0, 0}, {0, 1, 0}, {0, 0, 1}};
  memcpy(d, id, sizeof(id));
  const float s2 = x * x + y * y;
  if (s2 < 1e-6f) { if (z < 0) { d[1][1] = -1; d[2][2] = -1; } return; }
  const float k = (1.0f - z) / s2;
  d[0][0] = 1 - k * x * x; d[0][1] = -k * x * y;    d[0][2] = -x;
  d[1][0] = -k * x * y;    d[1][1] = 1 - k * y * y; d[1][2] = -y;
  d[2][0] = x;             d[2][1] = y;             d[2][2] = z;
}
static void imuApply(const float d[3][3]) {       // R = d * R, saved
  float m[3][3];
  for (int i = 0; i < 3; ++i) for (int j = 0; j < 3; ++j) m[i][j] = d[i][0] * g_imu_R[0][j] + d[i][1] * g_imu_R[1][j] + d[i][2] * g_imu_R[2][j];
  LOCK(); memcpy(g_imu_R, m, sizeof(m)); UNLOCK();
  // FW 2.3.11: open the NVS namespace for the write. Before, it was written while closed, so it was
  // lost at the next reset (and the boot auto-level then redid only the "level" half).
  g_prefs.begin("vcu", false);
  const size_t wrote = g_prefs.putBytes("imurot", g_imu_R, sizeof(g_imu_R));
  g_prefs.end();
  if (wrote != sizeof(g_imu_R)) Serial.println("[IMU] WARNING: could not save the IMU mounting to flash");
  g_att_reset = true;
}

static bool mpu6050Read(float accel_g[3], float gyro_dps[3]) {
  uint8_t b[14];
  if (!i2cRead(I2C_ADDR_MPU6050, 0x3B, b, 14)) return false;
  float a[3], g[3];
  for (int i = 0; i < 3; ++i) {
    a[i] = (int16_t)(b[2 * i] << 8 | b[2 * i + 1]) / 8192.0f;
    g[i] = (int16_t)(b[8 + 2 * i] << 8 | b[9 + 2 * i]) / 65.5f - g_gyro_bias[i];
  }
  for (int i = 0; i < 3; ++i) {
    accel_g[i] = IMU_AXIS_SIGN[i] * a[IMU_AXIS_MAP[i]];
    gyro_dps[i] = IMU_AXIS_SIGN[i] * g[IMU_AXIS_MAP[i]];
  }
  imuRotate(accel_g); imuRotate(gyro_dps);
  return true;
}

/* FW 2.3.13: raw gyro in the SENSOR frame (deg/s), no bias, no axis remap, no mounting rotation. */
static bool mpu6050ReadRawGyro(float g[3]) {
  uint8_t b[6];
  if (!i2cRead(I2C_ADDR_MPU6050, 0x43, b, 6)) return false;
  for (int i = 0; i < 3; ++i) g[i] = (int16_t)(b[2 * i] << 8 | b[2 * i + 1]) / 65.5f;
  return true;
}

/* Car must be stationary. Returns false if it moved (std-dev too high).
 * FW 2.3.13: measured in the sensor's own frame. Before, it was measured AFTER the axis remap and the `imu level/nose`
 * rotation but mapped back only through the remap, so with a rotated (e.g. vertical) IMU the yaw axis got another
 * axis's offset and yaw drifted steadily. */
static bool mpu6050CalibrateGyro(int samples = 300) {
  double sum[3] = {0, 0, 0}, sq[3] = {0, 0, 0};
  float g[3];
  int n = 0;
  for (int i = 0; i < samples; ++i) {
    if (mpu6050ReadRawGyro(g)) {
      for (int k = 0; k < 3; ++k) { sum[k] += g[k]; sq[k] += (double)g[k] * g[k]; }
      ++n;
    }
    delay(5);
    esp_task_wdt_reset();
  }
  if (n < samples / 2) return false;
  for (int k = 0; k < 3; ++k) {
    double mean = sum[k] / n, var = sq[k] / n - mean * mean;
    if (var > 4.0) return false;  // >2 dps std-dev: the car moved, keep the old bias
  }
  for (int k = 0; k < 3; ++k) g_gyro_bias[k] = (float)(sum[k] / n);   // SENSOR frame; mpu6050Read subtracts before remap
  return true;
}

// ---- INA260 ------------------------------------------------------------------
static bool ina260Init() {
  uint16_t mfg = 0;
  if (!i2cRead16(I2C_ADDR_INA260, 0xFE, mfg) || mfg != 0x5449) return false;  // TI
  uint16_t die;
  if (!i2cRead16(I2C_ADDR_INA260, 0xFF, die) || (die >> 4) != 0x227) return false;  // INA260 = 0x227x
  // avg 16 samples, 1.1 ms bus and shunt conversion, continuous both
  bool ok = i2cWrite16(I2C_ADDR_INA260, 0x00, 0x6527);
  return ok;
}
static bool ina260Read(float &bus_v, float &current_a) {
  uint16_t vb, ic;
  // INA260 register map: 0x01 current, 0x02 bus voltage (the INA226 put the
  // shunt voltage at 0x01 and current at 0x04 — different chip, different map).
  if (!i2cRead16(I2C_ADDR_INA260, 0x02, vb) || !i2cRead16(I2C_ADDR_INA260, 0x01, ic)) return false;
  bus_v = vb * INA260_BUS_LSB_V;
  current_a = (int16_t)ic * INA260_CURRENT_LSB_A;
  return true;
}

// ---- ADS1115 (single-shot, alternating channels) --------------------------------
[[maybe_unused]] static bool ads1115Start(uint8_t channel) {
  uint16_t cfg = 0x8000 | ((0x04 | (channel & 0x03)) << 12) | (0x01 << 9) | 0x0100 | (0x07 << 5) | 0x03;
  return i2cWrite16(I2C_ADDR_ADS1115, 0x01, cfg);  // OS, MUX AINx-GND, +/-4.096 V, single, 860 SPS
}
[[maybe_unused]] static bool ads1115ReadMv(float &mv) {
  uint16_t raw;
  if (!i2cRead16(I2C_ADDR_ADS1115, 0x00, raw)) return false;
  mv = (int16_t)raw * 0.125f;
  return true;
}
[[maybe_unused]] static bool ads1115Present() {
  uint16_t cfg;
  return i2cRead16(I2C_ADDR_ADS1115, 0x01, cfg);
}

// =============================================================================
// SECTION 5: DS18B20 on bit-banged 1-Wire (ROM search, multi-sensor)
// FIX (bug list #5, restored in FW 2.2): the pin is configured ONCE as OUTPUT_OPEN_DRAIN in
// setup(); the timing slots only write LOW (pull) / HIGH (release) and read. pinMode()
// inside a slot takes tens of microseconds on the ESP32 and corrupted the read slots.
// =============================================================================
static portMUX_TYPE g_ow_mux = portMUX_INITIALIZER_UNLOCKED;
static uint8_t g_ds_rom[4][8];
static uint8_t g_ds_count = 0;

static bool owReset() {
  bool presence;
  digitalWrite(PIN_ONEWIRE, LOW);
  delayMicroseconds(480);
  portENTER_CRITICAL(&g_ow_mux);
  digitalWrite(PIN_ONEWIRE, HIGH);                 // release: the 4.7k pull-up takes the bus high
  delayMicroseconds(70);
  presence = digitalRead(PIN_ONEWIRE) == LOW;
  portEXIT_CRITICAL(&g_ow_mux);
  delayMicroseconds(410);
  return presence;
}
static void owWriteBit(bool bit) {
  portENTER_CRITICAL(&g_ow_mux);
  digitalWrite(PIN_ONEWIRE, LOW);
  delayMicroseconds(bit ? 6 : 60);
  digitalWrite(PIN_ONEWIRE, HIGH);
  portEXIT_CRITICAL(&g_ow_mux);
  delayMicroseconds(bit ? 64 : 10);
}
static bool owReadBit() {
  bool b;
  portENTER_CRITICAL(&g_ow_mux);
  digitalWrite(PIN_ONEWIRE, LOW);
  delayMicroseconds(3);
  digitalWrite(PIN_ONEWIRE, HIGH);
  delayMicroseconds(10);
  b = digitalRead(PIN_ONEWIRE);
  portEXIT_CRITICAL(&g_ow_mux);
  delayMicroseconds(53);
  return b;
}
static void owWriteByte(uint8_t v) { for (int i = 0; i < 8; ++i) owWriteBit((v >> i) & 1); }
static uint8_t owReadByte() { uint8_t v = 0; for (int i = 0; i < 8; ++i) if (owReadBit()) v |= 1 << i; return v; }
static uint8_t owCrc8(const uint8_t *d, int n) {
  uint8_t crc = 0;
  for (int i = 0; i < n; ++i) {
    uint8_t in = d[i];
    for (int b = 0; b < 8; ++b) { uint8_t mix = (crc ^ in) & 1; crc >>= 1; if (mix) crc ^= 0x8C; in >>= 1; }
  }
  return crc;
}
/* Maxim application note 187 ROM search. */
static uint8_t owSearchAll() {
  uint8_t rom[8] = {0};
  int last_discrepancy = -1;
  bool last_device = false;
  g_ds_count = 0;
  while (!last_device && g_ds_count < 4) {
    if (!owReset()) break;
    owWriteByte(0xF0);
    int last_zero = -1;
    for (int bit = 0; bit < 64; ++bit) {
      bool b = owReadBit(), cb = owReadBit();
      bool dir;
      if (b && cb) return g_ds_count;  // no devices responding
      if (b != cb) dir = b;
      else {
        if (bit < last_discrepancy) dir = (rom[bit / 8] >> (bit % 8)) & 1;
        else dir = (bit == last_discrepancy);
        if (!dir) last_zero = bit;
      }
      if (dir) rom[bit / 8] |= 1 << (bit % 8); else rom[bit / 8] &= ~(1 << (bit % 8));
      owWriteBit(dir);
    }
    last_discrepancy = last_zero;
    if (last_discrepancy < 0) last_device = true;
    if (owCrc8(rom, 7) == rom[7] && rom[0] == 0x28) memcpy(g_ds_rom[g_ds_count++], rom, 8);
  }
  return g_ds_count;
}
static uint8_t owSearchAllLocked() { OW_LOCK(); uint8_t n = owSearchAll(); OW_UNLOCK(); return n; }
static void ds18b20StartConversion() { OW_LOCK(); if (owReset()) { owWriteByte(0xCC); owWriteByte(0x44); } OW_UNLOCK(); }
static bool ds18b20Read(uint8_t idx, float &temp_c) {
  OW_LOCK();
  if (idx >= g_ds_count || !owReset()) { OW_UNLOCK(); return false; }
  owWriteByte(0x55);
  for (int i = 0; i < 8; ++i) owWriteByte(g_ds_rom[idx][i]);
  owWriteByte(0xBE);
  uint8_t sp[9];
  for (int i = 0; i < 9; ++i) sp[i] = owReadByte();
  OW_UNLOCK();
  if (owCrc8(sp, 8) != sp[8]) return false;
  int16_t raw = (int16_t)(sp[1] << 8 | sp[0]);
  temp_c = raw / 16.0f;
  return temp_c > -55.0f && temp_c < 125.0f && raw != 0x0550;  // 0x0550 = 85 C power-on value
}

// =============================================================================
// SECTION 6: WHEEL SPEED (ISR period measurement) + ENCODER KNOB
//   prototype : A3144 hall modules, FALLING edge, 2 ms debounce
//   PCB rev B : TLE4941 -> LM393 comparators, RISING edge, 0.2 ms debounce
// =============================================================================
static const int WHEEL_PINS[4] = {PIN_WHEEL_FL, PIN_WHEEL_FR, PIN_WHEEL_RL, PIN_WHEEL_RR};
static volatile uint32_t g_hall_last_us[4] = {0, 0, 0, 0};
static volatile uint32_t g_hall_period_us[4] = {0, 0, 0, 0};
static volatile uint32_t g_hall_count[4] = {0, 0, 0, 0};   // pulses, for odometry

static void IRAM_ATTR hallIsrCommon(int i) {
  uint32_t now = micros();
  uint32_t dt = now - g_hall_last_us[i];
  if (dt < CFG_HALL_DEBOUNCE_US) return;
  if (g_hall_last_us[i] != 0) g_hall_period_us[i] = dt;
  g_hall_last_us[i] = now;
  g_hall_count[i] = g_hall_count[i] + 1;  // (volatile ++ is deprecated in C++20)
}
[[maybe_unused]] static void IRAM_ATTR hallIsr0() { hallIsrCommon(0); }
[[maybe_unused]] static void IRAM_ATTR hallIsr1() { hallIsrCommon(1); }
static void IRAM_ATTR hallIsr2() { hallIsrCommon(2); }
static void IRAM_ATTR hallIsr3() { hallIsrCommon(3); }

static float hallSpeedKmh(int i) {
  noInterrupts();
  uint32_t last = g_hall_last_us[i], period = g_hall_period_us[i];
  interrupts();
  uint32_t since = micros() - last;
  if (last == 0 || since > CFG_WHEEL_STOP_TIMEOUT_MS * 1000UL) return 0.0f;
  if (since > period) period = since;  // decelerating: use the longer, still-open interval
  return wheelSpeedKmhFromPeriod(period, CFG_WHEEL_DIAMETER_M, CFG_WHEEL_PULSES_PER_REV);
}

// KY-040 knob = STEERING WHEEL (FW 2.3). Counted in HARDWARE by the PCNT peripheral on both chips
// (FW 2.2.8 fix: a polled / low-priority read missed the middle state of a click and counted 0).
// Full quadrature (both edges of both lines) = 4 counts per electrical cycle; this KY-040 gives
// VCU_CAL_KNOB_COUNTS_PER_CLICK (= 2) counts per detent. Contact bounce is a +1/-1 pair and
// cancels. Plain inputs only — on the C6 module GPIO22/23 are also the TCA9554's I2C lines.
// Classic ESP32 stand-in: GPIO34/35 (input-only, the KY-040's own 10k pull-ups). A floating pin
// cannot storm an interrupt here (FW 2.2.4 problem): PCNT raises no interrupt at all.
#if !VCU_KNOB_PCNT
static volatile int32_t g_enc_quarters = 0;     // ISR fallback only (no PCNT on the chip)
static volatile uint8_t g_enc_state = 0;
static void IRAM_ATTR encoderIsr() {
  static const int8_t TABLE[16] = {0, -1, 1, 0, 1, 0, 0, -1, -1, 0, 0, 1, 0, 1, -1, 0};
  uint8_t s = (uint8_t)((digitalRead(PIN_ENC_CLK) << 1) | digitalRead(PIN_ENC_DT));
  g_enc_state = (uint8_t)(((g_enc_state << 2) | s) & 0x0F);
  g_enc_quarters = g_enc_quarters + TABLE[g_enc_state];
}
#endif
#if VCU_KNOB_PCNT
static bool knobPcntBegin() {
  pcnt_unit_config_t ucfg = {};
  ucfg.low_limit = -30000; ucfg.high_limit = 30000;
  if (pcnt_new_unit(&ucfg, &g_knob_unit) != ESP_OK) return false;
  pcnt_glitch_filter_config_t fcfg = {};
  fcfg.max_glitch_ns = 5000;                      // a hand-turned knob's edges are ms apart
  pcnt_unit_set_glitch_filter(g_knob_unit, &fcfg);
  pcnt_chan_config_t ca = {}; ca.edge_gpio_num = PIN_ENC_CLK; ca.level_gpio_num = PIN_ENC_DT;
  pcnt_chan_config_t cb = {}; cb.edge_gpio_num = PIN_ENC_DT; cb.level_gpio_num = PIN_ENC_CLK;
  pcnt_channel_handle_t a = nullptr, b = nullptr;
  if (pcnt_new_channel(g_knob_unit, &ca, &a) != ESP_OK || pcnt_new_channel(g_knob_unit, &cb, &b) != ESP_OK) return false;
  pcnt_channel_set_edge_action(a, PCNT_CHANNEL_EDGE_ACTION_DECREASE, PCNT_CHANNEL_EDGE_ACTION_INCREASE);
  pcnt_channel_set_level_action(a, PCNT_CHANNEL_LEVEL_ACTION_KEEP, PCNT_CHANNEL_LEVEL_ACTION_INVERSE);
  pcnt_channel_set_edge_action(b, PCNT_CHANNEL_EDGE_ACTION_INCREASE, PCNT_CHANNEL_EDGE_ACTION_DECREASE);
  pcnt_channel_set_level_action(b, PCNT_CHANNEL_LEVEL_ACTION_KEEP, PCNT_CHANNEL_LEVEL_ACTION_INVERSE);
  return pcnt_unit_enable(g_knob_unit) == ESP_OK && pcnt_unit_clear_count(g_knob_unit) == ESP_OK &&
         pcnt_unit_start(g_knob_unit) == ESP_OK;
}
#endif
/* Total quadrature counts since boot (never wraps in practice: the PCNT unit is cleared and
 * folded into a 32-bit total long before its +/-30000 limit). Control task only. */
static int32_t knobCounts() {
#if VCU_KNOB_PCNT
  static int32_t base = 0;
  if (!g_knob_unit) return 0;
  int c = 0;
  pcnt_unit_get_count(g_knob_unit, &c);
  if (c > 20000 || c < -20000) { base += c; pcnt_unit_clear_count(g_knob_unit); c = 0; }
  return base + c;
#else
  noInterrupts(); int32_t q = g_enc_quarters; interrupts();
  return q;
#endif
}

// =============================================================================
// SECTION 7: ACTUATOR OUTPUTS
// =============================================================================
static void servoWriteUs(int idx, uint16_t us) {
  uint32_t duty = (uint32_t)us * ((1UL << SERVO_PWM_BITS) - 1) / (1000000UL / SERVO_PWM_HZ);
#if !VCU_CAL_STEER_SERVO_ENABLED
  if (idx == SERVO_STEERING) duty = 0;             // FW 2.3.2: steering servo output OFF (vcu_calibration.h)
#endif
  ledcWrite(SERVO_PINS[idx], duty);
}
/* FW 2.3: angle -> pulse through the live calibration (vcu_calibration.h / keys 30-45).
 * idx 0 = steering (deg, + = LEFT), 1..4 = flap FL, FR, RL, RR (0..45 deg). */
static uint16_t servoPulseFor(const ServoTrim &t, int idx, float deg) {
  return idx == SERVO_STEERING ? steerPulseUs(t, deg) : flapPulseUs(t, idx - 1, deg);
}
static void servoWriteDeg(const ServoTrim &t, int idx, float deg) { servoWriteUs(idx, servoPulseFor(t, idx, deg)); }

/* Forward-only motor PWM. Prototype L298N: 0 = bridges disabled (coast).
 * PCB rev B BTN8982: 0 = low sides on (brake); the kill switch removes the supply. */
static void motorApply(float duty01) {
  ledcWrite(PIN_MOTOR_PWM, (uint32_t)(clampf(duty01, 0, 1) * ((1UL << MOTOR_PWM_BITS) - 1)));
}
/* PCB rev B: BTS7004 kill switch. HIGH = 12 V to both half-bridges. */
static void motorPowerApply(bool on) {
  if (PIN_MOTOR_EN >= 0) digitalWrite(PIN_MOTOR_EN, on ? HIGH : LOW);
}

// =============================================================================
// SECTION 8: CAN TRANSPORT (TWAI native controller + optional WiFi-UDP bench)
// =============================================================================
static bool g_twai_ok = false;

static bool canTransportInit() {
#if VCU_TRANSPORT_TWAI
  twai_general_config_t g = TWAI_GENERAL_CONFIG_DEFAULT((gpio_num_t)PIN_CAN_TX, (gpio_num_t)PIN_CAN_RX, TWAI_MODE_NORMAL);
  g.rx_queue_len = 32;
  g.tx_queue_len = 32;
  twai_timing_config_t t = TWAI_TIMING_CONFIG_500KBITS();  // must match Pi: ip link ... bitrate 500000
  twai_filter_config_t f = TWAI_FILTER_CONFIG_ACCEPT_ALL();
  g_twai_ok = twai_driver_install(&g, &t, &f) == ESP_OK && twai_start() == ESP_OK;
  Serial.printf("[CAN] TWAI 500 kbit/s TX=%d RX=%d : %s\n", PIN_CAN_TX, PIN_CAN_RX, g_twai_ok ? "started" : "FAILED");
#endif
#if VCU_TRANSPORT_WIFI_UDP
  WiFi.mode(WIFI_STA);
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  Serial.printf("[CAN] WiFi-UDP bench transport -> %s:%u\n", PI_IP, PI_PORT);
#endif
  return true;
}

#if VCU_TRANSPORT_WIFI_UDP
static WiFiUDP g_udp;
static bool g_udp_started = false;
#endif

static void canSend(const CanFrame &f) {
#if VCU_TRANSPORT_TWAI
  if (g_twai_ok) {
    twai_message_t m = {};
    m.identifier = f.id;
    m.data_length_code = f.dlc;
    memcpy(m.data, f.data, f.dlc);
    if (twai_transmit(&m, 0) == ESP_OK) g_comms.tx_frames++; else g_comms.tx_errors++;
  }
#endif
#if VCU_TRANSPORT_WIFI_UDP
  if (WiFi.status() == WL_CONNECTED) {
    uint8_t pkt[11] = {(uint8_t)(f.id & 0xFF), (uint8_t)(f.id >> 8), f.dlc};
    memcpy(&pkt[3], f.data, 8);
    g_udp.beginPacket(PI_IP, PI_PORT); g_udp.write(pkt, sizeof(pkt)); g_udp.endPacket();
  }
#endif
}

/* Non-blocking receive of one frame. */
static bool canReceive(CanFrame &f, uint32_t wait_ms) {
#if VCU_TRANSPORT_TWAI
  if (g_twai_ok) {
    twai_message_t m;
    if (twai_receive(&m, pdMS_TO_TICKS(wait_ms)) == ESP_OK && !m.extd && !m.rtr) {
      f.id = (uint16_t)m.identifier; f.dlc = m.data_length_code > 8 ? 8 : m.data_length_code;
      memset(f.data, 0, 8); memcpy(f.data, m.data, f.dlc);
      return true;
    }
  } else {
    vTaskDelay(pdMS_TO_TICKS(wait_ms));
  }
#else
  vTaskDelay(pdMS_TO_TICKS(wait_ms));
#endif
#if VCU_TRANSPORT_WIFI_UDP
  if (WiFi.status() == WL_CONNECTED) {
    if (!g_udp_started) { g_udp.begin(ESP32_LISTEN_PORT); g_udp_started = true; }
    if (g_udp.parsePacket() >= 3) {
      uint8_t pkt[11] = {0};
      int n = g_udp.read(pkt, sizeof(pkt));
      if (n >= 3) {
        f.id = pkt[0] | (pkt[1] << 8); f.dlc = pkt[2] > 8 ? 8 : pkt[2];
        memset(f.data, 0, 8); memcpy(f.data, &pkt[3], n - 3 < f.dlc ? n - 3 : f.dlc);
        return true;
      }
    }
  }
#endif
  return false;
}

static void canBusMaintenance() {
#if VCU_TRANSPORT_TWAI
  if (!g_twai_ok) return;
  twai_status_info_t s;
  if (twai_get_status_info(&s) != ESP_OK) return;
  if (s.state == TWAI_STATE_BUS_OFF) { g_comms.bus_off_count++; twai_initiate_recovery(); }
  else if (s.state == TWAI_STATE_STOPPED) twai_start();
#endif
}

// =============================================================================
// SECTION 9: CALIBRATION + SETTINGS STORAGE (NVS)
// =============================================================================
// Servo calibration NVS keys "sv<i>" since the 2026-10-01 servo set. FW 2.2 adds the drive
// mode ("dmode"), the dashboard-run lap count ("rlaps") and the traction-control target ("tcslip").
static void calLoad(ConfigStore &cfg) {
  g_prefs.begin("vcu", true);
  bool old_cal = false;
  for (int i = 0; i < SERVO_COUNT; ++i) {
    char key[8]; snprintf(key, sizeof(key), "sv%d", i);
    if (g_prefs.getBytesLength(key) == sizeof(ServoCal)) g_prefs.getBytes(key, &g_cal[i], sizeof(ServoCal));
    char old[8]; snprintf(old, sizeof(old), "cal%d", i);
    if (g_prefs.getBytesLength(old) > 0 && g_prefs.getBytesLength(key) == 0) old_cal = true;
  }
  if (old_cal)
    Serial.println("[CAL] old Stage 2c feedback calibration found and IGNORED (servo set changed).");
  // FW 2.3.3 servo calibration: saved PULSES always win (a re-flash never moves a servo past the end
  // points set on the car); the flap law / knob follow vcu_calibration.h whenever it is edited.
  {
    const ServoTrim defaults = servoTrimDefaults();
    ServoTrim saved{};
    const bool have = g_prefs.getBytesLength("trim") == sizeof(ServoTrim) && g_prefs.getBytes("trim", &saved, sizeof(saved)) == sizeof(saved);
    const uint32_t law_fp = g_prefs.getUInt("trimlaw", 0);
    bool law_file = false;
    if (!have) {
      cfg.trim = defaults; g_trim_source = "vcu_calibration.h (nothing saved on the car yet)";
    } else if (!servoTrimValid(saved)) {
      cfg.trim = defaults; g_trim_source = "vcu_calibration.h (saved values out of range: ignored)";
    } else {
      cfg.trim = servoTrimBoot(saved, defaults, law_fp, law_file);
      if (!servoTrimValid(cfg.trim)) { cfg.trim = saved; law_file = false; }
      g_trim_source = law_file ? "pulses saved on the car; flap law + knob from vcu_calibration.h (file edited)"
                               : "saved on the car (`cal save` / dashboard)";
      g_trim_file_differs = !servoTrimPulsesEqual(saved, defaults);
    }
  }
  if (g_prefs.getBytesLength("imurot") == sizeof(g_imu_R)) g_prefs.getBytes("imurot", g_imu_R, sizeof(g_imu_R));
  g_temp_role_battery = g_prefs.getChar("t_batt", 0);
  g_temp_role_motor = g_prefs.getChar("t_motor", 1);
  if (g_prefs.getBytesLength("tun2d") == sizeof(Tunables)) {
    g_prefs.getBytes("tun2d", &cfg.active, sizeof(Tunables));
    // FW 2.3.7: the old 85 % default cap saved by an earlier `cal save` is lifted to 100 % once.
    // A cap set on purpose to any other value is kept.
    if (!g_prefs.getBool("cap100", false)) {
      if (cfg.active.duty_cap_permille == 850) {
        cfg.active.duty_cap_permille = 1000;
        Serial.println("[CFG] motor duty cap: old 85 % default -> 100 % (FW 2.3.7)");
      }
      g_cap100_pending = true;          // FW 2.3.11: written after this read-only section closes
    }
    cfg.staged = cfg.active;
  }
  uint8_t tid = g_prefs.getUChar("track", TRACK_DEFAULT_ID);
  cfg.snap.track_id = tid < TRACK_COUNT ? tid : TRACK_DEFAULT_ID;
  int32_t ll = g_prefs.getInt("laplen", cfg.snap.lap_length_cm);
  if (ll >= 500 && ll <= 100000) cfg.snap.lap_length_cm = ll;
  cfg.dyno_vmax_ckmh = g_prefs.getInt("dyn_vmax", 0);
  cfg.dyno_start_permille = g_prefs.getInt("dyn_start", 0);
  uint8_t dm = g_prefs.getUChar("dmode", CFG_DRIVE_MODE_DEFAULT);
  cfg.snap.drive_mode = dm <= DRIVE_MODE_TRACK ? dm : CFG_DRIVE_MODE_DEFAULT;
  uint8_t rl = g_prefs.getUChar("rlaps", 0);
  cfg.snap.run_laps = rl <= 99 ? rl : 0;
  int32_t tc = g_prefs.getInt("tcslip", CFG_TC_SLIP_DEFAULT_PERMILLE);
  cfg.snap.tc_slip_permille = (tc == 0 || (tc >= 50 && tc <= 500)) ? tc : CFG_TC_SLIP_DEFAULT_PERMILLE;
  cfg.snap.track_run = false;  // never persisted: every power-up starts with the driver in control
  g_prefs.end();
  if (g_cap100_pending) {               // FW 2.3.11: the migration used to be "saved" read-only, i.e. never
    g_prefs.begin("vcu", false);
    g_prefs.putBool("cap100", true);
    g_prefs.putBytes("tun2d", &cfg.active, sizeof(Tunables));
    g_prefs.end();
    g_cap100_pending = false;
  }
}
/* Saves from a COPY of the store (the console reads the g_cfg mirror). */
static void calSave(const ConfigStore &c) {
  xSemaphoreTake(g_nvs_mutex, portMAX_DELAY);
  g_prefs.begin("vcu", false);
  for (int i = 0; i < SERVO_COUNT; ++i) {
    char key[8]; snprintf(key, sizeof(key), "sv%d", i);
    g_prefs.putBytes(key, &g_cal[i], sizeof(ServoCal));
  }
  g_prefs.putChar("t_batt", g_temp_role_battery);
  g_prefs.putChar("t_motor", g_temp_role_motor);
  g_prefs.putBytes("tun2d", &c.active, sizeof(Tunables));
  g_prefs.putUChar("track", c.snap.track_id);
  g_prefs.putInt("laplen", c.snap.lap_length_cm);
  g_prefs.putInt("dyn_vmax", c.dyno_vmax_ckmh);
  g_prefs.putInt("dyn_start", c.dyno_start_permille);
  g_prefs.putUChar("dmode", c.snap.drive_mode);
  g_prefs.putUChar("rlaps", c.snap.run_laps);
  g_prefs.putInt("tcslip", c.snap.tc_slip_permille);
  g_prefs.putBytes("trim", &c.trim, sizeof(ServoTrim));                       // FW 2.3
  g_prefs.putUInt("trimlaw", servoTrimLawFingerprint(servoTrimDefaults()));   // FW 2.3.3
  g_prefs.end();
  xSemaphoreGive(g_nvs_mutex);
}
static void calSaveFromMirror() { ConfigStore c; LOCK(); c = g_cfg; UNLOCK(); calSave(c); }

// =============================================================================
// SECTION 10: SELF TEST
// =============================================================================
enum SelfTestBit : uint32_t {
  ST_MPU6050 = 1 << 0, ST_INA260 = 1 << 1, ST_ADS1115 = 1 << 2, ST_DS18B20 = 1 << 3,
  ST_GYRO_CAL = 1 << 4, ST_BATTERY = 1 << 5, ST_SERVO_FB = 1 << 6, ST_CAN = 1 << 7,
};
static void printSelfTest(uint32_t m) {
  static const char *N[8] = {"MPU6050", "INA260", "ADS1115", "DS18B20", "gyro-cal (car moved?)", "supply voltage",
                             "rear-flap feedback wire", "CAN"};
  if (!m) { Serial.println("[VCU] self-test PASS"); return; }
  Serial.print("[VCU] self-test WARNING (the car still runs; FW 2.3 no longer parks it in FAULT):");
  for (int i = 0; i < 8; ++i) if (m & (1u << i)) Serial.printf(" [%s]", N[i]);
  Serial.println();
}

static uint32_t runSelfTest() {
  uint32_t fail = 0;
#if VCU_SENSORS_REAL
  if (!mpu6050Init()) fail |= ST_MPU6050;
  else if (!mpu6050CalibrateGyro()) fail |= ST_GYRO_CAL;
#if VCU_HAVE_INA260
  if (!ina260Init()) fail |= ST_INA260;
#endif
  if (owSearchAllLocked() == 0) fail |= ST_DS18B20;
#if VCU_HAVE_ADS1115
  if (!ads1115Present()) fail |= ST_ADS1115;
  float mv = 0;
  if (!(fail & ST_ADS1115) && ads1115Start(0)) {
    delay(3);
    if (!ads1115ReadMv(mv)) fail |= ST_ADS1115;
    float v = mv / 1000.0f * BATTERY_DIVIDER_RATIO;
    if (v < CFG_CELL_UNDERVOLT_V * CFG_BATTERY_CELLS || v > CFG_BATTERY_VOLT_MAX_VALID) fail |= ST_BATTERY;
  }
#elif VCU_HAVE_INA260
  // No ADS1115: pack voltage from the INA260 (first averaged conversion needs ~35 ms).
  if (fail & ST_INA260) {
    fail |= ST_BATTERY;
  } else {
    delay(80);
    float bv = 0, ia = 0;
    if (!ina260Read(bv, ia) || bv < CFG_CELL_UNDERVOLT_V * CFG_BATTERY_CELLS || bv > CFG_BATTERY_VOLT_MAX_VALID)
      fail |= ST_BATTERY;
    Serial.printf("[SELFTEST] pack voltage (INA260 VBUS) = %.2f V\n", bv);
  }
#endif
  for (int i = 0; i < SERVO_COUNT; ++i) {  // an unplugged feedback wire reads ~0 mV
    if (!fbWired(i)) continue;              // not fitted in this build — see VCU_FB_WIRED_MASK
    float v_mv;
    if (FB_ADC_PINS[i] >= 0) {
      v_mv = (float)analogReadMilliVolts(FB_ADC_PINS[i]);
    } else if (!(fail & ST_ADS1115) && ads1115Start(FB_ADS_CH[i])) {
      delay(3);
      if (!ads1115ReadMv(v_mv)) { fail |= ST_ADS1115; continue; }
    } else {
      continue;  // no ADS1115: its channels cannot be checked here
    }
    if (v_mv < 50 || v_mv > 3250) fail |= ST_SERVO_FB;
  }
#endif
#if VCU_TRANSPORT_TWAI
  if (!g_twai_ok) fail |= ST_CAN;
#endif
  return fail;
}

// =============================================================================
// SECTION 11: RTOS TASKS
// =============================================================================
static void watchdogAddCurrentTask() { esp_task_wdt_add(NULL); }
/* FW 2.2.5 fix (bug #20): a 10 ms loop that fell behind (a long self-test, an I2C timeout)
 * must NOT "catch up" back-to-back without ever blocking — that starved the lower-priority
 * tasks and the task watchdog rebooted the MCU. If the deadline has passed, block one tick and
 * restart the schedule from now. */
static void loopWait(TickType_t &last, TickType_t period) {
  if (xTaskDelayUntil(&last, period) == pdFALSE) { vTaskDelay(1); last = xTaskGetTickCount(); }
}

// ---- Sensor task: 100 Hz -----------------------------------------------------
static void sensorTask(void *) {
  watchdogAddCurrentTask();
  TickType_t last = xTaskGetTickCount();
  uint32_t tick = 0;
#if VCU_HAVE_ADS1115
  uint8_t ads_slot = 0;
  bool ads_pending = false;
  uint8_t ads_channel = 1;
#endif
  uint32_t last_temp_start = 0;
  bool temp_converting = false;
  uint32_t imu_fail_run = 0, i2c_restarts = 0;
  VibrationMeter vib;
  uint32_t prev_loop_ms = millis();
  for (;;) {
    loopWait(last, pdMS_TO_TICKS(10));
    esp_task_wdt_reset();
    g_hb_sensor = millis();
    uint32_t now = millis();
    ++tick;
    { uint32_t gap = now - prev_loop_ms; prev_loop_ms = now; if (tick > 300 && gap > g_diag.sens_gap_max_ms) g_diag.sens_gap_max_ms = gap; }
    const uint32_t t_loop = micros();
    uint32_t t_sec = t_loop, d_imu = 0, d_ina = 0, d_adc = 0, d_ds = 0;

    SensorData s;
    LOCK(); s = g_sensors; VehicleState st = g_control.out.state; UNLOCK();
    bool booting = st < VS_IDLE;

#if VCU_SENSORS_REAL
    float a[3], g[3];
    if (!booting && mpu6050Read(a, g)) {
#if VCU_CAL_IMU_AUTO_LEVEL
      {   // FW 2.3.9: board clearly not flat and nothing saved -> level it once (average of 0.5 s)
        static int n_lvl = 0; static float acc[3] = {0, 0, 0}; static bool done = false;
        if (!done && now > 3000) {
          for (int i = 0; i < 3; ++i) acc[i] += a[i];
          if (++n_lvl >= 50) {
            done = true;
            const float nn = sqrtf(acc[0] * acc[0] + acc[1] * acc[1] + acc[2] * acc[2]);
            if (g_prefs.getBytesLength("imurot") == 0 && nn > 40.0f && nn < 60.0f && acc[2] / nn < 0.85f) {
              float d[3][3]; imuLevelMatrix(acc[0] / nn, acc[1] / nn, acc[2] / nn, d); imuApply(d);
              Serial.println("[IMU] board is not mounted flat: levelled automatically (as `imu level`). Lift the nose and type `imu nose` to set forward.");
            }
          }
        }
      }
#endif
      if (g_att_reset) { g_att_reset = false; s.att = decltype(s.att)(); g_gyro_trim.reset(); }   // new mounting: relearn
      if (g_yaw_zero) { g_yaw_zero = false; s.att.yaw = 0; }
      // FW 2.3.13: learn the zero rate whenever the car is still, and do not let yaw creep while it is
      static float yaw_w0 = 0;
      if (g_gyro_trim.window_starting()) yaw_w0 = s.att.yaw;
      const int still = g_gyro_trim.feed(g, a);
      g_gyro_trim.apply(g);
      memcpy(s.accel_g, a, sizeof(a)); memcpy(s.gyro_dps, g, sizeof(g));
      s.att.update(a[0], a[1], a[2], g[0], g[1], g[2], 0.01f);
      if (still == 2) s.att.yaw = yaw_w0;
      s.imu_ms = now; s.imu_ok = true;
      s.vib_mg = vib.update(a[0], a[1], a[2]);
      imu_fail_run = 0;
    } else if (!booting && (g_diag.imu_fail = g_diag.imu_fail + 1, ++imu_fail_run >= 50)) {   // 0.5 s of failed IMU reads: restart the bus
      imu_fail_run = 0; ++i2c_restarts; g_diag.i2c_restarts = i2c_restarts;
      i2cRestartBus();
      mpu6050Init();
#if VCU_HAVE_INA260
      ina260Init();
#endif
      if (i2c_restarts <= 5 || i2c_restarts % 20 == 0)
        Serial.printf("[I2C] bus restarted (%lu) — IMU reads kept failing. Check SDA/SCL wiring and grounds near the servos.\n",
                      (unsigned long)i2c_restarts);
    }
    d_imu = micros() - t_sec; t_sec = micros();
    for (int i = 0; i < 4; ++i) s.wheel_kmh[i] = hallSpeedKmh(i);
#if VCU_CAL_SPEED_FROM_DUTY
    {   // FW 2.3.4: rear speed ESTIMATED from the applied duty (VcuController's DutySpeedModel)
      float v; LOCK(); v = g_control.out.model_speed_kmh; UNLOCK();
      if (!finite(v)) v = 0;
      s.wheel_kmh[0] = s.wheel_kmh[1] = 0; s.wheel_kmh[2] = s.wheel_kmh[3] = v; s.speed_kmh = v;
#if VCU_CAL_SPEED_SIM_NOISE
      // FW 2.3.5: SIMULATED sensor noise (see vcu_calibration.h). Per rear wheel: slow jitter + rare spikes.
      static float jit[2] = {0, 0}; static uint32_t next_spike[2] = {0, 0}, spike_end[2] = {0, 0}; static float spike_k[2] = {1, 1};
      const uint32_t t_ms = millis();
      auto rnd = [](float a, float b) { return a + (b - a) * (esp_random() / 4294967295.0f); };
      float sum = 0;
      for (int w = 0; w < 2; ++w) {
        jit[w] += (rnd(-1.0f, 1.0f) * (VCU_CAL_SPEED_JITTER_PCT / 100.0f) * 1.6f - jit[w]) * 0.25f;
        jit[w] = constrain(jit[w], -VCU_CAL_SPEED_JITTER_PCT / 100.0f, VCU_CAL_SPEED_JITTER_PCT / 100.0f);
        if (!next_spike[w]) next_spike[w] = t_ms + (uint32_t)(rnd(VCU_CAL_SPEED_SPIKE_MIN_S, VCU_CAL_SPEED_SPIKE_MAX_S) * 1000);
        if ((int32_t)(t_ms - next_spike[w]) >= 0) {
          spike_k[w] = rnd(1.5f, 3.5f); spike_end[w] = t_ms + VCU_CAL_SPEED_SPIKE_MS;
          next_spike[w] = t_ms + (uint32_t)(rnd(VCU_CAL_SPEED_SPIKE_MIN_S, VCU_CAL_SPEED_SPIKE_MAX_S) * 1000);
        }
        const bool spiking = (int32_t)(spike_end[w] - t_ms) > 0;
        const float base = v > 0.3f ? v * (1.0f + jit[w]) : 0.0f;
        s.wheel_kmh[2 + w] = spiking && v > 0.3f ? base * spike_k[w] : base;
        sum += s.wheel_kmh[2 + w];
      }
      s.speed_kmh = sum * 0.5f;        // FW 2.3.6: vehicle speed = mean of the two (a wheel spike shows here too)
#endif
    }
#else
    {
      // Vehicle speed: the undriven FRONT pair is the ground-speed reference (no wheelspin in
      // it). On a dyno whose rollers do not turn the fronts, they read 0: use the rear pair.
      float front = (s.wheel_kmh[0] + s.wheel_kmh[1]) * 0.5f, rear = (s.wheel_kmh[2] + s.wheel_kmh[3]) * 0.5f;
      s.speed_kmh = front >= 0.3f ? front : rear;
    }
#endif

    if (!booting) {
      if (VCU_HAVE_INA260 && tick % 5 == 0) {
        float bv, ia;
        if (ina260Read(bv, ia)) {
          s.ina_bus_v = bv; s.battery_a = ia;
#if !VCU_HAVE_ADS1115
          s.battery_v = bv; s.power_ms = now;  // no ADS1115: INA260 VBUS is the supply voltage
#endif
        } else {
          g_diag.ina_fail = g_diag.ina_fail + 1;
        }
      }
      d_ina = micros() - t_sec; t_sec = micros();
#if VCU_HAVE_ADS1115
      static const uint8_t ADS_SEQUENCE[5] = {1, 2, 1, 3, 0};
      float mv;
      if (ads_pending && ads1115ReadMv(mv)) {
        switch (ads_channel) {
          case 0: s.battery_v = mv / 1000.0f * BATTERY_DIVIDER_RATIO; s.power_ms = now; break;
          case 1: s.fb_raw[SERVO_STEERING] = (int32_t)mv; break;
          case 2: s.fb_raw[SERVO_LF] = (int32_t)mv; break;
          case 3: s.fb_raw[SERVO_RF] = (int32_t)mv; break;
          default: break;
        }
      }
      ads_channel = ADS_SEQUENCE[ads_slot++ % 5];
      ads_pending = ads1115Start(ads_channel);
#endif
    }
    for (int i = 0; i < SERVO_COUNT; ++i) {
      if (FB_ADC_PINS[i] < 0 || !fbWired(i)) continue;  // ADS1115 channel, or no wire fitted
      int32_t acc = 0;
      for (int k = 0; k < 4; ++k) acc += analogReadMilliVolts(FB_ADC_PINS[i]);
      s.fb_raw[i] = acc / 4;
    }
    d_adc = micros() - t_sec; t_sec = micros();
    // DS18B20: start conversion, read 800 ms later, repeat every 1 s
    if (!booting && g_ds_count > 0) {
      if (!temp_converting && now - last_temp_start >= 1000) {
        ds18b20StartConversion(); last_temp_start = now; temp_converting = true;
      } else if (temp_converting && now - last_temp_start >= 800) {
        float t;
        bool any = false;
        if (ds18b20Read(g_temp_role_battery, t)) { s.battery_temp_c = t; any = true; }
        if (ds18b20Read(g_temp_role_motor, t)) { s.motor_temp_c = t; any = true; }
        if (any) s.temp_ms = now; else g_diag.ds_fail = g_diag.ds_fail + 1;
        temp_converting = false;
      }
    }
    s.ds_count = g_ds_count;
    d_ds = micros() - t_sec;
    {
      const uint32_t d_loop = micros() - t_loop;
      if (d_imu > g_diag.sens_imu_max_us) g_diag.sens_imu_max_us = d_imu;
      if (d_ina > g_diag.sens_ina_max_us) g_diag.sens_ina_max_us = d_ina;
      if (d_adc > g_diag.sens_adc_max_us) g_diag.sens_adc_max_us = d_adc;
      if (d_ds > g_diag.sens_ds_max_us) g_diag.sens_ds_max_us = d_ds;
      if (d_loop > g_diag.sens_loop_max_us) {
        g_diag.sens_loop_max_us = d_loop;
        const uint32_t m = std::max(std::max(d_imu, d_ina), std::max(d_adc, d_ds));
        g_diag.sens_stall_section = m == d_imu ? 1 : m == d_ina ? 2 : m == d_adc ? 3 : 4;
      }
    }
#else
    float t = now / 1000.0f;  // SAMPLE MODE — same waveform as raspberry_pi/main.py
    float v = std::max(0.0f, 20.0f + 15.0f * std::sin(t / 6.0f));
    for (int i = 0; i < 4; ++i) s.wheel_kmh[i] = v;
    s.speed_kmh = v;
    s.accel_g[0] = 0.05f * std::sin(t / 2.0f); s.accel_g[1] = 0.4f * std::sin(t / 3.0f); s.accel_g[2] = 1.0f;
    s.gyro_dps[2] = 12.0f * std::sin(t / 3.0f);
    s.att.roll = 1.5f * std::sin(t / 4.0f); s.att.pitch = 0.8f * std::sin(t / 5.0f); s.att.yaw = fmod(t * 3.0f, 360.0f);
    s.battery_v = 12.6f - 0.2f * std::fabs(std::sin(t / 20.0f));
    s.battery_a = 3.0f + 2.0f * std::fabs(std::sin(t / 4.0f));
    s.battery_temp_c = 28.0f + 2.0f * std::sin(t / 30.0f);
    s.motor_temp_c = 32.0f + 3.0f * std::sin(t / 25.0f);
    s.imu_ms = s.power_ms = s.temp_ms = now; s.imu_ok = true; s.ds_count = 2;
#endif
    for (int i = 0; i < SERVO_COUNT; ++i)
      s.fb_deg[i] = fbWired(i) ? servoFeedbackToAngle(g_cal[i], s.fb_raw[i]) : NAN;

    LOCK(); g_sensors = s; UNLOCK();
  }
}

// ---- Control task: 100 Hz — inputs -> VcuController::step() -> pins -----------------
static const char *runName(uint8_t r) {
  static const char *RUNS[] = {"off", "track-drive", "bench-laps", "bench-motor", "dyno-cal", "demo sequence"};
  return r <= RUN_DEMO ? RUNS[r] : "?";
}

static void reportEvents(const VcuOutputs &o) {
  if (!o.events) return;
  if (o.events & EV_RUN_STARTED) {
    if (g_ctrl.bench_type() == RUN_BENCH_MOTOR)
      Serial.printf("[MOTOR] running at %.0f %% duty%s\n", o.manual_duty * 100, o.remote_run ? " (dashboard; stops if the ground link is lost)" : " (console; auto-stop after 120 s)");
    else Serial.printf("[RUN] started: %s%s\n", runName(g_ctrl.bench_type()), o.remote_run ? " (dashboard)" : "");
  }
  if (o.events & EV_MOTOR_DUTY) Serial.printf("[MOTOR] duty -> %.0f %%\n", o.manual_duty * 100);
  if (o.events & EV_DRS_OPEN) Serial.printf("[DRS] OPEN: all flaps FLAT for %d ms\n", VCU_CAL_DRS_OPEN_MS);
  if (o.events & EV_DRS_HOLD) Serial.printf("[DRS] closed: all flaps MAX for %d ms\n", VCU_CAL_DRS_HOLD_MS);
  if (o.events & EV_DRS_END) Serial.println("[DRS] off: flaps follow the steering again");
  if (o.events & EV_CAL_REVERTED) Serial.println("[CAL] servo calibration back to vcu_calibration.h (not saved yet: `cal save` to keep it)");
  if (o.events & EV_RUN_STOPPED) Serial.println("[RUN] stopped");
  if (o.events & EV_RUN_ABORT_FAULT) Serial.println("[RUN] stopped (state / fault / e-stop / ARM)");
  if (o.events & EV_RUN_ABORT_LINK) Serial.println("[RUN] stopped: ground link lost (a dashboard run needs the link as its deadman)");
  if (o.events & EV_RUN_MOTOR_TIMEOUT) Serial.println("[MOTOR] console motor auto-stop after 120 s");
  if (o.events & EV_RUN_LAPS_DONE) Serial.println("[RUN] lap count reached");
  if (o.events & EV_STALL) Serial.println("[MOTOR] STALL: duty applied but rear wheels not turning -> motor locked out");
  if (o.events & EV_TRACK_START) {
    ConfigStore c; LOCK(); c = g_cfg; UNLOCK();
    Serial.printf("[TRACK] %s: %s, %.1f m per lap\n", o.run_state == RUN_BENCH_LAPS ? "circuit run" : "track run in DRIVE",
                  TRACKS[c.snap.track_id].name, c.snap.lap_length_cm / 100.0f);
  }
  if (o.events & EV_LAP)
    Serial.printf("[TRACK] lap %u: %.2f s%s\n", g_ctrl.lap_count(), g_ctrl.last_lap_ms() / 1000.0f,
                  g_ctrl.take_lap_committed() ? "  (staged settings applied)" : "");
  if (o.events & EV_DYNO_DONE) {
    ConfigStore c; LOCK(); c = g_cfg; UNLOCK();
    Serial.printf("[DYNO] start duty %.0f %%, rear speed at %.0f %% duty = %.2f km/h (%.2f m/s). Saved.\n",
                  o.dyno_start_duty * 100, c.active.duty_cap_permille / 10.0f, o.dyno_vmax_kmh, o.dyno_vmax_kmh / 3.6f);
    Serial.printf("[DYNO] suggested duty floor: %.0f permille (`set 18 <v>`)\n", (o.dyno_start_duty + 0.05f) * 1000);
  }
  if (o.events & EV_DYNO_FAILED) Serial.println("[DYNO] calibration FAILED (wheels never turned, or a fault/timeout)");
  if (o.events & EV_MODE_CHANGED) {
    ConfigStore c; LOCK(); c = g_cfg; UNLOCK();
    Serial.printf("[MODE] %s mode%s\n", driveModeName(c.snap.drive_mode),
                  c.snap.drive_mode == DRIVE_MODE_DYNO ? " — car ON THE ROLLERS: runs may be started from the console and the dashboard"
                                                       : " — car on the floor: only a driver holding ARM can move it");
  }
}

static void controlTask(void *) {
  watchdogAddCurrentTask();
  uint32_t prev_counts[4] = {0, 0, 0, 0};
  bool counts_init = false;
  int32_t prev_knob = 0;
  uint32_t sweep_last_ms = 0;
  bool sweep_phase = false;
  int8_t fb_captured_for = -1;                       // preview whose rear-flap feedback was already taken

  // Boot sequence: BOOT -> INIT -> SELF_TEST -> IDLE (FW 2.3: a failed item is only a warning)
  uint32_t mask = runSelfTest();
  printSelfTest(mask);
  g_ctrl.boot(mask);
  LOCK(); g_control.out.state = g_ctrl.state(); g_cfg = g_ctrl.cfg; UNLOCK();
  prev_knob = knobCounts();
  // The schedule starts AFTER the (slow) self-test — see loopWait() / bug #20.
  TickType_t last = xTaskGetTickCount();
  uint32_t prev_us = micros();
  uint32_t ctrl_iter = 0;

  for (;;) {
    loopWait(last, pdMS_TO_TICKS(10));
    esp_task_wdt_reset();
    uint32_t now = millis();
    { uint32_t gap = now - g_hb_control; if (++ctrl_iter > 300 && gap > g_diag.ctrl_gap_max_ms) g_diag.ctrl_gap_max_ms = gap; }
    g_hb_control = now;
    uint32_t now_us = micros();
    float dt = (now_us - prev_us) / 1e6f;
    prev_us = now_us;
    if (dt > 0.02f) { LOCK(); if (g_control.loop_overruns < 255) g_control.loop_overruns++; UNLOCK(); }

    // --- writes / commands queued by the CAN task and the console ---
    PendingCmd cmds[8]; uint8_t n_cmd;
    SensorData s; CommsData c;
    LOCK();
    n_cmd = g_cmdq_n; memcpy(cmds, g_cmdq, sizeof(PendingCmd) * n_cmd); g_cmdq_n = 0;
    s = g_sensors; c = g_comms;
    g_comms.estop_request_local = false; g_comms.estop_reset_local = false;
    UNLOCK();
    for (uint8_t i = 0; i < n_cmd; ++i) {
      const PendingCmd &k = cmds[i];
      int32_t out = k.value; ConfigStatus r;
      if (k.kind == CMD_BENCH) {
        r = g_ctrl.request_run(k.run_type, k.duty, k.laps, false, now);
        if (k.run_type != RUN_OFF && r != CFG_APPLIED)
          Serial.printf("refused: circuit runs / dyno cal need DYNO mode (`mode dyno`), IDLE and no fault (state=%s, mode=%s)\n",
                        stateName(g_ctrl.state()), driveModeName(g_ctrl.cfg.snap.drive_mode));
        continue;   // console bench runs are not CONFIG_WRITEs: no ACK
      }
      r = g_ctrl.write(k.key, k.value, k.from_console, now, out);
      // pulse keys print the live value so the console doubles as a calibration jig
      Serial.printf("[CFG] %s key %u = %ld -> %s\n", k.from_console ? "console" : "CAN", k.key, (long)k.value,
                    configStatusName(r));
      if (k.key == CFG_KEY_MOTOR_CMD && r == CFG_REJ_STATE)
        Serial.printf("      motor refused: needs IDLE (now %s), no FAULT, no circuit run%s\n", stateName(g_ctrl.state()),
                      k.from_console ? "" : ", and the ground link (dashboard = no deadman at the car)");
      LOCK();
      if (g_ackq_n < sizeof(g_ackq) / sizeof(g_ackq[0])) g_ackq[g_ackq_n++] = {k.key, (uint8_t)r, out};
      UNLOCK();
    }

    // --- odometry from the rear (driven) wheel pulses ---
    uint32_t counts[4];
    noInterrupts(); for (int i = 0; i < 4; ++i) counts[i] = g_hall_count[i]; interrupts();
    float dist_m = 0;
    uint32_t rear_pulses = 0;
    if (counts_init) {
      uint32_t d_rl = counts[2] - prev_counts[2], d_rr = counts[3] - prev_counts[3];
      dist_m = (d_rl + d_rr) * 0.5f * (3.14159265f * CFG_WHEEL_DIAMETER_M) / CFG_WHEEL_PULSES_PER_REV;
      rear_pulses = d_rl + d_rr;
    }
    memcpy(prev_counts, counts, sizeof(counts)); counts_init = true;
    // --- knob: whole clicks since the last step (the remainder waits for the next step) ---
    int32_t detents = 0;
#if VCU_HAVE_ENCODER
    {
      const int32_t knob = knobCounts();
      detents = (knob - prev_knob) / VCU_CAL_KNOB_COUNTS_PER_CLICK;
      prev_knob += detents * VCU_CAL_KNOB_COUNTS_PER_CLICK;
    }
#endif

#if VCU_CAL_KNOB_BUTTON_DRS && VCU_HAVE_ENCODER
    {   // FW 2.3.4: knob PRESS = DRS. Debounced: low for 30 ms = one press; must be released before the next.
      static uint32_t low_since = 0; static bool fired = false;
      if (digitalRead(PIN_KNOB_SW) == LOW) {
        if (!low_since) low_since = now ? now : 1;
        else if (!fired && now - low_since >= 30) { fired = true; Serial.println("[KNOB] pressed -> DRS"); consoleConfigWrite(CFG_KEY_DRS_CMD, 1); }
      } else { low_since = 0; fired = false; }
    }
#endif

    // --- inputs (every age clamped: the timestamps are written by OTHER tasks, bug #22) ---
    VcuInputs in;
    in.now_ms = now; in.dt_s = dt;
    memcpy(in.wheel_kmh, s.wheel_kmh, sizeof(in.wheel_kmh)); in.speed_kmh = s.speed_kmh;
    memcpy(in.accel_g, s.accel_g, sizeof(in.accel_g)); memcpy(in.gyro_dps, s.gyro_dps, sizeof(in.gyro_dps));
    in.battery_v = s.battery_v; in.battery_a = s.battery_a;
    in.battery_temp_c = s.battery_temp_c; in.motor_temp_c = s.motor_temp_c;
    in.imu_age_ms = s.imu_ms ? ageMs(now, s.imu_ms) : UINT32_MAX;
    in.power_age_ms = s.power_ms ? ageMs(now, s.power_ms) : UINT32_MAX;
    in.temp_age_ms = s.temp_ms ? ageMs(now, s.temp_ms) : (s.ds_count ? UINT32_MAX : 0);
    in.power_sensor_present = VCU_HAVE_INA260 || VCU_HAVE_ADS1115;
    in.temp_sensors_present = s.ds_count > 0;
    memcpy(in.fb_deg, s.fb_deg, sizeof(in.fb_deg));
    in.rear_pulses = rear_pulses; in.rear_dist_m = dist_m;
    in.driver = c.driver;
    in.can_link_ok = c.last_rx_ms && ageMs(now, c.last_rx_ms) < CFG_CAN_RX_TIMEOUT_MS;
    in.hb_seen = c.hb_seen; in.hb = c.hb; in.hb_age_ms = c.hb_seen ? ageMs(now, c.hb.rx_ms) : UINT32_MAX;
    in.ai = c.ai;
    {
      const uint32_t sa = ageMs(now, g_hb_sensor), ca = ageMs(now, g_hb_can);
      in.watchdog_ok = sa < CFG_WATCHDOG_TIMEOUT_MS && ca < CFG_WATCHDOG_TIMEOUT_MS;
      static bool wd_was_ok = true;
      if (!in.watchdog_ok && wd_was_ok) {
        g_diag.wd_trips = g_diag.wd_trips + 1; g_diag.wd_sensor_age_ms = sa; g_diag.wd_can_age_ms = ca;
        Serial.printf("[WDOG] %s task silent for %lu ms (trip %lu). `diag` shows where the time went.\n",
                      sa >= CFG_WATCHDOG_TIMEOUT_MS ? "sensor" : "CAN", (unsigned long)(sa >= CFG_WATCHDOG_TIMEOUT_MS ? sa : ca),
                      (unsigned long)g_diag.wd_trips);
      }
      wd_was_ok = in.watchdog_ok;
    }
    in.estop_local = c.estop_request_local; in.estop_reset_local = c.estop_reset_local;
    in.bench_override = g_bench_override;
    in.knob_detents = detents;

    VcuOutputs o = g_ctrl.step(in);
    if (g_bench_override && (detents != 0 || (!g_sweep_on && now - g_bench_last_ms > VCU_BENCH_IDLE_RELEASE_MS))) {
      g_bench_override = false; g_sweep_on = false;
      for (int i = 0; i < SERVO_COUNT; ++i) g_bench_raw_us[i] = 0;
      Serial.println(detents ? "[BENCH] knob turned: servos back on the knob (jog values are not stored — use `cal ...`)"
                             : "[BENCH] 60 s without a jog: servos back on the knob");
    }
    if (o.request_self_test) {                       // after an e-stop reset: INIT -> SELF_TEST
      uint32_t m = runSelfTest();
      printSelfTest(m);
      g_ctrl.self_test_done(m);
      last = xTaskGetTickCount(); prev_us = micros();   // the self-test took ~2 s: restart the schedule
    }
    const ServoTrim &trim = g_ctrl.cfg.trim;

    // --- bench sweep: flaps 7.5 <-> 37.5 deg, steering +/- 15 deg, every N s ---
    VehicleState st = o.state;
    if (g_sweep_on && (st == VS_IDLE || st == VS_FAULT) && !o.lapping) {
      if (now - sweep_last_ms >= g_sweep_period_ms) {
        sweep_last_ms = now; sweep_phase = !sweep_phase;
        g_bench_angle[SERVO_STEERING] = sweep_phase ? 15.0f : -15.0f;
        for (int i = SERVO_LF; i <= SERVO_RR; ++i) { g_bench_angle[i] = sweep_phase ? 37.5f : 7.5f; g_bench_raw_us[i] = 0; }
        g_bench_raw_us[SERVO_STEERING] = 0;
        g_bench_override = true; g_bench_last_ms = now;
      }
    } else if (g_sweep_on && o.lapping) {
      g_sweep_on = false; g_bench_override = false;   // the map owns the servos during a run
    }

    // --- outputs ---
    if (g_bench_override && (st == VS_IDLE || st == VS_FAULT) && !o.lapping) {
      for (int i = 0; i < SERVO_COUNT; ++i)
        servoWriteUs(i, g_bench_raw_us[i] ? g_bench_raw_us[i] : servoPulseFor(trim, i, g_bench_angle[i]));
    } else {
      g_bench_override = false;
      servoWriteDeg(trim, SERVO_STEERING, o.steering_deg);
      for (int i = 0; i < 4; ++i) servoWriteDeg(trim, SERVO_LF + i, o.flaps[i]);
    }
    // calibration preview: the servo whose end-point was just set goes THERE for 2.5 s
    if (o.preview_servo >= 0 && o.preview_servo < SERVO_COUNT) {
      servoWriteUs(o.preview_servo, o.preview_us);
      // rear MG996R: once the horn has settled at FLAT / MAX, take the pot reading as that end
      // of the feedback calibration, so measured flap angles stay right after a re-calibration
      const int i = o.preview_servo;
      const bool is_flat = o.preview_key >= CFG_KEY_FLAP_FL_FLAT_US && ((o.preview_key - CFG_KEY_FLAP_FL_FLAT_US) % 2 == 0);
      if (i >= SERVO_LR && fbWired(i) && o.preview_elapsed_ms >= 1500 && fb_captured_for != (int8_t)o.preview_key) {
        fb_captured_for = (int8_t)o.preview_key;
        if (is_flat) g_cal[i].fb_raw_at_min = s.fb_raw[i]; else g_cal[i].fb_raw_at_max = s.fb_raw[i];
        g_cal[i].angle_min_deg = CFG_WING_ANGLE_MIN_DEG; g_cal[i].angle_max_deg = CFG_WING_ANGLE_MAX_DEG;
        g_cal[i].fb_calibrated = g_cal[i].fb_raw_at_min != 0 && g_cal[i].fb_raw_at_max != 0 &&
                                 abs(g_cal[i].fb_raw_at_max - g_cal[i].fb_raw_at_min) > 100;
        Serial.printf("[CAL] %s feedback at %s = %ld mV (feedback calibrated: %s)\n", SERVO_NAMES[i], is_flat ? "FLAT" : "MAX",
                      (long)s.fb_raw[i], g_cal[i].fb_calibrated ? "yes" : "not yet — set the other end too");
      }
    } else {
      fb_captured_for = -1;
    }
    motorPowerApply(o.motor_power);
    motorApply(o.motor_duty);

    reportEvents(o);
    if (o.events & (EV_DYNO_DONE | EV_MODE_CHANGED | EV_CAL_SAVE)) {
      LOCK(); g_cfg = g_ctrl.cfg; UNLOCK();
      calSaveFromMirror();
      if (o.events & EV_CAL_SAVE) { g_trim_source = "saved on the car (`cal save` / dashboard)"; Serial.println("[CAL] servo calibration saved on the car"); }
    }

    uint32_t prev_flags;
    LOCK();
    prev_flags = g_control.out.fault_flags;
    VehicleState prev_state = g_control.out.state;
    g_control.out = o;
    g_control.mode = g_ctrl.mode_status(o);
    g_control.aero_mode = (AeroMode)g_ctrl.cfg.snap.aero_mode;
    if (o.new_ai_rec) {
      g_control.last_ai_reason = o.ai_reason;
      if (o.ai_applied) g_control.ai_accepted++; else g_control.ai_rejected++;
    }
    if (o.lap_event) g_control.lap_event = true;
    g_cfg = g_ctrl.cfg;
    UNLOCK();

    if (o.fault_flags != prev_flags || o.state != prev_state) canSend(canEncodeFaultEvent(o.fault_flags, o.worst, o.state));
    if (o.state != prev_state) {                      // FW 2.3.1: say WHY the state changed
      Serial.printf("[STATE] %s -> %s  faults:", stateName(prev_state), stateName(o.state));
      printFaultNames(o.fault_flags);
    }
  }
}

// ---- CAN task: RX continuously, TX telemetry on a 10 ms schedule ----------------
static void canTask(void *) {
  watchdogAddCurrentTask();
  AiRecAssembler assembler;
  uint32_t next_tx = millis();
  uint32_t tick = 0;
  for (;;) {
    esp_task_wdt_reset();
    uint32_t now = millis();
    { static uint32_t n = 0; uint32_t gap = now - g_hb_can; if (++n > 3000 && gap > g_diag.can_gap_max_ms) g_diag.can_gap_max_ms = gap; }
    g_hb_can = now;

    CanFrame rx;
    while (canReceive(rx, 2)) {
      now = millis();
      DriverCommand dc; PiHeartbeat hb; AiWingRecommendation rec; uint8_t key; int32_t value;
      bool is_write = false;
      LOCK();
      g_comms.rx_frames++;
      g_comms.last_rx_ms = now;
      if (canDecodeDriverCommand(rx, now, dc)) g_comms.driver = dc;
      else if (canDecodePiHeartbeat(rx, now, hb)) { g_comms.hb = hb; g_comms.hb_seen = true; }
      else if (assembler.feed(rx, now, rec)) g_comms.ai = rec;
      else if (canDecodeConfigWrite(rx, key, value)) is_write = true;
      UNLOCK();
      // Every write (tunable OR command) is decided by the controller in the control task,
      // which answers with CONFIG_ACK, so "applied on car" on the dashboard is a fact.
      if (is_write) {
        PendingCmd pc{}; pc.kind = CMD_WRITE; pc.key = key; pc.value = value; pc.from_console = false;
        if (!queueCmd(pc)) canSend(canEncodeConfigAck(key, CFG_REJ_STATE, value));   // queue full: busy
      }
    }

    // ACKs produced by the control task
    PendingAck acks[8]; uint8_t n_ack;
    LOCK(); n_ack = g_ackq_n; memcpy(acks, g_ackq, sizeof(PendingAck) * n_ack); g_ackq_n = 0; UNLOCK();
    for (uint8_t i = 0; i < n_ack; ++i) canSend(canEncodeConfigAck(acks[i].key, acks[i].status, acks[i].value));

    now = millis();
    if ((int32_t)(now - next_tx) < 0) continue;
    next_tx += 10;
    if ((int32_t)(now - next_tx) > 50) next_tx = now + 10;  // don't burst after a stall
    ++tick;

    SensorData s; ControlData ctl; CommsData c; ConfigStore cfg;
    LOCK(); s = g_sensors; ctl = g_control; c = g_comms; cfg = g_cfg;
    bool lap_event = g_control.lap_event; g_control.lap_event = false; UNLOCK();
    const VcuOutputs &o = ctl.out;

    canSend(canEncodeImuAccel(s.accel_g[0], s.accel_g[1], s.accel_g[2]));      // 10 ms
    canSend(canEncodeImuGyro(s.gyro_dps[0], s.gyro_dps[1], s.gyro_dps[2]));
    if (tick % 2 == 0) {                                                        // 20 ms
      canSend(canEncodeVehicleState(o.state, o.fault_flags));
      canSend(canEncodeWheelSpeed(s.wheel_kmh[0], s.wheel_kmh[1], s.wheel_kmh[2], s.wheel_kmh[3]));
      float thr = o.motor_duty > 0 ? o.motor_duty * 100.0f : 0.0f;
      float brk = o.motor_braking ? 100.0f : 0.0f;
      canSend(canEncodeDriverInputs(finite(s.fb_deg[0]) ? s.fb_deg[0] : o.steering_deg, thr, brk));
      canSend(canEncodeImuAttitude(s.att.roll, s.att.pitch, s.att.yaw));
      canSend(canEncodeAero(CAN_ID_AERO_TARGET, o.flaps));
      float meas[4];
      bool any_meas = false;
      for (int i = 0; i < 4; ++i) {
        // Flaps without (calibrated) feedback report their COMMAND here; which channels are real
        // measurements is in 0x171 fb_calibrated_mask.
        bool real = finite(s.fb_deg[1 + i]);
        meas[i] = real ? s.fb_deg[1 + i] : o.flaps[i];
        any_meas |= real;
      }
      if (any_meas) canSend(canEncodeAero(CAN_ID_AERO_MEASURED, meas));
    }
    if (tick % 5 == 0) {                                                        // 50 ms
      canSend(canEncodeBattery(s.battery_v, s.battery_a));
      canSend(canEncodeTrackPosition(o.track_frac, o.lap, cfg.snap.track_id, o.track_flags, o.profile_duty, o.latg_model));
    }
    if (tick % 20 == 0 || lap_event) canSend(canEncodeLapTiming(o.last_lap_ms, o.cur_lap_ms));  // 200 ms
    if (tick % 10 == 0) {                                                       // 100 ms
      // No dedicated motor current sensor: the INA260 supply current is reported.
      canSend(canEncodeTemperature(finite(s.motor_temp_c) ? s.motor_temp_c : 0,
                                   finite(s.battery_temp_c) ? s.battery_temp_c : 0, s.battery_a));
      VcuStatus vs{};
      vs.aero_mode = ctl.aero_mode; vs.last_ai_reason = ctl.last_ai_reason;
      vs.driver_link_ok = driverCommandFresh(c.driver, now);
      for (int i = 0; i < SERVO_COUNT; ++i) if (g_cal[i].fb_calibrated) vs.fb_calibrated_mask |= 1 << i;
      vs.uptime_s = (uint16_t)(now / 1000); vs.loop_overruns = ctl.loop_overruns;
      vs.protocol_version = CAN_PROTOCOL_VERSION;
      canSend(canEncodeVcuStatus(vs));
      canSend(canEncodeVcuMode(ctl.mode));                                      // FW 2.2

      static ThermalEstimator motor_therm, batt_therm;
      uint16_t t_motor = finite(s.motor_temp_c) ? motor_therm.update(s.motor_temp_c, CFG_MOTOR_OVERTEMP_C, now)
                                                : THERMAL_NO_LIMIT;
      uint16_t t_batt = finite(s.battery_temp_c) ? batt_therm.update(s.battery_temp_c, CFG_BATTERY_OVERTEMP_C, now)
                                                 : THERMAL_NO_LIMIT;
      canSend(canEncodeVehicleDerived2(
          slipRatioPermille(s.wheel_kmh[0], s.wheel_kmh[1], s.wheel_kmh[2], s.wheel_kmh[3]), t_motor, t_batt, s.vib_mg));
      canSend(canEncodeDyno(o.odometer_m, s.vib_mg, o.run_state, o.cal_phase));
      // one value per 100 ms, round robin: the ground side always converges on the VCU's real
      // settings even if an ACK was lost on the radio link
      static uint8_t report_idx = 0;
      uint8_t k = CONFIG_REPORT_KEYS[report_idx++ % CONFIG_REPORT_KEY_COUNT];
      int32_t v = 0;
      if (cfg.read(k, v)) canSend(canEncodeConfigReport(k, v, cfg.pending));

      canBusMaintenance();
    }
  }
}

// =============================================================================
// SECTION 12: SERIAL CONSOLE (bench bring-up & calibration)
// =============================================================================
static void printFaultNames(uint32_t f) {
  static const char *N[16] = {"WATCHDOG", "SENSOR_TIMEOUT", "CAN_TIMEOUT", "LORA_TIMEOUT", "UNDERVOLT", "OVERCURRENT",
                              "MOTOR_OVERTEMP", "BATTERY_OVERTEMP", "INVALID_SENSOR", "STEER_POS", "AERO_POS",
                              "AI_REJECTED", "COMM_LOSS", "ESTOP", "SELFTEST", "MOTOR_STALL"};
  if (!f) Serial.print(" none");
  for (int i = 0; i < 16; ++i) if (f & (1u << i)) Serial.printf(" %s", N[i]);
  Serial.println();
}

static void printDiag() {
  SensorData s; LOCK(); s = g_sensors; UNLOCK();
  const uint32_t now = millis();
  Serial.printf("\n[DIAG] ages now: IMU %lu ms (limit %lu), INA260 %lu ms (limit %lu), DS18B20 %lu ms (limit %lu)\n",
                (unsigned long)ageMs(now, s.imu_ms), (unsigned long)CFG_IMU_TIMEOUT_MS, (unsigned long)ageMs(now, s.power_ms),
                (unsigned long)CFG_POWER_TIMEOUT_MS, (unsigned long)ageMs(now, s.temp_ms), (unsigned long)CFG_TEMP_TIMEOUT_MS);
  Serial.printf("[DIAG] sensor task: longest loop %lu us (in %s), longest gap between loops %lu ms (should be ~10)\n",
                (unsigned long)g_diag.sens_loop_max_us, DIAG_SECTIONS[g_diag.sens_stall_section < 5 ? g_diag.sens_stall_section : 0],
                (unsigned long)g_diag.sens_gap_max_ms);
  Serial.printf("[DIAG]   section maxima: IMU %lu us, INA260 %lu us, ADC %lu us, DS18B20 %lu us\n",
                (unsigned long)g_diag.sens_imu_max_us, (unsigned long)g_diag.sens_ina_max_us, (unsigned long)g_diag.sens_adc_max_us,
                (unsigned long)g_diag.sens_ds_max_us);
  Serial.printf("[DIAG]   failures: IMU reads %lu, INA260 reads %lu, DS18B20 reads %lu, I2C bus restarts %lu\n",
                (unsigned long)g_diag.imu_fail, (unsigned long)g_diag.ina_fail, (unsigned long)g_diag.ds_fail,
                (unsigned long)g_diag.i2c_restarts);
  Serial.printf("[DIAG] longest gap between loops: control %lu ms (should be ~10), CAN %lu ms (should be ~2-12)\n",
                (unsigned long)g_diag.ctrl_gap_max_ms, (unsigned long)g_diag.can_gap_max_ms);
  Serial.printf("[DIAG] watchdog trips %lu (last: sensor silent %lu ms, CAN silent %lu ms)\n", (unsigned long)g_diag.wd_trips,
                (unsigned long)g_diag.wd_sensor_age_ms, (unsigned long)g_diag.wd_can_age_ms);
  Serial.println("[DIAG] `diag reset` clears the maxima and counters.");
}

static void printHelp() {
  Serial.println(F(
      "\nVCU console commands (FW 2.3):\n"
      "  status                 state, faults, sensors, actuators, knob, motor\n"
      "  i2cscan                list I2C addresses (expect 0x40 INA260, 0x68 MPU6050)\n"
      "  ds | tempmap <batt> <motor>    list DS18B20s / assign roles (then cal save)\n"
      " Steering + flaps:\n"
      "  knob                   knob position and steering angle (knob = steering wheel)\n"
      "  knob zero              centre the steering\n"
      "  demo 2 [track id]      demo 2: one auto-driven lap of a circuit scaled to under 40 s (ids: track list)\n"
      "  imu level | imu nose | imu flip | imu reset   IMU mounting: car flat -> level; nose lifted -> nose;\n"
      "                         flip = pitch AND roll both read the wrong way round\n"
      "  demo | demo stop       demo corner sequence, 21 s: accelerate, brake, left, right, full speed + DRS, stop\n"
      "  drs | drs off          DRS: flaps FLAT 5 s, MAX 1 s, then follow the steering (also a dashboard button)\n"
      " Servo calibration (vcu_calibration.h = defaults; these change it live):\n"
      "  cal                    show the calibration + a copy to paste into vcu_calibration.h\n"
      "  pulse <id> <us>        BENCH: move servo <id> to a RAW pulse (find the end points). bench off = release\n"
      "                         id 0=STEER MG995  1=FL SG90  2=FR SG90  3=RL MG996R+fb  4=RR MG996R+fb\n"
      "  cal steer left|center|right <us>     steering end points (servo moves there for 2.5 s)\n"
      "  cal flap fl|fr|rl|rr flat|max <us>   flap end points: flat = 0 deg, max = 45 deg (most downforce)\n"
      "  cal flap <fl|fr|rl|rr> swap   swap FLAT/MAX of one flap (it moves the wrong way)\n"
      "  cal lock <deg> | cal neutral <deg> | cal swing <deg> | cal click <deg> | cal reverse 0|1\n"
      "  cal save               keep it on the car (until vcu_calibration.h is edited)\n"
      "  cal reset              back to vcu_calibration.h\n"
      "  servo <id> <deg>       BENCH: move servo to an angle through the calibration (IDLE/FAULT)\n"
      "  sweep on [secs] | sweep off    step servos every N s (default 3)\n"
      "  bench off              release bench override\n"
      " Motor (any drive mode, IDLE, no fault — wheels on the rollers / off the ground):\n"
      "  motor <duty%>          run the rear axle (console: auto-stop 120 s). The dashboard does the same.\n"
      "  motor stop\n"
      " Drive modes / circuit runs:\n"
      "  mode dyno | mode track DYNO = car on the rollers (circuit runs allowed). TRACK = on the floor\n"
      "  tc <permille>          traction-control slip target, 0 = off, 50..500 (track mode, driver in DRIVE)\n"
      "  dyno cal               find start duty + wheel speed at the duty cap (DYNO mode)\n"
      "  track list | track <id>       list circuits / select one\n"
      "  lap len <metres> | lap run [laps] | lap stop    circuit run (DYNO mode)\n"
      "  trackrun on|off        DRIVE follows the map (driver holds ARM + throttle as ceiling)\n"
      " Any mode:\n"
      "  aero <0|1|2>           0=baseline 1=AI-assist (circuit runs) 2=failsafe-hold (flaps flat)\n"
      "  tune | set <key> <value>   all settings / write one exactly as a CAN CONFIG_WRITE\n"
      "  cal fb <3|4> min|max   capture rear-flap pot feedback by hand (automatic when you set flat/max)\n"
      "  estop | reset          local emergency stop / reset request\n"
      "  diag | diag reset      task timing, sensor ages, read failures, watchdog trips (FW 2.3.1)\n"));
}

static void printCalibration() {
  ConfigStore cs; LOCK(); cs = g_cfg; UNLOCK();
  const ServoTrim &t = cs.trim;
  static const char *F[4] = {"FL", "FR", "RL", "RR"};
  Serial.printf("\nServo calibration — source at boot: %s\n", g_trim_source);
  Serial.printf("  steering  left %ld  center %ld  right %ld us   lock %.1f deg\n", (long)t.steer_left_us,
                (long)t.steer_center_us, (long)t.steer_right_us, t.steer_lock_cdeg / 100.0f);
  for (int i = 0; i < 4; ++i)
    Serial.printf("  flap %s   flat %ld  max %ld us%s\n", F[i], (long)t.flap_flat_us[i], (long)t.flap_max_us[i],
                  (i >= 2 && fbWired(i + 1)) ? (g_cal[i + 1].fb_calibrated ? "   (pot feedback calibrated)" : "   (pot feedback NOT calibrated)") : "");
  Serial.printf("  flaps     neutral %.1f deg, swing %.1f deg at full lock (outside up, inside down; 45/45 = straight all MAX, inside to FLAT)\n",
                t.flap_neutral_cdeg / 100.0f, t.flap_swing_cdeg / 100.0f);
  Serial.printf("  knob      %.1f deg per click, %s, %d counts per click (vcu_calibration.h only)\n",
                t.knob_cdeg_per_click / 100.0f, t.knob_reverse ? "REVERSED" : "clockwise = right", VCU_CAL_KNOB_COUNTS_PER_CLICK);
  Serial.println("\n---- paste into vcu_calibration.h to make these the defaults ----");
  Serial.printf("#define VCU_CAL_STEER_LEFT_US       %ld\n#define VCU_CAL_STEER_CENTER_US     %ld\n#define VCU_CAL_STEER_RIGHT_US      %ld\n",
                (long)t.steer_left_us, (long)t.steer_center_us, (long)t.steer_right_us);
  Serial.printf("#define VCU_CAL_STEER_LOCK_DEG      %g\n", t.steer_lock_cdeg / 100.0f);
  for (int i = 0; i < 4; ++i)
    Serial.printf("#define VCU_CAL_FLAP_%s_FLAT_US     %ld\n#define VCU_CAL_FLAP_%s_MAX_US      %ld\n", F[i],
                  (long)t.flap_flat_us[i], F[i], (long)t.flap_max_us[i]);
  Serial.printf("#define VCU_CAL_FLAP_NEUTRAL_DEG    %g\n#define VCU_CAL_FLAP_SWING_DEG      %g\n", t.flap_neutral_cdeg / 100.0f,
                t.flap_swing_cdeg / 100.0f);
  Serial.printf("#define VCU_CAL_KNOB_DEG_PER_CLICK  %g\n#define VCU_CAL_KNOB_REVERSE        %ld\n", t.knob_cdeg_per_click / 100.0f,
                (long)t.knob_reverse);
  Serial.println("------------------------------------------------------------------");
}

static void printStatus() {
  SensorData s; ControlData c; CommsData m; ConfigStore cfg;
  LOCK(); s = g_sensors; c = g_control; m = g_comms; cfg = g_cfg; UNLOCK();
  const VcuOutputs &o = c.out;
  uint32_t now = millis();
  Serial.printf("\nFW %s  hw=%s  MODE=%s  state=%s  faults=0x%04lX worst=%u  selftest=0x%02lX\n", FW_VERSION,
                VCU_HW_NAME, driveModeName(cfg.snap.drive_mode), stateName(o.state), (unsigned long)o.fault_flags,
                o.worst, (unsigned long)g_ctrl.self_test_mask());
  Serial.print("Faults:"); printFaultNames(o.fault_flags);
  Serial.printf("Stack free (bytes): can=%u sensor=%u control=%u   (below ~500 = raise it)\n",
                g_task_can ? (unsigned)uxTaskGetStackHighWaterMark(g_task_can) : 0u,
                g_task_sensor ? (unsigned)uxTaskGetStackHighWaterMark(g_task_sensor) : 0u,
                g_task_control ? (unsigned)uxTaskGetStackHighWaterMark(g_task_control) : 0u);
  Serial.printf("IMU a=[%.2f %.2f %.2f]g g=[%.1f %.1f %.1f]dps roll=%.1f pitch=%.1f yaw=%.1f\n", s.accel_g[0],
                s.accel_g[1], s.accel_g[2], s.gyro_dps[0], s.gyro_dps[1], s.gyro_dps[2], s.att.roll, s.att.pitch, s.att.yaw);
  Serial.printf("Wheels FL=%.2f FR=%.2f RL=%.2f RR=%.2f km/h (speed %.2f) — %s, %u pulse/rev\n", s.wheel_kmh[0],
                s.wheel_kmh[1], s.wheel_kmh[2], s.wheel_kmh[3], s.speed_kmh, VCU_WHEEL_SENSOR_NAME,
                (unsigned)CFG_WHEEL_PULSES_PER_REV);
  Serial.printf("Supply %.2f V / %.2f A (INA260)   Tbatt=%.1f Tmotor=%.1f (%u DS18B20)\n", s.battery_v, s.battery_a,
                s.battery_temp_c, s.motor_temp_c, s.ds_count);
  for (int i = 0; i < SERVO_COUNT; ++i) {
    if (!fbWired(i)) { Serial.printf("  %-5s feedback not wired (open-loop)\n", SERVO_NAMES[i]); continue; }
    Serial.printf("  %-5s raw=%5ld  meas=%6.1f deg  cal=%s\n", SERVO_NAMES[i], (long)s.fb_raw[i], s.fb_deg[i],
                  g_cal[i].fb_calibrated ? "yes" : "NO");
  }
  static const char *SRC[] = {"failsafe", "KNOB", "circuit map", "driver"};
  Serial.printf("Steering %.1f deg (%s, + = left) from %s; knob %ld clicks | flaps FL=%.1f FR=%.1f RL=%.1f RR=%.1f deg\n",
                o.steering_deg, o.steering_deg > 0.05f ? "LEFT" : o.steering_deg < -0.05f ? "RIGHT" : "straight",
                o.steer_source <= STEER_SRC_DRIVER ? SRC[o.steer_source] : "?", (long)o.knob_pos, o.flaps[0], o.flaps[1],
                o.flaps[2], o.flaps[3]);
  Serial.printf("Motor duty=%.2f%s%s%s\n", o.motor_duty, o.manual_duty > 0 ? (o.remote_run ? " (dashboard motor)" : " (console motor)") : "",
                o.motor_braking ? " (brake)" : "", o.motor_derating ? " DERATING (supply current limit, key 21)" : "");
  Serial.printf("Motor: %s, %lu Hz; kill switch %s; TC %s target %ld permille, cut %.0f %%\n", VCU_MOTOR_DRIVER_NAME,
                (unsigned long)MOTOR_PWM_HZ, PIN_MOTOR_EN >= 0 ? (o.motor_power ? "ON" : "off") : "not fitted",
                cfg.snap.tc_slip_permille ? "on" : "OFF", (long)cfg.snap.tc_slip_permille, o.tc_cut * 100);
  Serial.printf("AI mode=%u last=%s accepted=%lu rejected=%lu | CAN rx=%lu tx=%lu txerr=%lu busoff=%lu lastRx=%lums ago\n",
                c.aero_mode, aiReasonName(c.last_ai_reason), (unsigned long)c.ai_accepted, (unsigned long)c.ai_rejected,
                (unsigned long)m.rx_frames, (unsigned long)m.tx_frames, (unsigned long)m.tx_errors,
                (unsigned long)m.bus_off_count, (unsigned long)(m.last_rx_ms ? now - m.last_rx_ms : 0));
  Serial.printf("Run %s%s  track=%s  pos=%.3f lap=%u  cur=%.1fs last=%.1fs best=%.1fs  odo=%.1fm  vib=%.0fmg\n",
                runName(o.run_state), o.remote_run ? " (dashboard)" : "", TRACKS[cfg.snap.track_id].code, o.track_frac,
                o.lap, o.cur_lap_ms / 1000.0f, o.last_lap_ms / 1000.0f, o.best_lap_ms / 1000.0f, o.odometer_m, s.vib_mg);
  Serial.printf("Driver cmd %s steer=%d thr=%d flags=0x%02X | Pi heartbeat %s ground-link=%d backend=%d (%s)\n",
                driverCommandFresh(m.driver, now) ? "FRESH" : "stale", m.driver.steering_permille,
                m.driver.throttle_permille, m.driver.flags, m.hb_seen ? "seen" : "never", m.hb.lora_link_ok,
                m.hb.backend_ok, o.ground_link_ok ? "dashboard runs allowed" : "dashboard runs refused");
#if VCU_TRANSPORT_TWAI
  twai_status_info_t ts;
  if (g_twai_ok && twai_get_status_info(&ts) == ESP_OK) {
    static const char *TS[] = {"STOPPED", "RUNNING", "BUS_OFF", "RECOVERING"};
    Serial.printf("TWAI %s  TEC=%lu REC=%lu  bus_err=%lu arb_lost=%lu tx_failed=%lu txq=%lu   (%s)\n",
                  ts.state <= TWAI_STATE_RECOVERING ? TS[ts.state] : "?", (unsigned long)ts.tx_error_counter,
                  (unsigned long)ts.rx_error_counter, (unsigned long)ts.bus_error_count,
                  (unsigned long)ts.arb_lost_count, (unsigned long)ts.tx_failed_count, (unsigned long)ts.msgs_to_tx,
                  VCU_CAN_TRANSCEIVER_NAME);
    if (ts.tx_error_counter >= 128 && m.rx_frames == 0)
      Serial.println("  -> TEC>=128 and nothing received: no node is ACKing. Check common GND, CANH/CANL, Pi bitrate/oscillator, and which HAT channel is wired.");
  } else {
    Serial.println("TWAI driver not running");
  }
#endif
}

/* Console writes go through exactly the same path as CAN 0x180 (control task + ACK). */
static void consoleConfigWrite(uint8_t key, int32_t value) {
  PendingCmd pc{}; pc.kind = CMD_WRITE; pc.key = key; pc.value = value; pc.from_console = true;
  if (!queueCmd(pc)) Serial.println("busy — try again");
}

static void benchRequest(uint8_t type, float duty, uint16_t laps) {
  PendingCmd pc{}; pc.kind = CMD_BENCH; pc.run_type = type; pc.duty = duty; pc.laps = laps; pc.from_console = true;
  if (!queueCmd(pc)) { Serial.println("busy — try again"); return; }
  if (type == RUN_OFF) Serial.println("run stop requested");
  else if (type == RUN_BENCH_MOTOR) Serial.printf("bench motor %.0f %% (WHEELS ON ROLLERS / OFF GROUND). `motor stop` to end\n", duty * 100);
  else if (type == RUN_BENCH_LAPS) Serial.printf("circuit run%s. `lap stop` or `estop` to end\n", laps ? "" : " (until `lap stop`)");
  else if (type == RUN_DYNO_CAL) Serial.println("dyno calibration: ramps duty until the wheels turn, then holds the cap for 3 s");
}

static void handleCommand(String line) {
  line.trim();
  if (!line.length()) return;
  char cmd[16] = {0}, arg[16] = {0};
  int id = -1; float f1 = 0, f2 = 0;
  sscanf(line.c_str(), "%15s", cmd);
  String c(cmd);
  VehicleState st; bool lapping; LOCK(); st = g_control.out.state; lapping = g_control.out.lapping; UNLOCK();

  if (c == "help") printHelp();
  else if (c == "status") printStatus();
  else if (c == "i2cscan") {
    I2C_LOCK();
    for (uint8_t a = 1; a < 127; ++a) { Wire.beginTransmission(a); if (Wire.endTransmission() == 0) Serial.printf("  0x%02X\n", a); }
    I2C_UNLOCK();
  } else if (c == "ds") {
    for (int i = 0; i < g_ds_count; ++i) {
      Serial.printf("  [%d] ", i);
      for (int k = 0; k < 8; ++k) Serial.printf("%02X", g_ds_rom[i][k]);
      Serial.println(i == g_temp_role_battery ? "  (battery)" : i == g_temp_role_motor ? "  (motor)" : "");
    }
  } else if (c == "tempmap" && sscanf(line.c_str(), "%*s %f %f", &f1, &f2) == 2) {
    g_temp_role_battery = (int8_t)f1; g_temp_role_motor = (int8_t)f2; Serial.println("ok (run `cal save`)");
  } else if (c == "servo" && sscanf(line.c_str(), "%*s %d %f", &id, &f1) == 2 && id >= 0 && id < SERVO_COUNT) {
    if ((st != VS_IDLE && st != VS_FAULT) || lapping) { Serial.println("refused: bench servo moves only in IDLE or FAULT with no run"); return; }
    for (int i = 0; i < SERVO_COUNT; ++i) if (!g_bench_override) { g_bench_angle[i] = (i == 0) ? CFG_STEERING_FAILSAFE_DEG : CFG_WING_FAILSAFE_ANGLE_DEG; g_bench_raw_us[i] = 0; }
    g_bench_angle[id] = f1; g_bench_raw_us[id] = 0; g_bench_override = true; g_bench_last_ms = millis();
    ConfigStore cs; LOCK(); cs = g_cfg; UNLOCK();
    Serial.printf("bench %s -> %.1f deg (%u us)\n", SERVO_NAMES[id], f1, servoPulseFor(cs.trim, id, f1));
  } else if (c == "bench") {
    g_bench_override = false; for (int i = 0; i < SERVO_COUNT; ++i) g_bench_raw_us[i] = 0;
    Serial.println("bench override released");
  } else if (c == "pulse") {
    int n = sscanf(line.c_str(), "%*s %d %f %f", &id, &f1, &f2);
    if (n == 2 && id >= 0 && id < SERVO_COUNT) {               // raw jog: find the end points
      if ((st != VS_IDLE && st != VS_FAULT) || lapping) { Serial.println("refused: bench servo moves only in IDLE or FAULT with no run"); return; }
      if (f1 < 500 || f1 > 2500) { Serial.println("pulse must be 500..2500 us"); return; }
      ConfigStore cs; LOCK(); cs = g_cfg; UNLOCK();
      for (int i = 0; i < SERVO_COUNT; ++i) if (!g_bench_override) { g_bench_angle[i] = 0; g_bench_raw_us[i] = servoPulseFor(cs.trim, i, i == 0 ? 0.0f : cs.trim.flap_neutral_cdeg / 100.0f); }
      g_bench_raw_us[id] = (uint16_t)f1; g_bench_override = true; g_bench_last_ms = millis();
      Serial.printf("bench %s -> raw %u us (NOT stored). Store it with `cal %s ...`.\n"
                    "  The knob is paused while you jog: `bench off`, a knob turn, or 60 s idle gives the servos back.\n",
                    SERVO_NAMES[id], (unsigned)f1, id == 0 ? "steer left|center|right" : "flap <fl|fr|rl|rr> flat|max");
#if !VCU_CAL_STEER_SERVO_ENABLED
      if (id == SERVO_STEERING) Serial.println("  NOTE: steering servo output is OFF (VCU_CAL_STEER_SERVO_ENABLED 0) — nothing is sent to it.");
#endif
    } else if (n == 3 && id >= 0 && id < SERVO_COUNT) {        // FW 2.2 syntax: both ends at once
      if (id == 0) { consoleConfigWrite(CFG_KEY_STEER_LEFT_US, (int32_t)f1); consoleConfigWrite(CFG_KEY_STEER_RIGHT_US, (int32_t)f2); }
      else { consoleConfigWrite(CFG_KEY_FLAP_FL_FLAT_US + 2 * (id - 1), (int32_t)f1); consoleConfigWrite(CFG_KEY_FLAP_FL_MAX_US + 2 * (id - 1), (int32_t)f2); }
      Serial.println("set both ends (steering: left/right, flaps: flat/max). `cal save` to keep.");
    } else Serial.println("usage: pulse <id> <us>   (raw jog)");
  } else if (c == "cal") {
    char a2[16] = {0}, a3[16] = {0};
    int n = sscanf(line.c_str(), "%*s %15s %15s %15s", arg, a2, a3);
    String a(arg), b(a2), d(a3);
    float v = 0;
    if (n <= 0 || a == "show") printCalibration();
    else if (a == "save") consoleConfigWrite(CFG_KEY_CAL_CMD, CAL_CMD_SAVE);
    else if (a == "reset") consoleConfigWrite(CFG_KEY_CAL_CMD, CAL_CMD_REVERT);
    else if (a == "steer" && n == 3 && (v = atof(a3)) > 0) {
      uint8_t k = b == "left" ? CFG_KEY_STEER_LEFT_US : b == "center" || b == "centre" ? CFG_KEY_STEER_CENTER_US : b == "right" ? CFG_KEY_STEER_RIGHT_US : 0;
      if (k) consoleConfigWrite(k, (int32_t)lroundf(v)); else Serial.println("usage: cal steer left|center|right <us>");
    } else if (a == "flap") {
      char a4[16] = {0};
      sscanf(line.c_str(), "%*s %*s %*s %*s %15s", a4);
      int f = b == "fl" ? 0 : b == "fr" ? 1 : b == "rl" ? 2 : b == "rr" ? 3 : -1;
      v = atof(a4);
      if (f >= 0 && d == "swap") {     // FW 2.3.3: a mirror-mounted flap moving the wrong way
        int32_t fl, mx; LOCK(); fl = g_cfg.trim.flap_flat_us[f]; mx = g_cfg.trim.flap_max_us[f]; UNLOCK();
        consoleConfigWrite(CFG_KEY_FLAP_FL_FLAT_US + 2 * f, mx);
        consoleConfigWrite(CFG_KEY_FLAP_FL_MAX_US + 2 * f, fl);
        Serial.printf("flap %s: FLAT now %ld us, MAX now %ld us. Check it, then `cal save`.\n", b.c_str(), (long)mx, (long)fl);
        return;
      }
      if (f < 0 || (d != "flat" && d != "max") || v <= 0) { Serial.println("usage: cal flap fl|fr|rl|rr flat|max <us>   |   cal flap fl|fr|rl|rr swap"); return; }
      consoleConfigWrite((d == "flat" ? CFG_KEY_FLAP_FL_FLAT_US : CFG_KEY_FLAP_FL_MAX_US) + 2 * f, (int32_t)lroundf(v));
    } else if (n >= 2 && (a == "lock" || a == "neutral" || a == "swing" || a == "click")) {
      v = atof(a2);
      uint8_t k = a == "lock" ? CFG_KEY_STEER_LOCK_CDEG : a == "neutral" ? CFG_KEY_FLAP_NEUTRAL_CDEG
                : a == "swing" ? CFG_KEY_FLAP_SWING_CDEG : CFG_KEY_KNOB_CDEG_PER_CLICK;
      consoleConfigWrite(k, (int32_t)lroundf(v * 100.0f));
    } else if (a == "reverse" && n >= 2) {
      consoleConfigWrite(CFG_KEY_KNOB_REVERSE, atoi(a2) ? 1 : 0);
    } else if (a == "clear" && sscanf(line.c_str(), "%*s %*s %d", &id) == 1 && id >= 0 && id < SERVO_COUNT) {
      g_cal[id].fb_calibrated = false; Serial.println("feedback calibration cleared");
    } else if ((a == "fb" && sscanf(line.c_str(), "%*s %*s %d %15s", &id, arg) == 2) ||
               (sscanf(line.c_str(), "%*s %d %15s", &id, arg) == 2)) {   // cal fb <id> min|max  (or FW 2.2: cal <id> min|max)
      if (id < 0 || id >= SERVO_COUNT) { Serial.println("servo id 0..4"); return; }
      if (!fbWired(id)) { Serial.println("only servos 3 (RL) and 4 (RR) have a feedback wire"); return; }
      String which(arg);
      int32_t raw; LOCK(); raw = g_sensors.fb_raw[id]; UNLOCK();
      if (which == "min") g_cal[id].fb_raw_at_min = raw;
      else if (which == "max") g_cal[id].fb_raw_at_max = raw;
      else { Serial.println("use min|max"); return; }
      g_cal[id].angle_min_deg = CFG_WING_ANGLE_MIN_DEG; g_cal[id].angle_max_deg = CFG_WING_ANGLE_MAX_DEG;
      g_cal[id].fb_calibrated = g_cal[id].fb_raw_at_min != 0 && g_cal[id].fb_raw_at_max != 0 &&
                                abs(g_cal[id].fb_raw_at_max - g_cal[id].fb_raw_at_min) > 100;
      Serial.printf("%s fb %s = %ld (calibrated=%d). `cal save` to keep.\n", SERVO_NAMES[id], arg, (long)raw, g_cal[id].fb_calibrated);
    } else Serial.println("usage: cal | cal steer ... | cal flap ... | cal lock|neutral|swing|click <deg> | cal save | cal reset");
  } else if (c == "aero" && sscanf(line.c_str(), "%*s %d", &id) == 1) {
    consoleConfigWrite(CFG_KEY_AERO_MODE, id);
  } else if (c == "mode") {
    sscanf(line.c_str(), "%*s %15s", arg);
    String a(arg);
    if (a == "dyno") consoleConfigWrite(CFG_KEY_DRIVE_MODE, DRIVE_MODE_DYNO);
    else if (a == "track") consoleConfigWrite(CFG_KEY_DRIVE_MODE, DRIVE_MODE_TRACK);
    else { ConfigStore cs; LOCK(); cs = g_cfg; UNLOCK(); Serial.printf("mode %s (use: mode dyno | mode track)\n", driveModeName(cs.snap.drive_mode)); }
  } else if (c == "tc" && sscanf(line.c_str(), "%*s %d", &id) == 1) {
    consoleConfigWrite(CFG_KEY_TC_SLIP_PERMILLE, id);
  } else if (c == "imu") {       // FW 2.3.8: tell the VCU how the IMU is mounted
    sscanf(line.c_str(), "%*s %15s", arg);
    const String a = arg;
    float v[3] = {0, 0, 0};
    for (int k = 0; k < 40; ++k) { SensorData sd; LOCK(); sd = g_sensors; UNLOCK(); for (int i = 0; i < 3; ++i) v[i] += sd.accel_g[i] / 40.0f; delay(12); }
    const float n = sqrtf(v[0] * v[0] + v[1] * v[1] + v[2] * v[2]);
    float d[3][3] = {{1, 0, 0}, {0, 1, 0}, {0, 0, 1}};
    bool apply = false;
    if (a == "zero") {             // FW 2.3.13
      g_yaw_zero = true;
      Serial.printf("Heading (yaw) set to 0. Gyro zero trim [%.2f %.2f %.2f] deg/s, %s (relearned every second the car stands still).\n",
                    g_gyro_trim.trim[0], g_gyro_trim.trim[1], g_gyro_trim.trim[2], g_gyro_trim.learned ? "learned" : "not learned yet");
      return;
    } else if (a == "reset") {
      LOCK(); memcpy(g_imu_R, d, sizeof(d)); UNLOCK(); g_prefs.begin("vcu", false); g_prefs.remove("imurot"); g_prefs.end(); g_att_reset = true;
      Serial.println("IMU mounting: back to as-mounted (no rotation).");
    } else if (a == "level") {
      if (n < 0.8f || n > 1.2f) { Serial.printf("IMU reads %.2f g: keep the car still and try again.\n", n); return; }
      imuLevelMatrix(v[0] / n, v[1] / n, v[2] / n, d);
      apply = true;
      Serial.println("IMU levelled and saved: roll and pitch now read ~0 with the car like this.\n"
                     "Next: lift the NOSE of the car about a hand-width, hold it still and type `imu nose`.");
    } else if (a == "flip") {      // FW 2.3.10: forward was taken the wrong way round: turn 180 deg about vertical
      d[0][0] = -1; d[1][1] = -1;
      apply = true;
      Serial.println("IMU turned 180 deg about vertical and saved: pitch and roll both change sign.");
    } else if (a == "nose") {
      const float h = sqrtf(v[0] * v[0] + v[1] * v[1]);
      if (h < 0.12f) { Serial.println("Lift the nose more (at least ~10 degrees), hold still, and type `imu nose` again."); return; }
      const float cs = v[0] / h, sn = v[1] / h;                  // rotate about z so the lifted nose is +x
      d[0][0] = cs; d[0][1] = sn; d[1][0] = -sn; d[1][1] = cs;
      apply = true;
      Serial.println("IMU forward direction saved. Nose up = negative pitch, left side up = positive roll, left turn = +lateral g.");
    } else {
      Serial.printf("IMU now a=[%.2f %.2f %.2f] g, gyro zero trim [%.2f %.2f %.2f] deg/s (%s).\n"
                    "  imu level (car flat) | imu nose (nose lifted) | imu flip | imu reset | imu zero (heading to 0)\n",
                    v[0], v[1], v[2], g_gyro_trim.trim[0], g_gyro_trim.trim[1], g_gyro_trim.trim[2], g_gyro_trim.learned ? "learned" : "not learned yet");
    }
    if (apply) imuApply(d);
  } else if (c == "knob") {
    sscanf(line.c_str(), "%*s %15s", arg);
    if (String(arg) == "zero" || String(arg) == "center" || String(arg) == "centre") { consoleConfigWrite(CFG_KEY_KNOB_CMD, 1); return; }
    ControlData cd; LOCK(); cd = g_control; UNLOCK();
    Serial.printf("knob %s (%s), position %ld clicks -> steering %.1f deg %s. `knob zero` centres it.\n",
                  VCU_HAVE_ENCODER ? "enabled" : "disabled", VCU_KNOB_PCNT ? "PCNT hardware counter" : "interrupts",
                  (long)cd.out.knob_pos, cd.out.steering_deg, cd.out.steering_deg > 0.05f ? "LEFT" : cd.out.steering_deg < -0.05f ? "RIGHT" : "");
  } else if (c == "set" && sscanf(line.c_str(), "%*s %d %f", &id, &f1) == 2) {
    consoleConfigWrite((uint8_t)id, (int32_t)lroundf(f1));
  } else if (c == "tune") {
    ConfigStore cs; LOCK(); cs = g_cfg; UNLOCK();
    for (uint8_t i = 0; i < CONFIG_REPORT_KEY_COUNT; ++i) {
      int32_t v; cs.read(CONFIG_REPORT_KEYS[i], v);
      Serial.printf("  key %3u = %ld\n", CONFIG_REPORT_KEYS[i], (long)v);
    }
    Serial.printf("  staged changes waiting for the start/finish line: %s\n", cs.pending ? "YES" : "no");
  } else if (c == "track") {
    sscanf(line.c_str(), "%*s %15s", arg);
    if (String(arg) == "list" || !arg[0]) {
      uint8_t cur; LOCK(); cur = g_cfg.snap.track_id; UNLOCK();
      for (int i = 0; i < TRACK_COUNT; ++i)
        Serial.printf("  %2d %s %-22s %5lu m  model lap %.1f s%s\n", i, TRACKS[i].code, TRACKS[i].name,
                      (unsigned long)TRACKS[i].length_m, TRACKS[i].model_lap_ms / 1000.0f, i == cur ? "  <-" : "");
    } else {
      consoleConfigWrite(CFG_KEY_TRACK_ID, atoi(arg));
    }
  } else if (c == "trackrun") {
    sscanf(line.c_str(), "%*s %15s", arg);
    consoleConfigWrite(CFG_KEY_TRACK_RUN, String(arg) == "on" ? 1 : 0);
  } else if (c == "lap") {
    sscanf(line.c_str(), "%*s %15s", arg);
    String a(arg);
    if (a == "len" && sscanf(line.c_str(), "%*s %*s %f", &f1) == 1) {
      consoleConfigWrite(CFG_KEY_LAP_LENGTH_CM, (int32_t)lroundf(f1 * 100.0f));
    } else if (a == "run") {
      int n = 0; sscanf(line.c_str(), "%*s %*s %d", &n);
      benchRequest(RUN_BENCH_LAPS, 0, n > 0 ? n : 0);
    } else if (a == "stop") {
      benchRequest(RUN_OFF, 0, 0);
    } else Serial.println("usage: lap len <m> | lap run [laps] | lap stop");
  } else if (c == "motor") {
    sscanf(line.c_str(), "%*s %15s", arg);
    if (String(arg) == "stop" || !arg[0]) consoleConfigWrite(CFG_KEY_MOTOR_CMD, 0);
    else consoleConfigWrite(CFG_KEY_MOTOR_CMD, (int32_t)lroundf(clampf(atof(arg), 0.0f, 100.0f) * 10.0f));
  } else if (c == "sweep") {
    sscanf(line.c_str(), "%*s %15s", arg);
    if (String(arg) == "on") {
      if (st != VS_IDLE && st != VS_FAULT) { Serial.println("refused: sweep only in IDLE or FAULT"); return; }
      float secs = 3; sscanf(line.c_str(), "%*s %*s %f", &secs);
      g_sweep_period_ms = (uint32_t)(clampf(secs, 0.5f, 30.0f) * 1000); g_sweep_on = true;
      Serial.printf("sweep on, every %.1f s\n", g_sweep_period_ms / 1000.0f);
    } else { g_sweep_on = false; g_bench_override = false; Serial.println("sweep off"); }
  } else if (c == "dyno") {
    sscanf(line.c_str(), "%*s %15s", arg);
    if (String(arg) == "cal") benchRequest(RUN_DYNO_CAL, 0, 0);
    else Serial.println("usage: dyno cal");
  } else if (c == "demo") {
    sscanf(line.c_str(), "%*s %15s", arg);
    const bool off = String(arg) == "off" || String(arg) == "stop";
    const bool lap = String(arg) == "2" || String(arg) == "lap";
    int tid = -1; sscanf(line.c_str(), "%*s %*s %d", &tid);           // `demo 2 <track id>` (ids: `track list`)
    if (lap && tid >= TRACK_COUNT) { Serial.printf("no such circuit: 0..%d (`track list`)\n", TRACK_COUNT - 1); return; }
    consoleConfigWrite(CFG_KEY_DEMO_CMD, off ? 0 : lap ? (tid >= 0 ? 100 + tid : 2) : 1);
    if (lap) {
      uint8_t cur; LOCK(); cur = g_cfg.snap.track_id; UNLOCK();
      Serial.printf("demo 2 requested: one lap of %s scaled to ~%d s (WHEELS OFF THE GROUND). `demo stop` ends it.\n",
                    TRACKS[tid >= 0 ? tid : cur].name, VCU_CAL_DEMO_LAP_SECONDS);
    }
    else if (!off) Serial.println("demo sequence requested (21 s; WHEELS OFF THE GROUND). `demo stop` or `estop` ends it.");
  } else if (c == "drs") {
    sscanf(line.c_str(), "%*s %15s", arg);
    consoleConfigWrite(CFG_KEY_DRS_CMD, String(arg) == "off" ? 0 : 1);
  } else if (c == "diag") {
    sscanf(line.c_str(), "%*s %15s", arg);
    if (String(arg) == "reset") { memset((void *)&g_diag, 0, sizeof(g_diag)); Serial.println("diag counters cleared"); }
    else printDiag();
  } else if (c == "estop") { LOCK(); g_comms.estop_request_local = true; UNLOCK(); Serial.println("E-STOP requested"); }
  else if (c == "reset") { LOCK(); g_comms.estop_reset_local = true; UNLOCK(); Serial.println("reset requested"); }
  else Serial.println("unknown command — type `help`");
}

// =============================================================================
// SECTION 13: SETUP / LOOP
// =============================================================================
void setup() {
  // Actuators safe FIRST, before anything that can fail or take time. The external
  // pull-downs already hold the motor PWM (GPIO17; GPIO4 on the classic ESP32 stand-in) and the
  // kill switch (GPIO19, rev B) low through reset.
  if (PIN_MOTOR_EN >= 0) { digitalWrite(PIN_MOTOR_EN, LOW); pinMode(PIN_MOTOR_EN, OUTPUT); digitalWrite(PIN_MOTOR_EN, LOW); }
  ledcAttach(PIN_MOTOR_PWM, MOTOR_PWM_HZ, MOTOR_PWM_BITS);
  ledcWrite(PIN_MOTOR_PWM, 0);

  Serial.begin(115200);
  delay(200);
  Serial.printf("\n=== VCU %s (CAN protocol v%u) — %s ===\n", FW_VERSION, CAN_PROTOCOL_VERSION, VCU_HW_NAME);
  {  // FW 2.2.3: say WHY the last reset happened (a reboot loop is almost always one of these)
    const esp_reset_reason_t rr = esp_reset_reason();
    const char *why = "other";
    switch (rr) {
      case ESP_RST_POWERON: why = "power-on"; break;
      case ESP_RST_EXT: why = "external pin (EN)"; break;
      case ESP_RST_SW: why = "software restart"; break;
      case ESP_RST_PANIC: why = "CRASH (panic)"; break;
      case ESP_RST_INT_WDT: why = "CRASH (interrupt watchdog)"; break;
      case ESP_RST_TASK_WDT: why = "CRASH (task watchdog)"; break;
      case ESP_RST_WDT: why = "watchdog"; break;
      case ESP_RST_BROWNOUT: why = "BROWN-OUT (supply dipped)"; break;
      default: break;
    }
    Serial.printf("[BOOT] last reset: %s (%d)\n", why, (int)rr);
    if (rr == ESP_RST_BROWNOUT)
      Serial.println("[BOOT]   -> the 3.3/5 V supply sagged. Usual cause: servos on the same 5 V rail as VIN. "
                     "Unplug the servos to confirm; check buck >= 5.0 V under load, bulk cap, VIN/GND jumpers.");
    if (rr == ESP_RST_PANIC || rr == ESP_RST_INT_WDT || rr == ESP_RST_TASK_WDT)
      Serial.println("[BOOT]   -> firmware crash: copy the 'Guru Meditation' / 'Backtrace' lines printed just before this boot.");
  }
  Serial.printf("build: MCU = %s\n", VCU_MCU_NAME);
#if VCU_MCU_CLASSIC_ESP32
  Serial.println("build: classic ESP32 stand-in pin map (VCU_classic_ESP32_stopgap.md). NVS calibration starts EMPTY on a new chip: "
                 "redo pulse/cal/tempmap/dyno cal, then `cal save`.");
#endif
  Serial.printf("build: motor = %s, %lu Hz%s\n", VCU_MOTOR_DRIVER_NAME, (unsigned long)MOTOR_PWM_HZ,
                VCU_MOTOR_OFF_BRAKES ? " (PWM off-time BRAKES)" : " (PWM off-time coasts)");
  Serial.printf("build: wheels = %s, %u pulse/rev, %.0f mm tyre (ASSUMED until measured)\n", VCU_WHEEL_SENSOR_NAME,
                (unsigned)CFG_WHEEL_PULSES_PER_REV, CFG_WHEEL_DIAMETER_M * 1000);
  Serial.printf("build: CAN = %s; ina260=%s; %d circuit maps, default %s\n", VCU_CAN_TRANSCEIVER_NAME,
                VCU_HAVE_INA260 ? "yes" : "NO (no power sensor -> DRIVE refused)", TRACK_COUNT, TRACKS[TRACK_DEFAULT_ID].name);
  Serial.printf("build: sensors=%s ads1115=%s fb_wired=0x%02X transport=%s encoder=%s\n", VCU_SENSORS_REAL ? "REAL" : "SAMPLE",
                VCU_HAVE_ADS1115 ? "yes" : "NO (STEER/LF/RF open-loop)", VCU_FB_WIRED_MASK,
                VCU_TRANSPORT_TWAI ? "TWAI/CAN" : "WiFi-UDP", VCU_HAVE_ENCODER ? "KY-040" : "off");
#if VCU_HAVE_ENCODER
  Serial.printf("build: knob = STEERING WHEEL, CLK=GPIO%d DT=GPIO%d (%s)\n", PIN_ENC_CLK, PIN_ENC_DT,
                VCU_KNOB_PCNT ? "PCNT hardware counter" : "pin-change interrupts");
#endif

  g_mutex = xSemaphoreCreateMutex();
  g_nvs_mutex = xSemaphoreCreateMutex();
  g_i2c_mutex = xSemaphoreCreateRecursiveMutex();
  g_ow_mutex = xSemaphoreCreateMutex();
  g_ctrl.begin(VCU_HW_PROFILE_ID, PIN_MOTOR_EN >= 0, VCU_HAVE_ENCODER != 0);
  g_ctrl.set_speed_from_duty(VCU_CAL_SPEED_FROM_DUTY != 0, !(VCU_CAL_SPEED_SIM_NOISE && VCU_CAL_SPEED_REPORT_AS_SENSOR));
#if VCU_CAL_SPEED_FROM_DUTY
  Serial.printf("wheel speed: ESTIMATED from motor duty (%d rpm, %d mm wheel = %.1f km/h at 100 %%, 0 below %d %%). Stall watchdog off.%s\n",
                VCU_CAL_MOTOR_MAX_RPM, VCU_CAL_WHEEL_DIAMETER_MM, DutySpeedModel::vmaxKmh(), VCU_CAL_MOTOR_MIN_DUTY_PCT,
                VCU_CAL_SPEED_SIM_NOISE ? " SIMULATED jitter + spikes ON." : "");
#endif
  calLoad(g_ctrl.cfg);
  g_cfg = g_ctrl.cfg;
  Serial.printf("drive mode at boot: %s%s\n", driveModeName(g_ctrl.cfg.snap.drive_mode),
                g_ctrl.cfg.snap.drive_mode == DRIVE_MODE_DYNO ? " (circuit runs allowed — the car must be on the rollers)" : "");
  Serial.printf("servo calibration: %s  (`cal` shows it)\n", g_trim_source);
  if (g_trim_file_differs)
    Serial.println("[CAL] note: the pulses in vcu_calibration.h differ from the ones saved on the car. The SAVED ones are used.\n"
                   "      `cal reset` would MOVE the servos to the file values (only do that on purpose).");
#if !VCU_CAL_STEER_SERVO_ENABLED
  Serial.println("steering servo OUTPUT OFF (vcu_calibration.h VCU_CAL_STEER_SERVO_ENABLED 0): the knob sets the steering angle "
                 "for the flaps and the dashboard, the servo gets no pulses. Set 1 with a 180-degree servo fitted.");
#endif

  // FW 2.2.3: start the servos ONE AT A TIME, 150 ms apart (fronts, rears, then steering).
  // All five jumping to their failsafe position together can pull the 5 V rail down far enough
  // to brown out the MCU. Each step is printed, so a reboot loop shows where it dies.
  {
    const int start_order[SERVO_COUNT] = {1, 2, 3, 4, 0};  // LF, RF, LR, RR, STEER
    for (int k = 0; k < SERVO_COUNT; ++k) {
      const int i = start_order[k];
      Serial.printf("[BOOT] servo %-5s on GPIO%d\n", SERVO_NAMES[i], SERVO_PINS[i]);
      Serial.flush();
      ledcAttach(SERVO_PINS[i], SERVO_PWM_HZ, SERVO_PWM_BITS);
      servoWriteDeg(g_ctrl.cfg.trim, i, i == SERVO_STEERING ? CFG_STEERING_FAILSAFE_DEG : CFG_WING_FAILSAFE_ANGLE_DEG);
      delay(150);
    }
  }
  Serial.println("[BOOT] I2C, 1-Wire, wheel and knob inputs"); Serial.flush();
  analogReadResolution(12);
  for (int i = 0; i < SERVO_COUNT; ++i)
    if (FB_ADC_PINS[i] >= 0) analogSetPinAttenuation(FB_ADC_PINS[i], ADC_11db);

  Wire.begin(PIN_I2C_SDA, PIN_I2C_SCL, VCU_I2C_HZ);
  Wire.setTimeOut(5);

  digitalWrite(PIN_ONEWIRE, HIGH);
  pinMode(PIN_ONEWIRE, OUTPUT_OPEN_DRAIN);          // bug #5: configured once, never inside a slot
  digitalWrite(PIN_ONEWIRE, HIGH);
  for (int i = 0; i < 4; ++i) pinMode(WHEEL_PINS[i], VCU_WHEEL_PIN_MODE);
  attachInterrupt(PIN_WHEEL_FL, hallIsr0, VCU_WHEEL_EDGE);
#if VCU_CAL_KNOB_BUTTON_DRS && VCU_HAVE_ENCODER
  pinMode(PIN_KNOB_SW, INPUT_PULLUP);       // the front-right wheel input is the knob's push switch
  Serial.printf("knob press = DRS: KY-040 SW on GPIO%d (J12 pin 3, front-right wheel-sensor signal pin)\n", PIN_KNOB_SW);
#else
  attachInterrupt(PIN_WHEEL_FR, hallIsr1, VCU_WHEEL_EDGE);
#endif
  attachInterrupt(PIN_WHEEL_RL, hallIsr2, VCU_WHEEL_EDGE);
  attachInterrupt(PIN_WHEEL_RR, hallIsr3, VCU_WHEEL_EDGE);
#if VCU_HAVE_ENCODER
  pinMode(PIN_ENC_CLK, INPUT);                      // the module's own 10k pull-ups (TCA9554 lines)
  pinMode(PIN_ENC_DT, INPUT);
  Serial.printf("[BOOT] knob levels CLK=%d DT=%d (both 1 at rest; 0 0 = not plugged in / no pull-ups)\n",
                digitalRead(PIN_ENC_CLK), digitalRead(PIN_ENC_DT));
#if VCU_KNOB_PCNT
  if (!knobPcntBegin()) Serial.println("[BOOT] knob PCNT setup FAILED — the knob will not steer");
#else
  g_enc_state = (uint8_t)((digitalRead(PIN_ENC_CLK) << 1) | digitalRead(PIN_ENC_DT));
  attachInterrupt(PIN_ENC_CLK, encoderIsr, CHANGE);
  attachInterrupt(PIN_ENC_DT, encoderIsr, CHANGE);
#endif
#endif

  Serial.println("[BOOT] CAN"); Serial.flush();
  canTransportInit();

  esp_task_wdt_config_t wdt = {.timeout_ms = 3000, .idle_core_mask = 0, .trigger_panic = true};
  if (esp_task_wdt_init(&wdt) == ESP_ERR_INVALID_STATE) esp_task_wdt_reconfigure(&wdt);

  uint32_t t0 = millis();
  g_hb_sensor = t0; g_hb_can = t0; g_hb_control = t0;
  // All tasks on the Arduino core (the one the ISRs were attached on). On the single-core C6
  // this is what xTaskCreate did anyway; on the dual-core classic ESP32 it keeps the
  // noInterrupts() snapshots of the ISR counters atomic (they only mask the CURRENT core).
  // FW 2.2.4: Xtensa (classic ESP32) code needs noticeably more stack than RISC-V (register
  // windows); a stack overflow panics and reboots (rst:0xc SW_CPU_RESET). Double them there.
  Serial.println("[BOOT] tasks"); Serial.flush();
  xTaskCreatePinnedToCore(canTask, "can", 4096 * VCU_STACK_SCALE, nullptr, 3, &g_task_can, ARDUINO_RUNNING_CORE);
  xTaskCreatePinnedToCore(sensorTask, "sensor", 4096 * VCU_STACK_SCALE, nullptr, 3, &g_task_sensor, ARDUINO_RUNNING_CORE);
  xTaskCreatePinnedToCore(controlTask, "control", 8192 * VCU_STACK_SCALE, nullptr, 4, &g_task_control, ARDUINO_RUNNING_CORE);
  printHelp();
}

void loop() {
  static String line;
  while (Serial.available()) {
    char ch = (char)Serial.read();
    if (ch == '\n' || ch == '\r') { handleCommand(line); line = ""; }
    else if (line.length() < 80) line += ch;
  }
  delay(20);
}
