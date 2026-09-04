import { errorMessage } from '@/lib/api'
import {
  CircleAlert,
  Lock,
  Plug,
  Plus,
  RefreshCw,
  ShieldCheck,
  Trash2,
  TriangleAlert,
  KeyRound,
} from 'lucide-react'
import { useEffect, useState } from 'react'
import { PageBody } from '@/components/layout/AppShell'
import { TopBar } from '@/components/layout/TopBar'
import {
  Badge,
  Button,
  Card,
  EmptyState,
  LoadingState,
  ReloadNotice,
  Field,
  Input,
  Modal,
  PageHeader,
  Switch,
  Tabs,
  Textarea,
} from '@/components/ui'
import { kindMeta } from '@/lib/kinds'
import { cn, relativeTime } from '@/lib/utils'
import { BulkBar, PickBox, useBulkSelect } from '@/components/ui/BulkSelect'
import { useStore } from '@/store/useStore'
import type { CatalogEntry } from '@/lib/api'
import type { Connector, ConnectorStatus } from '@/types'
import { useT } from '@/lib/useT'

const statusMeta: Record<ConnectorStatus, { label: string; tone: 'success' | 'warn' | 'danger' | 'neutral' }> = {
  connected: { label: '연결됨', tone: 'success' },
  needs_auth: { label: '인증 필요', tone: 'warn' },
  error: { label: '오류', tone: 'danger' },
  disconnected: { label: '미연결', tone: 'neutral' },
}

/** An installed connector; catalogue entries are a separate shape with no id. */
function ConnectorCard({
  connector,
  onOpen,
  onEditCredentials,
  needsCredentials,
  picked,
  onPick,
}: {
  connector: Connector
  onOpen: () => void
  onEditCredentials: () => void
  needsCredentials: boolean
  picked: boolean
  onPick: () => void
}) {
  const t = useT()
  const { toggleConnector, syncConnector } = useStore()
  const [busy, setBusy] = useState(false)
  const status = statusMeta[connector.status]
  const writeTools = connector.tools.filter((t) => !t.readOnly && t.enabled).length

  return (
    // `data-connector` is the e2e hook.
    <Card className="flex flex-col p-4" data-connector={connector.slug}>
      <div className="flex items-start gap-3">
        <PickBox
          checked={picked}
          onChange={onPick}
          label={t('{name} 선택').replace('{name}', connector.name)}
          className="mt-2.5"
        />
        <span
          className="grid size-9 shrink-0 place-items-center rounded-card text-lg"
          style={{ background: `${connector.color}1a` }}
        >
          {connector.icon}
        </span>
        <button onClick={onOpen} className="min-w-0 flex-1 text-left">
          <span className="flex flex-wrap items-center gap-1.5">
            <span className="text-base font-medium">{t(connector.name)}</span>
            {connector.official && (
              <Badge tone="accent">
                <ShieldCheck size={10} />
                {t('공식')}
              </Badge>
            )}
            <Badge tone={status.tone}>{t(status.label)}</Badge>
          </span>
          <span className="mt-1 block text-base text-muted">{t(connector.description)}</span>
        </button>
        {connector.installed && connector.status === 'connected' && (
          <Switch
            checked={connector.enabled}
            onChange={() => toggleConnector(connector.id)}
            label={t('{name} 사용').replace('{name}', t(connector.name))}
          />
        )}
      </div>

      {connector.error && (
        <p className="mt-3 flex items-start gap-1.5 rounded-control border border-danger/25 bg-danger/5 px-2.5 py-2 text-sm text-danger">
          <TriangleAlert size={13} className="mt-0.5 shrink-0" />
          {connector.error}
        </p>
      )}

      <div className="mb-3 mt-3 flex flex-wrap gap-1.5">
        <Badge>{t(connector.category)}</Badge>
        <Badge>{t('도구 {n}').replace('{n}', String(connector.tools.length))}</Badge>
        {writeTools > 0 && (
          <Badge tone="warn">
            <Lock size={10} />
            {t('쓰기 {n}').replace('{n}', String(writeTools))}
          </Badge>
        )}
        {connector.kinds.map((k) => (
          <Badge key={k}>{t(kindMeta[k].label)}</Badge>
        ))}
      </div>

      {/* `mt-auto` keeps the foot aligned across a row of cards. */}
      <div className="mt-auto flex items-center justify-between border-t border-line pt-3">
        <span className="text-xs text-faint">
          {connector.lastSyncAt
            ? t('{when} 동기화').replace('{when}', relativeTime(connector.lastSyncAt))
            : t('동기화 이력 없음')}
        </span>
        <div className="flex gap-1.5">
          {needsCredentials && (
            <Button size="sm" onClick={onEditCredentials}>
              <KeyRound size={13} />
              {t('자격증명')}
            </Button>
          )}
          {connector.status === 'connected' ? (
            <Button size="sm" onClick={onOpen}>
              {t('도구 설정')}
            </Button>
          ) : (
            <Button
              size="sm"
              variant="primary"
              disabled={busy}
              onClick={async () => {
                setBusy(true)
                try {
                  await syncConnector(connector.id)
                } finally {
                  setBusy(false)
                }
              }}
            >
              <RefreshCw size={13} className={cn(busy && 'animate-spin')} />
              {t('다시 연결')}
            </Button>
          )}
        </div>
      </div>
    </Card>
  )
}

