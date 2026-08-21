import { Loader2, MapPin, ShieldAlert } from 'lucide-react'
import { useEffect, useState } from 'react'
import { Badge, Card } from '@/components/ui'
import { accessApi, errorMessage, type AccessEventRow } from '@/lib/api'
import { browserName, formatDateTime } from '@/lib/utils'
import { useT } from '@/lib/useT'

/**
 * This account's own access record.
 *
 * Deliberately not a session list: there is nothing here to sign out remotely,
 * and a screen that looked like one would imply a button that does not exist.
 * It is the record of what happened — and the reason to read it is to find the
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

  if (error) return <Card className="p-8 text-center text-base text-danger">{error}</Card>
  if (rows === null) {
    return (
      <Card className="grid place-items-center p-10">
        <Loader2 size={16} className="animate-spin text-faint" />
      </Card>
    )
  }
  if (rows.length === 0) {
    return (
      <Card className="p-10 text-center text-base text-muted">{t('아직 기록이 없습니다.')}</Card>
    )
  }

  return (
    <div className="space-y-3">
      <p className="text-base text-muted">
        {t('이 계정에 대한 접속과 보안 변경 기록입니다. 최근 100건까지 남습니다. 기억나지 않는 접속이 있으면 비밀번호를 바꾸세요.')}
      </p>
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
                          {e.region}
                        </span>
                      )}
                    </td>
                    {/* The raw string on hover — the short form drops exactly
                        what would matter if this became a serious question. */}
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
    </div>
  )
}
