import { CircleCheck, TriangleAlert } from 'lucide-react'
import { useCallback, useEffect, useState } from 'react'
import { Badge, Button, Card, ConfirmDialog, Field, Input, LoadingState, Switch } from '@/components/ui'
import { CopyButton } from '@/components/ui/CopyButton'
import { PasswordInput } from '@/components/ui/PasswordInput'
import {
  errorMessage,
  integrationsApi,
  type AdminIntegration,
  type AdminIntegrationAccount,
  type IntegrationProvider,
} from '@/lib/api'
import { formatDateTime } from '@/lib/utils'
import { useT } from '@/lib/useT'
import { FEATURE_SENTENCE } from '@/lib/integrations'

const NAMES: Record<IntegrationProvider, string> = { google: 'Google 드라이브', slack: 'Slack' }

/** 시스템 › 연동: the one OAuth client each provider uses for this installation. */
export function IntegrationsSection() {
  const t = useT()
  const [data, setData] = useState<{ providers: AdminIntegration[]; baseUrl: string } | null>(null)
  const [error, setError] = useState<string | null>(null)
  const load = useCallback(async () => {
    try {
      setData(await integrationsApi.admin())
      setError(null)
    } catch (err) {
      setError(errorMessage(err, t('연동 설정을 불러오지 못했습니다.')))
    }
  }, [t])
  useEffect(() => {
    void load()
  }, [load])

  if (!data) {
    return error ? (
      <p role="alert" className="text-base text-danger">{error}</p>
    ) : (
      <LoadingState label={t('불러오는 중입니다…')} />
    )
  }
  return (
    <div className="space-y-4">
      {!data.baseUrl && (
        <p role="alert" className="flex items-start gap-1.5 rounded-control border border-warn/25 bg-warn/5 px-3 py-2 text-base text-warn">
          <TriangleAlert size={14} className="mt-0.5 shrink-0" />
          {t('시스템 › 메일 탭의 「서비스 주소」를 먼저 적으세요. 연동 로그인 후 돌아올 주소입니다.')}
        </p>
      )}
      {data.providers.map((p) => (
        <ProviderCard
          key={p.provider}
          provider={p}
          onSaved={(next) => setData((d) => d && { ...d, providers: d.providers.map((x) => (x.provider === next.provider ? next : x)) })}
        />
      ))}
    </div>
  )
}

