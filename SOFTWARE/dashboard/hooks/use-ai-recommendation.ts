'use client'

import { useEffect, useState } from 'react'
import { aiApi, type AiRecommendationResponse } from '@/services/api'

/** Polls /api/ai/recommendation — the Racing Engineer Station's current
 * recommendation, computed deterministically from live telemetry (see
 * SOFTWARE/backend/app/ai_engine.py). `available: false` is a normal,
 * expected state (no telemetry yet, or the AI is idle) and is returned
 * as-is rather than mapped to null, so callers can distinguish "haven't
 * checked yet" from "checked, nothing to recommend". Network/backend
 * failures resolve the same way as an explicit unavailable response —
 * per the project brief, "If AI stops: vehicle remains operational" —
 * so the UI never blocks or errors, it just shows nothing to report. */
export function useAiRecommendation(intervalMs = 750) {
  const [recommendation, setRecommendation] = useState<AiRecommendationResponse | null>(null)

  useEffect(() => {
    let cancelled = false
    const poll = async () => {
      try {
        const res = await aiApi.recommendation()
        if (!cancelled) setRecommendation(res)
      } catch {
        if (!cancelled) setRecommendation({ available: false, reason: 'Racing Engineer Station unreachable.' })
      }
    }
    poll()
    const id = window.setInterval(poll, intervalMs)
    return () => {
      cancelled = true
      window.clearInterval(id)
    }
  }, [intervalMs])

  return recommendation
}
