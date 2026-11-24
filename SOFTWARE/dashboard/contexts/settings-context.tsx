'use client'

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useState,
  type ReactNode,
} from 'react'
import type { UnitSystem } from '@/lib/format'

export type LinkTransport = 'lora' | 'wifi' | 'usb'

export interface AlertSettings {
  critical: boolean
  warning: boolean
  info: boolean
}

export interface Settings {
  units: UnitSystem
  sampleRate: number // Hz — drives the live telemetry cadence
  link: LinkTransport
  developerMode: boolean
  dataRecording: boolean
  reduceMotion: boolean
  alerts: AlertSettings
}

export const DEFAULT_SETTINGS: Settings = {
  units: 'metric',
  sampleRate: 10,
  link: 'lora',
  developerMode: false,
  dataRecording: true,
  reduceMotion: false,
  alerts: { critical: true, warning: true, info: false },
}

const STORAGE_KEY = 'res.settings.v1'

interface SettingsContextValue {
  settings: Settings
  update: <K extends keyof Settings>(key: K, value: Settings[K]) => void
  reset: () => void
  hydrated: boolean
}

const SettingsContext = createContext<SettingsContextValue | null>(null)

export function SettingsProvider({ children }: { children: ReactNode }) {
  const [settings, setSettings] = useState<Settings>(DEFAULT_SETTINGS)
  const [hydrated, setHydrated] = useState(false)

  // Load persisted settings after mount to avoid hydration mismatch.
  useEffect(() => {
    try {
      const raw = localStorage.getItem(STORAGE_KEY)
      if (raw) setSettings({ ...DEFAULT_SETTINGS, ...JSON.parse(raw) })
    } catch {
      /* ignore malformed storage */
    }
    setHydrated(true)
  }, [])

  useEffect(() => {
    if (!hydrated) return
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(settings))
    } catch {
      /* storage unavailable */
    }
  }, [settings, hydrated])

  const update = useCallback<SettingsContextValue['update']>((key, value) => {
    setSettings((s) => ({ ...s, [key]: value }))
  }, [])

  const reset = useCallback(() => setSettings(DEFAULT_SETTINGS), [])

  return (
    <SettingsContext.Provider value={{ settings, update, reset, hydrated }}>
      {children}
    </SettingsContext.Provider>
  )
}

export function useSettings() {
  const ctx = useContext(SettingsContext)
  if (!ctx) throw new Error('useSettings must be used within SettingsProvider')
  return ctx
}
