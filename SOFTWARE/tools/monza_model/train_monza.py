#!/usr/bin/env python3
"""Train the Monza model: (lap position, wheel speed) -> (motor duty %, steering deg, straight mode, speed).
Data: monza_laps.csv from gen_monza (the real VCU logic driving its own Monza map). numpy only."""
import csv, json, sys, datetime
import numpy as np
rng = np.random.default_rng(7)
K, VN, HID = 16, 20.0, [48, 48]
rows = list(csv.DictReader(open(sys.argv[1])))
col = lambda k: np.array([float(r[k]) for r in rows])
lap, frac, spd, duty, steer, straight = col('lap'), col('frac'), col('speed_kmh'), col('duty_pct'), col('steer_deg'), col('straight')
def feats(f, v):
    k = np.arange(1, K + 1)
    return np.concatenate([f[:, None], np.sin(2 * np.pi * f[:, None] * k), np.cos(2 * np.pi * f[:, None] * k), (v / VN)[:, None]], 1)
Y = np.stack([duty / 100, steer / 30, straight, spd / VN], 1)
val = lap >= lap.max() - 1
tr = ~val
X = feats(frac, spd)
xm, xs = X[tr].mean(0), X[tr].std(0) + 1e-6
sizes = [X.shape[1]] + HID + [4]
W = [rng.normal(0, np.sqrt(2 / a), (a, b)) for a, b in zip(sizes[:-1], sizes[1:])]
B = [np.zeros(b) for b in sizes[1:]]
def fwd(x):
    h = (x - xm) / xs; acts = [h]
    for l in range(len(W)):
        h = h @ W[l] + B[l]
        if l < len(W) - 1: h = np.maximum(h, 0)
        acts.append(h)
    return acts
wout = np.array([1.0, 4.0, 0.5, 0.5])            # loss weights (steering is small in /30 units)
m = [np.zeros_like(p) for p in W + B]; v = [np.zeros_like(p) for p in W + B]
lr, b1, b2 = 2e-3, 0.9, 0.999
Xtr, Ytr, ftr, str_ = X[tr], Y[tr], frac[tr], spd[tr]
best = (1e9, None); step = 0
for ep in range(400):
    idx = rng.permutation(len(Xtr))
    for i in range(0, len(idx), 64):
        j = idx[i:i + 64]
        # augmentation: the car's displayed speed jitters by +-10 %, and the position estimate is never exact
        xb = feats((ftr[j] + rng.normal(0, 0.002, len(j))) % 1.0, str_[j] * (1 + rng.uniform(-0.12, 0.12, len(j))))
        acts = fwd(xb); g = 2 * (acts[-1] - Ytr[j]) * wout / len(j)
        gW, gB = [], []
        for l in reversed(range(len(W))):
            gW.insert(0, acts[l].T @ g); gB.insert(0, g.sum(0))
            if l: g = (g @ W[l].T) * (acts[l] > 0)
        step += 1
        for k, (p, gp) in enumerate(zip(W + B, gW + gB)):
            m[k] = b1 * m[k] + (1 - b1) * gp; v[k] = b2 * v[k] + (1 - b2) * gp * gp
            p -= lr * (m[k] / (1 - b1 ** step)) / (np.sqrt(v[k] / (1 - b2 ** step)) + 1e-8)
    if ep == 250: lr = 5e-4
    pv = fwd(X[val])[-1]; loss = float((((pv - Y[val]) ** 2) * wout).mean())
    if loss < best[0]: best = (loss, [p.copy() for p in W + B], ep)
W, B = best[1][:len(W)], best[1][len(W):]
pv = fwd(X[val])[-1]
d_mae = float(np.abs(np.clip(pv[:, 0] * 100, 0, 100) - duty[val]).mean())
d_const = float(np.abs(duty[val] - duty[tr].mean()).mean())
s_mae = float(np.abs(pv[:, 1] * 30 - steer[val]).mean())
s_const = float(np.abs(steer[val] - steer[tr].mean()).mean())
drs_acc = float(((pv[:, 2] > 0.5) == (straight[val] > 0.5)).mean())
# how much does the speed input matter? same validation rows with the speed input off by +-10 %
pv_hi = fwd(feats(frac[val], spd[val] * 1.1))[-1]; pv_lo = fwd(feats(frac[val], spd[val] * 0.9))[-1]
sens = float(np.abs(pv_hi[:, 0] - pv_lo[:, 0]).mean() * 100)
model = {
    "format": "vcu-track-model", "version": 1, "name": "monza-vcu-map", "created": datetime.datetime.utcnow().isoformat() + "Z",
    "kind": "mlp", "hidden": HID, "fourier_k": K, "v_norm": VN, "track_id": 12, "track_name": "Monza",
    "features": "[lap_frac, sin(2*pi*k*frac) k=1..K, cos(...), wheel_speed_kmh/v_norm]",
    "outputs": ["duty", "steer", "drs", "speed"], "out_scale": {"duty": 100, "steer": 30, "drs": 1, "speed": VN},
    "lap_length_m": float(rows[-1]["lap_len_m"]), "duty_floor": 0, "duty_cap": 100, "steer_sign": "+ = left",
    "x_mean": xm.tolist(), "x_std": xs.tolist(), "weights": [w.tolist() for w in W], "biases": [b.tolist() for b in B],
    "n_params": int(sum(p.size for p in W + B)),
    "training": {"source": "the VCU's own Monza circuit run (vcu_logic.h, FW 2.3.12, duty-based speed model), 12 laps at 20 Hz",
                 "rows_train": int(tr.sum()), "rows_val": int(val.sum()), "best_epoch": best[2],
                 "val_duty_mae_pct": d_mae, "val_duty_mae_if_constant_pct": d_const, "val_steer_mae_deg": s_mae,
                 "val_steer_mae_if_constant_deg": s_const, "val_straight_mode_accuracy": drs_acc,
                 "duty_change_for_speed_pm10pct": sens}}
json.dump(model, open(sys.argv[2], "w"))
print(json.dumps(model["training"], indent=1))
