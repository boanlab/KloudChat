import { Shield } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import { Badge, Button, Card, Field, Input } from '@/components/ui'
import { USER_STATUS_LABEL } from '@/lib/userStatus'
import { formatDate } from '@/lib/utils'
import { useStore } from '@/store/useStore'
import { useT } from '@/lib/useT'

/** Name only: email is the login identity; password under 보안. */
function ProfileFields() {
  const t = useT()
  const { user, updateProfile } = useStore()
  const [name, setName] = useState(user?.name ?? '')
  const [saved, setSaved] = useState(false)
  const [nameError, setNameError] = useState<string | null>(null)
  const nameRef = useRef<HTMLInputElement>(null)
  useEffect(() => {
    setName(user?.name ?? '')
  }, [user?.name])

  return (
    <div className="space-y-4 border-t border-line pt-5">
        <form
          className="flex items-start gap-2"
          noValidate
          onSubmit={async (e) => {
            e.preventDefault()
            if (!name.trim()) {
              setNameError(t('이름을 입력하세요.'))
              nameRef.current?.focus()
              return
            }
            await updateProfile({ name: name.trim() })
            setSaved(true)
          }}
        >
          <Field label={t('이름')} required error={nameError}>
            <Input
              ref={nameRef}
              value={name}
              onChange={(e) => {
                setName(e.target.value)
                setSaved(false)
                setNameError(null)
              }}
              className="w-64"
            />
          </Field>
          <div className="flex items-center gap-2 pt-7">
            <Button type="submit" disabled={name === user?.name} title={name === user?.name ? t('바뀐 내용이 없습니다') : undefined}>
              {t('저장')}
            </Button>
            {saved && <span className="text-sm text-success">{t('저장됨')}</span>}
          </div>
        </form>

    </div>
  )
}

/** Identity, allowance, reset cycle. */
export function ProfileTab() {
  const t = useT()
  const { user } = useStore()
  const remaining = (user?.monthlyCredits ?? 0) - (user?.creditsUsed ?? 0)
  const pct = user?.monthlyCredits ? (user.creditsUsed / user.monthlyCredits) * 100 : 0

  return (
    <div className="space-y-6">
      <Card className="flex flex-wrap items-center gap-3.5 p-4">
        <span
          className="grid size-11 shrink-0 place-items-center rounded-full text-md font-semibold text-white"
          style={{ background: user?.avatarColor }}
        >
          {user?.name?.[0]}
        </span>
        <div className="min-w-0 flex-1">
          <p className="text-base font-medium">{user?.name}</p>
          <p className="text-base text-muted">{user?.email}</p>
          <div className="mt-2 flex flex-wrap gap-1.5">
            {user?.role === 'admin' && (
              <Badge tone="accent">
                <Shield size={10} />
                {t('관리자')}
              </Badge>
            )}
            <Badge tone={user?.status === 'active' ? 'success' : 'warn'}>{user && t(USER_STATUS_LABEL[user.status])}</Badge>
            <Badge>{t('가입')} {user && formatDate(user.createdAt)}</Badge>
          </div>
        </div>
        <div className="text-right">
          <p className="text-xs text-faint">{t('남은 크레딧')}</p>
          <p className="text-lg font-semibold tabular-nums">{remaining.toLocaleString()}</p>
          <p className="text-xs text-faint">/ {t('{n} 월').replace('{n}', user?.monthlyCredits.toLocaleString() ?? '0')}</p>
        </div>
      </Card>

      <div>
        <div className="h-1.5 overflow-hidden rounded-full bg-elevated">
          <div
            className="h-full rounded-full bg-accent"
            style={{ width: `${Math.min(pct, 100)}%` }}
          />
        </div>
        <p className="mt-1.5 text-xs text-faint">
          {t('{date}에 {n} 크레딧으로 리필됩니다. 남은 크레딧은 이월되지 않습니다. 한도 변경은 관리자에게 문의하세요.')
            .replace('{date}', user ? formatDate(user.cycleResetsAt) : '')
            .replace('{n}', user?.monthlyCredits.toLocaleString() ?? '0')}
        </p>
      </div>

      <ProfileFields />
    </div>
  )
}
