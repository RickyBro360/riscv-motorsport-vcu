'use client'

import { Component, type ErrorInfo, type ReactNode } from 'react'
import { AlertTriangle, RotateCcw } from 'lucide-react'
import { Button } from '@/components/ui/button'

export class AppErrorBoundary extends Component<{ children: ReactNode }, { hasError: boolean }> {
  state = { hasError: false }
  static getDerivedStateFromError() { return { hasError: true } }
  componentDidCatch(error: Error, info: ErrorInfo) { console.error('[v0] App shell error', error, info) }
  render() { if (this.state.hasError) return <div className="flex min-h-[50vh] flex-col items-center justify-center gap-4 text-center"><AlertTriangle className="size-8 text-warning" /><div><h1 className="text-lg font-semibold">Engineering surface unavailable</h1><p className="mt-1 text-sm text-muted-foreground">The view failed to render. Reset the surface and try again.</p></div><Button onClick={() => this.setState({ hasError: false })}><RotateCcw className="size-4" /> Reset surface</Button></div>; return this.props.children }
}
