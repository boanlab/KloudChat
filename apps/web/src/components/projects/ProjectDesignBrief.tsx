import { useEffect, useState } from 'react'
import { Button, Card } from '@/components/ui'
import { designBriefApi, type BriefReading } from '@/lib/api'
import { useMarkUnsaved } from '@/lib/unsaved'
import { useT } from '@/lib/useT'
import { useStore } from '@/store/useStore'
import type { Project } from '@/types'

/** The mark migration 0091 leaves on a 지침 written from a theme. */
const FROM_THEME = '(테마에서 옮김)'

const FACES: Record<string, string> = { serif: '명조', gothic: '고딕' }
const DENSITY: Record<string, string> = { compact: '촘촘하게', normal: '보통', airy: '여유 있게' }

/** The values the writers read from a 지침, as chips. */
function readChips(reading: BriefReading, t: (key: string) => string): { key: string; text: string; colour?: string }[] {
  const spec = reading.spec
  const out: { key: string; text: string; colour?: string }[] = []
  if (spec.accent) out.push({ key: 'accent', text: t('강조 {value}').replace('{value}', spec.accent), colour: spec.accent })
  if (spec.ink) out.push({ key: 'ink', text: t('글자 {value}').replace('{value}', spec.ink), colour: spec.ink })
  const face = (value?: string) => (value ? t(FACES[value] ?? value) : '')
  if (spec.body_face || spec.heading_face) {
    out.push({
      key: 'face',
      text: [spec.body_face && t('본문 {face}').replace('{face}', face(spec.body_face)),
        spec.heading_face && t('제목 {face}').replace('{face}', face(spec.heading_face))].filter(Boolean).join(' · '),
    })
  }
  if (spec.table) {
    const table = [
      spec.table.header_fill === true && t('머리 행 칠'),
      spec.table.header_fill === false && t('머리 행 칠 없음'),
      spec.table.vertical_rules === false && t('세로선 없음'),
      spec.table.vertical_rules === true && t('세로선'),
      spec.table.zebra && t('줄무늬'),
    ].filter(Boolean)
    if (table.length) out.push({ key: 'table', text: `${t('표')}: ${table.join(', ')}`, colour: spec.table.colour })
  }
  if (spec.density) out.push({ key: 'density', text: `${t('밀도')}: ${t(DENSITY[spec.density] ?? spec.density)}` })
  if (spec.max_bullets) out.push({ key: 'bullets', text: t('글머리 {n}개까지').replace('{n}', String(spec.max_bullets)) })
  if (spec.page_size || spec.orientation) {
    out.push({ key: 'page', text: [spec.page_size, spec.orientation === 'landscape' ? t('가로') : spec.orientation ? t('세로') : ''].filter(Boolean).join(' ') })
  }
  if (reading.tone.length) out.push({ key: 'tone', text: t('말투는 글로 전달') })
  return out
}

/** A 디자인 지침 textarea with the values the writers will read from it. */
export function DesignBriefField({
  value,
  onChange,
  label,
}: {
  value: string
  onChange: (value: string) => void
  label: string
}) {
  const t = useT()
  const [reading, setReading] = useState<BriefReading | null>(null)
  useEffect(() => {
    if (!value.trim()) {
      setReading(null)
      return
    }
    let live = true
    const timer = setTimeout(() => {
      void designBriefApi.read(value).then((found) => { if (live) setReading(found) }).catch(() => {})
    }, 500)
    return () => {
      live = false
      clearTimeout(timer)
    }
  }, [value])
  const chips = reading ? readChips(reading, t) : []
  return (
    <div className="space-y-2">
      {value.includes(FROM_THEME) && (
        <p className="rounded-control border border-accent/30 bg-accent-soft px-3 py-2 text-sm text-accent" data-testid="brief-from-theme">
          {t('예전 테마를 글로 옮긴 지침입니다. 고쳐 써도 됩니다.')}
        </p>
      )}
      <textarea
        value={value}
        maxLength={2000}
        rows={4}
        aria-label={label}
        placeholder={t('예: 남색 하나만 강조색으로. 본문은 명조, 제목은 고딕. 표는 머리 행만 옅게 칠하고 세로선 없이. 슬라이드는 여백 넉넉히.')}
        onChange={(e) => onChange(e.target.value)}
        className="w-full resize-y rounded-control border border-line bg-panel px-3 py-2 text-base focus:border-accent focus:outline-none"
      />
      {chips.length > 0 && (
        <div className="flex flex-wrap items-center gap-1.5" data-testid="brief-reading">
          <span className="text-xs text-faint">{t('읽은 값')}</span>
          {chips.map((chip) => (
            <span key={chip.key} className="inline-flex items-center gap-1.5 rounded-full border border-line px-2 py-0.5 text-xs text-muted">
              {chip.colour && <span className="size-2.5 rounded-sm" style={{ background: chip.colour }} aria-hidden />}
              {chip.text}
            </span>
          ))}
        </div>
      )}
    </div>
  )
}

/** 디자인 지침: how the project's documents look when no form decides, in words. */
export function ProjectDesignBrief({ project }: { project: Project }) {
  const t = useT()
  const updateProject = useStore((s) => s.updateProject)
  const saved = project.designBrief ?? ''
  const [text, setText] = useState(saved)
  const [saving, setSaving] = useState(false)
  const dirty = text !== saved
  useMarkUnsaved(`project.designBrief.${project.id}`, dirty)
  useEffect(() => {
    if (!dirty) setText(saved)
    // a save elsewhere replaces what is shown, not what is being typed
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [saved])
  return (
    <Card className="space-y-3 p-4" data-testid="project-design-brief">
      <div className="flex items-start justify-between gap-2">
        <div>
          <p className="text-base font-medium">{t('디자인 지침')}</p>
          <p className="text-sm text-muted">
            {t('양식 파일이 정하지 않은 문서·슬라이드 모양을 글로 정합니다.')}
          </p>
        </div>
        <Button
          size="sm"
          variant="primary"
          aria-label={t('디자인 지침 저장')}
          disabled={!dirty || saving}
          onClick={async () => {
            setSaving(true)
            await updateProject(project.id, { designBrief: text })
            setSaving(false)
          }}
        >
          {saving ? t('저장 중…') : t('저장')}
        </Button>
      </div>
      <DesignBriefField value={text} onChange={setText} label={t('디자인 지침')} />
    </Card>
  )
}
