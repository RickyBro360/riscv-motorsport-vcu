'use client'

import { useEffect, useMemo, useState } from 'react'
import { loadTracks, type TrackDoc, type TrackDef } from '@/lib/tracks'

/** The circuit library (public/tracks.json), loaded once per page load. */
export function useTracks(): TrackDoc | null {
  const [doc, setDoc] = useState<TrackDoc | null>(null)
  useEffect(() => {
    let cancelled = false
    loadTracks().then((d) => { if (!cancelled) setDoc(d) }).catch(() => {})
    return () => { cancelled = true }
  }, [])
  return doc
}

export function useTrack(id: number | null | undefined): TrackDef | null {
  const doc = useTracks()
  return useMemo(() => {
    if (!doc) return null
    const tid = typeof id === 'number' && id >= 0 && id < doc.tracks.length ? id : doc.defaultTrackId
    return doc.tracks[tid]
  }, [doc, id])
}
