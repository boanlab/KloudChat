import { Pencil, Plus, Trash2 } from 'lucide-react'
import { useCallback, useEffect, useState } from 'react'
import { Badge, Button, ConfirmDialog, Field, Input, Modal, Select } from '@/components/ui'
import { connectorCatalogApi, errorCode, errorMessage, type CatalogAdmin, type CatalogEntryIn } from '@/lib/api'
import { useT } from '@/lib/useT'
import type { CatalogEntryAdmin } from '@/types'

const BLANK: CatalogEntryIn = {
  slug: '', name: '', description: '', category: '', transport: 'http', endpoint: '', env: {},
  requiredEnv: [], toolsKeyVar: '', toolsKeyFeature: '', kinds: ['chat'], enabled: true, listed: true,
  position: 0,
}

const ERRORS: Record<string, string> = {
  catalog_slug: '슬러그는 영문 소문자·숫자·하이픈으로 적으세요.',
  catalog_slug_taken: '이미 있는 슬러그입니다.',
  catalog_slug_fixed: '설치된 커넥터가 슬러그로 찾으므로 슬러그는 바꿀 수 없습니다.',
  catalog_endpoint: 'stdio는 명령 한 줄, http·sse는 https:// 주소나 ${TOOLS_*_URL} 자리표시로 적으세요.',
  catalog_placeholder: '모르는 자리표시입니다.',
  catalog_env: '환경 변수 이름은 영문 대문자로, 값은 한 줄로 적으세요. TOOLS_GATEWAY_KEY는 직접 넣을 수 없습니다.',
  catalog_tools_key: '게이트웨이 키를 넣을 변수는 환경 변수 중 하나여야 하고, 도구를 골라야 합니다.',
  catalog_kinds: '쓸 화면을 하나 이상 고르세요.',
}

type EnvRow = { key: string; value: string; stored: boolean }