/** A catalogue entry that is not installed yet. */
function CatalogCard({ entry, onNeedsCredentials }: {
  entry: CatalogEntry
  onNeedsCredentials: (entry: CatalogEntry) => void
}) {
  const t = useT()
  const { installConnector } = useStore()
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  return (
    // `data-connector` is the e2e hook.
    <Card className="flex flex-col p-4" data-connector={entry.slug}>
      <div className="flex items-start gap-3">
        <span className="grid size-9 shrink-0 place-items-center rounded-card bg-elevated text-lg">
          🔌
        </span>
        <div className="min-w-0 flex-1">
          <span className="flex flex-wrap items-center gap-1.5">
            <span className="text-base font-medium">{t(entry.name)}</span>
            {entry.official && (
              <Badge tone="accent">
                <ShieldCheck size={10} />
                {t('공식')}
              </Badge>
            )}
          </span>
          <span className="mt-1 block text-base text-muted">{t(entry.description)}</span>
        </div>
      </div>

      {error && (
        <p className="mt-3 flex items-start gap-1.5 rounded-control border border-danger/25 bg-danger/5 px-2.5 py-2 text-sm text-danger">
          <TriangleAlert size={13} className="mt-0.5 shrink-0" />
          {error}
        </p>
      )}

      <div className="mt-3 flex flex-wrap gap-1.5">
        <Badge>{t(entry.category)}</Badge>
        <Badge>{entry.transport}</Badge>
        {entry.kinds.map((k) => (
          <Badge key={k}>{t(kindMeta[k as keyof typeof kindMeta]?.label ?? k)}</Badge>
        ))}
      </div>

      <div className="mt-3 flex items-center justify-end border-t border-line pt-3">
        <Button
          size="sm"
          variant="primary"
          disabled={busy || entry.installed}
          onClick={async () => {
            if (entry.requiredEnv.length > 0) {
              onNeedsCredentials(entry)
              return
            }
            setBusy(true)
            setError(null)
            try {
              await installConnector(entry.slug)
            } catch (err) {
              setError(errorMessage(err, t('설치에 실패했습니다.')))
            } finally {
              setBusy(false)
            }
          }}
        >
          {busy ? (
            <RefreshCw size={13} className="animate-spin" />
          ) : entry.requiredEnv.length > 0 ? (
            <Lock size={13} />
          ) : (
            <Plus size={13} />
          )}
          {entry.installed ? t('설치됨') : t('추가')}
        </Button>
      </div>
    </Card>
  )
}

