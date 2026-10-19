import { ChevronRight, Info, Layers } from 'lucide-react'
import { useEffect, useMemo, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { Button, Field, Input, Modal, ResourceStatus, Select } from '@/components/ui'
import { StepList } from '@/components/agents/PipelineEditor'
import { bundlesApi, errorMessage, userPipelinesApi } from '@/lib/api'
import { bareSteps, useProcessCatalogue, useProcessCatalogueState, useStepVerdict, type ProcessDraft } from '@/lib/process'
import { useT } from '@/lib/useT'
import { cn } from '@/lib/utils'
import { useStore } from '@/store/useStore'
import type {
  Agent,
  Bundle,
  BundleItem,
  BundleSelection,
  GapQuestion,
  PipelineProblem,
  PipelineStep,
  ProcessMode,
  SessionKind,
  UserPipeline,
} from '@/types'

const FORMAT_LABEL: Record<string, string> = { docx: 'DOCX', pdf: 'PDF', hwpx: 'HWPX', md: 'MD' }

/** Where a bundle item runs on this surface; null: hidden there. */
const targetOn = (item: BundleItem, surface: SessionKind) =>
  surface === 'report' || surface === 'chat' ? item.targets[surface] : null

/** Format of an export ticked without one: server's `by_format` for the agent's format (공문 → HWPX), else first. */
function exportDefault(item: BundleItem, docFormat: string | null | undefined): string {
  return (docFormat && item.settings.by_format?.[docFormat]) || item.settings.formats?.[0] || 'docx'
}

/** Format a tick sets: the one the agent's words name (「워드」 「HWPX」 「PDF」), else the server default. */
function exportTick(item: BundleItem, docFormat: string | null | undefined, asked: string | null | undefined): string {
  return asked || exportDefault(item, docFormat)
}

/** Tick: the person's, else the bundle default; an export's tick is its format. */
function tickOf(item: BundleItem, sel: BundleSelection | undefined, docFormat?: string | null): boolean | string {
  const raw = sel && item.id in sel.items ? sel.items[item.id] : item.default
  if (item.kind === 'export') {
    if (raw === true) return exportDefault(item, docFormat)
    return typeof raw === 'string' ? raw : ''
  }
  return !!raw
}

/** Words asking for a file back (「워드 파일로」 「PDF로 내보내」), not about given files; 바로 답하기 makes none. */
const WANTS_FILE = /docx|hwpx|\bpdf\b|워드|한글\s{0,2}(?:문서|파일)|파일\s{0,2}(?:로|형식)|내보내|\bword\b/i

/** Format named outright, else null. */
function namedFormat(text: string, formats: string[]): string | null {
  const want = /hwpx|한글\s{0,2}(?:문서|파일)/i.test(text)
    ? 'hwpx'
    : /\bpdf\b/i.test(text)
      ? 'pdf'
      : /docx|워드|\bword\b/i.test(text)
        ? 'docx'
        : null
  return want && formats.includes(want) ? want : null
}

/** Named format: HWPX or PDF when said, else DOCX. */
function askedFormat(text: string, formats: string[]): string {
  const want = namedFormat(text, formats) ?? 'docx'
  return formats.includes(want) ? want : (formats[0] ?? 'docx')
}

/** Base name with its output (「자료 추출·집계 (표)」); brackets only when they add words. */
function baseLabelOf(name: string, made: string | undefined): { name: string; made: string | null } {
  const flat = (s: string) => s.replace(/\s+/g, '')
  if (!made) return { name, made: null }
  const a = flat(name)
  const b = flat(made)
  return a.includes(b) || b.includes(a) ? { name, made: null } : { name, made }
}

/** One 묶음: checkbox, items behind 「자세히」. */
function BundleRow({
  bundle,
  surface,
  selection,
  formats,
  docFormat,
  asked,
  onChange,
  onDocumentBase,
}: {
  bundle: Bundle
  surface: SessionKind
  selection: BundleSelection | undefined
  formats: string[]
  /** Agent's document format: what a format-less export makes. */
  docFormat?: string | null
  /** Format named in the agent's name, description or 지시: a tick's first pick. */
  asked?: string | null
  onChange: (next: BundleSelection | undefined) => void
  /** Switch to a document-writing base, where a file export runs. */
  onDocumentBase?: () => void
}) {
  const t = useT()
  const [open, setOpen] = useState(false)
  const items = bundle.items.filter((i) => targetOn(i, surface) !== null && targetOn(i, surface) !== 'self')
  // Items unrunnable here (a file export on an answering agent).
  const cannot = bundle.items.filter((i) => targetOn(i, surface) === null)
  if (items.length === 0) return null
  const on = !!selection
  const set = (item: BundleItem, value: boolean | string) =>
    onChange({ bundle: bundle.ref, items: { ...(selection?.items ?? {}), [item.id]: value } })
  const summary = items
    .filter((i) => i.kind !== 'export' && (targetOn(i, surface) === 'flow' || (on && tickOf(i, selection))))
    .map((i) => t(i.label))
    .join(' · ')
  // Note names only what ticking does here; a held file export always named with where it runs.
  const exportElsewhere = cannot.some((i) => i.kind === 'export')
  const listed = summary || (cannot.length > 0 ? items.map((i) => t(i.label)).join(' · ') : t(bundle.description))
  const note = exportElsewhere ? `${listed} · ${t('파일로 내보내기(문서 과정에서)')}` : listed
  const exportItem = items.find((i) => i.kind === 'export')
  return (
    <div className="rounded-control border border-line" data-testid="bundle-row">
      <div className="flex items-start gap-3 px-3 py-2.5">
        <input
          type="checkbox"
          id={`bundle-${bundle.ref}`}
          aria-describedby={`bundle-${bundle.ref}-note`}
          checked={on}
          onChange={() => onChange(on ? undefined : { bundle: bundle.ref, items: {} })}
          className="mt-1 size-4 shrink-0 accent-[var(--accent)]"
        />
        <div className="min-w-0 flex-1">
          <label htmlFor={`bundle-${bundle.ref}`} className="block cursor-pointer text-base font-medium text-fg">
            {t(bundle.name)}
          </label>
          <p id={`bundle-${bundle.ref}-note`} className="text-sm text-muted">
            {note}
            {on && cannot.some((i) => i.kind === 'export') && (
              <span className="block text-warn" data-testid="bundle-cannot">
                {t('파일 내보내기는 문서를 쓰는 과정에서만 됩니다 — 진행 과정에서 기본 과정(보고서)을 고르세요.')}
                {onDocumentBase && (
                  <button type="button" className="ml-1 text-accent underline-offset-2 hover:underline" onClick={onDocumentBase}>
                    {t('기본 과정(보고서)으로 바꾸기')}
                  </button>
                )}
              </span>
            )}
            {on && cannot.some((i) => i.kind !== 'export') && (
              <span className="block text-warn">
                {t('이 과정에서는 하지 않는 일')}: {cannot.filter((i) => i.kind !== 'export').map((i) => t(i.label)).join(' · ')}
              </span>
            )}
            {bundle.risky && bundle.review !== 'approved' && (
              <span className="block text-warn">{t('공유하면 관리자가 승인한 뒤 다른 사람에게도 적용됩니다.')}</span>
            )}
          </p>
        </div>
        <button
          type="button"
          aria-expanded={open}
          onClick={() => setOpen(!open)}
          className="flex shrink-0 items-center gap-0.5 text-sm text-muted hover:text-fg"
        >
          <ChevronRight size={13} aria-hidden className={cn('transition-transform', open && 'rotate-90')} />
          {t('자세히')}
        </button>
      </div>
      {on && exportItem && (
        <div className="flex flex-wrap items-center gap-2 border-t border-line px-3 py-2 pl-10" data-testid="bundle-export">
          <input
            type="checkbox"
            id={`bundle-${bundle.ref}-${exportItem.id}-shown`}
            checked={!!tickOf(exportItem, selection, docFormat)}
            onChange={() => set(exportItem, tickOf(exportItem, selection, docFormat) ? '' : exportTick(exportItem, docFormat, asked))}
            className="size-4 accent-[var(--accent)]"
          />
          <label htmlFor={`bundle-${bundle.ref}-${exportItem.id}-shown`} className="text-base">
            {t(exportItem.label)}
          </label>
          {!!tickOf(exportItem, selection, docFormat) && (
            <Select
              aria-label={t('파일 형식')}
              value={String(tickOf(exportItem, selection, docFormat))}
              onChange={(e) => set(exportItem, e.target.value)}
              className="h-8 w-auto py-0"
            >
              {formats.map((f) => (
                <option key={f} value={f}>
                  {FORMAT_LABEL[f] ?? f}
                </option>
              ))}
            </Select>
          )}
        </div>
      )}
      {open && (
        <ul className="space-y-1.5 border-t border-line px-3 py-2.5 pl-10" aria-label={t(bundle.name)}>
          {items.filter((item) => item.kind !== 'export').map((item) => {
            const always = targetOn(item, surface) === 'flow'
            const value = tickOf(item, selection, docFormat)
            const id = `bundle-${bundle.ref}-${item.id}`
            return (
              <li key={item.id} className="flex flex-wrap items-center gap-2" data-testid="bundle-item">
                <input
                  type="checkbox"
                  id={id}
                  checked={always || (on && !!value)}
                  disabled={always || !on}
                  onChange={() => set(item, item.kind === 'export' ? (value ? '' : exportTick(item, docFormat, asked)) : !value)}
                  className="size-4 accent-[var(--accent)]"
                />
                <label htmlFor={id} className={cn('text-base', (!on || always) && 'text-muted')}>
                  {t(item.label)}
                </label>
                {always && <span className="text-sm text-faint">{t('이 과정은 늘 합니다')}</span>}
                {item.kind === 'export' && on && !!value && (
                  <Select
                    aria-label={t('파일 형식')}
                    value={String(value)}
                    onChange={(e) => set(item, e.target.value)}
                    className="h-8 w-auto py-0"
                  >
                    {formats.map((f) => (
                      <option key={f} value={f}>
                        {FORMAT_LABEL[f] ?? f}
                      </option>
                    ))}
                  </Select>
                )}
              </li>
            )
          })}
        </ul>
      )}
    </div>
  )
}

/** 「이 순서를 묶음으로 저장」: a 직접 구성 kept as a 묶음 every agent form can tick. */
function SaveAsBundle({ steps, onSaved }: { steps: PipelineStep[]; onSaved: () => void }) {
  const t = useT()
  const [open, setOpen] = useState(false)
  const [name, setName] = useState('')
  const [error, setError] = useState<string | null>(null)
  const save = async () => {
    try {
      await bundlesApi.create({ name: name.trim(), stages: steps })
      setOpen(false)
      onSaved()
    } catch (err) {
      setError(errorMessage(err, t('묶음으로 저장하지 못했습니다.')))
    }
  }
  return (
    <>
      <Button type="button" size="sm" disabled={steps.length === 0} onClick={() => setOpen(true)}>
        <Layers size={13} />
        {t('이 순서를 묶음으로 저장')}
      </Button>
      <Modal
        open={open}
        onClose={() => setOpen(false)}
        title={t('묶음으로 저장')}
        footer={
          <>
            <Button type="button" onClick={() => setOpen(false)}>{t('취소')}</Button>
            <Button type="button" variant="primary" disabled={!name.trim()} onClick={() => void save()}>
              {t('저장')}
            </Button>
          </>
        }
      >
        <Field label={t('묶음 이름')} hint={t('저장하면 내 모든 에이전트의 「추가로 할 일」에 나옵니다.')}>
          <Input
            value={name}
            maxLength={80}
            onChange={(e) => setName(e.target.value)}
            // Inside the agent form: Enter saves the bundle, not the agent.
            onKeyDown={(e) => {
              if (e.key !== 'Enter') return
              e.preventDefault()
              if (name.trim()) void save()
            }}
            placeholder={t('예: 두 번 검토하고 PDF로')}
          />
        </Field>
        {error && <p role="alert" className="mt-2 text-sm text-danger">{error}</p>}
      </Modal>
    </>
  )
}

/** 「진행 과정」 and 「추가로 할 일」; 워크플로·작동 단계 never shown. */
export function ProcessSection({
  agent,
  value,
  onChange,
  surface,
  onSurface,
  questions,
  onAskQuestions,
}: {
  agent: Agent
  value: ProcessDraft
  onChange: (next: ProcessDraft) => void
  /** What a new conversation opens on, when no base fixes it. */
  surface: SessionKind
  /** 직접 구성: the agent step's 사용할 화면; returns an undo for the one-click fix. */
  onSurface?: (next: SessionKind) => (() => void) | void
  /** Current 빠진 정보 질문; a 확인 질문 step is judged with them. */
  questions?: GapQuestion[]
  /** Opens 빠진 정보 질문 with a new question. */
  onAskQuestions?: () => void
}) {
  const t = useT()
  const setToast = useStore((s) => s.setToast)
  const [reloaded, setReloaded] = useState(0)
  const catState = useProcessCatalogueState(reloaded)
  const cat = catState.data
  const [imports, setImports] = useState<UserPipeline[] | null>(null)
  const fixed = !!agent.catalogKey
  const base = cat?.bases.find((b) => b.key === value.base) ?? null
  const on: SessionKind = base?.surface ?? surface
  const customOffered = (base ? base.custom : on === 'report' || on === 'chat')
  const selfName = agent.name.trim() || t('이 에이전트')
  const verdict = useStepVerdict(value.steps, agent.id || null, value.mode === 'custom', { surface: on, name: selfName }, questions)
  const surfaceRef = useRef<HTMLSelectElement>(null)
  // First document-writing base: target for a 바로 답하기 agent needing a file.
  const documentBase = cat?.bases.find((b) => b.surface === 'report' && !b.draft) ?? null
  // Shown once the catalogue has one (or failed, with its retry); an agent on one keeps it.
  const offersBase = (cat?.bases.length ?? 0) > 0 || catState.status === 'error' || value.mode === 'base'
  const wantsFile = WANTS_FILE.test(`${agent.name}\n${agent.description}\n${agent.systemPrompt}`)
  const baseLabel = (name: string, made: string | undefined) => {
    const label = baseLabelOf(name, made)
    return label.made ? `${t(label.name)} (${t(label.made)})` : t(label.name)
  }
  const bundles = useMemo(() => cat?.bundles ?? [], [cat])

  useEffect(() => {
    if (value.mode !== 'custom' || imports !== null) return
    void userPipelinesApi.list().then(setImports).catch(() => setImports([]))
  }, [value.mode, imports])

  const pickBase = (key: string, exportAs?: string) => {
    const next = cat?.bases.find((b) => b.key === key)
    let bundles = next ? next.bundles.map((b) => ({ ...b, items: { ...b.items } })) : value.bundles
    if (exportAs) {
      // Switched for a file: its export ticked too.
      const finish = (cat?.bundles ?? []).find((b) => b.items.some((i) => i.kind === 'export'))
      const item = finish?.items.find((i) => i.kind === 'export')
      if (finish && item) {
        const held = bundles.find((b) => b.bundle === finish.ref)
        bundles = held
          ? bundles.map((b) => (b === held ? { ...b, items: { ...b.items, [item.id]: exportAs } } : b))
          : [...bundles, { bundle: finish.ref, items: { [item.id]: exportAs } }]
      }
    }
    onChange({ ...value, mode: 'base', base: key || null, bundles })
  }
  const setMode = (mode: ProcessMode) => {
    if (mode === 'base') {
      // Kept base, else the catalogue's first.
      const key = value.base ?? cat?.bases[0]?.key ?? null
      if (key) return pickBase(key)
    }
    if (mode === 'custom' && value.steps.length === 0) {
      const steps: PipelineStep[] = [
        ...(on === 'report' ? [{ id: 's1', kind: 'human' as const, ref: 'outline', settings: {} }] : []),
        { id: on === 'report' ? 's2' : 's1', kind: 'agent', ref: '@self', settings: {} },
      ]
      return onChange({ ...value, mode, steps })
    }
    onChange({ ...value, mode, base: mode === 'direct' ? null : value.base })
  }
  const ownWords = `${agent.name}\n${agent.description}\n${agent.systemPrompt}`
  const exportAs = askedFormat(ownWords, cat?.exportFormats ?? ['docx', 'pdf', 'hwpx', 'md'])
  // Manual export tick picks the named format too, as the switch does.
  const namedAs = namedFormat(ownWords, cat?.exportFormats ?? ['docx', 'pdf', 'hwpx', 'md'])
  // Every path here wants a file: export comes ticked.
  const toDocumentBase = documentBase && !fixed ? () => pickBase(documentBase.key, exportAs) : undefined
  /** Under a step's problem: its one-click fix. */
  const actionFor = (p: PipelineProblem, step: PipelineStep | null) => {
    const exportProblem =
      p.code === 'export_needs_document' || (step?.kind === 'export' && p.code === 'needs_product')
    if (exportProblem && onSurface && !base) {
      return (
        <button
          type="button"
          className="ml-7 text-sm text-accent hover:underline"
          data-testid="export-fix"
          onClick={() => {
            // Sets it, not just points; the notice undoes it.
            const before = on
            const undo = onSurface('report')
            setToast({
              text: t('「이 에이전트가 만드는 것」을 보고서(문서)로 바꿨습니다.'),
              action: { label: t('실행 취소'), run: () => (undo ? undo() : onSurface(before)) },
            })
            surfaceRef.current?.scrollIntoView({ block: 'center', behavior: 'smooth' })
            surfaceRef.current?.focus()
          }}
        >
          {t('「이 에이전트가 만드는 것」에서 보고서(문서)를 고르세요')}
        </button>
      )
    }
    if (p.code === 'nothing_to_ask' && onAskQuestions) {
      return (
        <button type="button" className="ml-7 text-sm text-accent hover:underline" data-testid="ask-fix" onClick={onAskQuestions}>
          {t('빠진 정보 질문에서 추가')}
        </button>
      )
    }
    return null
  }
  const setBundle = (ref: string, next: BundleSelection | undefined) => {
    const rest = value.bundles.filter((b) => b.bundle !== ref)
    if (!next) return onChange({ ...value, bundles: rest })
    const at = value.bundles.findIndex((b) => b.bundle === ref)
    const out = [...rest]
    out.splice(at < 0 ? out.length : at, 0, next)
    onChange({ ...value, bundles: out })
  }

  const radio = (mode: ProcessMode, label: string, note: string, disabled = false) => (
    <label
      className={cn(
        'flex cursor-pointer items-start gap-3 rounded-control border px-3 py-2.5',
        value.mode === mode ? 'border-accent bg-accent-soft/40' : 'border-line',
        disabled && 'cursor-not-allowed opacity-60',
      )}
    >
      <input
        type="radio"
        name={`process-${agent.id}`}
        checked={value.mode === mode}
        disabled={disabled}
        onChange={() => setMode(mode)}
        className="mt-1 size-4 accent-[var(--accent)]"
      />
      <span className="min-w-0 flex-1">
        <span className="block text-base font-medium">{t(label)}</span>
        <span className="block text-sm text-muted">{t(note)}</span>
      </span>
    </label>
  )

  return (
    <div className="space-y-5" data-testid="agent-process">
      <fieldset className="space-y-2">
        <legend className="mb-1.5 text-base font-medium text-fg">{t('진행 과정')}</legend>
        {radio(
          'direct',
          '바로 답하기',
          on !== 'chat'
            ? '요청을 받아 곧바로 답합니다.'
            : documentBase
              ? '요청을 받아 대화로 답합니다 (파일로 받으려면 기본 과정을 고르세요).'
              : '요청을 받아 대화로 답합니다 (파일로 받으려면 고급 › 처음 여는 화면을 보고서로 하고 마무리하기의 파일로 내보내기를 켜세요).',
          fixed,
        )}
        {value.mode === 'direct' && on === 'chat' && wantsFile && toDocumentBase && documentBase && (
          <div
            className="ml-7 flex flex-wrap items-center gap-2 rounded-control border border-warn/40 bg-warn/5 px-3 py-2 text-sm text-fg"
            data-testid="file-suggestion"
          >
            <Info size={14} className="shrink-0 text-warn" aria-hidden />
            <span className="min-w-0 flex-1 basis-56">
              {t('설명이나 지시에 파일 이야기가 있습니다. 바로 답하기는 대화로만 답하고 파일을 만들지 않습니다. DOCX·HWPX·PDF로 받으려면 문서를 쓰는 기본 과정으로 바꾸세요.')}
            </span>
            <Button type="button" size="sm" onClick={toDocumentBase}>
              {t('「{name}」 과정으로 바꾸기').replace('{name}', baseLabel(documentBase.name, documentBase.templateName))}
            </Button>
          </div>
        )}
        {offersBase && radio(
          'base',
          '기본 과정에서 시작',
          cat
            ? '기본 에이전트가 일하는 순서를 그대로 따릅니다.'
            : catState.status === 'error'
              ? '기본 과정을 불러오지 못했습니다.'
              : catState.slow
                ? '기본 과정을 불러오는 중… 평소보다 오래 걸립니다.'
                : '기본 과정을 불러오는 중…',
          !cat,
        )}
        {!cat && catState.status === 'error' && (
          <div className="ml-7">
            <ResourceStatus resource={catState} compact failed={t('기본 과정을 불러오지 못했습니다.')} />
          </div>
        )}
        {value.mode === 'base' && (
          <div className="ml-7 space-y-2" data-testid="process-base">
            <Select
              aria-label={t('기본 과정')}
              value={value.base ?? ''}
              disabled={fixed || !cat}
              onChange={(e) => pickBase(e.target.value)}
            >
              {(cat?.bases ?? []).map((b) => (
                <option key={b.key} value={b.key}>
                  {baseLabel(b.name, b.templateName)}
                  {b.draft ? ` · ${t('평가 중')}` : ''}
                </option>
              ))}
            </Select>
            {base && (
              <ol className="space-y-0.5 text-sm text-muted" aria-label={t('이렇게 일합니다')}>
                {base.steps.map((line, i) => (
                  <li key={line} className="flex gap-2">
                    <span className="w-4 shrink-0 text-right tabular-nums text-faint">{i + 1}</span>
                    <span>{t(line)}</span>
                  </li>
                ))}
              </ol>
            )}
          </div>
        )}
        {customOffered && radio('custom', '직접 구성 (고급)', '단계를 하나씩 골라 순서를 짭니다.')}
        {value.mode === 'custom' && (
          <div className="ml-7 space-y-3" data-testid="process-custom">
            {onSurface && !base && (
              <Field label={t('이 에이전트가 만드는 것')} hint={t('문서를 만들어야 내보내기 단계를 쓸 수 있습니다.')}>
                <Select ref={surfaceRef} value={on} onChange={(e) => onSurface(e.target.value as SessionKind)} className="w-auto">
                  <option value="report">{t('보고서(문서)')}</option>
                  <option value="chat">{t('대화(답)')}</option>
                </Select>
              </Field>
            )}
            <StepList
              steps={value.steps}
              onChange={(steps) => onChange({ ...value, steps })}
              verdict={verdict}
              selfName={selfName}
              actionFor={actionFor}
            />
            <div className="flex flex-wrap items-center gap-2">
              <SaveAsBundle steps={value.steps} onSaved={() => setReloaded((n) => n + 1)} />
              {(imports ?? []).length > 0 && (
                <Select
                  aria-label={t('저장한 순서 가져오기')}
                  value=""
                  onChange={(e) => {
                    const row = imports?.find((p) => p.id === e.target.value)
                    if (row) onChange({ ...value, steps: bareSteps(row.stages) })
                  }}
                  className="h-8 w-auto py-0"
                >
                  <option value="">{t('저장한 순서 가져오기')}</option>
                  {(imports ?? []).map((p) => (
                    <option key={p.id} value={p.id}>
                      {p.name}
                    </option>
                  ))}
                </Select>
              )}
            </div>
          </div>
        )}
      </fieldset>

      {value.mode !== 'custom' && (on === 'report' || on === 'chat') && (
        <section className="space-y-2" aria-label={t('추가로 할 일')} data-testid="agent-bundles">
          <div className="flex items-center justify-between gap-2">
            <p className="text-base font-medium text-fg">{t('추가로 할 일')}</p>
            <Link to="/customize/bundles" className="text-sm text-accent hover:underline">
              {t('묶음 관리')}
            </Link>
          </div>
          {bundles.map((b) => (
            <BundleRow
              key={b.ref}
              bundle={b}
              surface={on}
              selection={value.bundles.find((s) => s.bundle === b.ref)}
              formats={cat?.exportFormats ?? ['docx', 'pdf', 'hwpx', 'md']}
              docFormat={agent.docFormat ?? base?.docFormat}
              asked={namedAs}
              onChange={(next) => setBundle(b.ref, next)}
              onDocumentBase={toDocumentBase}
            />
          ))}
          {base && base.formatChecks.length > 0 && (
            <p className="flex items-start gap-1.5 text-sm text-faint">
              <Info size={13} className="mt-0.5 shrink-0" aria-hidden />
              {t('형식 검사')}: {base.formatChecks.map((c) => t(c)).join(' · ')}
            </p>
          )}
        </section>
      )}
    </div>
  )
}

/** 「이렇게 일합니다: a → b → c」 and, behind 자세히 보기, the 묶음 it ticks. */
export function ProcessChain({ agent }: { agent: Agent }) {
  const t = useT()
  const cat = useProcessCatalogue()
  const [open, setOpen] = useState(false)
  const p = agent.process
  if (!p || p.chain.length === 0) return null
  const ticked = p.bundles
    .map((s) => cat?.bundles.find((b) => b.ref === s.bundle))
    .filter((b): b is Bundle => !!b)
  return (
    <div className="space-y-1.5" data-testid="process-chain">
      <p className="text-base text-fg">
        <span className="font-medium">{t('이렇게 일합니다')}: </span>
        {p.chain.map((line) => t(line)).join(' → ')}
      </p>
      {(ticked.length > 0 || p.mode === 'custom') && (
        <button type="button" onClick={() => setOpen(!open)} aria-expanded={open} className="text-sm text-muted hover:text-fg">
          {open ? t('접기') : t('자세히 보기')}
        </button>
      )}
      {open && (
        <ul className="space-y-0.5 pl-4 text-sm text-muted">
          {p.mode === 'custom' && <li>{t('직접 구성한 순서로 일합니다.')}</li>}
          {ticked.map((b) => (
            <li key={b.ref}>
              {t(b.name)}
              {b.description ? ` — ${t(b.description)}` : ''}
            </li>
          ))}
          {p.needsReview && <li className="text-warn">{t('관리자가 승인하면 다른 사람에게도 이 순서가 적용됩니다.')}</li>}
        </ul>
      )}
    </div>
  )
}

