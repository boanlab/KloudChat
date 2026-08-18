import { Palette, Plus, Trash2 } from 'lucide-react'
import { useEffect, useState } from 'react'
import { Badge, Button, Field, Input, Switch, Textarea } from '@/components/ui'
import { designsApi, errorMessage, type DesignRow, type DesignTokens } from '@/lib/api'
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
  const [rows, setRows] = useState<DesignRow[]>([])
  const [draft, setDraft] = useState<Partial<DesignRow> | null>(null)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const load = async () => setRows(await designsApi.list().catch(() => []))
  useEffect(() => {
    void load()
  }, [])

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
      // Projects read this list from the store, and an edit made here should
      // show up on the project screen without a reload.
      void loadWorkspace()
    } catch (e) {
      setError(errorMessage(e, t('저장하지 못했습니다.')))
    } finally {
      setSaving(false)
    }
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

  if (draft) {
    return (
      <section aria-label={t('디자인 시스템')} className="space-y-4">
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
          <select
            value={tokens.font}
            onChange={(e) => setTokens({ font: e.target.value as DesignTokens['font'] })}
            className="h-9 w-full rounded-control border border-line bg-panel px-3 text-base focus:border-accent focus:outline-none"
          >
            {FONTS.map((f) => (
              <option key={f.key} value={f.key}>
                {t(f.label)}
              </option>
            ))}
          </select>
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
      <ul className="space-y-1">
        {rows.map((row) => (
          <li
            key={row.id}
            className="flex items-center gap-2 rounded-control border border-line bg-panel px-2.5 py-1.5 text-base"
          >
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
                <button
                  type="button"
                  aria-label={t('{name} 삭제').replace('{name}', row.name)}
                  onClick={() => void remove(row)}
                  className="shrink-0 text-faint hover:text-danger"
                >
                  <Trash2 size={13} />
                </button>
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
      <Button onClick={() => setDraft(blank())}>
        <Plus size={14} />
        {t('디자인 추가')}
      </Button>
    </section>
  )
}
