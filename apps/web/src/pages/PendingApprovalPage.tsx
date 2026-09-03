import { Ban, Clock, LogOut, Mail, MailCheck, RefreshCw, Send } from 'lucide-react'
import { useEffect, useState } from 'react'
import { Button, Card } from '@/components/ui'
import { ApiError, authConfig } from '@/lib/api'
import { formatDate } from '@/lib/utils'
import { ThemeToggle } from '@/components/layout/ThemeToggle'
import { useStore } from '@/store/useStore'
import { useT } from '@/lib/useT'

/**
 * Gate for accounts that are not `active`. Signup creates a `pending` user, and
 * nothing in the app is reachable until an admin approves and assigns a monthly
 * credit allowance.
 */
export function PendingApprovalPage() {
  const t = useT()
  const { user, logout, refreshMe } = useStore()
  const suspended = user?.status === 'suspended'
  //: The mailed link has not been clicked yet. Verifying happens in a mail
  //: client and lands in another tab; the poll below is what moves this one.
  const unverified = !suspended && user?.emailVerifiedAt === null
  //: Whom 관리자에게 문의 reaches — the address the administrator set, or the
  //: first administrator's own. It was `admin@example.com`, literally.
  const [contact, setContact] = useState<string>('')
  useEffect(() => {
    void authConfig
      .get()
      .then((c) => setContact(c.contactEmail ?? ''))
      .catch(() => undefined)
  }, [])
  const [resent, setResent] = useState<string | null>(null)
  const [sending, setSending] = useState(false)
  const resend = async () => {
    setSending(true)
    setResent(null)
    try {
      await authConfig.resendVerification()
      setResent(t('확인 메일을 다시 보냈습니다. 메일함을 확인하세요.'))
    } catch (err) {
      const code = err instanceof ApiError ? err.detail : ''
      setResent(
        code === 'verify_resend_too_soon'
          ? t('방금 보냈습니다. 1분 뒤에 다시 시도하세요.')
          : code === 'mail_not_configured'
            ? t('메일 발송이 설정되지 않았습니다. 관리자에게 문의하세요.')
            : t('보내지 못했습니다. 잠시 후 다시 시도하세요.'),
      )
    } finally {
      setSending(false)
    }
  }

  // Approval happens in another browser, on someone else's schedule. Polling is
  // what turns "reload until it works" into a screen that just opens. Suspension
  // is terminal, so there is nothing to wait for there.
  useEffect(() => {
    if (suspended) return
    const timer = setInterval(() => void refreshMe(), 15_000)
    return () => clearInterval(timer)
  }, [suspended, refreshMe])

  return (
    <div className="relative flex h-full items-center justify-center bg-bg p-6 text-fg">
      <ThemeToggle className="absolute top-5 right-5" />

      <div className="w-full max-w-md text-center">
        <div
          className={`mx-auto mb-5 grid size-12 place-items-center rounded-panel ${
            suspended ? 'bg-danger/10 text-danger' : 'bg-warn/10 text-warn'
          }`}
        >
          {suspended ? <Ban size={22} /> : unverified ? <MailCheck size={22} /> : <Clock size={22} />}
        </div>

        <h1 className="text-xl font-semibold tracking-tight">
          {suspended
            ? t('계정이 정지되었습니다')
            : unverified
              ? t('메일의 링크를 눌러 주소를 확인해 주세요')
              : t('승인을 기다리는 중입니다')}
        </h1>
        <p className="mt-2 text-base leading-relaxed text-muted">
          {suspended
            ? t('관리자가 이 계정의 접근을 중지했습니다. 사유가 궁금하다면 관리자에게 문의하세요.')
            : unverified
              ? t('가입한 주소로 확인 메일을 보냈습니다. 메일의 링크를 누르면 가입이 끝납니다. 메일이 보이지 않으면 스팸함을 확인하거나 다시 보내세요.')
              : t('가입 요청이 접수되었습니다. 관리자가 승인하고 월 크레딧을 배정하면 바로 사용할 수 있습니다. 승인되면 등록하신 메일로 알려 드립니다.')}
        </p>

        <Card className="mt-6 space-y-2.5 p-4 text-left">
          {[
            [t('이름'), user?.name],
            [t('이메일'), user?.email],
            [t('요청일'), user && formatDate(user.createdAt)],
            [t('상태'), suspended ? t('정지') : unverified ? t('주소 확인 대기') : t('승인 대기')],
          ].map(([k, v]) => (
            <div key={k} className="flex gap-3 text-base">
              <span className="w-16 shrink-0 text-faint">{k}</span>
              <span className="min-w-0 flex-1 break-words">{v}</span>
            </div>
          ))}
        </Card>

        {resent && <p className="mt-4 text-base text-muted">{resent}</p>}

        <div className="mt-5 flex flex-wrap justify-center gap-2">
          {unverified && (
            <Button variant="primary" disabled={sending} onClick={() => void resend()}>
              <Send size={15} />
              {t('확인 메일 다시 보내기')}
            </Button>
          )}
          <Button onClick={() => void refreshMe()}>
            <RefreshCw size={15} />
            {t('상태 새로고침')}
          </Button>
          {contact && (
            <Button
              title={contact}
              onClick={() =>
                (window.location.href = `mailto:${contact}?subject=${encodeURIComponent(
                  `[KloudChat] ${t('가입 문의')} — ${user?.email ?? ''}`,
                )}`)
              }
            >
              <Mail size={15} />
              {t('관리자에게 문의')}
            </Button>
          )}
          <Button variant="ghost" onClick={() => void logout()}>
            <LogOut size={15} />
            {t('로그아웃')}
          </Button>
        </div>

        {!suspended && (
          <p className="mt-6 text-xs text-faint">
            {unverified
              ? t('이 화면은 15초마다 상태를 확인합니다. 링크를 누르면 자동으로 넘어갑니다.')
              : t('이 화면은 15초마다 상태를 확인합니다. 승인되면 자동으로 넘어갑니다.')}
          </p>
        )}
      </div>
    </div>
  )
}
