/** 양식 (form files): an attachment's role chip, the saved form chip, and the line under a written file that says which form it followed. */
import { useState } from 'react'
import { Check, ChevronDown, FileStack, X } from 'lucide-react'
import type { FileRow, FollowedForm, FormKind, FormRole } from '@/lib/api'
import { useT } from '@/lib/useT'
import { cn } from '@/lib/utils'

/** Output kinds whose writer follows a form so far (`form_files.FOLLOWED`). */
export const FOLLOWED_KINDS: readonly FormKind[] = ['pptx', 'docx']

/** Whether a file can be followed as a form at all. */
export function followable(file: FileRow): boolean {
  const kind = file.form?.kind
  return !!kind && kind !== 'hwp' && FOLLOWED_KINDS.includes(kind) && !file.form?.error
}

/** The role a file goes with: the chip's pick, else its own verdict; null when unsure. */
export function formRoleOf(file: FileRow, picked?: FormRole): FormRole | null {
  if (!followable(file)) return 'material'
  if (picked) return picked
  if (file.form?.verdict === 'form') return 'form'
  if (file.form?.verdict === 'material') return 'material'
  return null
}

/** 양식 | 자료 on an attachment chip; neither lit while the server has to decide. */
export function FormRoleToggle({
  file,
  role,
  onPick,
}: {
  file: FileRow
  role: FormRole | null
  onPick: (role: FormRole) => void
}) {
  const t = useT()
  return (
    <span
      role="radiogroup"
      aria-label={t('{name}을 어떻게 쓸까요').replace('{name}', file.name)}
      data-testid="form-role"
      data-role={role ?? 'unsure'}
      className="ml-0.5 inline-flex overflow-hidden rounded-[5px] border border-line text-[11px] leading-5"
    >
      {(['form', 'material'] as const).map((value) => (
        <button
          key={value}
          type="button"
          role="radio"
          aria-checked={role === value}
          onClick={() => onPick(value)}
          title={value === 'form'
            ? t('이 파일의 레이아웃·글꼴·색에 맞춰 씁니다')
            : t('이 파일의 내용을 자료로 읽습니다')}
          className={cn(
            'whitespace-nowrap px-1.5',
            role === value ? 'bg-accent text-accent-fg' : 'bg-panel text-muted hover:text-fg',
            role === null && value === 'form' && 'text-accent',
          )}
        >
          {value === 'form' ? (role === null ? t('양식?') : t('양식')) : t('자료')}
        </button>
      ))}
    </span>
  )
}

/** The project's (or agent's) saved form for this output, with ✕ to leave it off for this conversation. */
export function SavedFormChip({
  name,
  kind,
  source,
  off,
  onOff,
}: {
  name: string
  kind: FormKind
  source: 'project' | 'agent'
  off: boolean
  onOff: (off: boolean) => void
}) {
  const t = useT()
  const from = source === 'project' ? t('프로젝트 양식') : t('에이전트 양식')
  if (off) {
    return (
      <button
        type="button"
        data-testid="saved-form-chip"
        data-off=""
        onClick={() => onOff(false)}
        className="flex h-8 items-center gap-1.5 rounded-control border border-dashed border-line px-2.5 text-sm text-faint hover:bg-elevated hover:text-fg"
      >
        <FileStack size={13} />
        <span className="line-through">{from}</span>
        <span className="text-accent">{t('다시 쓰기')}</span>
      </button>
    )
  }
  return (
    <span
      data-testid="saved-form-chip"
      className="flex h-8 max-w-[18rem] items-center gap-1.5 rounded-control border border-accent/40 bg-accent-soft pl-2.5 pr-1 text-sm text-accent"
      title={kind === 'docx'
        ? t('이 대화의 Word 파일은 이 양식에 맞춰 씁니다')
        : t('이 대화의 PowerPoint 파일은 이 양식에 맞춰 씁니다')}
    >
      <FileStack size={13} />
      <span className="text-xs">{from}</span>
      <span className="truncate font-medium">{name}</span>
      <button
        type="button"
        onClick={() => onOff(true)}
        aria-label={t('{name} 양식 빼기').replace('{name}', name)}
        className="grid size-6 place-items-center rounded-control hover:bg-panel"
      >
        <X size={12} />
      </button>
    </span>
  )
}

const SOURCES: Record<FollowedForm['source'], string> = {
  attached: '첨부',
  agent: '에이전트 기본',
  project: '프로젝트 기본',
}

/** 「✓ 양식을 따름」 under a written file: which form, from where, and what was taken from it. */
export function FollowedFormLine({ form }: { form: FollowedForm }) {
  const t = useT()
  const [open, setOpen] = useState(false)
  const followed = form.followed ?? {}
  const layouts = Object.entries(followed.layouts ?? {})
  const standIns = followed.standIns ?? []
  return (
    <div
      data-testid="followed-form"
      className="mt-2 rounded-control border border-success/30 bg-success/5 px-3 py-2 text-xs text-muted"
    >
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        className="flex w-full items-center gap-1.5 text-left"
      >
        <Check size={13} className="shrink-0 text-success" />
        <span className="shrink-0 whitespace-nowrap font-medium text-success">{t('양식을 따름')}</span>
        <span className="min-w-0 truncate">· {form.name} ({t(SOURCES[form.source])})</span>
        <ChevronDown size={13} className={cn('ml-auto shrink-0 transition-transform', open && 'rotate-180')} />
      </button>
      {open && form.kind === 'docx' && (
        <ul className="mt-1.5 list-disc space-y-0.5 pl-5">
          <li>{t('스타일·번호·머리글/바닥글·표지: 양식 그대로')}</li>
          {(followed.chapters ?? []).length > 0 && (
            <li>{t('양식의 장에 맞춰 씀: {list}').replace('{list}', (followed.chapters ?? []).join(', '))}</li>
          )}
          {(followed.filled ?? []).length > 0 && (
            <li>{t('표지 칸 채움: {list}').replace('{list}', (followed.filled ?? []).join(', '))}</li>
          )}
          {(followed.guidanceRemoved ?? 0) > 0 && (
            <li>{t('작성요령 {n}줄 지움').replace('{n}', String(followed.guidanceRemoved))}</li>
          )}
          {(followed.added ?? []).map((name) => (
            <li key={`added-${name}`} className="text-warn">
              {t('양식에 없는 「{name}」 장은 양식의 장 제목 모양으로 덧붙임').replace('{name}', name)}
            </li>
          ))}
          {(followed.unwritten ?? []).map((name) => (
            <li key={`unwritten-${name}`} className="text-warn">
              {t('「{name}」 장은 쓸 내용이 없어 제목만 남김').replace('{name}', name)}
            </li>
          ))}
        </ul>
      )}
      {open && form.kind !== 'docx' && (
        <ul className="mt-1.5 list-disc space-y-0.5 pl-5">
          <li>{t('슬라이드 마스터·글꼴·색: 양식 그대로')}{followed.size ? ` (${followed.size})` : ''}</li>
          {layouts.length > 0 && (
            <li>
              {t('쓴 레이아웃: {list}').replace(
                '{list}',
                layouts.map(([name, count]) => `${name} ${count}`).join(', '),
              )}
            </li>
          )}
          {standIns.map((item) => (
            <li key={item.slide} className="text-warn">
              {t('{n}번 슬라이드: 양식에 맞는 레이아웃이 없어 「{layout}」에 놓음')
                .replace('{n}', String(item.slide))
                .replace('{layout}', item.layout)}
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}
