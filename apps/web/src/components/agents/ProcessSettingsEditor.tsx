import { Plus, Trash2 } from 'lucide-react'
import type { ReactNode } from 'react'
import { Button, Field, Input, Select, Textarea } from '@/components/ui'
import { useT } from '@/lib/useT'
import type { ProcessCatalogue, ProcessName, ProcessSettings, ProcessStructure } from '@/types'

/** Every key filled: a stored setting (only the keys it sets) over the defaults. */
export function fullSettings(base: ProcessSettings, own?: Partial<ProcessSettings> | null): ProcessSettings {
  return {
    ...base,
    ...(own ?? {}),
    structure: { ...base.structure, ...(own?.structure ?? {}) } as ProcessStructure,
  }
}

const lines = (text: string) => text.split('\n').map((l) => l.trim()).filter(Boolean)

/** A titled block of the editor. */
function Part({ title, hint, children }: { title: string; hint?: string; children: ReactNode }) {
  const t = useT()
  return (
    <fieldset className="space-y-2 rounded-control border border-line px-3 py-2.5">
      <legend className="px-1 text-base font-medium text-fg">{t(title)}</legend>
      {hint && <p className="text-sm text-muted">{t(hint)}</p>}
      {children}
    </fieldset>
  )
}

/** Checkboxes over a closed list of built-in names; each says what it reads first. */
function Names({
  names,
  value,
  onChange,
  max,
  labels,
  testId,
}: {
  names: ProcessName[]
  value: string[]
  onChange: (next: string[]) => void
  max?: number
  labels: Record<string, string>
  testId: string
}) {
  const t = useT()
  return (
    <ul className="grid gap-1.5 sm:grid-cols-2" data-testid={testId}>
      {names.map((n) => {
        const on = value.includes(n.name)
        const full = !on && max !== undefined && value.length >= max
        return (
          <li key={n.name}>
            <label className="flex items-start gap-2 text-base">
              <input
                type="checkbox"
                className="mt-1 size-4 accent-[var(--accent)]"
                checked={on}
                disabled={full}
                onChange={() => onChange(on ? value.filter((x) => x !== n.name) : [...value, n.name])}
              />
              <span className="min-w-0">
                {t(n.label)}
                {n.needs && (
                  <span className="block text-sm text-faint">
                    {t('먼저 필요한 계산')}: {t(labels[n.needs] ?? n.needs)}
                  </span>
                )}
              </span>
            </label>
          </li>
        )
      })}
    </ul>
  )
}

/**
 * 진행 과정 설정: the built-in calculations a document runs before writing, the named 서식 보정
 * stages, the attachable checks, 검토 규칙 a person writes in words, the structure rules checked
 * by code, and the options. The same editor serves an agent (over its template's settings) and
 * a person's template. Every built-in item is labelled as what it is: a calculation the
 * product ships.
 */