function ProviderCard({ provider, onSaved }: { provider: AdminIntegration; onSaved: (next: AdminIntegration) => void }) {
  const t = useT()
  const google = provider.provider === 'google'
  const [clientId, setClientId] = useState(provider.clientId)
  const [secret, setSecret] = useState('')
  const [signing, setSigning] = useState('')
  const [pickerKey, setPickerKey] = useState(provider.pickerKey ?? '')
  const [appId, setAppId] = useState(provider.appId ?? '')
  const [features, setFeatures] = useState<string[]>(provider.features)
  const [busy, setBusy] = useState(false)
  const [saved, setSaved] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [turningOff, setTurningOff] = useState(false)
  const [people, setPeople] = useState<AdminIntegrationAccount[] | null>(null)

  const save = async (patch: Record<string, unknown>) => {
    setBusy(true)
    setError(null)
    setSaved(false)
    try {
      const next = await integrationsApi.adminSave(provider.provider, patch)
      onSaved(next)
      setSecret('')
      setSigning('')
      setSaved(true)
    } catch (err) {
      setError(errorMessage(err, t('저장하지 못했습니다.')))
    } finally {
      setBusy(false)
    }
  }
  const name = t(NAMES[provider.provider])

  return (
    <Card className="space-y-4 p-4" data-testid={`integration-admin-${provider.provider}`}>
      <div className="flex flex-wrap items-center gap-3">
        <div className="min-w-0 flex-1 basis-48">
          <p className="flex items-center gap-2 text-base font-medium">
            {name}
            {provider.usable ? <Badge tone="success">{t('켜짐')}</Badge> : <Badge>{t('꺼짐')}</Badge>}
          </p>
          <p className="text-sm text-muted">
            {google
              ? t('사람마다 자기 Google 계정을 연결합니다.')
              : t('사람마다 자기 Slack 계정을 연결합니다.')}
          </p>
        </div>
        <Switch
          checked={provider.enabled}
          label={t('{name} 켜기').replace('{name}', name)}
          disabled={busy}
          onChange={(on) => (on ? void save({ enabled: true }) : setTurningOff(true))}
        />
      </div>

      <Field label={t('리디렉션 URI')} hint={t('클라이언트 설정에 이 주소를 그대로 넣으세요.')}>
        <div className="flex items-center gap-1.5">
          <Input readOnly value={provider.redirectUri} className="min-w-0 flex-1 font-mono text-sm" />
          {provider.redirectUri && <CopyButton text={provider.redirectUri} label={t('리디렉션 URI')} />}
        </div>
      </Field>
      <div className="grid gap-4 sm:grid-cols-2">
        <Field label={t('클라이언트 ID')}>
          <Input value={clientId} onChange={(e) => setClientId(e.target.value)} className="font-mono text-sm" />
        </Field>
        <Field
          label={t('클라이언트 보안 비밀번호')}
          hint={provider.clientSecretSet ? t('저장됨 {preview} · 비워 두면 그대로 둡니다').replace('{preview}', provider.clientSecretPreview) : undefined}
        >
          <PasswordInput value={secret} onChange={(e) => setSecret(e.target.value)} autoComplete="off" />
        </Field>
        {google ? (
          <>
            <Field label={t('Picker API 키')} hint={t('이 사이트 주소로 제한한 브라우저 키입니다.')}>
              <Input value={pickerKey} onChange={(e) => setPickerKey(e.target.value)} className="font-mono text-sm" />
            </Field>
            <Field label={t('프로젝트 번호')} hint={t('Google Cloud 프로젝트 번호(앱 ID)입니다.')}>
              <Input value={appId} onChange={(e) => setAppId(e.target.value)} className="font-mono text-sm" inputMode="numeric" />
            </Field>
          </>
        ) : (
          <Field
            label={t('서명 보안 비밀번호')}
            hint={provider.signingSecretSet ? t('저장됨 · 비워 두면 그대로 둡니다') : t('공개 에이전트 봇이 받는 이벤트를 확인합니다.')}
          >
            <PasswordInput value={signing} onChange={(e) => setSigning(e.target.value)} autoComplete="off" />
          </Field>
        )}
      </div>
      {!google && provider.eventsUrl && (
        <Field label={t('이벤트 요청 URL')} hint={t('Slack 앱의 Event Subscriptions에 넣습니다.')}>
          <div className="flex items-center gap-1.5">
            <Input readOnly value={provider.eventsUrl} className="min-w-0 flex-1 font-mono text-sm" />
            <CopyButton text={provider.eventsUrl} label={t('이벤트 요청 URL')} />
          </div>
        </Field>
      )}

      <fieldset>
        <legend className="mb-1.5 text-base font-medium">{t('쓸 수 있는 기능')}</legend>
        <div className="space-y-1">
          {provider.allFeatures.map((f) => (
            <label key={f} className="flex items-center gap-2 text-base">
              <input
                type="checkbox"
                checked={features.includes(f)}
                onChange={(e) => setFeatures(e.target.checked ? [...features, f] : features.filter((x) => x !== f))}
              />
              {t(FEATURE_SENTENCE[`${provider.provider}.${f}`] ?? f)}
            </label>
          ))}
        </div>
        <p className="mt-1.5 break-all font-mono text-xs text-faint">{provider.scopes.join(' ')}</p>
      </fieldset>

      <div className="flex flex-wrap items-center gap-2">
        <Button
          variant="primary"
          disabled={busy}
          onClick={() =>
            void save({
              clientId,
              ...(secret ? { clientSecret: secret } : {}),
              ...(google ? { pickerKey, appId } : signing ? { signingSecret: signing } : {}),
              features,
            })
          }
        >
          {t('저장')}
        </Button>
        {saved && (
          <span role="status" className="flex items-center gap-1 text-sm text-success">
            <CircleCheck size={13} />
            {t('저장됨')}
          </span>
        )}
        {error && <span role="alert" className="text-sm text-danger">{error}</span>}
      </div>

      {!google && (provider.workspaces?.length ?? 0) > 0 && (
        <div>
          <p className="text-base font-medium">{t('앱이 설치된 워크스페이스')}</p>
          <p className="mb-1 text-sm text-muted">{t('우리 조직으로 표시한 곳에서만 공개 에이전트가 구성원에게 하듯 답합니다.')}</p>
          <ul className="space-y-1 text-base">
            {provider.workspaces!.map((w) => (
              <li key={w.teamId} className="flex flex-wrap items-center gap-2">
                <span className="min-w-0 flex-1">{w.name || w.teamId}</span>
                <label className="flex items-center gap-1.5 text-sm">
                  <input
                    type="checkbox"
                    checked={Boolean(w.internal)}
                    disabled={busy}
                    onChange={async (e) => onSaved(await integrationsApi.adminMarkSlack(w.teamId, e.target.checked))}
                  />
                  {t('우리 조직 워크스페이스')}
                </label>
                <span className="text-sm text-faint">{formatDateTime(w.installedAt)}</span>
                <Button
                  size="sm"
                  variant="ghost"
                  disabled={busy}
                  onClick={async () => onSaved(await integrationsApi.adminUninstallSlack(w.teamId))}
                >
                  {t('봇 제거')}
                </Button>
              </li>
            ))}
          </ul>
        </div>
      )}

      <div>
        <Button
          size="sm"
          variant="ghost"
          onClick={async () => setPeople(people ? null : await integrationsApi.adminAccounts(provider.provider))}
        >
          {t('연결한 사람 {n}명').replace('{n}', String(provider.connectedPeople))}
        </Button>
        {people && (
          <ul className="mt-1 space-y-0.5 text-sm text-muted" aria-label={t('연결한 사람')}>
            {people.length === 0 && <li>{t('아직 없습니다.')}</li>}
            {people.map((p) => (
              <li key={p.userId}>
                {p.name} ({p.email}) · {p.label} · {formatDateTime(p.connectedAt)}
              </li>
            ))}
          </ul>
        )}
      </div>

      <ConfirmDialog
        open={turningOff}
        onClose={() => setTurningOff(false)}
        onConfirm={() => void save({ enabled: false })}
        title={t('{name}을 끌까요?').replace('{name}', name)}
        description={t('모든 사람의 연결을 끊고 권한을 되돌립니다.')}
        confirmLabel={t('끄기')}
      />
    </Card>
  )
}
