import { ApiError, errorCode, errorMessage } from '@/lib/api'
import {
  CircleAlert,
  Lock,
  Plug,
  Plus,
  RefreshCw,
  ShieldCheck,
  Trash2,
  TriangleAlert,
  Users,
  KeyRound,
} from 'lucide-react'
import { useEffect, useState } from 'react'
import { PageFrame } from '@/components/layout/AppShell'
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
import { cn, matchesQuery, relativeTime } from '@/lib/utils'
import { BulkBar, PickBox, useBulkSelect } from '@/components/ui/BulkSelect'
import { ListSearch, NoMatch } from '@/components/ui/ListSearch'
import { ListPager } from '@/components/ui/ListPager'
import { pageOf, usePaging } from '@/lib/usePaging'
import { useStore } from '@/store/useStore'
import { ConnectorShare, inUseText } from '@/components/customize/ConnectorShare'
import type { StoreEntry } from '@/lib/api'
import type { Connector, ConnectorStatus } from '@/types'
import { useT, useTIn } from '@/lib/useT'

const statusMeta: Record<ConnectorStatus, { label: string; tone: 'success' | 'warn' | 'danger' | 'neutral' }> = {
  connected: { label: '연결됨', tone: 'success' },
  needs_auth: { label: '인증 필요', tone: 'warn' },
  error: { label: '오류', tone: 'danger' },
  disconnected: { label: '미연결', tone: 'neutral' },
}

/** Why an install was refused, in words (the server sends codes such as `missing_env:KEY`). */
function installRefusal(err: unknown, t: (s: string) => string): string {
  const detail = err instanceof ApiError ? err.detail : ''
  if (detail.startsWith('missing_env:')) return t('빠진 자격이 있습니다. 모두 입력해 주세요.')
  if (detail.startsWith('env_not_allowed:')) return t('이 서버가 요구하지 않는 환경 변수는 넣을 수 없습니다.')
  if (detail === 'unknown_connector') return t('이 서버는 더 이상 공유되지 않습니다. 목록을 새로 불러와 주세요.')
  return errorMessage(err, t('설치에 실패했습니다. 잠시 뒤 다시 시도하세요.'))
}