/** Re-supplies credentials for an installed connector. Stored values never leave the server. */
function ReCredentialModal({
  connector,
  entry,
  onClose,
}: {
  connector: Connector | null
  entry: CatalogEntry | null
  onClose: () => void
}) {
  const t = useT()
  const { updateConnectorEnv } = useStore()
  const [values, setValues] = useState<Record<string, string>>({})
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    setValues({})
    setError(null)
  }, [connector?.id])

  // A self-registered server has no catalogue entry; fall back to its env keys.
  const fields =
    entry?.requiredEnv?.length
      ? entry.requiredEnv
      : (connector?.envKeys ?? []).map((key) => ({ key, label: key, hint: '' }))
  const complete = fields.every((f) => (values[f.key] ?? '').trim())

  return (
    <Modal
      open={!!connector && fields.length > 0}
      onClose={onClose}
      title={t('{name} 자격증명').replace('{name}', t(connector?.name ?? ''))}
      description={t('바꿀 값만 입력하세요. 저장한 값은 보안을 위해 표시하지 않습니다.')}
      footer={
        <>
          <Button onClick={onClose}>{t('취소')}</Button>
          <Button
            variant="primary"
            disabled={busy || !complete}
            onClick={async () => {
              if (!connector) return
              setBusy(true)
              setError(null)
              try {
                await updateConnectorEnv(connector.id, values)
                onClose()
              } catch (err) {
                setError(errorMessage(err, t('저장에 실패했습니다.')))
              } finally {
                setBusy(false)
              }
            }}
          >
            {busy ? t('확인 중…') : t('저장하고 다시 연결')}
          </Button>
        </>
      }
    >
      {fields.map((f) => (
        <Field key={f.key} label={f.label} hint={f.hint}>
          <Input
            type="password"
            autoComplete="off"
            value={values[f.key] ?? ''}
            onChange={(e) => setValues((v) => ({ ...v, [f.key]: e.target.value }))}
          />
        </Field>
      ))}
      {error && <p className="text-base text-danger">{error}</p>}
    </Modal>
  )
}

/** Asks for the credentials a catalogue entry declares. Stored values never leave the server. */
function CredentialsModal({
  entry,
  onClose,
}: {
  entry: CatalogEntry | null
  onClose: () => void
}) {
  const t = useT()
  const { installConnector } = useStore()
  const [values, setValues] = useState<Record<string, string>>({})
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    setValues({})
    setError(null)
  }, [entry?.slug])

  const missing = (entry?.requiredEnv ?? []).some((f) => !(values[f.key] ?? '').trim())

  return (
    <Modal
      open={!!entry}
      onClose={onClose}
      title={entry ? t('{name} 연결').replace('{name}', t(entry.name)) : ''}
      description={t('입력한 값은 서버에만 보관하며, 저장한 뒤에는 표시하지 않습니다.')}
      footer={
        <>
          <Button onClick={onClose}>{t('취소')}</Button>
          <Button
            variant="primary"
            disabled={busy || missing}
            onClick={async () => {
              if (!entry) return
              setBusy(true)
              setError(null)
              try {
                await installConnector(entry.slug, values)
                onClose()
              } catch (err) {
                setError(errorMessage(err, t('설치에 실패했습니다.')))
              } finally {
                setBusy(false)
              }
            }}
          >
            {busy ? t('연결 중…') : t('연결')}
          </Button>
        </>
      }
    >
      {entry?.requiredEnv.map((field) => (
        <Field key={field.key} label={field.label} hint={field.hint || undefined}>
          <Input
            type={field.secret ? 'password' : 'text'}
            value={values[field.key] ?? ''}
            onChange={(e) => setValues({ ...values, [field.key]: e.target.value })}
            autoComplete="off"
          />
        </Field>
      ))}
      {error && (
        <p className="flex items-start gap-1.5 rounded-control border border-danger/25 bg-danger/5 px-2.5 py-2 text-sm text-danger">
          <TriangleAlert size={13} className="mt-0.5 shrink-0" />
          {error}
        </p>
      )}
    </Modal>
  )
}

