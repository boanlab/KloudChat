import { ArrowRight, CircleCheck, Clock, Loader2, TriangleAlert } from 'lucide-react'
import { useEffect, useState } from 'react'
import { Button, Field, Input } from '@/components/ui'
import { Brand } from '@/components/layout/Brand'
import { ThemeToggle } from '@/components/layout/ThemeToggle'
import { applyBrand } from '@/lib/brand'
import { ApiError, authConfig } from '@/lib/api'
import { kindMeta, kindOrder } from '@/lib/kinds'
import type { SessionKind } from '@/types'
import { useStore } from '@/store/useStore'
import { useT } from '@/lib/useT'

/** Backend `detail` codes mapped to user-facing messages. */
const ERRORS: Record<string, string> = {
  invalid_credentials: '이메일 또는 비밀번호가 올바르지 않습니다.',
  invalid_reset_token: '재설정 링크가 올바르지 않습니다. 다시 요청하세요.',
  reset_token_used: '이미 사용한 링크입니다. 새 비밀번호로 로그인하거나 다시 요청하세요.',
  reset_token_expired: '링크가 만료되었습니다. 다시 요청하세요.',
  email_unavailable: '이미 사용 중인 이메일입니다.',
  account_suspended: '정지된 계정입니다. 관리자에게 문의하세요.',
  account_locked: '로그인에 다섯 번 연속 실패해 잠시 잠겼습니다. 15분 뒤에 다시 시도하세요.',
  signup_closed: '지금은 회원가입을 받지 않습니다. 관리자에게 문의하세요.',
  signup_domain_not_allowed: '이 이메일 도메인으로는 가입할 수 없습니다. 허용된 주소를 쓰세요.',
  invalid_verify_token: '확인 링크가 올바르지 않습니다. 로그인해서 확인 메일을 다시 받으세요.',
  verify_token_used: '이미 확인한 링크입니다. 로그인하세요.',
  verify_token_expired: '확인 링크가 만료되었습니다. 로그인해서 확인 메일을 다시 받으세요.',
  network_error: '서버에 연결하지 못했습니다. 잠시 후 다시 시도하세요.',
}

const UNKNOWN_ERROR = '요청을 처리하지 못했습니다. 잠시 후 다시 시도하세요.'