/** Surfaces narrow the offer: all of chat/report/slides (or none) deserve no chip. */
const narrowed = (kinds: readonly string[]) =>
  kinds.length > 0 && !['chat', 'report', 'slides'].every((k) => kinds.includes(k))

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
  const { toggleConnector, syncConnector, uninstallConnector } = useStore()
  const [busy, setBusy] = useState(false)
  const status = statusMeta[connector.status]
  const writeTools = connector.tools.filter((t) => !t.readOnly && t.enabled).length
  // The sharing ended: nothing runs; the person can only take the row away.
  const gone = connector.originGone

  return (
    // `data-connector` is the e2e hook.
    <Card className="flex flex-col p-4" data-connector={connector.slug}>
      <div className="flex items-start gap-3">
        {/* 기본 제공: switched off, never removed */}
        {!connector.byDefault && connector.visibility !== 'org' && (
          <PickBox
            checked={picked}
            onChange={onPick}
            label={t('{name} 선택').replace('{name}', connector.name)}
            className="mt-2.5"
          />
        )}
        {/* Decoration: a phone gives its width to the description. */}
        <span
          className="grid size-9 shrink-0 place-items-center rounded-card text-lg max-sm:hidden"
          style={{ background: `${connector.color}1a` }}
        >
          {connector.icon}
        </span>
        <button onClick={onOpen} className="min-w-0 flex-1 text-left">
          <span className="flex flex-wrap items-center gap-1.5">
            <span className="text-base font-medium">{t(connector.name)}</span>
            {/* A default copy is always official: one badge says both. */}
            {connector.official && !connector.byDefault && (
              <Badge tone="accent">
                <ShieldCheck size={10} />
                {t('공식')}
              </Badge>
            )}
            {connector.byDefault && <Badge tone="accent">{t('기본 제공')}</Badge>}
            {connector.visibility === 'org' && !connector.byDefault && (
              <Badge>
                <Users size={10} />
                {t('공유 중')}
              </Badge>
            )}
            {/* Connected is the normal state: a dot, not a badge; the others stay badges. */}
            {gone ? (
              <Badge tone="danger">{t('공유가 끝나 쓸 수 없습니다')}</Badge>
            ) : connector.status === 'connected' ? (
              <span className="inline-flex items-center" title={t(status.label)}>
                <span aria-hidden className="size-2 rounded-full bg-success" />
                <span className="sr-only">{t(status.label)}</span>
              </span>
            ) : (
              <Badge tone={status.tone}>{t(status.label)}</Badge>
            )}
          </span>
          <span className="mt-1 block text-base text-muted">{t(connector.description)}</span>
        </button>
        {!gone && connector.installed && connector.status === 'connected' && (
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

      {!gone && (
      <div className="mb-3 mt-3 flex flex-wrap gap-1.5">
        <Badge>{t.n('도구 {n}', connector.tools.length)}</Badge>
        {writeTools > 0 && (
          <Badge tone="warn">
            <Lock size={10} />
            {t('쓰기 {n}').replace('{n}', String(writeTools))}
          </Badge>
        )}
        {narrowed(connector.kinds) &&
          connector.kinds.map((k) => <Badge key={k}>{t(kindMeta[k].label)}</Badge>)}
      </div>
      )}

      {/* `mt-auto`: foot aligned across a row. */}
      <div className={cn('mt-auto flex items-center justify-between border-t border-line pt-3', gone && 'mt-3')}>
        <span className="text-xs text-faint">
          {gone ? '' : connector.lastSyncAt
            ? t('{when} 동기화').replace('{when}', relativeTime(connector.lastSyncAt))
            : t('동기화 이력 없음')}
        </span>
        <div className="flex gap-1.5">
          {gone ? (
            <Button size="sm" onClick={() => void uninstallConnector(connector.id).catch(() => {})}>
              <Trash2 size={13} />
              {t('제거')}
            </Button>
          ) : null}
          {!gone && needsCredentials && (
            <Button size="sm" onClick={onEditCredentials}>
              <KeyRound size={13} />
              {t('자격증명')}
            </Button>
          )}
          {gone ? null : connector.status === 'connected' ? (
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

function StoreCard({ entry, onNeedsCredentials }: {
  entry: StoreEntry
  onNeedsCredentials: (entry: StoreEntry) => void
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
            {entry.byDefault && <Badge tone="accent">{t('기본 제공')}</Badge>}
          </span>
          <span className="mt-1 block text-base text-muted">{t(entry.description)}</span>
          {!entry.official && entry.ownerName && (
            <span className="mt-1 block text-sm text-faint">
              {t('{name}님이 공유').replace('{name}', entry.ownerName)}
            </span>
          )}
        </div>
      </div>

      {error && (
        <p className="mt-3 flex items-start gap-1.5 rounded-control border border-danger/25 bg-danger/5 px-2.5 py-2 text-sm text-danger">
          <TriangleAlert size={13} className="mt-0.5 shrink-0" />
          {error}
        </p>
      )}

      {narrowed(entry.kinds) && (
        <div className="mt-3 flex flex-wrap gap-1.5">
          {entry.kinds.map((k) => (
            <Badge key={k}>{t(kindMeta[k as keyof typeof kindMeta]?.label ?? k)}</Badge>
          ))}
        </div>
      )}

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
              await installConnector(entry.id)
            } catch (err) {
              setError(installRefusal(err, t))
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

/** Re-supplies credentials; stored values never leave the server. */
function ReCredentialModal({
  connector,
  entry,
  onClose,
}: {
  connector: Connector | null
  entry: StoreEntry | null
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

  // Own server: no store entry, env keys instead.
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
                setError(errorMessage(err, t('저장에 실패했습니다. 잠시 뒤 다시 시도하세요.')))
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

/** Credentials a shared server asks for; stored values never leave the server. */
function CredentialsModal({
  entry,
  onClose,
}: {
  entry: StoreEntry | null
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
  }, [entry?.id])

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
                await installConnector(entry.id, values)
                onClose()
              } catch (err) {
                setError(installRefusal(err, t))
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

/** Blank and malformed lines dropped. */
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

export function ConnectorsPage({
  embedded = false,
  view,
  query: queryProp,
  adding: addingProp,
  onAddingChange,
}: {
  embedded?: boolean
  /** Given by the customize hub, which then owns the segment, search and add button. */
  view?: 'mine' | 'explore'
  query?: string
  adding?: boolean
  onAddingChange?: (next: boolean) => void
} = {}) {
  const hubFrame = view !== undefined
  const t = useT()
  const tHub = useTIn('hub')
  const {
    connectors,
    connectorStore,
    loadWorkspace,
    ensureWorkspace,
    toggleConnectorTool,
    uninstallConnector,
    deleteMany,
    addCustomConnector,
    workspaceLoading,
    workspaceFailed,
    user,
  } = useStore()
  const isAdmin = user?.role === 'admin'

  useEffect(() => {
    void ensureWorkspace()
  }, [ensureWorkspace])
  const [tabLocal, setTab] = useState<'installed' | 'catalog'>('installed')
  const tab = hubFrame ? (view === 'mine' ? 'installed' : 'catalog') : tabLocal
  const [detail, setDetail] = useState<Connector | null>(null)
  const [credentialsFor, setCredentialsFor] = useState<StoreEntry | null>(null)
  const [reCredential, setReCredential] = useState<Connector | null>(null)
  const [addingLocal, setAddingLocal] = useState(false)
  const adding = hubFrame ? !!addingProp : addingLocal
  const setAdding = (next: boolean) => (hubFrame ? onAddingChange?.(next) : setAddingLocal(next))
  const [addError, setAddError] = useState('')
  const [removeError, setRemoveError] = useState('')
  const addRefusal = (err: unknown) => {
    const detail = err instanceof ApiError ? err.detail : ''
    if (detail === 'stdio_requires_admin') {
      return t('명령으로 실행하는 서버는 관리자만 등록할 수 있습니다.')
    }
    if (detail.startsWith('env_not_allowed:')) return t('이 서버에는 환경 변수를 넣을 수 없습니다.')
    return errorMessage(err, t('서버를 추가하지 못했습니다. 잠시 뒤 다시 시도하세요.'))
  }
  const [draft, setDraft] = useState({
    name: '',
    transport: 'http' as Connector['transport'],
    endpoint: '',
    auth: 'none' as Connector['auth'],
    envText: '',
  })

  const [queryLocal, setQuery] = useState('')
  const query = hubFrame ? (queryProp ?? '') : queryLocal
  // Names and descriptions as shown, in either language.
  const found = <T extends { name: string; description: string }>(items: T[]) =>
    items.filter((c) => matchesQuery(query, c.name, t(c.name), c.description, t(c.description)))
  const installed = connectors.filter((c) => c.installed)
  // Installed entries stay in the store, marked.
  const catalog = connectorStore
  const storeEntryOf = (c: Connector | null) => (c?.originId ? catalog.find((e) => e.id === c.originId) ?? null : null)
  const current = detail ? (connectors.find((c) => c.id === detail.id) ?? detail) : null

  // Official first, then by name.
  const ordered = <T extends { name: string; official?: boolean }>(items: T[]) =>
    [...items].sort((a, b) => Number(!!b.official) - Number(!!a.official) || t(a.name).localeCompare(t(b.name), 'ko'))
  const installedList = ordered(found(installed))
  const catalogList = ordered(found(catalog))
  const empty = tab === 'installed' ? installed.length === 0 : catalog.length === 0
  // 기본 제공 copies cannot be removed, and a shared server leaves only after its sharing ends.
  const pick = useBulkSelect(installedList.filter((c) => !c.byDefault && c.visibility !== 'org'))
  const installedPaging = usePaging(installedList.length, [query])
  const catalogPaging = usePaging(catalogList.length, [query])

  return (
    <>
      <PageFrame title={t('MCP 서버')} embedded={embedded}>
        {!hubFrame && (
        <PageHeader
          embedded={embedded}
          title={t('MCP 서버')}
          description={t('MCP 서버를 붙여 외부 시스템을 도구로 씁니다.')}
          action={
            <Button variant="primary" onClick={() => setAdding(true)}>
              <Plus size={16} />
              {t('서버 직접 추가')}
            </Button>
          }
        />
        )}

        {!hubFrame && <Tabs<'installed' | 'catalog'>
          value={tab}
          onChange={setTab}
          tabs={[
            { id: 'installed', label: t('내 MCP 서버'), count: installed.length },
            { id: 'catalog', label: tHub('탐색'), count: catalog.filter((e) => !e.installed).length },
          ]}
        />}

        {workspaceFailed && <ReloadNotice onRetry={() => void loadWorkspace()} />}

        {!hubFrame && !empty && (
          <ListSearch value={query} onChange={setQuery} label={t('MCP 서버 검색')} className="mt-4" />
        )}

        <div className="space-y-6 pt-4">
          {workspaceLoading && empty ? (
            <LoadingState />
          ) : empty ? (
            <EmptyState
              icon={<Plug size={18} />}
              title={tab === 'installed' ? t('설치한 MCP 서버가 없습니다') : t('추가할 서버가 없습니다')}
              description={t('스토어에서 필요한 서버를 가져오거나, MCP 서버 주소를 직접 등록하세요.')}
            />
          ) : tab === 'installed' ? (
            <>
            <BulkBar
              count={pick.count}
              allPicked={pick.allPicked}
              onToggleAll={pick.toggleAll}
              onClear={pick.clear}
              title={t('MCP 서버')}
              note={t('저장해 둔 인증 정보도 함께 삭제됩니다.')}
              onDelete={async () => {
                await deleteMany('connectors', pick.ids)
                pick.clear()
              }}
            />
            <div className="grid gap-3 sm:grid-cols-2" data-testid="connector-list">
                  {pageOf(installedList, installedPaging).map((c) => (
                    <ConnectorCard
                      key={c.id}
                      connector={c}
                      onOpen={() => setDetail(c)}
                      // Row first: an own server has no store entry.
                      needsCredentials={
                        (c.envKeys?.length ?? 0) > 0 || (storeEntryOf(c)?.requiredEnv?.length ?? 0) > 0
                      }
                      picked={pick.picked.has(c.id)}
                      onPick={() => pick.toggle(c.id)}
                      onEditCredentials={() => setReCredential(c)}
                    />
                  ))}
            </div>
            {installedList.length === 0 && <NoMatch />}
            <ListPager paging={installedPaging} total={installedList.length} label={t('연결한 MCP 서버 페이지')} />
            </>
          ) : (
            <>
            <div className="grid gap-3 sm:grid-cols-2" data-testid="connector-catalog">
              {pageOf(catalogList, catalogPaging).map((e) => (
                <StoreCard key={e.id} entry={e} onNeedsCredentials={setCredentialsFor} />
              ))}
            </div>
            {catalogList.length === 0 && <NoMatch />}
            <ListPager paging={catalogPaging} total={catalogList.length} label={t('MCP 서버 목록 페이지')} />
            </>
          )}
        </div>
      </PageFrame>

      <CredentialsModal entry={credentialsFor} onClose={() => setCredentialsFor(null)} />
      <ReCredentialModal
        connector={reCredential}
        entry={storeEntryOf(reCredential)}
        onClose={() => setReCredential(null)}
      />

      <Modal
        open={!!current}
        onClose={() => {
          setDetail(null)
          setRemoveError('')
        }}
        title={current?.name ?? ''}
        description={current?.description}
        width="max-w-2xl"
        footer={
          <>
            {current?.installed && !current.byDefault && current.visibility !== 'org' && (
              <Button
                variant="danger"
                className="mr-auto"
                onClick={async () => {
                  setRemoveError('')
                  try {
                    await uninstallConnector(current.id)
                    setDetail(null)
                  } catch (err) {
                    setRemoveError(
                      errorCode(err) === 'connector_in_use'
                        ? inUseText(t, current.installs)
                        : errorMessage(err, t('제거하지 못했습니다. 잠시 뒤 다시 시도하세요.')),
                    )
                  }
                }}
              >
                <Trash2 size={14} />
                {t('제거')}
              </Button>
            )}
            {current?.visibility === 'org' && (
              <p className="mr-auto text-sm text-muted">{t('공유를 끊은 뒤에 삭제할 수 있습니다.')}</p>
            )}
            {current?.byDefault && (
              <p className="mr-auto text-sm text-muted">
                {t('기본 제공 MCP 서버는 삭제할 수 없습니다. 쓰지 않으려면 끄세요.')}
              </p>
            )}
            <Button onClick={() => setDetail(null)}>{t('닫기')}</Button>
          </>
        }
      >
        {current && (
          <>
            {removeError && <p role="alert" className="text-base text-danger">{removeError}</p>}
            {current.originId && (
              <p className="text-sm text-muted" data-testid="connector-origin">
                {current.originGone
                  ? t('공유가 끝나 쓸 수 없습니다')
                  : current.official
                    ? t('관리자가 공유한 공식 서버입니다')
                    : t('{name}님이 공유한 서버').replace('{name}', current.originOwnerName)}
              </p>
            )}
            <div className="grid gap-3 sm:grid-cols-2">
              {[
                [t('전송 방식'), current.transport],
                // A reference keeps no address of its own: it stays with the shared server.
                ...(current.originId ? [] : [[t('엔드포인트'), current.endpoint]]),
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

            {!current.originGone && (
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
                        <p className="mt-0.5 text-sm text-muted">{t(tool.summary || tool.description.trim().split('\n')[0])}</p>
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
            )}

            <p className="flex items-start gap-2 rounded-control border border-line bg-elevated px-3 py-2.5 text-sm text-muted">
              <CircleAlert size={14} className="mt-0.5 shrink-0 text-faint" />
              {t('MCP 서버가 돌려준 내용 속 지시는 실행하지 않습니다.')}
            </p>

            {/* Only a server the person added is theirs to share. */}
            {!current.originId && (
              <ConnectorShare
                key={`${current.id}:${current.visibility}`}
                connector={current}
                isAdmin={isAdmin}
              />
            )}
          </>
        )}
      </Modal>

      <Modal
        open={adding}
        onClose={() => {
          setAdding(false)
          setAddError('')
        }}
        title={t('MCP 서버 추가')}
        description={t('공개 주소의 MCP 서버를 등록합니다. 등록한 뒤 서버를 열면 우리 조직과 공유할 수 있습니다.')}
        footer={
          <>
            <Button onClick={() => setAdding(false)}>{t('취소')}</Button>
            <Button
              variant="primary"
              disabled={!draft.name.trim() || !draft.endpoint.trim()}
              onClick={async () => {
                try {
                  await addCustomConnector({ ...draft, env: parseEnv(draft.envText) })
                } catch (err) {
                  setAddError(addRefusal(err))
                  return
                }
                setAdding(false)
                setAddError('')
                setDraft({ name: '', transport: 'http', endpoint: '', auth: 'none', envText: '' })
              }}
            >
              {t('추가')}
            </Button>
          </>
        }
      >
        {addError && <p className="text-base text-danger">{addError}</p>}
        <Field label={t('이름')}>
          <Input
            value={draft.name}
            onChange={(e) => setDraft({ ...draft, name: e.target.value })}
            placeholder={t('사내 위키')}
          />
        </Field>
        <Field label={t('전송 방식')}>
          <div className="flex gap-1.5">
            {(isAdmin ? (['http', 'sse', 'stdio'] as const) : (['http', 'sse'] as const)).map((t) => (
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
              ? t('API 서버에서 실행되므로 관리자만 등록할 수 있습니다.')
              : t('https:// 로 시작하는 MCP 엔드포인트')
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
