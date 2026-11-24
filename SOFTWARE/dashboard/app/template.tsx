'use client'

import { motion } from 'framer-motion'
import { useSettings } from '@/contexts/settings-context'

/** Wraps every route to provide a subtle enter transition on navigation. */
export default function Template({ children }: { children: React.ReactNode }) {
  const { settings } = useSettings()

  if (settings.reduceMotion) return <>{children}</>

  return (
    <motion.div
      initial={{ opacity: 0, y: 8 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.28, ease: [0.16, 1, 0.3, 1] }}
    >
      {children}
    </motion.div>
  )
}
