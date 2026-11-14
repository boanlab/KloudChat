import { Eye, EyeOff } from 'lucide-react'
import { useState, type ComponentProps, type KeyboardEvent } from 'react'
import { Input } from '@/components/ui'
import { useT } from '@/lib/useT'
import { cn } from '@/lib/utils'

/** A password box with 보기/숨기기 and a Caps Lock warning, as sign-in forms usually have. */
export function PasswordInput({ className, onKeyDown, onKeyUp, onBlur, ...props }: Omit<ComponentProps<'input'>, 'type'>) {
  const t = useT()
  const [shown, setShown] = useState(false)
  const [caps, setCaps] = useState(false)
  const readCaps = (e: KeyboardEvent<HTMLInputElement>) => setCaps(e.getModifierState?.('CapsLock') ?? false)
  return (
    <div>
      <div className="relative">
        <Input
          {...props}
          type={shown ? 'text' : 'password'}
          className={cn('pr-10', className)}
          onKeyDown={(e) => {
            readCaps(e)
            onKeyDown?.(e)
          }}
          onKeyUp={(e) => {
            readCaps(e)
            onKeyUp?.(e)
          }}
          onBlur={(e) => {
            setCaps(false)
            onBlur?.(e)
          }}
        />
        <button
          type="button"
          onClick={() => setShown((v) => !v)}
          aria-label={shown ? t('입력한 글자 숨기기') : t('입력한 글자 보기')}
          aria-pressed={shown}
          title={shown ? t('입력한 글자 숨기기') : t('입력한 글자 보기')}
          className="absolute top-1/2 right-1 grid size-8 -translate-y-1/2 place-items-center rounded-control text-faint hover:bg-elevated hover:text-fg focus-visible:outline-2 focus-visible:outline-accent"
        >
          {shown ? <EyeOff size={15} aria-hidden /> : <Eye size={15} aria-hidden />}
        </button>
      </div>
      {caps && (
        <p role="status" className="mt-1 text-xs text-warn">
          {t('Caps Lock이 켜져 있습니다')}
        </p>
      )}
    </div>
  )
}
