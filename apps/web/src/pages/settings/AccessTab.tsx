import { Loader2, LogOut, MapPin, Monitor, ShieldAlert } from 'lucide-react'
import { useEffect, useState } from 'react'
import { Badge, Button, Card, ConfirmDialog } from '@/components/ui'
import { Pager } from '@/components/ui/Pager'
import {
  accessApi,
  errorMessage,
  type AccessEventRow,
  type AccessPage,
  type ActiveSessionPage,
  type ActiveSessionRow,
} from '@/lib/api'
import { pageCount } from '@/lib/paging'
import { browserName, formatDateTime, relativeTime } from '@/lib/utils'
import { useStore } from '@/store/useStore'
import { useT } from '@/lib/useT'
import { PasswordSection } from './PasswordSection'

const ACTION_LABEL: Record<string, string> = {
  login: '로그인',
  signup: '가입',
  'password.change': '비밀번호 변경',
  'password.reset': '비밀번호 재설정',
  'password.reset_requested': '비밀번호 재설정 요청',
  'key.create': 'API 키 발급',
  'key.revoke': 'API 키 폐기',
}

const SESSIONS_PER_PAGE = 10
const EVENTS_PER_PAGE = 20

/** 「로그인된 기기 23」. */
function Heading({ title, total }: { title: string; total: number | null }) {
  return (
    <h3 className="flex items-baseline gap-2 text-base font-medium">
      {title}
      {total !== null && (
        <span className="text-sm font-normal tabular-nums text-faint">
          {total.toLocaleString()}
        </span>
      )}
    </h3>
  )
}

/** Ten a page; own sign-in leads page 1, rest by latest activity; ending the current one signs out. */
function ActiveSessions() {
  const t = useT()
  const idleTimeoutMinutes = useStore((s) => s.idleTimeoutMinutes)
  const [page, setPage] = useState(1)
  const [data, setData] = useState<ActiveSessionPage | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState<string | null>(null)
  const [confirming, setConfirming] = useState<ActiveSessionRow | 'others' | null>(null)
  const [reloads, setReloads] = useState(0)

  useEffect(() => {
    let live = true
    accessApi
      .sessions(page, SESSIONS_PER_PAGE)
      .then((res) => {
        if (!live) return
        // Emptied page: step back to the last one.
        const last = pageCount(res.total, SESSIONS_PER_PAGE)
        if (res.items.length === 0 && page > last) return setPage(last)
        setData(res)
      })
      .catch((err) => live && setError(errorMessage(err, t('로그인 목록을 불러오지 못했습니다.'))))
    return () => {
      live = false
    }
  }, [page, reloads, t])

  const end = async (target: ActiveSessionRow | 'others') => {
    setBusy(target === 'others' ? 'others' : target.familyId)
    try {
      if (target === 'others') {
        await accessApi.endOtherSessions()
        setPage(1)
      } else {
        await accessApi.endSession(target.familyId)
        // Cookie burned: tear down locally.
        if (target.current) return void useStore.getState().logout()
      }
      setReloads((n) => n + 1)
    } catch (err) {
      setError(errorMessage(err, t('로그아웃하지 못했습니다.')))
    } finally {
      setBusy(null)
    }
  }

  const rows = data?.items ?? null
  const total = data?.total ?? null

  return (
    <section className="space-y-3" data-testid="active-sessions">
      <Heading title={t('로그인된 기기')} total={total} />
      <div className="flex flex-wrap items-end justify-between gap-3">
        <p className="text-base text-muted">
          {t('이 계정이 지금 로그인되어 있는 브라우저입니다. 실습실이나 도서관 PC처럼 기억나지 않는 항목이 있으면 여기서 바로 종료하세요.')}
          {idleTimeoutMinutes > 0 && (
            <>
              {' '}
              {t('{n}분 동안 사용하지 않으면 자동으로 로그아웃됩니다.').replace(
                '{n}',
                String(idleTimeoutMinutes),
              )}
            </>
          )}
        </p>
        {(data?.others ?? 0) > 0 && (
          <Button
            size="sm"
            onClick={() => setConfirming('others')}
            disabled={busy !== null}
          >
            <LogOut size={13} />
            {t('다른 기기 모두 로그아웃')}
          </Button>
        )}
      </div>

      {error && <Card className="p-4 text-base text-danger">{error}</Card>}

      {rows === null ? (
        <Card className="grid place-items-center p-8">
          <Loader2 size={16} className="animate-spin text-faint" />
        </Card>
      ) : (
        <Card className="divide-y divide-line">
          {rows.map((r) => (
            <div
              key={r.familyId}
              data-testid="session-row"
              className="flex min-h-14 items-center gap-3 px-4 py-2"
            >
              <Monitor size={14} className="shrink-0 text-faint" />
              <div className="min-w-0 flex-1">
                <p className="flex items-center gap-2 text-sm leading-5">
                  <span className="truncate" title={r.userAgent}>
                    {browserName(r.userAgent) || t('알 수 없는 브라우저')}
                  </span>
                  {r.current && <Badge tone="success">{t('현재 기기')}</Badge>}
                </p>
                <p className="flex flex-wrap items-center gap-x-2.5 text-xs leading-4 text-faint">
                  <span className="font-mono tabular-nums">{r.ip || t('주소 없음')}</span>
                  {r.region && (
                    <span className="inline-flex items-center gap-1">
                      <MapPin size={10} />
                      {t(r.region)}
                    </span>
                  )}
                  {/* Relative: one line on a phone; exact time in the tooltip. */}
                  <time
                    dateTime={r.lastSeenAt}
                    title={formatDateTime(r.lastSeenAt)}
                    className="whitespace-nowrap"
                  >
                    {t('최근 사용')} {relativeTime(r.lastSeenAt)}
                  </time>
                </p>
              </div>
              <Button
                size="sm"
                variant="ghost"
                onClick={() => setConfirming(r)}
                disabled={busy !== null}
              >
                {busy === r.familyId ? (
                  <Loader2 size={13} className="animate-spin" />
                ) : (
                  t('종료')
                )}
              </Button>
            </div>
          ))}
        </Card>
      )}

      {data && data.total > SESSIONS_PER_PAGE && (
        <Pager
          page={page}
          size={SESSIONS_PER_PAGE}
          total={data.total}
          onPage={setPage}
          label={t('로그인된 기기 페이지')}
          className=""
        />
      )}

      <ConfirmDialog
        open={confirming !== null}
        onClose={() => setConfirming(null)}
        title={confirming === 'others' ? t('다른 기기 모두 로그아웃') : t('이 로그인 종료')}
        description={
          confirming === 'others'
            ? t('현재 기기를 제외한 모든 로그인이 해제됩니다.')
            : confirming?.current
              ? t('현재 사용 중인 기기입니다. 종료하면 이 화면에서도 로그아웃됩니다.')
              : t('해당 브라우저의 로그인이 즉시 해제됩니다.')
        }
        confirmLabel={t('로그아웃')}
        onConfirm={() => {
          const target = confirming
          setConfirming(null)
          if (target) void end(target)
        }}
      />
    </section>
  )
}

