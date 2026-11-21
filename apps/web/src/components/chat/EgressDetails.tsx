import { ChevronDown, ChevronRight, ShieldCheck } from 'lucide-react'
import { useId, useState } from 'react'
import { FINDING_LABEL } from '@/lib/privacy'
import { useT } from '@/lib/useT'
import type { EgressRecord } from '@/types'

/** What each part of the turn is called in the panel. */
const PART_LABEL: Record<EgressRecord['parts'][number]['source'], string> = {
  prompt: '보낸 요청',
  attachments: '보낸 첨부',
  material: '보낸 참고 자료',
  instructions: '보낸 지침',
  conversation_history: '보낸 이전 대화',
}

/** A mask's placeholder, e.g. 「[이메일]」, as the server writes it. */
const PLACEHOLDER = /(\[(?:이메일|전화번호|주민번호|카드번호|IP주소|API키|JWT|개인키|민감정보)\])/g

/** 「이메일 2 · 전화번호 1 가림」, untranslated labels translated by the caller's `t`. */
export function egressSummary(record: EgressRecord, t: (text: string) => string): string {
  const counts = new Map<string, number>()
  for (const row of record.masked) {
    if (!Number.isSafeInteger(row.count) || row.count <= 0) continue
    const label = Object.hasOwn(FINDING_LABEL, row.category) ? FINDING_LABEL[row.category] : '기타 민감정보'
    counts.set(label, (counts.get(label) ?? 0) + row.count)
  }
  const listed = [...counts].map(([label, n]) => `${t(label)} ${n.toLocaleString()}`).join(' · ')
  return t('{list} 가림').replace('{list}', listed)
}

function Highlighted({ text }: { text: string }) {
  return (
    <>
      {text.split(PLACEHOLDER).map((piece, i) =>
        i % 2 === 1 ? (
          <mark key={i} data-testid="egress-placeholder" className="rounded-[3px] bg-accent-soft px-0.5 text-accent">
            {piece}
          </mark>
        ) : (
          piece
        ),
      )}
    </>
  )
}

/**
 * 「외부로 보낸 내용 보기」 under an answer whose turn left for an outside model with personal
 * data masked: the masked request and material exactly as they were sent, each placeholder
 * marked, and how many of each kind were masked. The record holds masked text only.
 */
export function EgressDetails({ record }: { record: EgressRecord }) {
  const t = useT()
  const [open, setOpen] = useState(false)
  const id = useId()
  const summary = egressSummary(record, t)
  return (
    <div className="mb-2 max-w-2xl" data-testid="egress-details">
      <button
        type="button"
        aria-expanded={open}
        aria-controls={id}
        onClick={() => setOpen((o) => !o)}
        data-testid="egress-toggle"
        className="flex min-h-7 items-center gap-1 rounded-control text-xs text-muted hover:text-fg focus-visible:text-fg"
      >
        {open ? <ChevronDown size={13} aria-hidden /> : <ChevronRight size={13} aria-hidden />}
        <ShieldCheck size={13} aria-hidden className="text-faint" />
        <span>{t('외부로 보낸 내용 보기')}</span>
        <span className="text-faint">· {summary}</span>
      </button>
      {open && (
        <div
          id={id}
          role="region"
          aria-label={t('외부로 보낸 내용')}
          data-testid="egress-panel"
          className="mt-1 space-y-2 rounded-control border border-line bg-elevated px-3 py-2 text-sm text-fg"
        >
          <p className="text-xs text-muted">
            {t('외부 모델에는 아래 내용만 보냈습니다. 표시된 부분은 원래 값 대신 보낸 자리 표시입니다.')}
          </p>
          {record.parts.map((part) => (
            <section key={part.source} data-testid={`egress-part-${part.source}`}>
              <h4 className="mb-0.5 text-xs font-medium text-muted">{t(PART_LABEL[part.source] ?? part.source)}</h4>
              <pre className="max-h-64 overflow-auto whitespace-pre-wrap break-words rounded-[4px] bg-panel px-2 py-1.5 font-sans text-sm [overflow-wrap:anywhere]">
                <Highlighted text={part.text} />
              </pre>
              {part.truncated && (
                <p className="mt-0.5 text-xs text-faint">{t('앞부분만 보여 줍니다.')}</p>
              )}
            </section>
          ))}
        </div>
      )}
    </div>
  )
}
