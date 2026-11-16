import { useEffect, useRef, useState } from 'react'
import { FileStack, FolderOpen, Loader2, Upload, X } from 'lucide-react'
import { Button, Card } from '@/components/ui'
import { FileBoxPicker } from '@/components/files/FileBoxPicker'
import { FOLLOWED_KINDS } from '@/components/chat/FormChips'
import { errorCode, filesApi, formsApi, type FileRow, type FormKind } from '@/lib/api'
import { useT } from '@/lib/useT'
import { useStore } from '@/store/useStore'
import type { Project } from '@/types'
import type { Translator } from '@/lib/useT'

const SLOTS: { kind: FormKind; label: string; accept: string }[] = [
  { kind: 'pptx', label: 'PowerPoint', accept: '.pptx,.potx' },
  { kind: 'docx', label: 'Word', accept: '.docx,.dotx' },
  { kind: 'hwpx', label: '한글', accept: '.hwpx' },
]

/** Why a file cannot be a saved form, as the server says (`routers/forms.py`). */
const REFUSALS: Record<string, string> = {
  form_hwp_needs_hwpx:
    '예전 한글 형식(.hwp)은 양식으로 쓸 수 없습니다. 한글에서 다른 이름으로 저장 › 「한글 표준 문서 (*.hwpx)」로 저장해 올려 주세요.',
  form_kind_unknown: 'PowerPoint·Word·한글(HWPX) 파일만 양식으로 쓸 수 있습니다.',
  form_unreadable: '이 파일을 양식으로 읽지 못했습니다.',
  form_kind_mismatch: '이 칸에 맞는 종류의 파일이 아닙니다.',
}

/** What a saved form holds, one line. */
function summary(row: FileRow, t: Translator): string {
  const form = row.form
  if (!form) return ''
  if (form.kind === 'pptx') {
    return [
      form.layouts !== undefined && t.n('레이아웃 {n}개', form.layouts),
      form.font,
      form.accent,
      form.size,
    ].filter(Boolean).join(' · ')
  }
  if (form.kind === 'docx') {
    return [
      form.sections ? t.n('장 {n}개', form.sections) : t('장 제목 스타일 없음'),
      form.header && t('머리글'),
      form.cover && t('표지'),
      form.fields ? t.n('채울 칸 {n}개', form.fields) : '',
    ].filter(Boolean).join(' · ')
  }
  if (form.kind === 'hwpx') {
    return [
      t.n('채울 칸 {n}개', form.fields ?? 0),
      form.tables ? t.n('표 {n}개', form.tables) : '',
    ].filter(Boolean).join(' · ')
  }
  return ''
}

/** The saved form per output kind, with upload, file-box pick and remove. `onSet` stores the
 *  choice (a project saves at once, an agent's form with the agent). */
