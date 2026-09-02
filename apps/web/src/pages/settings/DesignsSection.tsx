import { FileUp, Palette, Plus, Trash2 } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import { Badge, Button, ConfirmDialog, Field, Input, Select, Switch, Textarea } from '@/components/ui'
import {
  designsApi,
  errorMessage,
  filesApi,
  type DesignRow,
  type DesignTokens,
} from '@/lib/api'
import { BulkBar, PickBox, useBulkSelect } from '@/components/ui/BulkSelect'
import { useStore } from '@/store/useStore'
import { useT } from '@/lib/useT'

/**
 * The craft rules the API knows, and what to call them on screen.
 *
 * Mirrors `CRAFT` in `apps/api/app/services/design.py`; a key that is not there
 * is dropped on save rather than stored, so this list going stale costs a
 * checkbox that does nothing, not a broken document.
 */
const CRAFT: { key: string; label: string; hint: string }[] = [
  {
    key: 'restraint',
    label: '군더더기 덜기',
    hint: '이모지와 채움말을 빼고, 채울 내용이 없으면 분량을 줄입니다.',
  },
  {
    key: 'typography',
    label: '글의 결 맞추기',
    hint: '강조 방법과 제목 단계를 문서 안에서 하나로 유지합니다.',
  },
]

const FONTS: { key: DesignTokens['font']; label: string }[] = [
  { key: 'gothic', label: '고딕 — 발표·화면' },
  { key: 'serif', label: '명조 — 보고서·인쇄' },
]

const BODY_MAX = 400

/** A blank look, using the same defaults the API falls back to. */
const blank = (): Partial<DesignRow> => ({
  name: '',
  description: '',
  tokens: { accent: '#5b5bd6', ink: '#1a1a1a', muted: '#666666', font: 'gothic' },
  body: '',
  imageStyle: '',
  craft: [],
  shared: false,
})

function Swatch({
  label,
  value,
  onChange,
}: {
  label: string
  value: string
  onChange: (v: string) => void
}) {
  const t = useT()
  return (
    <Field label={t(label)}>
      <span className="flex items-center gap-2">
        <input
          type="color"
          aria-label={t(label)}
          value={value}
          onChange={(e) => onChange(e.target.value)}
          className="h-9 w-12 shrink-0 cursor-pointer rounded-control border border-line bg-panel p-1"
        />
        {/* The picker and the code are two views of one value. The code field
            carries its own name because a `<label>` binds to the first control
            inside it, which is the picker — leaving this one unaddressable. */}
        <Input
          aria-label={t('{label} 색상 코드').replace('{label}', t(label))}
          value={value}
          onChange={(e) => onChange(e.target.value)}
        />
      </span>
    </Field>
  )
}

/**
 * Reading a design system out of something that already exists.
 *
 * The four colours and the paragraph of style rules are the part nobody types.
 * The material is usually on hand — the 공문 template everything is filed on,
 * last year's report, a page on the department site.
 *
 * What comes back opens the editor rather than becoming a row: it is one
 * model's reading of a document, and only its owner can say whether it read it
 * right.
 */
/**
 * Refusals this form knows how to say out loud.
 *
 * The API answers 4xx with a stable code, which `errorMessage` shows as-is —
 * fine for a log, useless on a screen. The service's own failures already come
 * back as sentences and fall through untouched.
 */
const REFUSAL: Record<string, string> = {
  file_unreadable: '이 파일에서는 글자를 읽지 못했습니다.',
  url_unreadable: '그 주소에서 내용을 읽지 못했습니다.',
  fetch_unavailable: '이 인스턴스에는 문서 가져오기가 연결되어 있지 않습니다.',
  file_or_url: '파일이나 주소 중 하나만 정하세요.',
  insufficient_credits: '남은 크레딧이 부족합니다.',
  extract_failed: '모델이 응답하지 않았습니다. 잠시 후 다시 시도하세요.',
}

