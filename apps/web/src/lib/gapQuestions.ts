import type { GapQuestion } from '@/types'

/** A field of a question that must be filled before saving, and what to say about it. */
export type GapProblem = { field: 'question' | 'words' | 'name' | 'pattern'; message: string }

/** Unbounded repeats the server refuses (`gap_questions.regex_ok`): `*`, `+`, `{n,}`. */
const UNBOUNDED = /(?<!\\)[*+]|\{\d{1,4},\}/

/**
 * What each question still needs, by its id: 「물어볼 것」 always, and what its way of
 * deciding needs (낱말, a built-in detector, a bounded pattern). Empty: it can be saved.
 */
export function gapProblems(rows: GapQuestion[]): Record<string, GapProblem> {
  const out: Record<string, GapProblem> = {}
  for (const q of rows) {
    const d = q.detector
    if (!q.question.trim()) out[q.id] = { field: 'question', message: '「물어볼 것」을 적으세요.' }
    else if (d.type === 'keyword' && !(d.words ?? []).some((w) => w.trim()))
      out[q.id] = { field: 'words', message: '답이 있다고 볼 낱말을 하나 이상 적으세요. 쉼표로 나눕니다.' }
    else if (d.type === 'builtin' && !d.name) out[q.id] = { field: 'name', message: '찾을 것을 고르세요.' }
    else if (d.type === 'regex' && (!(d.pattern ?? '').trim() || UNBOUNDED.test(d.pattern ?? '')))
      out[q.id] = {
        field: 'pattern',
        message: (d.pattern ?? '').trim() ? '반복은 {0,10}처럼 횟수를 정해 씁니다. * 와 + 는 쓸 수 없습니다.' : '패턴을 적으세요.',
      }
  }
  return out
}

/** The id of a question's field, for focusing it from a save that stopped on it. */
export const gapFieldId = (id: string, field: GapProblem['field']) => `gap-${id}-${field}`
