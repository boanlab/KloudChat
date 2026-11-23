import { Globe, Lock, Plus, Trash2 } from 'lucide-react'
import { useState } from 'react'
import { Button, ConfirmDialog, Field, Input, Select } from '@/components/ui'
import { errorCode, errorMessage, type ConnectorPatch, type RequiredEnvField } from '@/lib/api'
import { useT } from '@/lib/useT'
import { cn } from '@/lib/utils'
import { useStore } from '@/store/useStore'
import type { Connector } from '@/types'

/** Credentials a person who installs the server enters: the most the server accepts. */
const MAX_REQUIRED = 8
/** Tools whose address a server may carry, and so whose key the app puts in beside it. */
const FEATURES = ['research', 'stt', 'search', 'fetch', 'exec'] as const

const ERRORS: Record<string, string> = {
  shared_admin_only: '관리자만 바꿀 수 있는 항목입니다.',
  stdio_requires_admin: '명령으로 실행하는 서버는 관리자만 등록할 수 있습니다.',
  catalog_endpoint: 'stdio는 명령 한 줄, http·sse는 https:// 주소나 ${TOOLS_*_URL} 자리표시로 적으세요.',
  catalog_placeholder: '모르는 자리표시입니다.',
  catalog_env: '환경 변수 이름은 영문 대문자로, 값은 한 줄로 적으세요. TOOLS_GATEWAY_KEY는 직접 넣을 수 없습니다.',
  catalog_tools_key: '게이트웨이 키를 넣을 변수는 환경 변수 중 하나여야 하고, 도구를 골라야 합니다.',
  catalog_required_env: '받는 사람이 넣을 자격은 영문 대문자 이름으로, 최대 8개까지 적으세요.',
  catalog_default_env: '사람마다 자격증명을 받아야 하는 항목은 기본 제공할 수 없습니다.',
}

type EnvRow = { key: string; value: string; original: string | null; isNew: boolean }

const envRowsOf = (c: Connector): EnvRow[] =>
  Object.entries(c.sharedEnv).map(([key, v]) => ({ key, value: v ?? '', original: v, isNew: false }))

/** How many people use a shared server, in the sentence the withdraw warning and the delete refusal share. */
export function inUseText(t: ReturnType<typeof useT>, installs: number): string {
  return t.n('{n}명이 쓰고 있어, 끊으면 그분들의 이 서버가 멈춥니다.', installs)
}

/**
 * 「공유」 of a person's own MCP server: not shared, or shared with the organization (listed in the
 * store). The sharer sets which credentials each receiver enters; an administrator's server also
 * carries shared values, a tool-address key, 「모두에게 기본 제공」 and 「스토어에 보이기」.
 */