function ExtractForm({
  onDraft,
  onCancel,
}: {
  onDraft: (draft: Partial<DesignRow>, source: string) => void
  onCancel: () => void
}) {
  const t = useT()
  const [url, setUrl] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const fileInput = useRef<HTMLInputElement>(null)

  const run = async (payload: { fileId?: string; url?: string }) => {
    setBusy(true)
    setError(null)
    try {
      const draft = await designsApi.extract(payload)
      onDraft(
        {
          name: draft.name,
          description: draft.description,
          tokens: draft.tokens,
          body: draft.body,
          imageStyle: draft.imageStyle,
          craft: draft.craft,
        },
        draft.source,
      )
    } catch (e) {
      const said = errorMessage(e, t('문서에서 디자인 시스템을 읽어내지 못했습니다.'))
      setError(REFUSAL[said] ? t(REFUSAL[said]) : said)
    } finally {
      setBusy(false)
    }
  }

  return (
    <section aria-label={t('문서에서 가져오기')} className="space-y-3">
      <p className="text-sm text-muted">
        {t('공문 양식이나 지난 보고서를 올리면 색·서체·문체를 읽어 초안을 만듭니다. 저장은 확인한 뒤에 합니다.')}
      </p>

      <Field label={t('문서 올리기')} hint={t('hwpx · docx · pdf · 텍스트')}>
        <input
          ref={fileInput}
          type="file"
          aria-label={t('문서 올리기')}
          disabled={busy}
          onChange={async (e) => {
            const file = e.target.files?.[0]
            if (!file) return
            setBusy(true)
            setError(null)
            try {
              const row = await filesApi.upload(file)
              await run({ fileId: row.id })
            } catch (err) {
              setError(errorMessage(err, t('파일을 올리지 못했습니다.')))
              setBusy(false)
            }
            if (fileInput.current) fileInput.current.value = ''
          }}
          className="block w-full text-sm file:mr-3 file:rounded-control file:border file:border-line file:bg-panel file:px-2.5 file:py-1 file:text-sm"
        />
      </Field>

      <Field label={t('주소에서 읽기')} hint={t('이 인스턴스에 문서 가져오기가 연결되어 있어야 합니다.')}>
        <span className="flex gap-2">
          <Input
            value={url}
            onChange={(e) => setUrl(e.target.value)}
            placeholder="https://"
            disabled={busy}
          />
          <Button onClick={() => void run({ url })} disabled={busy || !url.trim()}>
            {t('읽기')}
          </Button>
        </span>
      </Field>

      {busy && <p className="text-sm text-faint">{t('읽는 중입니다…')}</p>}
      {error && <p className="text-base text-danger">{error}</p>}

      <Button onClick={onCancel} disabled={busy}>
        {t('취소')}
      </Button>
    </section>
  )
}

/**
 * The looks this account can put on a project.
 *
 * One screen for both halves of a design system: the four tokens the exporters
 * draw with, and the short block of prose the model is given. They are edited
 * together because they are one decision — and kept apart in storage because
 * only one of them costs anything per turn.
 */
