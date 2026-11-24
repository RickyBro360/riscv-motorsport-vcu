'use client'

import { useMemo } from 'react'
import { useSettings } from '@/contexts/settings-context'
import { convertSpeed, convertTemp, speedUnit, tempUnit } from '@/lib/format'

/**
 * Unit-aware helpers derived from the global settings. Converts SI telemetry
 * values into the user's selected measurement system for display.
 */
export function useUnits() {
  const { settings } = useSettings()
  const system = settings.units

  return useMemo(
    () => ({
      system,
      speedUnit: speedUnit(system),
      tempUnit: tempUnit(system),
      /** Convert a km/h value to the active system. */
      speed: (kmh: number) => convertSpeed(kmh, system),
      /** Convert a °C value to the active system. */
      temp: (celsius: number) => convertTemp(celsius, system),
    }),
    [system],
  )
}