export function ConnectorShare({ connector, isAdmin }: { connector: Connector; isAdmin: boolean }) {
  const t = useT()
  const patchConnector = useStore((s) => s.patchConnector)
  const [visibility, setVisibility] = useState<Connector['visibility']>(connector.visibility)
  const [required, setRequired] = useState<RequiredEnvField[]>(connector.requiredEnv)
  const [env, setEnv] = useState<EnvRow[]>(() => envRowsOf(connector))
  const [keyVar, setKeyVar] = useState(connector.toolsKeyVar)
  const [keyFeature, setKeyFeature] = useState(connector.toolsKeyFeature)
  const [byDefault, setByDefault] = useState(connector.byDefault)
  const [listed, setListed] = useState(connector.listed)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [saved, setSaved] = useState(false)
  const [confirming, setConfirming] = useState(false)

  const shared = visibility === 'org'
  const sharable = isAdmin || connector.transport !== 'stdio'
  const needsCredentials = required.length > 0
  const withdrawing = connector.visibility === 'org' && !shared

  const save = async () => {
    setBusy(true)
    setError(null)
    setSaved(false)
    const patch: ConnectorPatch = {}
    if (visibility !== connector.visibility) patch.visibility = visibility
    if (shared) patch.requiredEnv = required.map((f) => ({ ...f, key: f.key.trim() })).filter((f) => f.key)
    if (isAdmin) {
      const changed = env.filter((r) => r.key.trim() && r.value.trim() && r.value !== (r.original ?? ''))
      if (changed.length > 0) patch.env = Object.fromEntries(changed.map((r) => [r.key.trim(), r.value]))
      if (keyVar !== connector.toolsKeyVar || keyFeature !== connector.toolsKeyFeature) {
        patch.toolsKeyVar = keyVar
        patch.toolsKeyFeature = keyVar ? keyFeature : ''
      }
      // Credentials per person and a default for everyone do not go together.
      if (shared && needsCredentials && connector.byDefault) patch.byDefault = false
      else if (shared && byDefault !== connector.byDefault) patch.byDefault = byDefault
      if (shared && listed !== connector.listed) patch.listed = listed
    }
    try {
      await patchConnector(connector.id, patch)
      setSaved(true)
      // Stored values never come back: the rows show 「저장됨」 again.
      const row = useStore.getState().connectors.find((c) => c.id === connector.id)
      if (row) {
        setEnv(envRowsOf(row))
        setByDefault(row.byDefault)
        setVisibility(row.visibility)
      }
    } catch (err) {
      const code = errorCode(err)
      setError(
        code === 'connector_in_use'
          ? inUseText(t, connector.installs)
          : ERRORS[code]
            ? t(ERRORS[code])
            : errorMessage(err, t('저장하지 못했습니다. 잠시 뒤 다시 시도하세요.')),
      )
    } finally {
      setBusy(false)
    }
  }

  const setField = (i: number, patch: Partial<RequiredEnvField>) =>
    setRequired(required.map((f, k) => (k === i ? { ...f, ...patch } : f)))

  return (
    <section className="space-y-3" data-testid="connector-share" aria-label={t('공유')}>
      <p className="text-base font-medium">{t('공유')}</p>
      {!sharable ? (
        <p className="text-sm text-muted">{t('명령으로 실행하는 서버는 관리자만 공유할 수 있습니다.')}</p>
      ) : (
        <>
          <div className="flex gap-2">
            {(
              [
                { id: 'private', label: t('공유 안 함'), icon: Lock },
                { id: 'org', label: t('우리 조직과 공유'), icon: Globe },
              ] as const
            ).map((o) => {
              const Icon = o.icon
              return (
                <button
                  type="button"
                  key={o.id}
                  aria-pressed={visibility === o.id}
                  onClick={() => setVisibility(o.id)}
                  className={cn(
                    'flex flex-1 items-center justify-center gap-1.5 rounded-control border px-3 py-2 text-base transition-colors',
                    visibility === o.id ? 'border-accent bg-accent-soft text-accent' : 'border-line text-muted hover:bg-elevated',
                  )}
                >
                  <Icon size={14} />
                  {o.label}
                </button>
              )
            })}
          </div>
          <p className="text-sm text-faint">
            {isAdmin
              ? t('공유하면 스토어에 올라가 다른 사람이 이 서버를 가져다 씁니다. 주소와 공용 환경 변수는 그대로 쓰이고, 값은 다른 사람에게 보이지 않습니다.')
              : t('공유하면 스토어에 올라가 다른 사람이 이 서버를 가져다 씁니다. 내 환경 변수 값은 남이 쓰지 않으니, 받는 사람이 각자 자격을 넣습니다.')}
          </p>
          {connector.visibility === 'org' && connector.installs > 0 && (
            <p className="text-sm text-muted" data-testid="connector-installs">
              {t.n('{n}명이 이 서버를 쓰고 있습니다.', connector.installs)}
            </p>
          )}
        </>
      )}

      {sharable && shared && (
        <div className="space-y-2" data-testid="connector-required-env">
          <p className="text-base font-medium">{t('받는 사람이 직접 넣을 자격')}</p>
          <p className="text-sm text-faint">
            {t('서버가 요구하는 키나 토큰의 이름을 적어 두면, 설치할 때 받는 사람이 자기 값을 넣습니다. 최대 8개까지 적을 수 있습니다.')}
          </p>
          {required.map((f, i) => (
            <div key={i} className="grid gap-2 rounded-control border border-line p-2 sm:grid-cols-[1fr_1fr]" data-testid="required-env-row">
              <Input aria-label={t('변수 이름')} value={f.key} placeholder="API_TOKEN" className="font-mono"
                onChange={(e) => setField(i, { key: e.target.value.toUpperCase() })} />
              <Input aria-label={t('화면에 보일 이름')} value={f.label} maxLength={40} placeholder={t('화면에 보일 이름')}
                onChange={(e) => setField(i, { label: e.target.value })} />
              <Input aria-label={t('설명')} value={f.hint} maxLength={120} placeholder={t('설명')} className="sm:col-span-2"
                onChange={(e) => setField(i, { hint: e.target.value })} />
              <label className="flex items-center gap-1.5 text-base">
                <input type="checkbox" className="size-4 accent-[var(--accent)]" checked={f.secret}
                  onChange={(e) => setField(i, { secret: e.target.checked })} />
                {t('비밀 값 (입력할 때 가림)')}
              </label>
              <div className="flex justify-end">
                <Button type="button" size="sm" variant="ghost" aria-label={t('자격 빼기')}
                  onClick={() => setRequired(required.filter((_, k) => k !== i))}>
                  <Trash2 size={13} />
                </Button>
              </div>
            </div>
          ))}
          {required.length < MAX_REQUIRED && (
            <Button type="button" size="sm"
              onClick={() => setRequired([...required, { key: '', label: '', hint: '', secret: true }])}>
              <Plus size={13} />
              {t('자격 추가')}
            </Button>
          )}
        </div>
      )}

      {isAdmin && (
        <div className="space-y-3" data-testid="connector-admin-share">
          <div className="space-y-2">
            <p className="text-base font-medium">{t('공용 환경 변수')}</p>
            <p className="text-sm text-faint">{t('이 서버를 쓰는 모두에게 같은 값이 들어갑니다. 저장한 값은 다시 보여 주지 않습니다.')}</p>
            {env.map((row, i) => (
              <div key={i} className="flex flex-wrap items-center gap-2" data-testid="shared-env-row">
                <Input aria-label={t('변수 이름')} value={row.key} className="w-48 font-mono" disabled={!row.isNew}
                  onChange={(e) => setEnv(env.map((r, k) => (k === i ? { ...r, key: e.target.value.toUpperCase() } : r)))} />
                <Input aria-label={t('값')} value={row.value} className="min-w-40 flex-1 font-mono"
                  placeholder={row.original === null ? t('저장됨 — 바꿀 때만 적으세요') : ''}
                  onChange={(e) => setEnv(env.map((r, k) => (k === i ? { ...r, value: e.target.value } : r)))} />
                {row.isNew && (
                  <Button type="button" size="sm" variant="ghost" aria-label={t('변수 빼기')}
                    onClick={() => setEnv(env.filter((_, k) => k !== i))}>
                    <Trash2 size={13} />
                  </Button>
                )}
              </div>
            ))}
            <Button type="button" size="sm" onClick={() => setEnv([...env, { key: '', value: '', original: '', isNew: true }])}>
              <Plus size={13} />
              {t('변수 추가')}
            </Button>
          </div>
          <div className="grid gap-3 sm:grid-cols-2">
            <Field label={t('게이트웨이 키를 넣을 변수')} hint={t('이 변수가 고른 도구의 주소일 때만 TOOLS_GATEWAY_KEY가 들어갑니다.')}>
              <Select value={keyVar} onChange={(e) => setKeyVar(e.target.value)}>
                <option value="">{t('넣지 않음')}</option>
                {env.filter((r) => r.key.trim()).map((r) => <option key={r.key} value={r.key}>{r.key}</option>)}
              </Select>
            </Field>
            <Field label={t('그 주소의 도구')}>
              <Select value={keyFeature} disabled={!keyVar} onChange={(e) => setKeyFeature(e.target.value)}>
                <option value="">{t('고르세요')}</option>
                {FEATURES.map((f) => <option key={f} value={f}>{f}</option>)}
              </Select>
            </Field>
          </div>
          {sharable && shared && (
            <div className="flex flex-wrap items-center gap-4">
              <label className={cn('flex items-center gap-1.5 text-base', needsCredentials && 'text-faint')}
                title={t('켜 두면 모든 사용자에게 설치됩니다. 사용자는 끌 수 있지만 삭제할 수는 없습니다.')}>
                <input type="checkbox" className="size-4 accent-[var(--accent)]" checked={byDefault && !needsCredentials}
                  disabled={needsCredentials} data-testid="connector-default"
                  onChange={(e) => setByDefault(e.target.checked)} />
                {t('모두에게 기본 제공')}
              </label>
              <label className="flex items-center gap-1.5 text-base"
                title={t('끄면 스토어 목록에 나오지 않지만, 이미 설치한 사람은 계속 씁니다.')}>
                <input type="checkbox" className="size-4 accent-[var(--accent)]" checked={listed}
                  data-testid="connector-listed" onChange={(e) => setListed(e.target.checked)} />
                {t('스토어에 보이기')}
              </label>
              {needsCredentials && (
                <p className="basis-full text-sm text-faint">{t('사람마다 자격증명을 받아야 하는 항목은 기본 제공할 수 없습니다.')}</p>
              )}
            </div>
          )}
        </div>
      )}

      {error && <p role="alert" className="text-base text-danger" data-testid="connector-share-error">{error}</p>}
      {saved && !error && <p role="status" className="text-sm text-success">{t('저장했습니다.')}</p>}
      {(sharable || isAdmin) && (
        <div className="flex justify-end">
          <Button variant="primary" disabled={busy}
            onClick={() => (withdrawing && connector.installs > 0 ? setConfirming(true) : void save())}>
            {t('공유 설정 저장')}
          </Button>
        </div>
      )}

      <ConfirmDialog
        open={confirming}
        onClose={() => setConfirming(false)}
        onConfirm={() => void save()}
        title={t('공유를 끊을까요?')}
        description={inUseText(t, connector.installs)}
        confirmLabel={t('공유 끊기')}
      />
    </section>
  )
}
