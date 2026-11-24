import type { ReactNode } from 'react'
import type { LucideIcon } from 'lucide-react'
import { cn } from '@/lib/utils'
import { Card } from '@/components/ui/card'

interface PanelProps {
  title?: string
  icon?: LucideIcon
  action?: ReactNode
  children: ReactNode
  className?: string
  bodyClassName?: string
}

export function Panel({
  title,
  icon: Icon,
  action,
  children,
  className,
  bodyClassName,
}: PanelProps) {
  return (
    <Card className={cn('flex flex-col overflow-hidden', className)}>
      {(title || action) && (
        <div className="flex items-center justify-between gap-2 border-b border-border px-4 py-3">
          <div className="flex items-center gap-2">
            {Icon && <Icon className="size-4 text-muted-foreground" />}
            <h2 className="text-xs font-semibold uppercase tracking-widest text-muted-foreground">
              {title}
            </h2>
          </div>
          {action}
        </div>
      )}
      <div className={cn('flex-1 p-4', bodyClassName)}>{children}</div>
    </Card>
  )
}
