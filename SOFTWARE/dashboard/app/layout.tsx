import { Analytics } from '@vercel/analytics/next'
import type { Metadata, Viewport } from 'next'
import { SettingsProvider } from '@/contexts/settings-context'
import { TelemetryProvider } from '@/contexts/telemetry-context'
import { AppShell } from '@/components/layout/app-shell'
import './globals.css'

// NOTE: next/font/google (Geist/Geist_Mono) was removed here — it requires
// fetching fonts.googleapis.com at BUILD TIME, which fails the production
// build on any offline/restricted-network machine (this sandbox, many
// university/lab networks, CI runners without egress). globals.css already
// defines --font-sans/--font-mono with a ui-sans-serif/system-ui/monospace
// fallback chain, so dropping the Google-hosted variable simply lets that
// system-font fallback take over everywhere — no visual/layout regression,
// no dependency on network access at build time. To reinstate Geist later,
// self-host the .woff2 files with next/font/local instead of next/font/google.

export const metadata: Metadata = {
  title: 'Racing Engineer Suite — RISC-V VCU Telemetry',
  description:
    'Formula-style pit wall telemetry suite for a RISC-V Vehicle Control Unit driving a 1:10 scale RC race car.',
  generator: 'v0.app',
}

export const viewport: Viewport = {
  colorScheme: 'dark',
  themeColor: '#09090b',
  width: 'device-width',
  initialScale: 1,
}

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode
}>) {
  return (
    <html lang="en" className="dark bg-background">
      <body className="font-sans antialiased">
        <SettingsProvider>
          <TelemetryProvider>
            <AppShell>{children}</AppShell>
          </TelemetryProvider>
        </SettingsProvider>
        {process.env.NODE_ENV === 'production' && <Analytics />}
      </body>
    </html>
  )
}
