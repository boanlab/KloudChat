import { CircleCheck, Plug, TriangleAlert } from 'lucide-react'
import { useCallback, useEffect, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { Badge, Button, Card, ConfirmDialog, LoadingState } from '@/components/ui'
import { errorMessage, integrationsApi, type IntegrationProvider, type IntegrationRow } from '@/lib/api'
import { relativeTime } from '@/lib/utils'
import { FEATURE_SENTENCE } from '@/lib/integrations'
import { useT } from '@/lib/useT'

const NAMES: Record<IntegrationProvider, string> = { google: 'Google 드라이브', slack: 'Slack' }

/** One sentence per service: what connecting it lets the app do. */
const ABOUT: Record<IntegrationProvider, string> = {
  google: '내 드라이브에서 고른 파일만 읽고, 만든 문서를 저장합니다.',
  slack: '내 Slack 계정으로 채널을 읽고, 확인한 메시지만 보냅니다.',
}

const FAILURE: Record<string, string> = {
  denied: '연결하지 않았습니다.',
  state_invalid: '연결 시간이 지났습니다. 다시 시도하세요.',
  integration_unavailable: '관리자가 이 연동을 꺼 두었습니다.',
}

/** 설정 › 연결: the person's own Google and Slack accounts, and nobody else's. */
export function ConnectionsTab() {
  const t = useT()
  const [params, setParams] = useSearchParams()
  const [rows, setRows] = useState<IntegrationRow[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState<IntegrationProvider | null>(null)
  const [leaving, setLeaving] = useState<IntegrationProvider | null>(null)

  const load = useCallback(async () => {
    try {
      setRows(await integrationsApi.list())
      setError(null)
    } catch (err) {
      setError(errorMessage(err, t('연결 목록을 불러오지 못했습니다.')))
    }
  }, [t])
  useEffect(() => {
    void load()
  }, [load])

  // Back from the provider: say how it went once, then drop it from the address.
  const connected = params.get('connected') as IntegrationProvider | null
  const failed = params.get('error')
  const failedProvider = params.get('provider') as IntegrationProvider | null
  const [notice] = useState(() =>
    connected
      ? { ok: true, text: t('{name}에 연결했습니다.').replace('{name}', t(NAMES[connected] ?? connected)) }
      : failed
        ? {
            ok: false,
            text:
              failed === 'denied' && failedProvider === 'slack'
                ? t('연결하지 않았습니다. 워크스페이스에 앱을 설치할 수 없다면 Slack 관리자에게 요청하세요.')
                : t(FAILURE[failed] ?? '연결하지 못했습니다. 다시 시도하세요.'),
          }
        : null,
  )
  useEffect(() => {
    if (connected || failed) setParams({}, { replace: true })
  }, [connected, failed, setParams])

  const connect = async (provider: IntegrationProvider) => {
    setBusy(provider)
    try {
      const { url } = await integrationsApi.connect(provider)
      window.location.assign(url)
    } catch (err) {
      setError(errorMessage(err, t('연결을 시작하지 못했습니다.')))
      setBusy(null)
    }
  }
  const disconnect = async (provider: IntegrationProvider) => {
    setBusy(provider)
    try {
      await integrationsApi.disconnect(provider)
      await load()
    } catch (err) {
      setError(errorMessage(err, t('연결을 끊지 못했습니다.')))
    } finally {
      setBusy(null)
    }
  }

  return (
    <div className="space-y-5">
      <div>
        <h2 className="text-base font-semibold">{t('연결')}</h2>
        <p className="text-sm text-muted">{t('내 계정만 연결됩니다. 다른 사람은 내 연결을 쓸 수 없습니다.')}</p>
      </div>

      {notice && (
        <p
          role="status"
          className={`flex items-start gap-1.5 rounded-control border px-3 py-2 text-base ${
            notice.ok ? 'border-success/25 bg-success/5 text-success' : 'border-warn/25 bg-warn/5 text-warn'
          }`}
        >
          {notice.ok ? <CircleCheck size={14} className="mt-0.5 shrink-0" /> : <TriangleAlert size={14} className="mt-0.5 shrink-0" />}
          {notice.text}
        </p>
      )}
      {error && (
        <p role="alert" className="rounded-control border border-danger/25 bg-danger/5 px-3 py-2 text-base text-danger">
          {error}
        </p>
      )}

      {rows === null && !error ? (
        <Card>
          <LoadingState label={t('불러오는 중입니다…')} />
        </Card>
      ) : rows && rows.length === 0 ? (
        <Card className="p-8 text-center">
          <p className="text-base font-medium">{t('연결할 수 있는 서비스가 없습니다')}</p>
          <p className="mt-1 text-base text-muted">{t('관리자가 연동을 켜면 여기에 나타납니다.')}</p>
        </Card>
      ) : (
        <div className="space-y-2">
          {(rows ?? []).map((row) => {
            const c = row.connection
            const name = t(NAMES[row.provider])
            const features = c ? c.features : row.features
            return (
              <Card key={row.provider} className="p-4" data-testid={`connection-${row.provider}`}>
                <div className="flex flex-wrap items-start gap-3">
                  <span className="grid size-9 shrink-0 place-items-center rounded-control bg-elevated text-muted">
                    <Plug size={16} />
                  </span>
                  <div className="min-w-0 flex-1 basis-48">
                    <p className="flex flex-wrap items-center gap-2 text-base font-medium">
                      {name}
                      {c?.status === 'connected' && <Badge tone="success">{t('연결됨')}</Badge>}
                      {c?.status === 'needs_auth' && <Badge tone="warn">{t('다시 연결 필요')}</Badge>}
                    </p>
                    <p className="text-sm text-muted">{t(ABOUT[row.provider])}</p>
                    {c && (
                      <p className="mt-0.5 text-sm text-faint">
                        {c.label}
                        {c.lastUsedAt
                          ? ` · ${t('{when} 사용').replace('{when}', relativeTime(c.lastUsedAt))}`
                          : ` · ${t('{when} 연결').replace('{when}', relativeTime(c.connectedAt))}`}
                      </p>
                    )}
                  </div>
                  <div className="flex shrink-0 gap-1.5">
                    {(!c || c.status === 'needs_auth') && (
                      <Button variant="primary" disabled={busy === row.provider} onClick={() => void connect(row.provider)}>
                        {c ? t('다시 연결') : t('연결')}
                      </Button>
                    )}
                    {c && (
                      <Button disabled={busy === row.provider} onClick={() => setLeaving(row.provider)}>
                        {t('연결 해제')}
                      </Button>
                    )}
                  </div>
                </div>
                {features.length > 0 && (
                  <ul className="mt-3 space-y-0.5 border-t border-line pt-2.5 text-sm text-muted" aria-label={t('{name} 권한').replace('{name}', name)}>
                    {features
                      .filter((f) => FEATURE_SENTENCE[`${row.provider}.${f}`])
                      .map((f) => (
                        <li key={f}>· {t(FEATURE_SENTENCE[`${row.provider}.${f}`])}</li>
                      ))}
                  </ul>
                )}
              </Card>
            )
          })}
        </div>
      )}

      <ConfirmDialog
        open={leaving !== null}
        onClose={() => setLeaving(null)}
        onConfirm={() => leaving && void disconnect(leaving)}
        title={t('{name} 연결을 해제할까요?').replace('{name}', leaving ? t(NAMES[leaving]) : '')}
        description={t('권한을 되돌리고 저장한 토큰을 지웁니다. 가져온 파일은 남습니다.')}
        confirmLabel={t('연결 해제')}
      />
    </div>
  )
}
