import { TriangleAlert } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import { Badge, Button, Field, Input } from '@/components/ui'
import { ApiError, adminApi, type SystemSettings } from '@/lib/api'
import { useT } from '@/lib/useT'

const MODES: { value: 'open' | 'approval' | 'closed'; label: string; note: string }[] = [
  {
    value: 'approval',
    label: '승인 후 사용',
    note: '가입하면 대기 상태가 되고, 관리자가 승인하며 월 크레딧을 정합니다.',
  },
  {
    value: 'open',
    label: '바로 사용',
    note: '가입 즉시 기본 월 크레딧으로 사용할 수 있습니다.',
  },
  {
    value: 'closed',
    label: '받지 않음',
    note: '가입 화면이 요청을 거절합니다. 계정은 관리자만 만듭니다.',
  },
]

export function SignupSection({
  settings,
  onSaved,
}: {
  settings: SystemSettings | null
  onSaved: () => Promise<void>
}) {
  const t = useT()
  const [mode, setMode] = useState<'open' | 'approval' | 'closed'>('approval')
  const [domains, setDomains] = useState('')
  const [verify, setVerify] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  // Typing wins over a fetch that lands late.
  const dirty = useRef(false)

  useEffect(() => {
    if (!settings || dirty.current) return
    setMode(settings.signup.mode)
    setDomains(settings.signup.domains.join(', '))
    setVerify(settings.signup.verifyEmail)
  }, [settings])

  if (!settings) return null

  const stored = settings.signup
  const changed =
    mode !== stored.mode ||
    domains.trim() !== stored.domains.join(', ') ||
    verify !== stored.verifyEmail

  const save = async () => {
    setBusy(true)
    setError(null)
    try {
      await adminApi.updateSettings({
        signupMode: mode,
        signupDomains: domains,
        signupVerifyEmail: verify ? 'on' : '',
      })
      dirty.current = false
      await onSaved()
    } catch (err) {
      setError(
        err instanceof ApiError && err.detail
          ? `${t('저장하지 못했습니다.')} (${err.detail})`
          : t('저장하지 못했습니다.'),
      )
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="space-y-5">
      <Field label={t('가입 방식')}>
        <div className="space-y-2">
          {MODES.map((row) => (
            <label
              key={row.value}
              className="flex cursor-pointer items-start gap-3 rounded-control border border-line px-3 py-2.5"
            >
              <input
                type="radio"
                name="signup-mode"
                checked={mode === row.value}
                onChange={() => {
                  dirty.current = true
                  setMode(row.value)
                }}
                className="mt-1 size-4 accent-[var(--accent)]"
              />
              <span className="min-w-0 flex-1">
                <span className="text-base font-medium">{t(row.label)}</span>
                <span className="ml-2 text-sm text-muted">{t(row.note)}</span>
              </span>
              {stored.mode === row.value && stored.modeSource === 'environment' && (
                <Badge title={t('SIGNUP_MODE 환경변수의 값입니다. 여기서 고르면 그 값을 덮어씁니다.')}>
                  {t('환경변수')}
                </Badge>
              )}
            </label>
          ))}
        </div>
      </Field>

      <Field
        label={t('가입 가능한 이메일 도메인')}
        hint={t(
          '쉼표로 구분합니다. 비우면 어떤 주소로도 가입할 수 있습니다. 하위 도메인은 따로 적어야 합니다.',
        )}
      >
        <Input
          value={domains}
          onChange={(e) => {
            dirty.current = true
            setDomains(e.target.value)
          }}
          placeholder="dankook.ac.kr, kloud.zone"
          className="font-mono text-base"
        />
      </Field>

      <Field label={t('이메일 인증')}>
        <label className="flex cursor-pointer items-start gap-3 rounded-control border border-line px-3 py-2.5">
          <input
            type="checkbox"
            checked={verify}
            onChange={(e) => {
              dirty.current = true
              setVerify(e.target.checked)
            }}
            className="mt-1 size-4 accent-[var(--accent)]"
          />
          <span className="min-w-0 flex-1">
            <span className="text-base font-medium">{t('가입한 주소로 확인 메일을 보냅니다')}</span>
            <span className="ml-2 text-sm text-muted">
              {t('메일의 링크를 누른 뒤에야 가입한 것으로 칩니다. 승인 방식이면 그 뒤에 승인을 기다립니다.')}
            </span>
          </span>
        </label>
        {verify && !settings.smtp.passwordResetEnabled && (
          <p className="mt-2 flex items-start gap-1.5 text-sm text-warn">
            <TriangleAlert size={14} className="mt-0.5 shrink-0" />
            {t('메일 탭에 발송 서버가 설정되지 않아, 켜 두어도 확인 메일은 나가지 않고 인증 없이 가입됩니다.')}
          </p>
        )}
      </Field>

      {error && (
        <p className="flex items-start gap-1.5 rounded-control border border-danger/25 bg-danger/5 px-3 py-2 text-base text-danger">
          <TriangleAlert size={14} className="mt-0.5 shrink-0" />
          {error}
        </p>
      )}

      <div className="flex items-center gap-2">
        <Button
          disabled={busy || !changed}
          title={!changed ? t('바뀐 내용이 없습니다') : busy ? t('저장 중…') : undefined}
          onClick={() => void save()}
        >
          {t('저장')}
        </Button>
        <span className="text-sm text-muted">
          {t('바로 적용됩니다. 이미 가입한 계정에는 영향이 없습니다.')}
        </span>
      </div>
    </div>
  )
}