/** Listing, run mode, gateway-key injection. */
function EntryForm({
  initial,
  meta,
  onSaved,
  onClose,
}: {
  initial: CatalogEntryAdmin | null
  meta: CatalogAdmin
  onSaved: (rows: CatalogEntryAdmin[]) => void
  onClose: () => void
}) {
  const t = useT()
  const [value, setValue] = useState<CatalogEntryIn>(() =>
    initial ? { ...BLANK, ...initial } : BLANK)
  // Stored value never sent back: 「저장됨」, kept unless replaced.
  const [env, setEnv] = useState<EnvRow[]>(() =>
    Object.entries(initial?.env ?? {}).map(([key, v]) => ({ key, value: v ?? '', stored: v === null })))
  const [error, setError] = useState<string | null>(null)
  const set = <K extends keyof CatalogEntryIn>(key: K, v: CatalogEntryIn[K]) => setValue({ ...value, [key]: v })
  const save = async () => {
    setError(null)
    const payload: CatalogEntryIn = {
      ...value,
      env: Object.fromEntries(env.filter((r) => r.key.trim()).map((r) => [r.key.trim(), r.stored && !r.value ? null : r.value])),
    }
    try {
      const out = initial ? await connectorCatalogApi.edit(initial.id, payload) : await connectorCatalogApi.add(payload)
      onSaved(out.entries)
    } catch (err) {
      const said = ERRORS[errorCode(err) ?? '']
      setError(said ? t(said) : errorMessage(err, t('저장하지 못했습니다.')))
    }
  }
  return (
    <Modal
      open
      onClose={onClose}
      title={initial ? t('카탈로그 항목 편집') : t('카탈로그 항목 추가')}
      width="max-w-2xl"
      footer={
        <>
          <Button type="button" onClick={onClose}>{t('취소')}</Button>
          <Button type="button" variant="primary" onClick={() => void save()} disabled={!value.slug || !value.name || !value.endpoint}>
            {t('저장')}
          </Button>
        </>
      }
    >
      <div className="space-y-3" data-testid="catalog-entry-form">
        {error && <p role="alert" className="text-sm text-danger">{error}</p>}
        <div className="grid gap-3 sm:grid-cols-2">
          <Field label={t('이름')}>
            <Input value={value.name} maxLength={80} onChange={(e) => set('name', e.target.value)} />
          </Field>
          <Field label={t('슬러그')} hint={t('설치된 커넥터가 이 이름으로 항목을 찾습니다.')}>
            <Input value={value.slug} maxLength={60} disabled={!!initial} className="font-mono"
              onChange={(e) => set('slug', e.target.value.toLowerCase())} />
          </Field>
        </div>
        <Field label={t('설명')}>
          <Input value={value.description} maxLength={300} onChange={(e) => set('description', e.target.value)} />
        </Field>
        <div className="grid gap-3 sm:grid-cols-3">
          <Field label={t('분류')}>
            <Input value={value.category} maxLength={30} onChange={(e) => set('category', e.target.value)} />
          </Field>
          <Field label={t('연결 방식')}>
            <Select value={value.transport} onChange={(e) => set('transport', e.target.value as CatalogEntryIn['transport'])}>
              {meta.transports.map((x) => <option key={x} value={x}>{x === 'stdio' ? t('명령 실행 (stdio)') : x.toUpperCase()}</option>)}
            </Select>
          </Field>
          <Field label={t('순서')}>
            <Input type="number" min={0} max={999} value={value.position}
              onChange={(e) => set('position', Math.max(0, Math.min(999, Number(e.target.value) || 0)))} />
          </Field>
        </div>
        <Field
          label={value.transport === 'stdio' ? t('명령') : t('주소')}
          hint={`${t('기능 연동 주소는 자리표시로')}: ${meta.placeholders.join(' ')}`}
        >
          <Input value={value.endpoint} maxLength={500} className="font-mono" onChange={(e) => set('endpoint', e.target.value)} />
        </Field>
        <div className="space-y-2">
          <p className="text-base font-medium">{t('환경 변수')}</p>
          {env.map((row, i) => (
            <div key={i} className="flex flex-wrap items-center gap-2" data-testid="catalog-env-row">
              <Input aria-label={t('변수 이름')} value={row.key} className="w-48 font-mono"
                onChange={(e) => setEnv(env.map((r, k) => (k === i ? { ...r, key: e.target.value.toUpperCase() } : r)))} />
              <Input aria-label={t('값')} value={row.value} className="min-w-40 flex-1 font-mono"
                placeholder={row.stored ? t('저장됨 — 바꿀 때만 적으세요') : ''}
                onChange={(e) => setEnv(env.map((r, k) => (k === i ? { ...r, value: e.target.value } : r)))} />
              <Button type="button" size="sm" variant="ghost" aria-label={t('변수 빼기')}
                onClick={() => setEnv(env.filter((_, k) => k !== i))}>
                <Trash2 size={13} />
              </Button>
            </div>
          ))}
          <Button type="button" size="sm" onClick={() => setEnv([...env, { key: '', value: '', stored: false }])}>
            <Plus size={13} />
            {t('변수 추가')}
          </Button>
        </div>
        <div className="grid gap-3 sm:grid-cols-2">
          <Field label={t('게이트웨이 키를 넣을 변수')} hint={t('이 변수가 고른 도구의 주소일 때만 TOOLS_GATEWAY_KEY가 들어갑니다.')}>
            <Select value={value.toolsKeyVar} onChange={(e) => set('toolsKeyVar', e.target.value)}>
              <option value="">{t('넣지 않음')}</option>
              {env.filter((r) => r.key.trim()).map((r) => <option key={r.key} value={r.key}>{r.key}</option>)}
            </Select>
          </Field>
          <Field label={t('그 주소의 도구')}>
            <Select value={value.toolsKeyFeature} disabled={!value.toolsKeyVar} onChange={(e) => set('toolsKeyFeature', e.target.value)}>
              <option value="">{t('고르세요')}</option>
              {meta.features.map((f) => <option key={f} value={f}>{f}</option>)}
            </Select>
          </Field>
        </div>
        <div className="flex flex-wrap items-center gap-4">
          <span className="text-base font-medium">{t('쓸 화면')}</span>
          {meta.kinds.map((k) => (
            <label key={k} className="flex items-center gap-1.5 text-base">
              <input type="checkbox" className="size-4 accent-[var(--accent)]" checked={value.kinds.includes(k)}
                onChange={() => set('kinds', value.kinds.includes(k) ? value.kinds.filter((x) => x !== k) : [...value.kinds, k])} />
              {t({ chat: '대화', report: '보고서', slides: '슬라이드' }[k] ?? k)}
            </label>
          ))}
          <label className="flex items-center gap-1.5 text-base">
            <input type="checkbox" className="size-4 accent-[var(--accent)]" checked={value.enabled}
              onChange={(e) => set('enabled', e.target.checked)} />
            {t('설치할 수 있게 켜기')}
          </label>
          <label className="flex items-center gap-1.5 text-base" title={t('끄면 멤버 카탈로그에 나오지 않지만, 설치된 것은 계속 동작합니다.')}>
            <input type="checkbox" className="size-4 accent-[var(--accent)]" checked={value.listed}
              data-testid="catalog-entry-listed"
              onChange={(e) => set('listed', e.target.checked)} />
            {t('목록에 보이기')}
          </label>
        </div>
      </div>
    </Modal>
  )
}

