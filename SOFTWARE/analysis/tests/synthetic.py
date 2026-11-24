"""
synthetic.py — build telemetry CSVs with known physics so the metrics can be
checked against answers we computed by hand. The column names are exactly the
ones HARDWARE/raspberry_pi/main.py's VehicleDataLogger writes.
"""
from __future__ import annotations

import csv
import math
from pathlib import Path

COLUMNS = [
    "wall_time_s", "timestamp_us", "sequence", "vehicle_state", "vehicle_state_index",
    "speed_kmh", "wheel_speed_fl_kmh", "wheel_speed_fr_kmh", "wheel_speed_rl_kmh",
    "wheel_speed_rr_kmh", "steering_angle_deg", "throttle_pct", "brake_pct",
    "accel_x_g", "accel_y_g", "accel_z_g", "gyro_x_dps", "gyro_y_dps", "gyro_z_dps",
    "roll_deg", "pitch_deg", "yaw_deg", "battery_voltage_v", "battery_current_a",
    "battery_temperature_c", "motor_current_a", "motor_temperature_c",
    "wing_lf_deg", "wing_rf_deg", "wing_lr_deg", "wing_rr_deg",
    "wing_target_lf_deg", "wing_target_rf_deg", "wing_target_lr_deg", "wing_target_rr_deg",
    "fault_flags", "can_status", "lora_status",
    "distance_m", "wheel_rpm", "wheel_angle_rad", "power_w", "energy_wh", "wh_per_km",
    "soc_coulomb_pct", "pack_resistance_ohm", "aero_mode", "slip_ratio",
    "seconds_to_motor_limit", "seconds_to_battery_limit",
    "aero_measured_valid", "fault_severity",
]

DEFAULTS = {
    "vehicle_state": "DRIVE", "vehicle_state_index": 5, "accel_z_g": 1.0,
    "battery_voltage_v": 11.1, "battery_temperature_c": 25.0, "motor_temperature_c": 25.0,
    "can_status": "connected", "lora_status": "connected", "soc_coulomb_pct": 100.0,
    "aero_mode": 1, "fault_flags": 0, "fault_severity": 0, "aero_measured_valid": True,
}


def write_log(path: Path, rows: list[dict]) -> Path:
    with Path(path).open("w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=COLUMNS)
        writer.writeheader()
        for i, row in enumerate(rows):
            full = {c: 0 for c in COLUMNS}
            full.update(DEFAULTS)
            full["sequence"] = i
            full.update(row)
            writer.writerow(full)
    return Path(path)


def constant_drive(path: Path, *, seconds: float = 20.0, rate_hz: float = 20.0,
                   speed_kmh: float = 36.0, voltage: float = 10.0,
                   current: float = 2.0) -> Path:
    """Straight line at a constant speed: distance and energy are exact."""
    dt = 1.0 / rate_hz
    rows = []
    for k in range(int(seconds * rate_hz) + 1):
        t = k * dt
        rows.append({"wall_time_s": 1000.0 + t, "timestamp_us": int(t * 1e6), "speed_kmh": speed_kmh,
                     "wheel_speed_fl_kmh": speed_kmh, "wheel_speed_fr_kmh": speed_kmh,
                     "wheel_speed_rl_kmh": speed_kmh, "wheel_speed_rr_kmh": speed_kmh,
                     "throttle_pct": 40.0, "battery_voltage_v": voltage, "battery_current_a": current})
    return write_log(path, rows)


def coastdown(path: Path, *, rolling_a: float = 0.3, b_term: float = 0.004,
              v0_kmh: float = 60.0, seconds: float = 12.0, rate_hz: float = 20.0) -> Path:
    """Pure coast-down integrating a = rolling_a + b_term * v^2, so
    coastdown_drag() must recover rolling_a and b_term."""
    dt = 1.0 / rate_hz
    v = v0_kmh / 3.6
    rows = []
    for k in range(int(seconds * rate_hz) + 1):
        t = k * dt
        rows.append({"wall_time_s": t, "timestamp_us": int(t * 1e6), "speed_kmh": v * 3.6,
                     "wheel_speed_fl_kmh": v * 3.6, "wheel_speed_fr_kmh": v * 3.6,
                     "wheel_speed_rl_kmh": v * 3.6, "wheel_speed_rr_kmh": v * 3.6,
                     "throttle_pct": 0.0, "brake_pct": 0.0,
                     "accel_x_g": -(rolling_a + b_term * v * v) / 9.80665})
        v = max(0.0, v - (rolling_a + b_term * v * v) * dt)
    return write_log(path, rows)


def circuit(path: Path, *, laps: int = 4, lap_time_s: float = 12.0, rate_hz: float = 20.0,
            speed_kmh: float = 30.0, peak_lat_g: float = 0.8, aero_mode: int = 1) -> Path:
    """A repeating circuit: one full yaw-rate cycle per lap, lateral g in
    phase with it. detect_laps() must find `lap_time_s`."""
    dt = 1.0 / rate_hz
    rows = []
    radius = (speed_kmh / 3.6) ** 2 / (peak_lat_g * 9.80665)
    for k in range(int(laps * lap_time_s * rate_hz)):
        t = k * dt
        phase = 2 * math.pi * (t % lap_time_s) / lap_time_s
        lat_g = peak_lat_g * math.sin(phase)
        yaw_rate_dps = math.degrees(lat_g * 9.80665 / max(speed_kmh / 3.6, 0.1))
        rows.append({"wall_time_s": t, "timestamp_us": int(t * 1e6), "speed_kmh": speed_kmh,
                     "wheel_speed_fl_kmh": speed_kmh, "wheel_speed_fr_kmh": speed_kmh,
                     "wheel_speed_rl_kmh": speed_kmh, "wheel_speed_rr_kmh": speed_kmh,
                     "accel_y_g": lat_g, "gyro_z_dps": yaw_rate_dps,
                     "steering_angle_deg": math.degrees(0.26 / max(radius, 0.1)) * math.sin(phase),
                     "throttle_pct": 30.0, "battery_current_a": 6.0, "battery_voltage_v": 11.0,
                     "aero_mode": aero_mode})
    return write_log(path, rows)


def heating(path: Path, *, tau_s: float = 120.0, t0: float = 30.0, t_inf: float = 95.0,
            seconds: float = 180.0, rate_hz: float = 5.0) -> Path:
    """First-order thermal rise; thermal_model() must recover tau and T_inf."""
    dt = 1.0 / rate_hz
    rows = []
    for k in range(int(seconds * rate_hz) + 1):
        t = k * dt
        temp = t_inf - (t_inf - t0) * math.exp(-t / tau_s)
        rows.append({"wall_time_s": t, "timestamp_us": int(t * 1e6), "speed_kmh": 25.0,
                     "motor_temperature_c": temp, "throttle_pct": 50.0,
                     "battery_current_a": 12.0, "battery_voltage_v": 11.0})
    return write_log(path, rows)