/** Twenty a page, newest first. */
function AccessLog() {
  const t = useT()
  const [page, setPage] = useState(1)
  const [data, setData] = useState<AccessPage<AccessEventRow> | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let live = true
    accessApi
      .mine(page, EVENTS_PER_PAGE)
      .then((r) => live && setData(r))
      .catch((err) => live && setError(errorMessage(err, t('접속 기록을 불러오지 못했습니다.'))))
    return () => {
      live = false
    }
  }, [page, t])

  const rows = data?.items ?? null

  return (
    <section className="space-y-3" data-testid="access-log">
      <Heading title={t('접속 기록')} total={data?.total ?? null} />
      <p className="text-base text-muted">
        {t('이 계정에 대한 접속과 보안 변경 기록입니다. 기억나지 않는 접속이 있으면 비밀번호를 바꾸세요.')}
      </p>
      {error ? (
        <Card className="p-8 text-center text-base text-danger">{error}</Card>
      ) : rows === null ? (
        <Card className="grid place-items-center p-10">
          <Loader2 size={16} className="animate-spin text-faint" />
        </Card>
      ) : rows.length === 0 ? (
        <Card className="p-10 text-center text-base text-muted">
          {t('아직 기록이 없습니다.')}
        </Card>
      ) : (
        <Card className="overflow-hidden">
          <div className="overflow-x-auto">
            <table className="w-full text-base">
              <thead className="border-b border-line text-left text-xs tracking-wide text-faint uppercase">
                <tr>
                  <th className="px-4 py-2.5 font-medium">{t('시각')}</th>
                  <th className="px-4 py-2.5 font-medium">{t('행위')}</th>
                  <th className="px-4 py-2.5 font-medium">{t('접속 위치')}</th>
                  <th className="px-4 py-2.5 font-medium">{t('브라우저')}</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-line">
                {rows.map((e) => {
                  const failed = e.detail === 'failed' || e.severity === 'warn'
                  return (
                    <tr key={e.id} className="hover:bg-elevated">
                      <td className="px-4 py-2.5 whitespace-nowrap tabular-nums text-muted">
                        {formatDateTime(e.at)}
                      </td>
                      <td className="px-4 py-2.5 whitespace-nowrap">
                        {failed ? (
                          <Badge tone="warn">
                            <ShieldAlert size={10} />
                            {t('로그인 실패')}
                          </Badge>
                        ) : (
                          t(ACTION_LABEL[e.action] ?? e.action)
                        )}
                      </td>
                      <td className="px-4 py-2.5 text-sm whitespace-nowrap">
                        <span className="font-mono text-xs tabular-nums text-faint">
                          {e.ip || t('주소 없음')}
                        </span>
                        {e.region && (
                          <span className="ml-2 inline-flex items-center gap-1 text-muted">
                            <MapPin size={11} className="text-faint" />
                            {t(e.region)}
                          </span>
                        )}
                      </td>
                      <td
                        className="px-4 py-2.5 text-sm whitespace-nowrap text-muted"
                        title={e.userAgent}
                      >
                        {browserName(e.userAgent) || t('알 수 없음')}
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        </Card>
      )}
      {data && data.total > EVENTS_PER_PAGE && (
        <Pager
          page={page}
          size={EVENTS_PER_PAGE}
          total={data.total}
          onPage={setPage}
          label={t('접속 기록 페이지')}
          className=""
        />
      )}
    </section>
  )
}

export function AccessTab() {
  return (
    <div className="space-y-8">
      <PasswordSection />
      <ActiveSessions />
      <AccessLog />
    </div>
  )
}
