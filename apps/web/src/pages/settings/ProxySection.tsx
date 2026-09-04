import { CircleCheck, RefreshCw, Server, TriangleAlert } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import { Badge, Button, Card, Field, Input } from '@/components/ui'
import { ApiError, adminApi, modelsApi, type SystemSettings } from '@/lib/api'
import { useT } from '@/lib/useT'
import { useStore } from '@/store/useStore'

/**
 * LiteLLM proxy settings. The master key is never shown again: blank keeps the
 * stored value. A cleared base URL falls back to the environment.
 */
export function ProxySection({
  settings,
  reload,
}: {
  settings: SystemSettings | null
  reload: () => Promise<void>
}) {
  const t = useT()
  const { loadModels } = useStore()
  const [baseUrl, setBaseUrl] = useState('')
  const [masterKey, setMasterKey] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [probe, setProbe] = useState<{ ok: boolean; detail: string } | null>(null)
  const [refreshing, setRefreshing] = useState(false)
  // Typing wins over a fetch that lands late.
  const dirty = useRef(false)

  useEffect(() => {
    if (!settings || dirty.current) return
    setBaseUrl(settings.litellm.baseUrl)
    setMasterKey('')
  }, [settings])

  const resetForm = async () => {
    dirty.current = false
    await reload()
  }

  const connected = settings?.status === 'ok'

  return (
    <div className="space-y-5">
      <Card className="flex flex-wrap items-center gap-3 p-4">
        {/* Null settings means not fetched yet, not disconnected. */}
        <Server
          size={18}
          className={!settings ? 'text-muted' : connected ? 'text-success' : 'text-danger'}
        />
        <div className="min-w-0 flex-1">
          <p className="text-base font-medium">
            {!settings
              ? t('LiteLLM 연결 상태를 확인하는 중입니다')
              : connected
                ? t('LiteLLM 에 연결되어 있습니다')
                : t('LiteLLM 에 연결되지 않았습니다')}
          </p>
          <p className="text-base text-muted">
            {settings ? settings.litellm.baseUrl || t('주소가 설정되지 않았습니다') : ' '}
          </p>
        </div>
        <Button
          disabled={busy}
          title={busy ? t('설정을 불러오거나 저장하는 중입니다') : undefined}
          onClick={async () => {
            setBusy(true)
            setProbe(null)
            try {
              const result = await adminApi.testSettings()
              setProbe({
                ok: result.ok,
                detail: result.ok
                  ? t('연결됨 · 모델 {n}종').replace('{n}', String(result.models ?? 0))
                  : (result.detail ?? t('연결하지 못했습니다.')),
              })
              await reload()
            } finally {
              setBusy(false)
            }
          }}
        >
          {t('연결 테스트')}
        </Button>
        {/* Bypasses the server's 30s catalogue cache. */}
        <Button
          disabled={busy}
          title={busy ? t('설정을 불러오거나 저장하는 중입니다') : undefined}
          onClick={async () => {
            setBusy(true)
            setRefreshing(true)
            setProbe(null)
            try {
              const { models } = await modelsApi.refresh()
              // Pickers read the catalogue from the store.
              await loadModels()
              setProbe({
                ok: true,
                detail: t('모델 목록을 다시 읽었습니다 · 모델 {n}종').replace(
                  '{n}',
                  String(models.length),
                ),
              })
            } catch (err) {
              setProbe({
                ok: false,
                detail:
                  err instanceof ApiError ? err.detail : t('모델 목록을 다시 읽지 못했습니다.'),
              })
            } finally {
              setRefreshing(false)
              setBusy(false)
            }
          }}
        >
          <RefreshCw size={14} className={refreshing ? 'animate-spin' : undefined} />
          {t('모델 목록 새로고침')}
        </Button>
      </Card>

      {probe && (
        <p
          className={`flex items-start gap-1.5 rounded-control border px-3 py-2 text-base ${
            probe.ok
              ? 'border-success/25 bg-success/5 text-success'
              : 'border-danger/25 bg-danger/5 text-danger'
          }`}
        >
          {probe.ok ? (
            <CircleCheck size={14} className="mt-0.5 shrink-0" />
          ) : (
            <TriangleAlert size={14} className="mt-0.5 shrink-0" />
          )}
          {probe.detail}
        </p>
      )}

      <Field
        label={t('LiteLLM 주소')}
        hint={t('비우면 서버에 설정된 기본 주소를 사용합니다.')}
      >
        <div className="flex items-center gap-2">
          <Input
            value={baseUrl}
            onChange={(e) => {
              setBaseUrl(e.target.value)
              dirty.current = true
            }}
            placeholder="http://litellm:4000"
            className="font-mono text-base"
          />
          {settings && (
            <Badge>
              {settings.litellm.baseUrlSource === 'database'
                ? t('직접 지정')
                : settings.litellm.baseUrlSource === 'backend'
                  ? t('서버 주소 사용')
                  : t('기본값')}
            </Badge>
          )}
        </div>
      </Field>

      {(settings?.unpricedModels?.length ?? 0) > 0 && (
        <Card className="border-warn/30 bg-warn/5 p-4">
          <p className="flex items-center gap-1.5 text-base font-medium text-warn">
            <TriangleAlert size={14} />
            {t('가격을 알 수 없어 숨긴 모델 {n}개').replace('{n}', String(settings!.unpricedModels.length))}
          </p>
          <p className="mt-1 text-base text-muted">
            {t('프록시가 서빙하지만 단가를 보고하지 않습니다. 추정치로 과금하지 않기 위해 목록에서 빼두었습니다. 쓰려면 제공자의 가격표를 확인해 MODEL_OVERRIDES 에 실제 단가를 넣으세요.')}
          </p>
          <ul className="mt-2 space-y-0.5">
            {settings!.unpricedModels.map((m) => (
              <li key={m.id} className="font-mono text-sm text-faint">
                {m.id} <span className="text-muted">({m.provider})</span>
              </li>
            ))}
          </ul>
        </Card>
      )}

      <Field
        label={t('마스터 키')}
        hint={t('보안을 위해 저장한 키는 표시하지 않습니다. 그대로 두면 현재 키를 유지합니다.')}
      >
        <div className="flex items-center gap-2">
          <Input
            type="password"
            value={masterKey}
            onChange={(e) => {
              setMasterKey(e.target.value)
              dirty.current = true
            }}
            placeholder={
              settings?.litellm.masterKeySet
                ? t('현재 키 {preview} — 바꾸려면 새 키를 입력').replace('{preview}', settings.litellm.masterKeyPreview)
                : t('설정되지 않음')
            }
            autoComplete="off"
            className="font-mono text-base"
          />
          {settings && (
            <Badge tone={settings.litellm.masterKeySet ? 'success' : 'danger'}>
              {settings.litellm.masterKeySet
                ? settings.litellm.masterKeySource === 'database'
                  ? t('직접 지정')
                  : t('환경변수')
                : t('없음')}
            </Badge>
          )}
        </div>
      </Field>

      {error && (
        <p className="flex items-start gap-1.5 rounded-control border border-danger/25 bg-danger/5 px-3 py-2 text-base text-danger">
          <TriangleAlert size={14} className="mt-0.5 shrink-0" />
          {error}
        </p>
      )}

      <div className="flex items-center gap-2">
        <Button
          variant="primary"
          disabled={busy}
          title={busy ? t('설정을 불러오거나 저장하는 중입니다') : undefined}
          onClick={async () => {
            setBusy(true)
            setError(null)
            setProbe(null)
            try {
              await adminApi.updateSettings({
                baseUrl,
                // Omitted, not empty: an empty string clears the key.
                ...(masterKey ? { masterKey } : {}),
              })
              await resetForm()
              await loadModels()
            } catch (err) {
              setError(err instanceof ApiError ? err.detail : t('저장에 실패했습니다.'))
            } finally {
              setBusy(false)
            }
          }}
        >
          {busy ? t('저장 중…') : t('저장')}
        </Button>
        <Button
          disabled={busy}
          title={busy ? t('설정을 불러오거나 저장하는 중입니다') : undefined}
          onClick={async () => {
            setBusy(true)
            try {
              // Empty base URL clears the override; the key is untouched.
              await adminApi.updateSettings({ baseUrl: '' })
              await resetForm()
              await loadModels()
            } finally {
              setBusy(false)
            }
          }}
        >
          {t('환경변수로 되돌리기')}
        </Button>
      </div>

      <p className="text-xs text-faint">
        {t('키는 암호화해 서버에만 보관하며, 저장한 뒤에는 화면에 표시하지 않습니다. 저장하면 곧바로 적용됩니다.')}
      </p>
    </div>
  )
}
