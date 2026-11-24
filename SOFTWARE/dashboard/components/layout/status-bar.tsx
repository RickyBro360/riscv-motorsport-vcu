'use client'

import { Wifi, Radio, Cpu, Gauge, Activity, ShieldCheck, MemoryStick, Power } from 'lucide-react'
import { useTelemetry } from '@/contexts/telemetry-context'
import { StatusDot } from '@/components/common/status-dot'
import { LINK_LABEL, connToLevel, linkToLevel, VCU_STATE_LABEL, vcuStateLevel } from '@/lib/format'
import type { HealthLevel } from '@/types/telemetry'

function Item({
  icon: Icon,
  label,
  value,
  level,
}: {
  icon: typeof Wifi
  label: string
  value: string
  level?: HealthLevel
}) {
  return (
    <div className="flex items-center gap-1.5 whitespace-nowrap">
      <Icon className="size-3.5 text-muted-foreground" />
      <span className="text-[0.7rem] uppercase tracking-wide text-muted-foreground">{label}</span>
      <span className="tabular text-[0.7rem] font-semibold text-foreground">{value}</span>
      {level && <StatusDot level={level} pulse={false} className="size-1.5" />}
    </div>
  )
}

export function StatusBar() {
  const { diagnostics: d, frame, link } = useTelemetry()
  if (!d) return null

  const fromCar = link === 'live_hardware'
  const radio = (d as unknown as { radio?: { rssi?: number | null } | null }).radio
  const viaLora = fromCar && !!radio
  const canText = fromCar ? d.can : `${d.can} (SIMULATED)`
  const loraLabel = fromCar && !viaLora ? 'Link' : 'LoRa'
  const loraText = !fromCar ? `${d.loraLink.rssi.toFixed(0)} dBm (SIMULATED)`
    : viaLora ? (radio?.rssi != null ? `${Number(radio.rssi).toFixed(0)} dBm` : d.lora)
    : 'Wi-Fi (LoRa not used)'
  const loraLevel: HealthLevel | undefined = fromCar && !viaLora ? undefined : connToLevel(d.lora)

  const health: HealthLevel =
    d.watchdog === 'ok' && d.packetLoss < 3 ? 'ok' : d.packetLoss > 4.5 ? 'critical' : 'warning'

  return (
    <footer className="scrollbar-thin flex h-9 items-center gap-4 overflow-x-auto border-t border-border bg-background px-4">
      {frame && (
        <>
          <Item icon={Power} label="VCU" value={VCU_STATE_LABEL[frame.vcuState]} level={vcuStateLevel(frame.vcuState)} />
          <span className="h-4 w-px bg-border" />
        </>
      )}
      <Item icon={Wifi} label="Backend" value={LINK_LABEL[link]} level={linkToLevel(link)} />
      <span className="h-4 w-px bg-border" />
      <Item icon={Activity} label="Refresh" value={`${d.refreshRate} Hz`} />
      <Item icon={Gauge} label="Ping" value={`${d.ping.toFixed(0)} ms`} />
      <Item icon={Activity} label="Packets" value={`${d.packetRate.toFixed(0)}/s`} />
      <span className="h-4 w-px bg-border" />
      {/* LIVE from the car: CAN and LoRa come from the Raspberry Pi gateway (CAN = VCU frames arriving; LoRa =
          the radio's own RSSI, or "Wi-Fi" when the Pi reaches the laptop directly). Anything else (simulation,
          replay, a backend with no car behind it) is still simulated, and says so. */}
      <Item icon={Wifi} label="CAN" value={canText} level={connToLevel(d.can)} />
      <Item icon={Radio} label={loraLabel} value={loraText} level={loraLevel} />
      <span className="h-4 w-px bg-border" />
      <Item icon={Cpu} label="CPU" value={`${d.cpu.toFixed(0)}%`} />
      <Item icon={MemoryStick} label="MEM" value={`${d.memory.toFixed(0)}%`} />
      <div className="ml-auto flex items-center gap-1.5 whitespace-nowrap">
        <ShieldCheck className="size-3.5 text-muted-foreground" />
        <span className="text-[0.7rem] uppercase tracking-wide text-muted-foreground">System</span>
        <span className="text-[0.7rem] font-semibold capitalize text-foreground">
          {health === 'ok' ? 'Healthy' : health === 'warning' ? 'Degraded' : 'Fault'}
        </span>
        <StatusDot level={health} pulse={false} className="size-1.5" />
      </div>
    </footer>
  )
}
