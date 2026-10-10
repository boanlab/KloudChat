import { ArrowDown, ArrowUp, Plus, Trash2 } from 'lucide-react'
import { Button, Field, Input, Select } from '@/components/ui'
import { useT } from '@/lib/useT'
import type { GapDetector, GapQuestion } from '@/types'

const MAX_QUESTIONS = 12

/** What each detector type means, in the form's words. */
const TYPE_LABEL: Record<GapDetector['type'], string> = {
  keyword: '낱말이 있으면 답한 것으로',
  builtin: '기본 제공 판단',
  regex: '패턴(정규식)',
  judge: '모델이 자료를 읽고 판단',
}

const words = (text: string) =>
  text
    .split(/[,\n]/)
    .map((w) => w.trim())
    .filter(Boolean)

/** The next free id: q1, q2, … */
function nextId(rows: GapQuestion[]): string {
  const taken = new Set(rows.map((r) => r.id))
  let n = rows.length + 1
  while (taken.has(`q${n}`)) n += 1
  return `q${n}`
}

const blankQuestion = (rows: GapQuestion[]): GapQuestion => ({
  id: nextId(rows),
  label: '',
  question: '',
  why: '',
  placeholder: '',
  detector: { type: 'keyword', words: [] },
})

/** One question read back as a line: what it asks and how it decides. */
function detectorLine(d: GapDetector, labels: Record<string, string>, t: (s: string) => string): string {
  if (d.type === 'builtin') return `${t('기본 제공 판단')} · ${t(labels[d.name ?? ''] ?? d.name ?? '')}`
  if (d.type === 'keyword') return `${t('낱말')} · ${(d.words ?? []).join(', ')}`
  if (d.type === 'regex') return t('패턴(정규식)')
  return t('모델이 자료를 읽고 판단')
}

/** The questions an agent inherits: read only, so a person sees what is asked already. */
export function InheritedQuestions({ rows, labels }: { rows: GapQuestion[]; labels: Record<string, string> }) {
  const t = useT()
  if (rows.length === 0) return null
  return (
    <div className="space-y-1.5" data-testid="gap-inherited">
      <p className="text-sm font-medium text-muted">{t('템플릿·기본 과정이 묻는 질문')}</p>
      <ul className="space-y-1">
        {rows.map((q) => (
          <li key={q.id} className="rounded-control border border-line bg-elevated px-3 py-2">
            <p className="text-base text-fg">{q.question}</p>
            <p className="text-sm text-faint">{detectorLine(q.detector, labels, t)}</p>
          </li>
        ))}
      </ul>
    </div>
  )
}

/**
 * 「빠진 정보 질문」: what this agent asks before writing when the material does not answer it,
 * on top of what its 템플릿 asks. Each question says how it decides the material answers it.
 * The server asks at most four on one card, only when 「빠진 정보 먼저 묻기」 is on.
 */
