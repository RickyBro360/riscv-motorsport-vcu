"""
engineer.py — the AI engineer's desk (Stage 2d).

  GET  /api/ai/engineer                     live analysis: position, inferences, open proposals,
                                            history, commands (the dashboard polls this ~1 Hz)
  POST /api/ai/suggestions/{id}/approve     operator says yes -> CONFIG_WRITEs queued for the VCU
  POST /api/ai/suggestions/{id}/reject      operator says no
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from app import ai_engineer
from app.routers.telemetry import get_latest_telemetry
from app.services import vehicle_commands

router = APIRouter(prefix="/api/ai", tags=["ai"])


@router.get("/engineer")
def engineer() -> dict:
    latest = get_latest_telemetry()
    session_id = latest.get("sessionId") if latest else None
    analysis = ai_engineer.analyse(session_id)
    ai_engineer.sync_proposals(analysis)
    ai_engineer.update_lifecycle(session_id)
    frame = (latest or {}).get("frame") or {}
    live = ai_engineer.live_aero_recommendation(frame, lookahead_s=analysis.get("settings", {}).get("lookaheadS")) \
        if frame else None
    return {
        **{k: v for k, v in analysis.items() if k != "proposals"},
        "live": live,
        "suggestions": ai_engineer.list_suggestions(),
        "commands": vehicle_commands.recent(15),
        "engine": "deterministic rules + digital-twin optimiser (no trained model)",
    }


@router.post("/suggestions/{sid}/approve")
def approve(sid: int) -> dict:
    try:
        return ai_engineer.approve(sid)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="no such suggestion") from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/suggestions/{sid}/reject")
def reject(sid: int) -> dict:
    try:
        return ai_engineer.reject(sid)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="no such suggestion") from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