/** `KEY=value` per line to an env map; blank and malformed lines are dropped. */
function parseEnv(text: string): Record<string, string> {
  const out: Record<string, string> = {}
  for (const line of text.split('\n')) {
    const at = line.indexOf('=')
    if (at <= 0) continue
    const key = line.slice(0, at).trim()
    if (key) out[key] = line.slice(at + 1).trim()
  }
  return out
}

export function ConnectorsPage() {
  const t = useT()
  const {
    connectors,
    connectorCatalog,
    loadWorkspace,
    toggleConnectorTool,
    uninstallConnector,
    deleteMany,
    addCustomConnector,
    workspaceLoading,
    workspaceFailed,
  } = useStore()

  useEffect(() => {
    void loadWorkspace()
  }, [loadWorkspace])
  const [tab, setTab] = useState<'installed' | 'catalog'>('installed')
  const [detail, setDetail] = useState<Connector | null>(null)
  const [credentialsFor, setCredentialsFor] = useState<CatalogEntry | null>(null)
  const [reCredential, setReCredential] = useState<Connector | null>(null)
  const [adding, setAdding] = useState(false)
  const [draft, setDraft] = useState({
    name: '',
    transport: 'http' as Connector['transport'],
    endpoint: '',
    auth: 'none' as Connector['auth'],
    envText: '',
  })

  const installed = connectors.filter((c) => c.installed)
  const pick = useBulkSelect(installed)
  // Installed entries stay in the catalogue, marked.
  const catalog = connectorCatalog
  const current = detail ? (connectors.find((c) => c.id === detail.id) ?? detail) : null

  const groupBy = <T extends { category: string }>(items: T[]) =>
    items.reduce<Record<string, T[]>>((acc, item) => {
      ;(acc[item.category] ??= []).push(item)
      return acc
    }, {})
  const installedByCategory = groupBy(installed)
  const catalogByCategory = groupBy(catalog)
  const empty = tab === 'installed' ? installed.length === 0 : catalog.length === 0

  return (
    <>
      <TopBar left={<span className="text-base font-medium">{t('커넥터')}</span>} />
      <PageBody>
        <PageHeader
          title={t('커넥터')}
          description={t('MCP 서버를 붙여 외부 시스템을 도구로 씁니다. 인증 정보는 서버에만 저장되고, 도구 단위로 켜고 끌 수 있습니다.')}
          action={
            <Button variant="primary" onClick={() => setAdding(true)}>
              <Plus size={16} />
              {t('서버 직접 추가')}
            </Button>
          }
        />

        <Tabs<'installed' | 'catalog'>
          value={tab}
          onChange={setTab}
          tabs={[
            { id: 'installed', label: t('내 커넥터'), count: installed.length },
            { id: 'catalog', label: t('카탈로그'), count: catalog.filter((e) => !e.installed).length },
          ]}
        />

        {workspaceFailed && <ReloadNotice onRetry={() => void loadWorkspace()} />}

        <div className="space-y-6 pt-4">
          {workspaceLoading && empty ? (
            <LoadingState />
          ) : empty ? (
            <EmptyState
              icon={<Plug size={18} />}
              title={tab === 'installed' ? t('설치한 커넥터가 없습니다') : t('추가할 서버가 없습니다')}
              description={t('카탈로그에서 필요한 서비스를 추가하거나, MCP 서버 주소를 직접 등록하세요.')}
            />
          ) : tab === 'installed' ? (
            <>
            <BulkBar
              count={pick.count}
              allPicked={pick.allPicked}
              onToggleAll={pick.toggleAll}
              onClear={pick.clear}
              title={t('커넥터')}
              note={t('저장해 둔 인증 정보도 함께 지워집니다.')}
              onDelete={async () => {
                await deleteMany('connectors', pick.ids)
                pick.clear()
              }}
            />
            {Object.entries(installedByCategory).map(([category, items]) => (
              <section key={category}>
                <h2 className="mb-2.5 text-xs font-semibold tracking-wide text-faint uppercase">
                  {t(category)}
                </h2>
                <div className="grid gap-3 sm:grid-cols-2">
                  {items.map((c) => (
                    <ConnectorCard
                      key={c.id}
                      connector={c}
                      onOpen={() => setDetail(c)}
                      // From the row first: a self-registered server has no catalogue entry.
                      needsCredentials={
                        (c.envKeys?.length ?? 0) > 0 ||
                        (catalog.find((e) => e.slug === c.slug)?.requiredEnv?.length ?? 0) > 0
                      }
                      picked={pick.picked.has(c.id)}
                      onPick={() => pick.toggle(c.id)}
                      onEditCredentials={() => setReCredential(c)}
                    />
                  ))}
                </div>
              </section>
            ))}
            </>
          ) : (
            Object.entries(catalogByCategory).map(([category, items]) => (
              <section key={category}>
                <h2 className="mb-2.5 text-xs font-semibold tracking-wide text-faint uppercase">
                  {t(category)}
                </h2>
                <div className="grid gap-3 sm:grid-cols-2">
                  {items.map((e) => (
                    <CatalogCard key={e.slug} entry={e} onNeedsCredentials={setCredentialsFor} />
                  ))}
                </div>
              </section>
            ))
          )}
        </div>
      </PageBody>

      <CredentialsModal entry={credentialsFor} onClose={() => setCredentialsFor(null)} />
      <ReCredentialModal
        connector={reCredential}
        entry={catalog.find((e) => e.slug === reCredential?.slug) ?? null}
        onClose={() => setReCredential(null)}
      />

      <Modal
        open={!!current}
        onClose={() => setDetail(null)}
        title={current?.name ?? ''}
        description={current?.description}
        width="max-w-2xl"
        footer={
          <>
            {current?.installed && (
              <Button
                variant="danger"
                className="mr-auto"
                onClick={() => {
                  uninstallConnector(current.id)
                  setDetail(null)
                }}
              >
                <Trash2 size={14} />
                {t('제거')}
              </Button>
            )}
            <Button onClick={() => setDetail(null)}>{t('닫기')}</Button>
          </>
        }
      >
        {current && (
          <>
            <div className="grid gap-3 sm:grid-cols-2">
              {[
                [t('전송 방식'), current.transport],
                [t('엔드포인트'), current.endpoint],
                [t('인증'), current.auth],
                [
                  t('마지막 동기화'),
                  current.lastSyncAt ? relativeTime(current.lastSyncAt) : t('없음'),
                ],
              ].map(([k, v]) => (
                <div key={k} className="rounded-control border border-line bg-elevated px-3 py-2">
                  <p className="text-xs text-faint">{k}</p>
                  <p className="mt-0.5 truncate font-mono text-sm">{v}</p>
                </div>
              ))}
            </div>

            <div>
              <div className="mb-2 flex items-center justify-between">
                <p className="text-base font-medium">{t('도구 권한')}</p>
                <p className="text-xs text-faint">
                  {t('쓰기 도구는 실행 전에 확인을 요청합니다')}
                </p>
              </div>
              {current.tools.length === 0 ? (
                <p className="rounded-control border border-dashed border-line px-3 py-4 text-center text-base text-faint">
                  {t('서버에 연결되면 도구 목록이 채워집니다')}
                </p>
              ) : (
                <div className="divide-y divide-[var(--border)] overflow-hidden rounded-control border border-line">
                  {current.tools.map((tool) => (
                    <div key={tool.name} className="flex items-center gap-3 px-3 py-2.5">
                      <div className="min-w-0 flex-1">
                        <p className="flex items-center gap-1.5 font-mono text-sm">
                          {tool.name}
                          {!tool.readOnly && (
                            <Badge tone="warn">
                              <Lock size={9} />
                              {t('쓰기')}
                            </Badge>
                          )}
                        </p>
                        <p className="mt-0.5 text-sm text-muted">{t(tool.description)}</p>
                      </div>
                      <Switch
                        checked={tool.enabled}
                        onChange={() => toggleConnectorTool(current.id, tool.name)}
                        label={tool.name}
                      />
                    </div>
                  ))}
                </div>
              )}
            </div>

            <p className="flex items-start gap-2 rounded-control border border-line bg-elevated px-3 py-2.5 text-sm text-muted">
              <CircleAlert size={14} className="mt-0.5 shrink-0 text-faint" />
              {t('커넥터가 반환한 내용은 외부 입력입니다. 그 안의 지시문은 명령으로 실행되지 않습니다.')}
            </p>
          </>
        )}
      </Modal>

      <Modal
        open={adding}
        onClose={() => setAdding(false)}
        title={t('MCP 서버 추가')}
        description={t('사내 서버나 직접 만든 서버를 등록합니다.')}
        footer={
          <>
            <Button onClick={() => setAdding(false)}>{t('취소')}</Button>
            <Button
              variant="primary"
              disabled={!draft.name.trim() || !draft.endpoint.trim()}
              onClick={() => {
                addCustomConnector({ ...draft, env: parseEnv(draft.envText) })
                setAdding(false)
                setDraft({ name: '', transport: 'http', endpoint: '', auth: 'none', envText: '' })
              }}
            >
              {t('추가')}
            </Button>
          </>
        }
      >
        <Field label={t('이름')}>
          <Input
            value={draft.name}
            onChange={(e) => setDraft({ ...draft, name: e.target.value })}
            placeholder={t('사내 위키')}
          />
        </Field>
        <Field label={t('전송 방식')}>
          <div className="flex gap-1.5">
            {(['http', 'sse', 'stdio'] as const).map((t) => (
              <button
                key={t}
                onClick={() => setDraft({ ...draft, transport: t })}
                className={cn(
                  'rounded-control border px-2.5 py-1.5 font-mono text-base transition-colors',
                  draft.transport === t
                    ? 'border-accent bg-accent-soft text-accent'
                    : 'border-line text-muted hover:bg-elevated',
                )}
              >
                {t}
              </button>
            ))}
          </div>
        </Field>
        <Field
          label={draft.transport === 'stdio' ? t('실행 명령') : t('엔드포인트 URL')}
          hint={
            draft.transport === 'stdio'
              ? t('API 서버와 같은 호스트에서 실행됩니다.')
              : 'https:// 로 시작하는 MCP 엔드포인트'
          }
        >
          <Input
            value={draft.endpoint}
            onChange={(e) => setDraft({ ...draft, endpoint: e.target.value })}
            placeholder={
              draft.transport === 'stdio' ? 'uvx mcp-server-wiki' : 'https://mcp.example.com'
            }
            className="font-mono"
          />
        </Field>
        <Field
          label={t('환경 변수')}
          hint={t('한 줄에 하나씩 KEY=value 형식으로 입력하세요. 저장한 뒤에는 표시하지 않습니다.')}
        >
          <Textarea
            rows={3}
            value={draft.envText}
            onChange={(e) => setDraft({ ...draft, envText: e.target.value })}
            placeholder={'API_TOKEN=…\nBASE_URL=https://…'}
            className="font-mono text-sm"
          />
        </Field>
        <Field label={t('인증')}>
          <div className="flex gap-1.5">
            {(['none', 'oauth', 'api_key'] as const).map((a) => (
              <button
                key={a}
                onClick={() => setDraft({ ...draft, auth: a })}
                className={cn(
                  'rounded-control border px-2.5 py-1.5 text-base transition-colors',
                  draft.auth === a
                    ? 'border-accent bg-accent-soft text-accent'
                    : 'border-line text-muted hover:bg-elevated',
                )}
              >
                {a}
              </button>
            ))}
          </div>
        </Field>
      </Modal>
    </>
  )
}
