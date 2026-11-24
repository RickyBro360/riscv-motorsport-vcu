from __future__ import annotations

from fastapi import APIRouter, HTTPException

from app.db import dumps, get_conn, loads, now
from app.models import ConfigurationOut, VehicleSetup

router = APIRouter(prefix="/api/config", tags=["configuration"])


@router.get("", response_model=ConfigurationOut)
def get_config() -> ConfigurationOut:
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM configuration WHERE id = 'current'").fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="No configuration saved yet")
    return ConfigurationOut(setup=loads(row["setup_json"]), version=row["version"], updatedAt=row["updated_at"])


@router.post("", response_model=ConfigurationOut)
def save_config(setup: VehicleSetup) -> ConfigurationOut:
    with get_conn() as conn:
        existing = conn.execute("SELECT version FROM configuration WHERE id = 'current'").fetchone()
        next_version = (existing["version"] + 1) if existing else 1
        ts = now()
        conn.execute(
            "INSERT INTO configuration (id, setup_json, version, updated_at) VALUES ('current', ?, ?, ?) "
            "ON CONFLICT(id) DO UPDATE SET setup_json = excluded.setup_json, version = excluded.version, "
            "updated_at = excluded.updated_at",
            (dumps(setup.model_dump()), next_version, ts),
        )
    return ConfigurationOut(setup=setup, version=next_version, updatedAt=ts)