export function GapQuestionsEditor({
  value,
  onChange,
  detectors,
}: {
  value: GapQuestion[]
  onChange: (next: GapQuestion[]) => void
  detectors: { name: string; label: string }[]
}) {
  const t = useT()
  const set = (i: number, patch: Partial<GapQuestion>) => onChange(value.map((q, j) => (j === i ? { ...q, ...patch } : q)))
  const setDetector = (i: number, patch: Partial<GapDetector>) => set(i, { detector: { ...value[i].detector, ...patch } })
  const move = (i: number, by: number) => {
    const next = [...value]
    const [row] = next.splice(i, 1)
    next.splice(i + by, 0, row)
    onChange(next)
  }
  return (
    <div className="space-y-3" data-testid="gap-questions">
      {value.map((q, i) => {
        const d = q.detector
        const base = `gap-${q.id}`
        return (
          <fieldset key={q.id} aria-label={t('질문 {n}').replace('{n}', String(i + 1))} className="space-y-3 rounded-card border border-line p-3" data-testid="gap-question">
            <div className="flex items-center gap-2">
              <span className="flex-1 text-sm font-medium text-muted">{t('질문 {n}').replace('{n}', String(i + 1))}</span>
              <Button type="button" variant="ghost" size="icon" aria-label={t('위로')} disabled={i === 0} onClick={() => move(i, -1)}>
                <ArrowUp size={14} />
              </Button>
              <Button type="button" variant="ghost" size="icon" aria-label={t('아래로')} disabled={i === value.length - 1} onClick={() => move(i, 1)}>
                <ArrowDown size={14} />
              </Button>
              <Button
                type="button"
                variant="ghost"
                size="icon"
                aria-label={t('질문 {n} 빼기').replace('{n}', String(i + 1))}
                onClick={() => onChange(value.filter((_, j) => j !== i))}
              >
                <Trash2 size={14} />
              </Button>
            </div>
            <Field label={t('물어볼 것')}>
              <Input
                id={`${base}-question`}
                value={q.question}
                maxLength={300}
                onChange={(e) => set(i, { question: e.target.value })}
                placeholder={t('예: 행사 장소는 어디인가요?')}
              />
            </Field>
            <div className="grid gap-3 sm:grid-cols-2">
              <Field label={t('짧은 이름')} hint={t('답을 자료에 붙일 때 앞에 씁니다.')}>
                <Input value={q.label} maxLength={40} onChange={(e) => set(i, { label: e.target.value })} placeholder={t('예: 장소')} />
              </Field>
              <Field label={t('답이 없으면 남길 빈칸')} hint={t('「있는 자료로 진행」을 고르면 이 빈칸으로 둡니다.')}>
                <Input value={q.placeholder} maxLength={40} onChange={(e) => set(i, { placeholder: e.target.value })} placeholder={t('예: [장소]')} />
              </Field>
            </div>
            <Field label={t('왜 묻는지')}>
              <Input value={q.why} maxLength={400} onChange={(e) => set(i, { why: e.target.value })} placeholder={t('예: 안내문 첫 줄에 장소가 들어갑니다.')} />
            </Field>
            <div className="grid gap-3 sm:grid-cols-2">
              <Field label={t('자료가 답했는지 판단')}>
                <Select
                  value={d.type}
                  data-testid="gap-detector"
                  onChange={(e) => {
                    const type = e.target.value as GapDetector['type']
                    set(i, {
                      detector:
                        type === 'builtin'
                          ? { type, name: detectors[0]?.name ?? '' }
                          : type === 'keyword'
                            ? { type, words: [] }
                            : type === 'regex'
                              ? { type, pattern: '' }
                              : { type },
                    })
                  }}
                >
                  {(Object.keys(TYPE_LABEL) as GapDetector['type'][]).map((type) => (
                    <option key={type} value={type}>
                      {t(TYPE_LABEL[type])}
                    </option>
                  ))}
                </Select>
              </Field>
              {d.type === 'builtin' && (
                <Field label={t('찾는 것')}>
                  <Select value={d.name ?? ''} onChange={(e) => setDetector(i, { name: e.target.value })}>
                    {detectors.map((row) => (
                      <option key={row.name} value={row.name}>
                        {t(row.label)}
                      </option>
                    ))}
                  </Select>
                </Field>
              )}
              {d.type === 'keyword' && (
                <Field label={t('이 낱말이 있으면 답한 것')} hint={t('쉼표로 나눕니다.')}>
                  <Input
                    defaultValue={(d.words ?? []).join(', ')}
                    onBlur={(e) => setDetector(i, { words: words(e.target.value) })}
                    placeholder={t('예: 장소, 강당, 회의실')}
                  />
                </Field>
              )}
              {d.type === 'regex' && (
                <Field label={t('패턴')} hint={t('반복은 {0,10}처럼 횟수를 정해 씁니다. * 와 + 는 쓸 수 없습니다.')}>
                  <Input className="font-mono" value={d.pattern ?? ''} maxLength={300} onChange={(e) => setDetector(i, { pattern: e.target.value })} />
                </Field>
              )}
              {d.type === 'judge' && (
                <p className="self-end text-sm text-faint sm:col-span-1">
                  {t('내부 모델이 자료에서 답을 그대로 인용해야 답한 것으로 봅니다. 내부 모델이 없으면 묻지 않습니다.')}
                </p>
              )}
            </div>
            {d.type === 'keyword' && (
              <Field label={t('요청에 이 낱말이 있을 때만 묻기')} hint={t('비워 두면 늘 확인합니다.')}>
                <Input
                  defaultValue={Array.isArray(d.when) ? d.when.join(', ') : ''}
                  onBlur={(e) => {
                    const when = words(e.target.value)
                    setDetector(i, { when: when.length ? when : undefined })
                  }}
                  placeholder={t('예: 안내문, 공지')}
                />
              </Field>
            )}
          </fieldset>
        )
      })}
      <Button type="button" disabled={value.length >= MAX_QUESTIONS} onClick={() => onChange([...value, blankQuestion(value)])}>
        <Plus size={14} />
        {t('질문 추가')}
      </Button>
    </div>
  )
}