export function FormFilesField({
  value,
  rows,
  onSet,
}: {
  value: Partial<Record<FormKind, string | null>>
  /** Saved form rows already at hand; others are fetched by id. */
  rows?: FileRow[]
  onSet: (kind: FormKind, row: FileRow | null) => Promise<void> | void
}) {
  const t = useT()
  const [busy, setBusy] = useState<FormKind | null>(null)
  const [error, setError] = useState('')
  const [picking, setPicking] = useState<FormKind | null>(null)
  const [known, setKnown] = useState<FileRow[]>(rows ?? [])
  const inputs = useRef<Partial<Record<FormKind, HTMLInputElement | null>>>({})
  useEffect(() => {
    if (rows) setKnown((current) => [...rows, ...current.filter((row) => !rows.some((r) => r.id === row.id))])
  }, [rows])
  useEffect(() => {
    // a saved form not at hand (an agent's): read its row for the name and summary
    for (const id of Object.values(value)) {
      if (id && !known.some((row) => row.id === id)) {
        void filesApi.get(id).then((row) => setKnown((current) => [...current, row])).catch(() => {})
      }
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [value])

  const save = async (kind: FormKind, made: Promise<FileRow>) => {
    setBusy(kind)
    setError('')
    try {
      const row = await made
      if (row.form?.kind !== kind) {
        setError(t(REFUSALS.form_kind_mismatch))
        return
      }
      setKnown((current) => [row, ...current])
      await onSet(kind, row)
    } catch (err) {
      const code = errorCode(err)
      setError(t(REFUSALS[code ?? ''] ?? '양식을 저장하지 못했습니다. 다시 시도하세요.'))
    } finally {
      setBusy(null)
    }
  }

  const slots = SLOTS.filter((slot) => FOLLOWED_KINDS.includes(slot.kind))
  return (
    <div className="space-y-3">
      {slots.map((slot) => {
        const row = known.find((file) => file.id === value[slot.kind])
        return (
          <div key={slot.kind} className="space-y-1.5" data-testid={`form-slot-${slot.kind}`}>
            <p className="text-sm font-medium text-muted">{t(slot.label)}</p>
            {row ? (
              <div className="flex items-center gap-2 rounded-control border border-line bg-panel px-2.5 py-2">
                <FileStack size={16} className="shrink-0 text-accent" />
                <span className="min-w-0 flex-1">
                  <span className="block truncate text-base" title={row.name}>{row.name}</span>
                  <span className="block truncate text-xs text-faint">{summary(row, t)}</span>
                </span>
                <Button
                  size="sm"
                  variant="ghost"
                  className="size-8 px-0"
                  aria-label={t('{name} 양식 빼기').replace('{name}', row.name)}
                  onClick={() => void onSet(slot.kind, null)}
                >
                  <X size={14} />
                </Button>
              </div>
            ) : (
              <p className="text-sm text-faint">{t('없음')}</p>
            )}
            <div className="flex flex-wrap gap-1.5">
              <Button
                size="sm"
                variant="secondary"
                disabled={busy !== null}
                onClick={() => inputs.current[slot.kind]?.click()}
              >
                {busy === slot.kind ? <Loader2 size={13} className="animate-spin" /> : <Upload size={13} />}
                {row ? t('바꾸기') : t('올리기')}
              </Button>
              <Button
                size="sm"
                variant="ghost"
                disabled={busy !== null}
                aria-label={t('{kind} 양식을 파일함에서 고르기').replace('{kind}', t(slot.label))}
                onClick={() => setPicking(slot.kind)}
              >
                <FolderOpen size={13} />
                {t('파일함에서 고르기')}
              </Button>
              <input
                ref={(el) => { inputs.current[slot.kind] = el }}
                type="file"
                accept={slot.accept}
                hidden
                aria-label={t('{kind} 양식 올리기').replace('{kind}', t(slot.label))}
                onChange={(e) => {
                  const file = e.target.files?.[0]
                  e.target.value = ''
                  if (file) void save(slot.kind, formsApi.upload(file))
                }}
              />
            </div>
          </div>
        )
      })}
      {error && <p role="alert" className="text-sm text-danger">{error}</p>}
      <FileBoxPicker
        open={picking !== null}
        onClose={() => setPicking(null)}
        attached={[]}
        onPick={(rows) => {
          const kind = picking
          setPicking(null)
          if (kind && rows[0]) void save(kind, formsApi.fromFile(rows[0].id))
        }}
      />
    </div>
  )
}

/** 기본 양식 파일: the form each output kind of this project's documents is written on. */
export function ProjectFormFiles({ project }: { project: Project }) {
  const t = useT()
  const updateProject = useStore((s) => s.updateProject)
  return (
    <Card className="space-y-3 p-4" data-testid="project-form-files">
      <div>
        <p className="text-base font-medium">{t('기본 양식 파일')}</p>
        <p className="text-sm text-muted">
          {t('이 프로젝트의 파일이 따를 양식이며, 비워 둔 칸은 기본 구조와 디자인을 쓰고 대화에 첨부한 양식이 우선합니다.')}
        </p>
      </div>
      <FormFilesField
        value={project.formFiles ?? {}}
        rows={project.formRows}
        onSet={async (kind, row) => {
          await updateProject(project.id, { formFiles: { [kind]: row ? row.id : null } as never })
        }}
      />
    </Card>
  )
}
