import { Check, Circle } from 'lucide-react'
import { cn } from '@/lib/utils'

import { useT } from '@/lib/useT'

/** Live password checklist; rule from `/auth/config` → `password.minLength`. */
export function PasswordRule({ value, minLength }: { value: string; minLength: number }) {
  const t = useT()
  const met = value.length >= minLength
  return (
    <ul aria-live="polite" className="space-y-0.5">
      <li className={cn('flex items-center gap-1.5', met ? 'text-success' : 'text-faint')}>
        {met ? <Check size={13} aria-hidden /> : <Circle size={11} aria-hidden />}
        <span>
          {t('{n}자 이상').replace('{n}', String(minLength))}
          {value.length > 0 && !met && (
            <span> · {t('지금 {n}자').replace('{n}', String(value.length))}</span>
          )}
          {met && <span className="sr-only"> · {t('충족')}</span>}
        </span>
      </li>
    </ul>
  )
}