export function DesignsSection() {
  const t = useT()
  const isAdmin = useStore((s) => s.user?.role === 'admin')
  const loadWorkspace = useStore((s) => s.loadWorkspace)
  const projects = useStore((s) => s.projects)
  const [rows, setRows] = useState<DesignRow[]>([])
  const deleteMany = useStore((s) => s.deleteMany)
  const pick = useBulkSelect(rows.filter((r) => r.mine))
  const [draft, setDraft] = useState<Partial<DesignRow> | null>(null)
  const [extracting, setExtracting] = useState(false)
  //: What the draft was read out of, shown above the form so a person editing
  //: it knows which fields somebody else's document is responsible for.
  const [readFrom, setReadFrom] = useState('')
  const [saving, setSaving] = useState(false)
  //: Deleting a look also strips it off every project wearing it. Nothing on
  //: the row says so and nothing puts it back, so it is asked first.
  const [confirming, setConfirming] = useState<DesignRow | null>(null)
  const [error, setError] = useState<string | null>(null)

  const load = async () => setRows(await designsApi.list().catch(() => []))
  useEffect(() => {
    void load()
    // The projects are what deleting a look actually costs, and this screen is
    // reachable without ever having opened the one that fetches them.
    void loadWorkspace()
  }, [loadWorkspace])

  const tokens = (draft?.tokens ?? blank().tokens) as DesignTokens
  const setTokens = (patch: Partial<DesignTokens>) =>
    setDraft((d) => ({ ...d, tokens: { ...tokens, ...patch } }))

  const save = async () => {
    if (!draft?.name?.trim()) return
    setSaving(true)
    setError(null)
    try {
      const payload = { ...draft, name: draft.name.trim() }
      const row = draft.id
        ? await designsApi.update(draft.id, payload)
        : await designsApi.create(payload as Partial<DesignRow> & { name: string })
      setRows((r) => (draft.id ? r.map((x) => (x.id === row.id ? row : x)) : [...r, row]))
      setDraft(null)
      setReadFrom('')
      // Projects read this list from the store, and an edit made here should
      // show up on the project screen without a reload.
      void loadWorkspace()
    } catch (e) {
      setError(errorMessage(e, t('저장하지 못했습니다.')))
    } finally {
      setSaving(false)
    }
  }

  /**
   * The line under the question. A look is easy to say yes to losing; the
   * projects that were wearing it are the part you cannot see from this list,
   * so they are counted before the answer is given.
   */
  const wearing = (row: DesignRow | null) => {
    const n = row ? projects.filter((p) => p.designSystemId === row.id).length : 0
    return n === 0
      ? t('되돌릴 수 없습니다. 이 디자인을 쓰는 프로젝트는 없습니다.')
      : t('되돌릴 수 없습니다. 이 디자인을 쓰던 프로젝트 {n}개가 기본 모양으로 돌아갑니다.').replace(
          '{n}',
          String(n),
        )
  }

  const remove = async (row: DesignRow) => {
    setRows((r) => r.filter((x) => x.id !== row.id))
    try {
      await designsApi.remove(row.id)
      void loadWorkspace()
    } catch {
      await load()
    }
  }

  if (extracting) {
    return (
      <ExtractForm
        onCancel={() => setExtracting(false)}
        onDraft={(made, source) => {
          setDraft(made)
          setReadFrom(source)
          setExtracting(false)
        }}
      />
    )
  }

  if (draft) {
    return (
      <section aria-label={t('디자인 시스템')} className="space-y-4">
        {readFrom && (
          <p className="rounded-control border border-line bg-elevated px-2.5 py-1.5 text-sm text-muted">
            {t('“{name}” 에서 읽었습니다. 확인하고 고친 뒤 저장하세요.').replace(
              '{name}',
              readFrom,
            )}
          </p>
        )}
        <div className="grid gap-3 sm:grid-cols-2">
          <Field label={t('이름')}>
            <Input
              value={draft.name ?? ''}
              onChange={(e) => setDraft({ ...draft, name: e.target.value })}
              placeholder={t('예: 학과 공식 문서')}
            />
          </Field>
          <Field label={t('한 줄 설명')}>
            <Input
              value={draft.description ?? ''}
              onChange={(e) => setDraft({ ...draft, description: e.target.value })}
            />
          </Field>
        </div>

        <div className="grid gap-3 sm:grid-cols-3">
          <Swatch label="강조색" value={tokens.accent} onChange={(v) => setTokens({ accent: v })} />
          <Swatch label="본문색" value={tokens.ink} onChange={(v) => setTokens({ ink: v })} />
          <Swatch label="보조색" value={tokens.muted} onChange={(v) => setTokens({ muted: v })} />
        </div>

        <Field
          label={t('서체')}
          hint={t('내보내는 파일에 실제로 쓰이는 두 가지입니다.')}
        >
          <Select
            value={tokens.font}
            onChange={(e) => setTokens({ font: e.target.value as DesignTokens['font'] })}
          >
            {FONTS.map((f) => (
              <option key={f.key} value={f.key}>
                {t(f.label)}
              </option>
            ))}
          </Select>
        </Field>


        <Field
          label={`${t('문체 규율')} — ${(draft.body ?? '').length}/${BODY_MAX}`}
          hint={t('이 프로젝트의 모든 턴에 함께 전달됩니다. 길어질 내용은 프로젝트 지침에 적으세요.')}
        >
          <Textarea
            rows={3}
            maxLength={BODY_MAX}
            value={draft.body ?? ''}
            onChange={(e) => setDraft({ ...draft, body: e.target.value })}
            placeholder={t('예: 제목은 명사구로 쓴다. 한 문장에 한 사실만 담는다.')}
          />
        </Field>

        <Field
          label={t('이미지 스타일')}
          hint={t('이미지 프롬프트 뒤에 그대로 붙습니다. 영어로 적는 편이 잘 통합니다.')}
        >
          <Input
            value={draft.imageStyle ?? ''}
            onChange={(e) => setDraft({ ...draft, imageStyle: e.target.value })}
            placeholder="muted documentary photography, natural light"
          />
        </Field>

        <fieldset className="space-y-2">
          <legend className="text-base font-medium text-fg">{t('함께 적용할 규칙')}</legend>
          {CRAFT.map((rule) => {
            const on = (draft.craft ?? []).includes(rule.key)
            return (
              <label key={rule.key} className="flex items-start gap-2">
                <input
                  type="checkbox"
                  checked={on}
                  onChange={() =>
                    setDraft({
                      ...draft,
                      craft: on
                        ? (draft.craft ?? []).filter((k) => k !== rule.key)
                        : [...(draft.craft ?? []), rule.key],
                    })
                  }
                  className="mt-1 size-4 shrink-0 accent-accent"
                />
                <span>
                  <span className="block text-base">{t(rule.label)}</span>
                  <span className="block text-sm text-faint">{t(rule.hint)}</span>
                </span>
              </label>
            )
          })}
        </fieldset>

        {isAdmin && (
          <div className="flex items-center gap-2">
            <Switch
              checked={Boolean(draft.shared)}
              onChange={(v) => setDraft({ ...draft, shared: v })}
              label={t('모든 사용자에게 제공')}
            />
            <span className="text-base">{t('모든 사용자에게 제공')}</span>
          </div>
        )}

        {error && <p className="text-base text-danger">{error}</p>}

        <div className="flex gap-2">
          <Button variant="primary" onClick={() => void save()} disabled={saving}>
            {t('저장')}
          </Button>
          <Button onClick={() => setDraft(null)}>{t('취소')}</Button>
        </div>
      </section>
    )
  }

  return (
    <section aria-label={t('디자인 시스템')} className="space-y-3">
      <BulkBar
        count={pick.count}
        allPicked={pick.allPicked}
        onToggleAll={pick.toggleAll}
        onClear={pick.clear}
        title={t('디자인')}
        note={t('이 디자인을 쓰던 프로젝트는 기본 모양으로 돌아갑니다.')}
        onDelete={async () => {
          await deleteMany('designs', pick.ids)
          setRows((r) => r.filter((x) => !pick.ids.includes(x.id)))
          pick.clear()
        }}
      />
      <ul className="space-y-1">
        {rows.map((row) => (
          <li
            key={row.id}
            className="flex items-center gap-2 rounded-control border border-line bg-panel px-2.5 py-1.5 text-base"
          >
            {/* Mine only: a shared design belongs to whoever made it. */}
            {row.mine ? (
              <PickBox
                checked={pick.picked.has(row.id)}
                onChange={() => pick.toggle(row.id)}
                label={t('{name} 선택').replace('{name}', row.name)}
              />
            ) : (
              <span className="size-4 shrink-0" />
            )}
            <span
              aria-hidden
              className="size-4 shrink-0 rounded-full border border-line"
              style={{ background: row.tokens.accent }}
            />
            <span className="min-w-0 flex-1 truncate">{row.name}</span>
            {row.shared && <Badge>{t('공용')}</Badge>}
            {row.mine ? (
              <>
                <Button size="sm" onClick={() => setDraft({ ...row })}>
                  {t('편집')}
                </Button>
                <Button
                  variant="ghost"
                  size="icon"
                  aria-label={t('{name} 삭제').replace('{name}', row.name)}
                  title={t('이 디자인을 삭제합니다')}
                  onClick={() => setConfirming(row)}
                >
                  <Trash2 size={14} />
                </Button>
              </>
            ) : (
              <span className="shrink-0 text-sm text-faint">{t('읽기 전용')}</span>
            )}
          </li>
        ))}
      </ul>
      {rows.length === 0 && (
        <p className="flex items-center gap-1.5 text-base text-faint">
          <Palette size={13} />
          {t('아직 디자인이 없습니다. 하나 만들면 프로젝트에서 고를 수 있습니다.')}
        </p>
      )}
      <div className="flex flex-wrap gap-2">
        <Button
          onClick={() => {
            setReadFrom('')
            setDraft(blank())
          }}
        >
          <Plus size={14} />
          {t('디자인 추가')}
        </Button>
        <Button onClick={() => setExtracting(true)}>
          <FileUp size={14} />
          {t('문서에서 가져오기')}
        </Button>
      </div>

      <ConfirmDialog
        open={!!confirming}
        onClose={() => setConfirming(null)}
        onConfirm={() => confirming && void remove(confirming)}
        title={t('{name} 삭제').replace('{name}', confirming?.name ?? '')}
        description={wearing(confirming)}
      />
    </section>
  )
}
