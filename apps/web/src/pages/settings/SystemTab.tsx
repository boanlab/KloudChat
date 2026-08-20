import { CircleCheck, Mail, RefreshCw, Server, TriangleAlert } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import { Badge, Button, Card, Field, Input } from '@/components/ui'
import { ApiError, adminApi, modelsApi, type SystemSettings } from '@/lib/api'
import { useT } from '@/lib/useT'
import { useStore } from '@/store/useStore'
import { BrandingSection } from './BrandingSection'
import { FeaturesSection } from './FeaturesSection'
import { TemplatesSection } from './TemplatesSection'
import { ToolsSection } from './ToolsSection'
import { AutoRoutingSection } from './AutoRoutingSection'
import { OutlineModelSection } from './OutlineModelSection'

/**
 * Proxy configuration, editable without a redeploy.
 *
 * Two rules. **The master key is never shown again** — even when one is
 * stored, the field is empty and only the last four characters are displayed;
 * leaving it blank keeps the stored value. And **clearing a field falls back
 * to the environment**, with the provenance of each value displayed beside it.
 */
export function SystemTab() {
  const t = useT()
  const { loadModels } = useStore()
  const [current, setCurrent] = useState<SystemSettings | null>(null)
  const [baseUrl, setBaseUrl] = useState('')
  const [masterKey, setMasterKey] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [probe, setProbe] = useState<{ ok: boolean; detail: string } | null>(null)
  //: Kept apart from `busy` so the spinner turns on the control that is doing
  //: the work, while the disabled gate still covers the whole card.
  const [refreshing, setRefreshing] = useState(false)
  //: Mail is an independent block with its own save and test: a relay change
  //: should not refetch the model catalogue.
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
  const mailDirty = useRef(false)
    /**
     * True once typing has begun, so a late-arriving fetch cannot overwrite a
     * value being edited.
     *
     * A ref rather than state because `load` is also called from effects and
     * handlers that captured an earlier render.
     */
  const dirty = useRef(false)

  const load = async ({ resetForm = false } = {}) => {
    const data = await adminApi.settings().catch(() => null)
    if (!data) return
    setCurrent(data)
    if (resetForm || !dirty.current) {
      setBaseUrl(data.litellm.baseUrl)
      setMasterKey('')
      dirty.current = false
    }
    if (resetForm || !mailDirty.current) {
      setSmtp({
        host: data.smtp.host,
        port: data.smtp.port,
        security: data.smtp.security || 'starttls',
        username: data.smtp.username,
        // Same rule as the master key: an empty box means "leave it alone".
        password: '',
        from: data.smtp.from,
        appBaseUrl: data.smtp.appBaseUrl,
      })
      mailDirty.current = false
    }
  }

  useEffect(() => {
    void load()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const connected = current?.status === 'ok'

  return (
    <div className="space-y-5">
      <Card className="flex flex-wrap items-center gap-3 p-4">
        {/* `current` 가 null 이면 아직 아무것도 묻지 않은 상태다. 여기에 실패
            상태를 그리면 화면을 열 때마다 빨간 "연결되지 않았습니다" 가 뜬다. */}
        <Server
          size={18}
          className={!current ? 'text-muted' : connected ? 'text-success' : 'text-danger'}
        />
        <div className="min-w-0 flex-1">
          <p className="text-base font-medium">
            {!current
              ? t('LiteLLM 연결 상태를 확인하는 중입니다')
              : connected
                ? t('LiteLLM 에 연결되어 있습니다')
                : t('LiteLLM 에 연결되지 않았습니다')}
          </p>
          <p className="text-base text-muted">
            {current ? current.litellm.baseUrl || t('주소가 설정되지 않았습니다') : ' '}
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
              await load()
            } finally {
              setBusy(false)
            }
          }}
        >
          {t('연결 테스트')}
        </Button>
        {/* 프록시 설정 파일을 방금 고친 운영자를 위한 버튼이다. 서버는 30초
            동안 목록을 들고 있어서, 이것이 없으면 그 시간을 기다리는 수밖에
            없다. */}
        <Button
          disabled={busy}
          title={busy ? t('설정을 불러오거나 저장하는 중입니다') : undefined}
          onClick={async () => {
            setBusy(true)
            setRefreshing(true)
            setProbe(null)
            try {
              const { models } = await modelsApi.refresh()
              // The reply already carries the fresh catalogue, but every picker
              // reads it from the store, so pull it through there too.
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
          {current && (
            <Badge>
              {current.litellm.baseUrlSource === 'database'
                ? t('직접 지정')
                : current.litellm.baseUrlSource === 'backend'
                  ? t('서버 주소 사용')
                  : t('기본값')}
            </Badge>
          )}
        </div>
      </Field>

      {(current?.unpricedModels?.length ?? 0) > 0 && (
        <Card className="border-warn/30 bg-warn/5 p-4">
          <p className="flex items-center gap-1.5 text-base font-medium text-warn">
            <TriangleAlert size={14} />
            {t('가격을 알 수 없어 숨긴 모델 {n}개').replace('{n}', String(current!.unpricedModels.length))}
          </p>
          <p className="mt-1 text-base text-muted">
            {t('프록시가 서빙하지만 단가를 보고하지 않습니다. 추정치로 과금하지 않기 위해 목록에서 빼두었습니다. 쓰려면 제공자의 가격표를 확인해 MODEL_OVERRIDES 에 실제 단가를 넣으세요.')}
          </p>
          <ul className="mt-2 space-y-0.5">
            {current!.unpricedModels.map((m) => (
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
              current?.litellm.masterKeySet
                ? t('현재 키 {preview} — 바꾸려면 새 키를 입력').replace('{preview}', current.litellm.masterKeyPreview)
                : t('설정되지 않음')
            }
            autoComplete="off"
            className="font-mono text-base"
          />
          {current && (
            <Badge tone={current.litellm.masterKeySet ? 'success' : 'danger'}>
              {current.litellm.masterKeySet
                ? current.litellm.masterKeySource === 'database'
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
                // Omitted rather than empty: an empty string clears the key.
                ...(masterKey ? { masterKey } : {}),
              })
              await load({ resetForm: true })
              // The catalogue was fetched with the old connection.
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
              // Empty base URL clears the override; the key is left alone.
              await adminApi.updateSettings({ baseUrl: '' })
              await load({ resetForm: true })
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

      {/* ── Auto 비용 절약 라우팅 ─────────────────────────────── */}
      <div className="border-t border-line pt-5">
        <h2 className="mb-1 text-base font-medium">{t('모델 자동 라우팅')}</h2>
        <p className="mb-4 text-base text-muted">
          {t('질문 난이도에 맞는 모델을 사용해 불필요한 고비용 모델 호출을 줄입니다.')}
        </p>
        <AutoRoutingSection />
        {/* Beside it because both are questions about which model runs which
            call — one for chat turns, one for the call that plans a document. */}
        <div className="mt-3">
          <OutlineModelSection />
        </div>
      </div>

      {/* ── 사용할 기능 ─────────────────────────────────────────── */}
      <div className="border-t border-line pt-5">
        <h2 className="mb-1 text-base font-medium">{t('사용할 기능')}</h2>
        <p className="mb-4 text-base text-muted">
          {t('사용자에게 어떤 화면을 열어 둘지 정합니다.')}
        </p>
        <FeaturesSection settings={current} onSaved={() => load({ resetForm: true })} />
      </div>

      {/* ── 공용 템플릿 ─────────────────────────────────────────── */}
      <div className="border-t border-line pt-5">
        <h2 className="mb-1 text-base font-medium">{t('공용 템플릿')}</h2>
        <p className="mb-4 text-base text-muted">
          {t('기관 양식처럼 모두가 같은 형식으로 시작해야 하는 것을 한 번만 등록합니다.')}
        </p>
        <TemplatesSection />
      </div>

      {/* ── 브랜딩 ─────────────────────────────────────────────── */}
      <div className="border-t border-line pt-5">
        <h2 className="mb-1 text-base font-medium">{t('브랜딩')}</h2>
        <p className="mb-4 text-base text-muted">
          {t('사이드바와 로그인 화면에 보이는 이름과 로고입니다.')}
        </p>
        <BrandingSection settings={current} onSaved={() => load({ resetForm: true })} />
      </div>

      {/* ── 기능 연동 ───────────────────────────────────────────── */}
      <div className="border-t border-line pt-5">
        <h2 className="mb-1 text-base font-medium">{t('기능 연동')}</h2>
        <p className="mb-4 text-base text-muted">
          {t('웹 검색, 문서 가져오기, 코드 실행, 심층 조사, 음성 전사를 연결합니다.')}
        </p>
        <ToolsSection settings={current} onSaved={() => load({ resetForm: true })} />
      </div>

      {/* ── 메일 ────────────────────────────────────────────────── */}
      <div className="border-t border-line pt-5">
        <div className="flex flex-wrap items-center gap-3">
          <Mail size={18} className={current?.smtp.passwordResetEnabled ? 'text-success' : 'text-muted'} />
          <div className="min-w-0 flex-1">
            <p className="text-base font-medium">{t('메일 발송')}</p>
            {/* The consequence, not the configuration. An operator filling this
                in is doing it for one reason, and the screen should say whether
                that reason is satisfied yet. */}
            <p className="text-base text-muted">
              {current?.smtp.passwordResetEnabled
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
                  mailDirty.current = true
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
                  mailDirty.current = true
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
                  mailDirty.current = true
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
                  mailDirty.current = true
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
                  mailDirty.current = true
                }}
                placeholder={
                  current?.smtp.passwordSet
                    ? t('현재 {preview} — 바꾸려면 새 값을 입력').replace('{preview}', current.smtp.passwordPreview)
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
                mailDirty.current = true
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
                mailDirty.current = true
              }}
              placeholder="https://kchat.example.com"
              className="font-mono text-base"
            />
          </Field>

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
                await load({ resetForm: true })
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
    </div>
  )
}
