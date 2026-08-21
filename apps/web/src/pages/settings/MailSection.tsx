import { CircleCheck, Mail, TriangleAlert } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import { Button, Field, Input } from '@/components/ui'
import { ApiError, adminApi, type SystemSettings } from '@/lib/api'
import { useT } from '@/lib/useT'

/**
 * The relay, with its own save and its own test.
 *
 * Independent of the proxy on purpose: a mail change should not disturb the
 * model catalogue, and the one thing this block buys — password reset — is
 * either on or off, so the screen leads with that rather than with the fields.
 */
export function MailSection({
  settings,
  reload,
}: {
  settings: SystemSettings | null
  reload: () => Promise<void>
}) {
  const t = useT()
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [smtp, setSmtp] = useState({
    host: '',
    port: '',
    security: 'starttls',
    username: '',
    password: '',
    from: '',
    appBaseUrl: '',
  })
  const [mailProbe, setMailProbe] = useState<{ ok: boolean; detail: string } | null>(null)
  /** Typing wins over a fetch that lands late. See `ProxySection`. */
  const dirty = useRef(false)

  useEffect(() => {
    if (!settings || dirty.current) return
    setSmtp({
      host: settings.smtp.host,
      port: settings.smtp.port,
      security: settings.smtp.security || 'starttls',
      username: settings.smtp.username,
      // Same rule as the master key: an empty box means "leave it alone".
      password: '',
      from: settings.smtp.from,
      appBaseUrl: settings.smtp.appBaseUrl,
    })
  }, [settings])

  return (
    <div>
      <div className="flex flex-wrap items-center gap-3">
        <Mail size={18} className={settings?.smtp.passwordResetEnabled ? 'text-success' : 'text-muted'} />
        <div className="min-w-0 flex-1">
          <p className="text-base font-medium">{t('메일 발송')}</p>
          {/* The consequence, not the configuration. An operator filling this
              in is doing it for one reason, and the screen should say whether
              that reason is satisfied yet. */}
          <p className="text-base text-muted">
            {settings?.smtp.passwordResetEnabled
              ? t('비밀번호 재설정이 켜져 있습니다. 로그인 화면에 재설정 링크가 보입니다.')
              : t('설정되지 않아 비밀번호 재설정이 꺼져 있습니다. 로그인 화면은 관리자에게 문의하라고 안내합니다.')}
          </p>
        </div>
        <Button
          disabled={busy}
          title={busy ? t('설정을 불러오거나 저장하는 중입니다') : undefined}
          onClick={async () => {
            setBusy(true)
            setMailProbe(null)
            try {
              const result = await adminApi.testSmtp()
              setMailProbe({
                ok: result.ok,
                detail: result.detail ?? (result.ok ? t('보냈습니다.') : t('보내지 못했습니다.')),
              })
            } finally {
              setBusy(false)
            }
          }}
        >
          {t('테스트 발송')}
        </Button>
      </div>

      {mailProbe && (
        <p
          className={`mt-3 flex items-start gap-1.5 rounded-control border px-3 py-2 text-base ${
            mailProbe.ok
              ? 'border-success/25 bg-success/5 text-success'
              : 'border-danger/25 bg-danger/5 text-danger'
          }`}
        >
          {mailProbe.ok ? (
            <CircleCheck size={14} className="mt-0.5 shrink-0" />
          ) : (
            <TriangleAlert size={14} className="mt-0.5 shrink-0" />
          )}
          {mailProbe.detail}
        </p>
      )}

      <div className="mt-4 space-y-4">
        <div className="grid gap-4 sm:grid-cols-[1fr_8rem_9rem]">
          <Field label={t('SMTP 서버')}>
            <Input
              value={smtp.host}
              onChange={(e) => {
                setSmtp((v) => ({ ...v, host: e.target.value }))
                dirty.current = true
              }}
              placeholder="smtp.example.com"
              className="font-mono text-base"
            />
          </Field>
          <Field label={t('포트')}>
            <Input
              value={smtp.port}
              onChange={(e) => {
                setSmtp((v) => ({ ...v, port: e.target.value }))
                dirty.current = true
              }}
              placeholder="587"
              inputMode="numeric"
              className="font-mono text-base"
            />
          </Field>
          {/* Named modes rather than a checkbox: STARTTLS and SSL use different
              ports and a different handshake, and picking the wrong one fails
              as a timeout with no hint which half was wrong. */}
          <Field label={t('보안')}>
            <select
              value={smtp.security}
              onChange={(e) => {
                setSmtp((v) => ({ ...v, security: e.target.value }))
                dirty.current = true
              }}
              className="w-full rounded-control border border-line bg-panel px-3 py-2 text-base outline-none focus:border-accent"
            >
              <option value="starttls">STARTTLS (587)</option>
              <option value="ssl">SSL/TLS (465)</option>
              <option value="none">{t('사용 안 함')}</option>
            </select>
          </Field>
        </div>

        <div className="grid gap-4 sm:grid-cols-2">
          <Field label={t('사용자 이름')} hint={t('인증이 필요 없으면 비워 두세요.')}>
            <Input
              value={smtp.username}
              onChange={(e) => {
                setSmtp((v) => ({ ...v, username: e.target.value }))
                dirty.current = true
              }}
              autoComplete="off"
              className="font-mono text-base"
            />
          </Field>
          <Field label={t('비밀번호')} hint={t('저장한 비밀번호는 표시하지 않습니다. 그대로 두면 현재 값을 유지합니다.')}>
            <Input
              type="password"
              value={smtp.password}
              onChange={(e) => {
                setSmtp((v) => ({ ...v, password: e.target.value }))
                dirty.current = true
              }}
              placeholder={
                settings?.smtp.passwordSet
                  ? t('현재 {preview} — 바꾸려면 새 값을 입력').replace('{preview}', settings.smtp.passwordPreview)
                  : t('설정되지 않음')
              }
              autoComplete="off"
              className="font-mono text-base"
            />
          </Field>
        </div>

        <Field label={t('보내는 주소')} hint={t('메일 서버가 인정하는 주소여야 합니다. 그렇지 않으면 발송이 거부됩니다.')}>
          <Input
            value={smtp.from}
            onChange={(e) => {
              setSmtp((v) => ({ ...v, from: e.target.value }))
              dirty.current = true
            }}
            placeholder="KloudChat <no-reply@example.com>"
            className="font-mono text-base"
          />
        </Field>

        <Field
          label={t('서비스 주소')}
          hint={t('비밀번호 재설정 메일에 담길 주소입니다. 사용자가 접속하는 주소를 넣으세요.')}
        >
          <Input
            value={smtp.appBaseUrl}
            onChange={(e) => {
              setSmtp((v) => ({ ...v, appBaseUrl: e.target.value }))
              dirty.current = true
            }}
            placeholder="https://kchat.example.com"
            className="font-mono text-base"
          />
        </Field>

        {error && (
          <p className="flex items-start gap-1.5 rounded-control border border-danger/25 bg-danger/5 px-3 py-2 text-base text-danger">
            <TriangleAlert size={14} className="mt-0.5 shrink-0" />
            {error}
          </p>
        )}

        <Button
          variant="primary"
          disabled={busy}
          title={busy ? t('설정을 불러오거나 저장하는 중입니다') : undefined}
          onClick={async () => {
            setBusy(true)
            setError(null)
            setMailProbe(null)
            try {
              await adminApi.updateSettings({
                smtpHost: smtp.host,
                smtpPort: smtp.port,
                smtpSecurity: smtp.security,
                smtpUsername: smtp.username,
                ...(smtp.password ? { smtpPassword: smtp.password } : {}),
                smtpFrom: smtp.from,
                appBaseUrl: smtp.appBaseUrl,
              })
              dirty.current = false
              await reload()
            } catch (err) {
              setError(err instanceof ApiError ? err.detail : t('저장에 실패했습니다.'))
            } finally {
              setBusy(false)
            }
          }}
        >
          {busy ? t('저장 중…') : t('메일 설정 저장')}
        </Button>
      </div>
    </div>
  )
}
