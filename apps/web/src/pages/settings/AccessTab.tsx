import { Loader2, LogOut, MapPin, Monitor, ShieldAlert } from 'lucide-react'
import { useCallback, useEffect, useState } from 'react'
import { Badge, Button, Card, ConfirmDialog } from '@/components/ui'
import {
  accessApi,
  errorMessage,
  type AccessEventRow,
  type ActiveSessionRow,
} from '@/lib/api'
import { browserName, formatDateTime } from '@/lib/utils'
import { useStore } from '@/store/useStore'
import { useT } from '@/lib/useT'

/**
 * Where this account is signed in, and what has happened to it.
 *
 * Two halves, in the order somebody needs them. The sessions are the half that
 * can be acted on: a browser still holding a live cookie on a machine the
 * person has walked away from, and a button that ends it from here. The record
 * below is the half that explains — and the reason to read it is to find the
 * one line that was not you.
 *
 * Which is why failed sign-ins are on it and are marked. A successful login
 * from Seoul is a row nobody studies; four failures from an address nobody
 * recognises is the entire point of the screen, and burying them among the
 * successes would make it a screen that technically contains the answer.
 */
const ACTION_LABEL: Record<string, string> = {
  login: '로그인',
  signup: '가입',
  'password.change': '비밀번호 변경',
  'password.reset': '비밀번호 재설정',
  'password.reset_requested': '비밀번호 재설정 요청',
  'key.create': 'API 키 발급',
  'key.revoke': 'API 키 폐기',
}


/**
 * The live sign-ins, newest activity first.
 *
 * A shared lab PC is the case this exists for: somebody signs in, forgets, and
 * walks out. Until now the only way to end that session was to go back and sit
 * at the machine. The current browser is marked and kept at arm's length — its
 * 종료 signs the reader out on the spot, which is fine as long as it does not
 * happen by accident.
 */
function ActiveSessions() {
  const t = useT()
  const idleTimeoutMinutes = useStore((s) => s.idleTimeoutMinutes)
  const [rows, setRows] = useState<ActiveSessionRow[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState<string | null>(null)
  const [confirming, setConfirming] = useState<ActiveSessionRow | 'others' | null>(null)

  const load = useCallback(() => {
    accessApi
      .sessions()
      .then(setRows)
      .catch((err) => setError(errorMessage(err, t('로그인 목록을 불러오지 못했습니다.'))))
  }, [t])

  useEffect(load, [load])

  const end = async (target: ActiveSessionRow | 'others') => {
    setBusy(target === 'others' ? 'others' : target.familyId)
    try {
      if (target === 'others') {
        await accessApi.endOtherSessions()
      } else {
        await accessApi.endSession(target.familyId)
        // Ending your own session leaves the tab holding a burned cookie. The
        // local teardown is what turns that into a sign-in screen rather than
        // a screen that keeps failing.
        if (target.current) return void useStore.getState().logout()
      }
      load()
    } catch (err) {
      setError(errorMessage(err, t('로그아웃하지 못했습니다.')))
    } finally {
      setBusy(null)
    }
  }

  const others = (rows ?? []).filter((r) => !r.current).length

  return (
    <div className="space-y-3">
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
        {others > 0 && (
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
            <div key={r.familyId} className="flex items-center gap-3 px-4 py-3">
              <Monitor size={15} className="shrink-0 text-faint" />
              <div className="min-w-0 flex-1">
                <p className="flex items-center gap-2 text-base">
                  <span className="truncate" title={r.userAgent}>
                    {browserName(r.userAgent) || t('알 수 없는 브라우저')}
                  </span>
                  {r.current && <Badge tone="success">{t('현재 기기')}</Badge>}
                </p>
                <p className="mt-0.5 flex flex-wrap items-center gap-x-2.5 gap-y-0.5 text-sm text-faint">
                  <span className="font-mono text-xs tabular-nums">
                    {r.ip || t('주소 없음')}
                  </span>
                  {r.region && (
                    <span className="inline-flex items-center gap-1">
                      <MapPin size={11} />
                      {t(r.region)}
                    </span>
                  )}
                  <span>
                    {t('최근 사용')} {formatDateTime(r.lastSeenAt)}
                  </span>
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
    </div>
  )
}

export function AccessTab() {
  const t = useT()
  const [rows, setRows] = useState<AccessEventRow[] | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let live = true
    accessApi
      .mine()
      .then((r) => live && setRows(r))
      .catch((err) => live && setError(errorMessage(err, t('접속 기록을 불러오지 못했습니다.'))))
    return () => {
      live = false
    }
  }, [t])

  return (
    <div className="space-y-8">
      <section className="space-y-3">
        <h3 className="text-base font-medium">{t('로그인된 기기')}</h3>
        <ActiveSessions />
      </section>

      {/* The record sits below the sessions and fails on its own. It used to
          be the whole tab, so its loading and error states returned early —
          which would now take the one actionable half of the screen down with
          the half that only explains. */}
      <section className="space-y-3">
        <h3 className="text-base font-medium">{t('접속 기록')}</h3>
        <p className="text-base text-muted">
          {t('이 계정에 대한 접속과 보안 변경 기록입니다. 최근 100건까지 남습니다. 기억나지 않는 접속이 있으면 비밀번호를 바꾸세요.')}
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
            {/* Scrolls inside its own box: the browser column is long enough to
                push the page sideways on a laptop otherwise. */}
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
                        {/* The raw string on hover — the short form drops
                            exactly what would matter if this became a serious
                            question. */}
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
      </section>
    </div>
  )
}
