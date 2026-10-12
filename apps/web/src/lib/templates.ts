import { useEffect, useState } from 'react'
import { userTemplatesApi } from '@/lib/api'
import type { TemplateCatalogue } from '@/types'

let cached: Promise<TemplateCatalogue> | null = null

/** What the template editor and the agent form's 진행 과정 설정 offer (`/user-templates/catalogue`). */
export function useTemplateCatalogue(): TemplateCatalogue | null {
  const [value, setValue] = useState<TemplateCatalogue | null>(null)
  useEffect(() => {
    let live = true
    cached ??= userTemplatesApi.catalogue().catch((err) => {
      cached = null
      throw err
    })
    cached.then((v) => live && setValue(v)).catch(() => undefined)
    return () => {
      live = false
    }
  }, [])
  return value
}
