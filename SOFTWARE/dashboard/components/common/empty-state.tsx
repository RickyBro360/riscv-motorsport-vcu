import type { LucideIcon } from 'lucide-react'
import type { ReactNode } from 'react'
import { cn } from '@/lib/utils'

interface EmptyStateProps {
  icon: LucideIcon
  title: string
  description?: string
  action?: ReactNode
  tone?: 'default' | 'danger'
  className?: string
}

/** Consistent empty / error placeholder. */
export function EmptyState({
  icon: Icon,
  title,
  description,
  action,
  tone = 'default',
  className,
}: EmptyStateProps) {
  return (
    <div
      className={cn(
        'flex min-h-[320px] flex-col items-center justify-center gap-4 p-10 text-center',
        className,
      )}
    >
      <div
        className={cn(
          'flex size-16 items-center justify-center rounded-full',
          tone === 'danger' ? 'bg-danger/10 text-danger' : 'bg-primary/10 text-primary',
        )}
      >
        <Icon className="size-7" />
      </div>
      <div>
        <h2 className="text-lg font-semibold text-balance">{title}</h2>
        {description && (
          <p className="mx-auto mt-1 max-w-md text-sm text-muted-foreground text-pretty">
            {description}
          </p>
        )}
      </div>
      {action}
    </div>
  )
}
