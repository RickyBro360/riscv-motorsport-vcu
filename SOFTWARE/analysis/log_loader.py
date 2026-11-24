#!/usr/bin/env python3
"""
log_loader.py — read the Raspberry Pi gateway's CSV logs.

The gateway writes one row per telemetry sample (20 Hz by default) to
logs/vehicle_YYYYmmdd_HHMMSS.csv; the first column is wall-clock seconds and
the rest are the VehicleTelemetry fields (HARDWARE/raspberry_pi/main.py).

Deliberately dependency-free: standard library only, so this runs on any
laptop with Python 3.10+ and no install step. A few thousand rows per run is
nothing for pure Python.
"""
from __future__ import annotations

import csv
import math
from dataclasses import dataclass, field
from pathlib import Path

# Columns that are text, not numbers.
_TEXT_COLUMNS = {"vehicle_state", "can_status", "lora_status"}


def _to_float(value: str) -> float:
    if value in ("", "None", "nan", "NaN"):
        return math.nan
    if value in ("True", "False"):
        return 1.0 if value == "True" else 0.0
    try:
        return float(value)
    except ValueError:
        return math.nan


@dataclass
class Log:
    """One recorded run. `columns` maps column name -> list of values."""

    path: Path
    columns: dict[str, list] = field(default_factory=dict)

    # ---------------------------------------------------------------- access
    def __len__(self) -> int:
        return len(self.columns.get("wall_time_s", []))

    def has(self, *names: str) -> bool:
        return all(n in self.columns and len(self.columns[n]) == len(self) for n in names)

    def col(self, name: str, default: float = math.nan) -> list[float]:
        """Numeric column; a missing column comes back as the default value so
        analyses written for a newer log still run on an older one."""
        if name not in self.columns:
            return [default] * len(self)
        return self.columns[name]

    @property
    def t(self) -> list[float]:
        """Seconds from the start of the run."""
        raw = self.columns.get("wall_time_s") or []
        if not raw:
            return []
        t0 = raw[0]
        return [v - t0 for v in raw]

    @property
    def duration_s(self) -> float:
        t = self.t
        return (t[-1] - t[0]) if len(t) > 1 else 0.0

    @property
    def sample_rate_hz(self) -> float:
        return (len(self) - 1) / self.duration_s if self.duration_s > 0 else 0.0

    # ---------------------------------------------------------------- subsets
    def where(self, predicate) -> "Log":
        """New Log containing only the rows where predicate(row_index) is true."""
        keep = [i for i in range(len(self)) if predicate(self, i)]
        out = Log(path=self.path)
        out.columns = {name: [values[i] for i in keep] for name, values in self.columns.items()}
        return out

    def row(self, i: int) -> dict:
        return {name: values[i] for name, values in self.columns.items()}

    def moving(self, min_speed_kmh: float = 1.0) -> "Log":
        return self.where(lambda log, i: log.col("speed_kmh")[i] >= min_speed_kmh)

    def by_aero_mode(self) -> dict[int, "Log"]:
        """Split a run into its aero modes (0 baseline, 1 AI-assist, 2 hold).
        Only useful if the VCU reported mode changes during the run."""
        modes = sorted({int(v) for v in self.col("aero_mode", -1.0) if not math.isnan(v)})
        return {m: self.where(lambda log, i, m=m: int(log.col("aero_mode", -1.0)[i]) == m) for m in modes}


def load(path: str | Path) -> Log:
    path = Path(path)
    log = Log(path=path)
    with path.open(newline="") as fh:
        reader = csv.DictReader(fh)
        names = reader.fieldnames or []
        columns: dict[str, list] = {name: [] for name in names}
        for row in reader:
            for name in names:
                raw = row.get(name, "")
                columns[name].append(raw if name in _TEXT_COLUMNS else _to_float(raw))
    log.columns = columns
    return log


def load_all(directory: str | Path, pattern: str = "vehicle_*.csv") -> list[Log]:
    """Every log in a directory, oldest first."""
    return [load(p) for p in sorted(Path(directory).glob(pattern))]