/** What members can install; stored values never come back. */
export function ConnectorCatalogSection() {
  const t = useT()
  const [meta, setMeta] = useState<CatalogAdmin | null>(null)
  const [editing, setEditing] = useState<CatalogEntryAdmin | 'new' | null>(null)
  const [removing, setRemoving] = useState<CatalogEntryAdmin | null>(null)
  const [error, setError] = useState<string | null>(null)
  const load = useCallback(async () => {
    try {
      setMeta(await connectorCatalogApi.list())
    } catch (err) {
      setError(errorMessage(err, t('카탈로그를 불러오지 못했습니다.')))
    }
  }, [t])
  useEffect(() => {
    void load()
  }, [load])
  if (!meta) return error ? <p role="alert" className="text-sm text-danger">{error}</p> : null
  return (
    <div className="space-y-3" data-testid="connector-catalog-admin">
      <div className="flex justify-end">
        <Button size="sm" variant="primary" onClick={() => setEditing('new')}>
          <Plus size={13} />
          {t('항목 추가')}
        </Button>
      </div>
      <ul className="divide-y divide-line rounded-control border border-line">
        {meta.entries.map((row) => (
          <li key={row.id} className="flex flex-wrap items-center gap-3 px-3 py-2.5" data-testid="catalog-entry">
            <span className="min-w-0 flex-1">
              <span className="flex flex-wrap items-center gap-2">
                <span className="font-medium">{row.name}</span>
                <Badge>{row.transport === 'stdio' ? t('명령') : row.transport.toUpperCase()}</Badge>
                {row.toolsKeyFeature && <Badge>{t('게이트웨이 키')}: {row.toolsKeyFeature}</Badge>}
                {!row.enabled && <Badge>{t('꺼짐')}</Badge>}
                {row.enabled && row.listed === false && <Badge>{t('숨김')}</Badge>}
              </span>
              <span className="block truncate text-sm text-muted">{row.description}</span>
              <code className="block truncate text-sm text-faint">{row.endpoint}</code>
            </span>
            <Button size="sm" variant="ghost" aria-label={`${row.name} ${t('편집')}`} onClick={() => setEditing(row)}>
              <Pencil size={13} />
            </Button>
            <Button size="sm" variant="ghost" aria-label={`${row.name} ${t('삭제')}`} onClick={() => setRemoving(row)}>
              <Trash2 size={13} />
            </Button>
          </li>
        ))}
      </ul>
      {editing && (
        <EntryForm
          initial={editing === 'new' ? null : editing}
          meta={meta}
          onClose={() => setEditing(null)}
          onSaved={(entries) => {
            setMeta({ ...meta, entries })
            setEditing(null)
          }}
        />
      )}
      <ConfirmDialog
        open={!!removing}
        onClose={() => setRemoving(null)}
        onConfirm={async () => {
          if (!removing) return
          const out = await connectorCatalogApi.remove(removing.id)
          setMeta({ ...meta, entries: out.entries })
        }}
        title={t('{name} 삭제').replace('{name}', removing?.name ?? '')}
        description={t('카탈로그에서 빠져 새로 설치할 수 없습니다. 이미 설치한 사람의 커넥터는 남지만 게이트웨이 키는 더 이상 들어가지 않습니다.')}
      />
    </div>
  )
}
