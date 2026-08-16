import { ArrowRight, CircleCheck, Loader2, TriangleAlert } from 'lucide-react'
import { useEffect, useState } from 'react'
import { Button, Field, Input } from '@/components/ui'
import { Brand } from '@/components/layout/Brand'
import { ThemeToggle } from '@/components/layout/ThemeToggle'
import { applyBrand } from '@/lib/brand'
import { ApiError, authConfig } from '@/lib/api'
import { kindMeta, kindOrder } from '@/lib/kinds'
import { useStore } from '@/store/useStore'
import { useT } from '@/lib/useT'

/**
 * KloudChat authenticates against its own user table: email and password, argon2id,
 * a JWT access token in memory with a refresh cookie. LiteLLM never sees these
 * users, only the virtual keys minted on their behalf.
 */
/** Backend `detail` codes → what the person in front of the form needs to read. */
const ERRORS: Record<string, string> = {
  invalid_credentials: '이메일 또는 비밀번호가 올바르지 않습니다.',
  invalid_reset_token: '재설정 링크가 올바르지 않습니다. 다시 요청하세요.',
  reset_token_used: '이미 사용한 링크입니다. 새 비밀번호로 로그인하거나 다시 요청하세요.',
  reset_token_expired: '링크가 만료되었습니다. 다시 요청하세요.',
  email_unavailable: '이미 사용 중인 이메일입니다.',
  account_suspended: '정지된 계정입니다. 관리자에게 문의하세요.',
  signup_closed: '지금은 회원가입을 받지 않습니다. 관리자에게 문의하세요.',
  network_error: '서버에 연결하지 못했습니다. 잠시 후 다시 시도하세요.',
}

/** Anything the map above does not cover. The code goes to the console. */
const UNKNOWN_ERROR = '요청을 처리하지 못했습니다. 잠시 후 다시 시도하세요.'

export function LoginPage() {
  const t = useT()
  const { login, signup, authError, bootstrap } = useStore()
  const [mode, setMode] = useState<'login' | 'signup'>('login')
  //: A mailed link lands here with `?token=`. No router exists while signed
  //: out, so this page reads the query itself.
  const [resetToken, setResetToken] = useState(() =>
    new URLSearchParams(window.location.search).get('token'),
  )
  const [forgotOpen, setForgotOpen] = useState(false)
  const [sent, setSent] = useState(false)
  const [resetDone, setResetDone] = useState(false)
  const [localError, setLocalError] = useState<string | null>(null)
  //: Null until asked: the link is not rendered on a guess, since an instance
  //: with no mail server has no reset to offer.
  const [resetEnabled, setResetEnabled] = useState<boolean | null>(null)
  // This renders before authentication, when the store is empty, so the
  // values come straight from the public configuration.
  const [brand, setBrand] = useState({ name: 'KloudChat', logo: '' })

  useEffect(() => {
    void authConfig
      .get()
      .then((c) => {
        setResetEnabled(c.passwordResetEnabled)
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

  const submitReset = async (e: React.FormEvent) => {
    e.preventDefault()
    if (busy || !resetToken) return
    setBusy(true)
    setLocalError(null)
    try {
      await authConfig.resetPassword(resetToken, password)
      // Spent by the same request, so it does not stay in the address bar.
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
      // Always the same outcome: this form does not disclose registration.
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
      // The reason is already in `authError`; staying on the form is the
      // recovery.
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="flex h-full bg-bg text-fg">
      {/* ── brand column ─────────────────────────────────────────── */}
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
          {/* No component names here. LiteLLM is real, and it is named on the
              admin screens where an operator acts on it — but the person signing
              in is not choosing a proxy, and telling them which one runs the
              calls is an implementation detail wearing a marketing sentence. */}
          <p className="mt-3 text-base leading-relaxed text-muted">
            {t('자료와 지침을 프로젝트에 모아 두면, 다섯 화면이 같은 맥락 위에서 작동합니다. 만든 결과물은 아티팩트로 쌓이고 문서로 내보낼 수 있습니다.')}
          </p>
          <ul className="mt-8 space-y-3">
            {kindOrder.map((kind) => {
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

      {/* ── form column ──────────────────────────────────────────── */}
      <div className="relative flex flex-1 items-center justify-center p-6">
        <ThemeToggle className="absolute top-5 right-5" />

        <div className="w-full max-w-sm">
          {/* 좁은 화면에서는 브랜드 열이 접히므로 폼 위에 한 번 더 그린다 */}
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
                  : t('관리자 승인이 끝나면 크레딧이 배정되고 바로 쓸 수 있습니다.')}
          </p>

          {/* ── 링크로 들어온 재설정 ─────────────────────────────── */}
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

          {/* ── 재설정 요청 ──────────────────────────────────────── */}
          {!resetToken && forgotOpen && (
            <div className="mt-6">
              {sent ? (
                <>
                  {/* Deliberately says nothing about whether that address has an
                      account. Anything else turns this box into a way to ask
                      whether a particular person uses this service. */}
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

          {/* Normally unreachable: the reset signs the person in, so this page
              unmounts. It shows when the session cookie did not take — the
              password *was* changed, and saying nothing would send them back to
              the form wondering whether it worked. */}
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
            <Field label={t('이메일')}>
              <Input
                type="email"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                placeholder="you@example.com"
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

            {/* Offered only when mail is configured. Without a relay there is no
                reset flow at all — the API can change a password given the
                current one and nothing else — and a link that opens nothing is
                the thing that reads as a broken product. */}
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

          {/* The old note here explained that the first account becomes the
              administrator. True once per deployment, and shown to everyone
              forever — including on the sign-in tab, where it answers a question
              nobody asked. It belongs in the operator docs. */}
        </div>
      </div>
    </div>
  )
}