export function ProcessSettingsEditor({
  value,
  catalogue,
  onChange,
}: {
  value: ProcessSettings
  catalogue: ProcessCatalogue
  onChange: (next: ProcessSettings) => void
}) {
  const t = useT()
  const set = <K extends keyof ProcessSettings>(key: K, v: ProcessSettings[K]) => onChange({ ...value, [key]: v })
  const setStructure = <K extends keyof ProcessStructure>(key: K, v: ProcessStructure[K]) =>
    onChange({ ...value, structure: { ...value.structure, [key]: v } })
  const prepassLabels = Object.fromEntries(catalogue.prepass.map((n) => [n.name, n.label]))
  const tables = value.structure.tables

  return (
    <div className="space-y-3" data-testid="process-settings">
      <Part title="쓰기 전에 계산" hint="기본 제공 계산입니다. 자료에서 값을 읽어 코드로 계산해 두고, 글은 그 값만 씁니다.">
        <Names
          names={catalogue.prepass}
          value={value.prepass}
          max={catalogue.limits.prepass}
          labels={prepassLabels}
          testId="settings-prepass"
          onChange={(next) => set('prepass', next)}
        />
        <div className="grid gap-3 sm:grid-cols-2">
          <Field label={t('날짜 기준')} hint={t('「다음 주 금요일」 같은 날짜를 무엇에서 셀지')}>
            <Select value={value.date_base} onChange={(e) => set('date_base', e.target.value as ProcessSettings['date_base'])}>
              <option value="text">{t('자료에 적힌 날짜')}</option>
              <option value="meeting">{t('회의한 날(없으면 날짜를 풀지 않음)')}</option>
            </Select>
          </Field>
          <div className="space-y-1.5 pt-6">
            <label className="flex items-center gap-2 text-base">
              <input type="checkbox" className="size-4 accent-[var(--accent)]" checked={value.prepass_always}
                onChange={(e) => set('prepass_always', e.target.checked)} />
              {t('자료가 없어도 계산 단계를 거침')}
            </label>
            <label className="flex items-center gap-2 text-base">
              <input type="checkbox" className="size-4 accent-[var(--accent)]" checked={value.prepass_quiet}
                onChange={(e) => set('prepass_quiet', e.target.checked)} />
              {t('계산할 것이 없을 때 알리지 않음')}
            </label>
          </div>
        </div>
      </Part>

      <Part title="쓰는 방식">
        <div className="space-y-1.5">
          <label className="flex items-center gap-2 text-base">
            <input type="checkbox" className="size-4 accent-[var(--accent)]" checked={!value.search}
              onChange={(e) => set('search', !e.target.checked)} />
            {t('웹 검색 안 함 (올린 자료로만 씀)')}
          </label>
          <label className="flex items-center gap-2 text-base">
            <input type="checkbox" className="size-4 accent-[var(--accent)]" checked={value.draws === 'when-asked'}
              onChange={(e) => set('draws', e.target.checked ? 'when-asked' : 'auto')} />
            {t('그림은 요청할 때만')}
          </label>
          <label className="flex items-center gap-2 text-base">
            <input type="checkbox" className="size-4 accent-[var(--accent)]" checked={value.fixed_outline}
              disabled={value.structure.sections.length === 0}
              onChange={(e) => set('fixed_outline', e.target.checked)} />
            {t('고정 목차 (아래 목차대로만 씀)')}
          </label>
        </div>
      </Part>

      <Part title="구조 규칙" hint="코드가 확인합니다. 목차·필수 표는 어긋나면 알려 주고, 핵심 요약은 없으면 채우며, 쪽수는 넘으면 줄입니다.">
        <Field label={t('목차')} hint={t('한 줄에 하나, 이 순서로')}>
          <Textarea
            rows={3}
            value={value.structure.sections.join('\n')}
            onChange={(e) => {
              const sections = lines(e.target.value).slice(0, catalogue.limits.sections)
              onChange({ ...value, fixed_outline: sections.length ? value.fixed_outline : false,
                structure: { ...value.structure, sections } })
            }}
            placeholder={t('예: 현황\n문제점 및 검토\n향후 조치')}
          />
        </Field>
        <div className="space-y-2" data-testid="settings-tables">
          <p className="text-sm font-medium text-fg">{t('필수 표')}</p>
          {tables.map((row, i) => (
            <div key={i} className="flex flex-wrap items-end gap-2">
              <Field label={t('열 이름 (쉼표로)')}>
                <Input
                  value={row.columns.join(', ')}
                  onChange={(e) => setStructure('tables', tables.map((r, k) => (k === i
                    ? { ...r, columns: e.target.value.split(',').map((c) => c.trim()).filter(Boolean).slice(0, catalogue.limits.columns) }
                    : r)))}
                  placeholder={t('할 일, 담당, 기한')}
                />
              </Field>
              <Field label={t('있어야 할 절 (선택)')}>
                <Input value={row.heading ?? ''}
                  onChange={(e) => setStructure('tables', tables.map((r, k) => (k === i ? { ...r, heading: e.target.value } : r)))} />
              </Field>
              <Button type="button" size="sm" variant="ghost" aria-label={t('표 빼기')}
                onClick={() => setStructure('tables', tables.filter((_, k) => k !== i))}>
                <Trash2 size={13} />
              </Button>
            </div>
          ))}
          {tables.length < catalogue.limits.tables && (
            <Button type="button" size="sm" onClick={() => setStructure('tables', [...tables, { columns: [] }])}>
              <Plus size={13} />
              {t('필수 표 추가')}
            </Button>
          )}
        </div>
        <div className="grid gap-3 sm:grid-cols-3">
          <Field label={t('맨 위 핵심 요약 줄 수')} hint={t('0이면 없음')}>
            <Input type="number" min={0} max={catalogue.limits.summary} value={value.structure.summary_lines}
              onChange={(e) => setStructure('summary_lines', Math.max(0, Math.min(catalogue.limits.summary, Number(e.target.value) || 0)))} />
          </Field>
          <Field label={t('쪽수 제한')} hint={t('0이면 없음. 요청한 쪽수가 먼저')}>
            <Input type="number" min={0} max={catalogue.limits.pages} value={value.structure.page_limit}
              onChange={(e) => setStructure('page_limit', Math.max(0, Math.min(catalogue.limits.pages, Number(e.target.value) || 0)))} />
          </Field>
          <div className="space-y-1.5 pt-6">
            <label className="flex items-center gap-2 text-base">
              <input type="checkbox" className="size-4 accent-[var(--accent)]" checked={value.structure.style === 'gaejo'}
                onChange={(e) => setStructure('style', e.target.checked ? 'gaejo' : '')} />
              {t('개조식 문체')}
            </label>
            <label className="flex items-center gap-2 text-base">
              <input type="checkbox" className="size-4 accent-[var(--accent)]" checked={value.structure.page_limit_rfp}
                onChange={(e) => setStructure('page_limit_rfp', e.target.checked)} />
              {t('제안요청서의 쪽수 제한 따르기')}
            </label>
          </div>
        </div>
      </Part>

      <Part title="검토 규칙" hint="말로 적은 규칙입니다. 다 쓴 뒤 판단 모델이 어긋나는 문장을 찾아 보여 주고, 고치지는 않습니다.">
        <Textarea
          rows={3}
          aria-label={t('검토 규칙')}
          value={value.rules.join('\n')}
          onChange={(e) => set('rules', lines(e.target.value).slice(0, catalogue.limits.rules))}
          placeholder={t('한 줄에 하나. 예: 판단에는 근거 수치를 붙인다.')}
        />
      </Part>

      <Part title="계산 확인" hint="기본 제공 확인입니다. 쓰기 전 계산과 다른 값을 찾아 한 번 고치고 다시 확인합니다.">
        <Names names={catalogue.checks} value={value.checks} labels={prepassLabels} testId="settings-checks"
          onChange={(next) => set('checks', next)} />
      </Part>

      <Part title="판정 검사" hint="판단 모델이 찾아 보여 주기만 합니다.">
        <Names names={catalogue.judged} value={value.judged} labels={prepassLabels} testId="settings-judged"
          onChange={(next) => set('judged', next)} />
      </Part>

      <Part title="서식 보정" hint="기본 제공 단계입니다. 계산한 값으로 표·기간·쪽수를 맞춥니다.">
        <Names names={catalogue.stages} value={value.stages} labels={prepassLabels} testId="settings-stages"
          onChange={(next) => set('stages', next)} />
      </Part>

      <Part title="빠진 것 채우기">
        <Names names={[...catalogue.amend, ...catalogue.head]} value={[...value.amend, ...value.head]}
          labels={prepassLabels} testId="settings-amend"
          onChange={(next) => onChange({
            ...value,
            amend: next.filter((n) => catalogue.amend.some((a) => a.name === n)),
            head: next.filter((n) => catalogue.head.some((h) => h.name === n)),
          })} />
      </Part>

      <Field label={t('자료끼리 값이 다를 때')} hint={t('비워 두면 어느 값을 썼는지와 그 이유를 적게 합니다.')}>
        <Textarea rows={2} value={value.conflict_rule} onChange={(e) => set('conflict_rule', e.target.value.slice(0, 600))} />
      </Field>
    </div>
  )
}