export function LoginPage() {
  const t = useT()
  const { login, signup, authError, bootstrap, signedOutReason, adoptSession } = useStore()
  const [mode, setMode] = useState<'login' | 'signup'>('login')
  // No router while signed out: `?token=` on `/verify` is signup confirmation, elsewhere a password reset.
  const [resetToken, setResetToken] = useState(() =>
    window.location.pathname === '/verify'
      ? null
      : new URLSearchParams(window.location.search).get('token'),
  )
  const [verifying, setVerifying] = useState(() =>
    window.location.pathname === '/verify'
      ? new URLSearchParams(window.location.search).get('token')
      : null,
  )
  const [verified, setVerified] = useState<'active' | 'pending' | null>(null)
  const [signupPolicy, setSignupPolicy] = useState<{
    domains: string[]
    emailVerification: boolean
  } | null>(null)
  const [forgotOpen, setForgotOpen] = useState(false)
  const [sent, setSent] = useState(false)
  const [resetDone, setResetDone] = useState(false)
  const [localError, setLocalError] = useState<string | null>(null)
  // Null until the public config answers; nothing is rendered on a guess.
  const [resetEnabled, setResetEnabled] = useState<boolean | null>(null)
  const [enabledKinds, setEnabledKinds] = useState<SessionKind[] | null>(null)
  const [brand, setBrand] = useState({ name: 'KloudChat', logo: '' })

  useEffect(() => {
    void authConfig
      .get()
      .then((c) => {
        setResetEnabled(c.passwordResetEnabled)
        if (c.signup) setSignupPolicy(c.signup)
        // Ordered as in the sidebar.
        setEnabledKinds(kindOrder.filter((kind) => c.enabledKinds.includes(kind)))
        if (c.brand) {
          setBrand(c.brand)
          applyBrand(c.brand)
        }
      })
      .catch(() => setResetEnabled(false))
  }, [])
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [name, setName] = useState('')
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    if (!verifying) return
    const token = verifying
    void authConfig
      .verifyEmail(token)
      .then(({ status, session }) => {
        // Token is spent; drop it from the address bar.
        window.history.replaceState(null, '', '/')
        setVerifying(null)
        if (session) adoptSession(session)
        else setVerified(status === 'active' ? 'active' : 'pending')
      })
      .catch((err) => {
        window.history.replaceState(null, '', '/')
        setVerifying(null)
        const code = err instanceof ApiError ? err.detail : ''
        setLocalError(ERRORS[code] ?? t('주소를 확인하지 못했습니다. 로그인해서 확인 메일을 다시 받으세요.'))
      })
    // Run once: re-running would spend the token twice.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const submitReset = async (e: React.FormEvent) => {
    e.preventDefault()
    if (busy || !resetToken) return
    setBusy(true)
    setLocalError(null)
    try {
      await authConfig.resetPassword(resetToken, password)
      window.history.replaceState(null, '', window.location.pathname)
      setResetToken(null)
      setResetDone(true)
      await bootstrap()
    } catch (err) {
      const code = err instanceof ApiError ? err.detail : ''
      setLocalError(ERRORS[code] ?? t('비밀번호를 바꾸지 못했습니다. 링크를 다시 요청하세요.'))
    } finally {
      setBusy(false)
    }
  }

  const submitForgot = async (e: React.FormEvent) => {
    e.preventDefault()
    if (busy) return
    setBusy(true)
    try {
      await authConfig.forgotPassword(email).catch(() => null)
    } finally {
      // Same outcome either way: must not disclose whether the address is registered.
      setSent(true)
      setBusy(false)
    }
  }

  const submit = async (e: React.FormEvent) => {
    e.preventDefault()
    if (busy) return
    setBusy(true)
    try {
      if (mode === 'login') await login(email, password)
      else await signup(email, password, name)
    } catch {
      // Reason is in `authError`.
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="flex h-full bg-bg text-fg">
      <div className="relative hidden flex-1 flex-col justify-between overflow-hidden border-r border-line bg-sidebar p-10 lg:flex">
        <div
          className="pointer-events-none absolute -top-32 -left-24 size-[420px] rounded-full opacity-25 blur-3xl"
          style={{ background: 'var(--accent)' }}
        />
        <div className="relative">
          <Brand name={brand.name} logo={brand.logo} size="md" />
        </div>

        <div className="relative max-w-md">
          <h1 className="text-3xl leading-tight font-semibold tracking-tight">
            {t('대화에서 끝나지 않는')}
            <br />
            {t('생성형 AI 워크스페이스')}
          </h1>
          <p className="mt-3 text-base leading-relaxed text-muted">
            {t('자료와 지침을 프로젝트에 모아 두면, 모든 화면이 같은 맥락 위에서 작동합니다. 만든 결과물은 아티팩트로 쌓이고 문서로 내보낼 수 있습니다.')}
          </p>
          <ul className="mt-8 space-y-3">
            {(enabledKinds ?? []).map((kind) => {
              const meta = kindMeta[kind]
              const Icon = meta.icon
              return (
                <li key={kind} className="flex items-start gap-3">
                  <span
                    className="mt-0.5 grid size-6 shrink-0 place-items-center rounded-control text-white"
                    style={{ background: meta.color }}
                  >
                    <Icon size={13} />
                  </span>
                  <span>
                    <span className="text-base font-medium">{t(meta.label)}</span>
                    <span className="ml-2 text-base text-muted">{t(meta.tagline)}</span>
                  </span>
                </li>
              )
            })}
          </ul>
        </div>

        <p className="relative text-xs text-faint">Apache-2.0</p>
      </div>

      <div className="relative flex flex-1 items-center justify-center p-6">
        <ThemeToggle className="absolute top-5 right-5" />

        <div className="w-full max-w-sm">
          {/* Brand column is hidden on narrow screens. */}
          <div className="mb-7 lg:hidden">
            <Brand name={brand.name} logo={brand.logo} size="md" />
          </div>
          <h2 className="text-xl font-semibold tracking-tight">
            {resetToken ? t('새 비밀번호 설정') : forgotOpen ? t('비밀번호 재설정') : mode === 'login' ? t('로그인') : t('계정 만들기')}
          </h2>
          <p className="mt-1 text-base text-muted">
            {resetToken
              ? t('새 비밀번호를 정하면 바로 로그인됩니다. 다른 기기의 로그인은 모두 해제됩니다.')
              : forgotOpen
                ? t('가입한 이메일 주소로 재설정 링크를 보냅니다.')
                : mode === 'login'
                  ? t('등록된 계정으로 이어서 작업합니다.')
                  : signupPolicy?.emailVerification
                    ? t('가입하면 확인 메일이 갑니다. 메일의 링크를 누르면 가입이 끝납니다.')
                    : t('관리자 승인이 끝나면 크레딧이 배정되고 바로 쓸 수 있습니다.')}
          </p>

          {verifying && (
            <div className="mt-6 flex items-center gap-2 rounded-control border border-line bg-elevated px-3 py-2.5 text-base text-muted">
              <Loader2 size={14} className="animate-spin" />
              <span>{t('가입한 주소를 확인하는 중…')}</span>
            </div>
          )}
          {verified && (
            <div className="mt-6 flex items-start gap-2 rounded-control border border-success/25 bg-success/5 px-3 py-2.5 text-base text-success">
              <CircleCheck size={14} className="mt-0.5 shrink-0" />
              <span>
                {verified === 'active'
                  ? t('주소를 확인했습니다. 로그인하세요.')
                  : t('주소를 확인했습니다. 관리자가 승인하면 쓸 수 있습니다. 로그인하면 진행 상황이 보입니다.')}
              </span>
            </div>
          )}
          {localError && !resetToken && !forgotOpen && (
            <div className="mt-6 flex items-start gap-2 rounded-control border border-danger/25 bg-danger/5 px-3 py-2.5 text-base text-danger">
              <TriangleAlert size={14} className="mt-0.5 shrink-0" />
              <span>{localError}</span>
            </div>
          )}

          {signedOutReason === 'idle' && !resetToken && !forgotOpen && (
            <div className="mt-5 flex items-start gap-2 rounded-control border border-line bg-elevated px-3 py-2.5 text-base text-muted">
              <Clock size={14} className="mt-0.5 shrink-0" />
              <span>{t('한동안 사용하지 않아 자동으로 로그아웃되었습니다. 다시 로그인하세요.')}</span>
            </div>
          )}

          {resetToken && (
            <form className="mt-6 space-y-4" onSubmit={submitReset}>
              <Field label={t('새 비밀번호')} hint={t('10자 이상, 숫자와 기호 포함')}>
                <Input
                  type="password"
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  autoComplete="new-password"
                  autoFocus
                />
              </Field>
              {localError && (
                <div className="flex items-start gap-2 rounded-control border border-danger/30 bg-danger/5 px-3 py-2.5 text-base text-danger">
                  <TriangleAlert size={14} className="mt-0.5 shrink-0" />
                  <span>{localError}</span>
                </div>
              )}
              <Button
                type="submit"
                variant="primary"
                size="lg"
                className="w-full"
                disabled={busy || password.length < 10}
              >
                {busy ? <Loader2 size={16} className="animate-spin" /> : t('비밀번호 바꾸기')}
              </Button>
            </form>
          )}

          {!resetToken && forgotOpen && (
            <div className="mt-6">
              {sent ? (
                <>
                  {/* Must not disclose whether the address has an account. */}
                  <div className="flex items-start gap-2 rounded-control border border-success/25 bg-success/5 px-3 py-2.5 text-base text-success">
                    <CircleCheck size={14} className="mt-0.5 shrink-0" />
                    <span>
                      {t('해당 주소로 가입된 계정이 있다면 재설정 링크를 보냈습니다. 30분 안에 사용하세요.')}
                    </span>
                  </div>
                  <Button
                    className="mt-4 w-full"
                    onClick={() => {
                      setForgotOpen(false)
                      setSent(false)
                    }}
                  >
                    {t('로그인으로 돌아가기')}
                  </Button>
                </>
              ) : (
                <form className="space-y-4" onSubmit={submitForgot}>
                  <Field label={t('이메일')}>
                    <Input
                      type="email"
                      value={email}
                      onChange={(e) => setEmail(e.target.value)}
                      placeholder="you@example.com"
                      autoComplete="username"
                      autoFocus
                    />
                  </Field>
                  <Button
                    type="submit"
                    variant="primary"
                    size="lg"
                    className="w-full"
                    disabled={busy || !email}
                  >
                    {busy ? <Loader2 size={16} className="animate-spin" /> : t('재설정 링크 받기')}
                  </Button>
                  <Button className="w-full" onClick={() => setForgotOpen(false)}>
                    {t('취소')}
                  </Button>
                </form>
              )}
            </div>
          )}

          {/* Shown only when the reset succeeded but the session cookie did not take. */}
          {resetDone && (
            <div className="mt-6 flex items-start gap-2 rounded-control border border-success/25 bg-success/5 px-3 py-2.5 text-base text-success">
              <CircleCheck size={14} className="mt-0.5 shrink-0" />
              <span>{t('비밀번호를 바꿨습니다. 새 비밀번호로 로그인하세요.')}</span>
            </div>
          )}

          {!resetToken && !forgotOpen && (
          <div className="mt-6 flex gap-1 rounded-control border border-line bg-elevated p-1">
            {(
              [
                { id: 'login', label: t('로그인') },
                { id: 'signup', label: t('회원가입') },
              ] as const
            ).map((t) => (
              <button
                key={t.id}
                onClick={() => setMode(t.id)}
                className={`flex-1 rounded-control px-3 py-1.5 text-base font-medium transition-colors ${
                  mode === t.id ? 'bg-panel text-fg shadow-raised' : 'text-muted hover:text-fg'
                }`}
              >
                {t.label}
              </button>
            ))}
          </div>
          )}

          {!resetToken && !forgotOpen && (
          <form className="mt-5 space-y-4" onSubmit={submit}>
            {mode === 'signup' && (
              <Field label={t('이름')}>
                <Input
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                  placeholder={t('홍길동')}
                  autoComplete="name"
                />
              </Field>
            )}
            <Field
              label={t('이메일')}
              hint={
                mode === 'signup' && signupPolicy && signupPolicy.domains.length > 0
                  ? `${t('가입 가능한 주소')}: ${signupPolicy.domains.map((d) => `@${d}`).join(', ')}`
                  : undefined
              }
            >
              <Input
                type="email"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                placeholder={
                  mode === 'signup' && signupPolicy?.domains[0]
                    ? `you@${signupPolicy.domains[0]}`
                    : 'you@example.com'
                }
                autoComplete="username"
              />
            </Field>
            <Field
              label={t('비밀번호')}
              hint={mode === 'signup' ? t('10자 이상, 숫자와 기호 포함') : undefined}
            >
              <Input
                type="password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                autoComplete={mode === 'login' ? 'current-password' : 'new-password'}
              />
            </Field>

            {/* Reset link only when mail is configured. */}
            {mode === 'login' &&
              (resetEnabled ? (
                <div className="flex justify-end">
                  <button
                    type="button"
                    className="text-sm text-muted hover:text-fg"
                    onClick={() => {
                      setForgotOpen(true)
                      setSent(false)
                    }}
                  >
                    {t('비밀번호를 잊으셨나요?')}
                  </button>
                </div>
              ) : (
                resetEnabled === false && (
                  <p className="text-right text-sm text-faint">
                    {t('비밀번호를 잊었다면 관리자에게 문의하세요.')}
                  </p>
                )
              ))}

            {authError && (
              <div className="flex items-start gap-2 rounded-control border border-danger/30 bg-danger/5 px-3 py-2.5 text-base text-danger">
                <TriangleAlert size={14} className="mt-0.5 shrink-0" />
                <span>{ERRORS[authError] ?? UNKNOWN_ERROR}</span>
              </div>
            )}

            <Button
              type="submit"
              variant="primary"
              size="lg"
              className="w-full"
              disabled={busy || !email || !password || (mode === 'signup' && !name)}
            >
              {busy ? (
                <Loader2 size={16} className="animate-spin" />
              ) : (
                <>
                  {mode === 'login' ? t('로그인') : t('가입 요청')}
                  <ArrowRight size={16} />
                </>
              )}
            </Button>
          </form>
          )}
        </div>
      </div>
    </div>
  )
}
