import { useRef, useState } from 'react'
import { Button, Field, Input, Switch, Textarea } from '@/components/ui'
import { errorMessage } from '@/lib/api'
import { NAME_LIMIT } from '@/lib/limits'
import { useT } from '@/lib/useT'
import { cn } from '@/lib/utils'
import { useStore } from '@/store/useStore'
import type { Skill } from '@/types'
import { Block, Group } from './AgentForm'
import { blankSkill, type SkillDraft } from './formModel'
import { ToolPicker } from './Pickers'

/** A monospace editor with line numbers; the numbers scroll with the text. */
export function LineNumberedEditor({
  value,
  onChange,
  label,
  placeholder,
  rows = 18,
}: {
  value: string
  onChange: (next: string) => void
  label: string
  placeholder?: string
  rows?: number
}) {
  const gutter = useRef<HTMLDivElement>(null)
  const lines = Math.max(1, value.split('\n').length)
  return (
    <div className="flex overflow-hidden rounded-card border border-line bg-elevated focus-within:border-accent">
      <div
        ref={gutter}
        aria-hidden
        className="shrink-0 overflow-hidden border-r border-line py-3 pr-2 pl-3 text-right font-mono text-sm leading-6 text-faint select-none"
        style={{ height: `calc(${rows} * 1.5rem + 1.5rem)` }}
      >
        {Array.from({ length: lines }, (_, i) => (
          <div key={i}>{i + 1}</div>
        ))}
      </div>
      <textarea
        aria-label={label}
        value={value}
        rows={rows}
        spellCheck={false}
        placeholder={placeholder}
        onChange={(e) => onChange(e.target.value)}
        onScroll={(e) => {
          if (gutter.current) gutter.current.scrollTop = e.currentTarget.scrollTop
        }}
        className="min-w-0 flex-1 resize-none bg-transparent px-3 py-3 font-mono text-sm leading-6 text-fg placeholder:text-faint focus:outline-none"
        style={{ height: `calc(${rows} * 1.5rem + 1.5rem)` }}
      />
    </div>
  )
}

/**
 * 이름, 설명, one editor for the steps; 「초안」 keeps it out of the composer. 사용 시점 follows the
 * description unless written under 「더 설정하기」. Editing adds the required tools.
 */
export function SkillForm({
  current,
  onSaved,
  onCancel,
  aboveBottomBar = false,
}: {
  /** The skill being edited; none creates one. */
  current?: Skill
  onSaved: (saved: Skill) => void
  onCancel?: () => void
  /** A detail screen's phone action bar sits below; the save bar stops above it. */
  aboveBottomBar?: boolean
}) {
  const t = useT()
  const upsertSkill = useStore((s) => s.upsertSkill)
  const availableTools = useStore((s) => s.availableTools)
  const [draft, setDraft] = useState<SkillDraft>(() =>
    current
      ? {
          name: current.name,
          description: current.description,
          whenToUse: current.whenToUse,
          body: current.body,
          requiredTools: current.requiredTools,
          visibility: current.visibility,
          enabled: current.enabled,
        }
      : blankSkill(),
  )
  // 사용 시점 follows the description until someone writes it.
  const [ownWhen, setOwnWhen] = useState(!!current && current.whenToUse !== current.description)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [done, setDone] = useState(false)
  const edit = (patch: Partial<SkillDraft>) => {
    setDone(false)
    setDraft((d) => ({ ...d, ...patch }))
  }

  const save = async () => {
    if (!draft.name.trim()) return
    setSaving(true)
    setError(null)
    try {
      // Blank id means create; edits keep fields the form does not cover.
      const saved = await upsertSkill({
        id: current?.id ?? '',
        slug: current?.slug ?? '',
        source: current?.source ?? 'personal',
        catalogKey: current?.catalogKey ?? null,
        kinds: current?.kinds ?? ['chat', 'report', 'slides'],
        version: current?.version ?? '1.0.0',
        estimatedTokens: current?.estimatedTokens ?? 0,
        installs: current?.installs ?? 0,
        originId: current?.originId ?? null,
        updatedAt: new Date().toISOString(),
        ...draft,
        whenToUse: ownWhen ? draft.whenToUse : draft.description,
      })
      setDone(true)
      onSaved(saved)
    } catch (err) {
      setError(errorMessage(err, t('저장하지 못했습니다.')))
    } finally {
      setSaving(false)
    }
  }

  return (
    <form
      className="space-y-4"
      data-testid="skill-form"
      onSubmit={(e) => {
        e.preventDefault()
        void save()
      }}
    >
      {error && (
        <p role="status" className="rounded-control border border-danger/30 bg-danger/5 px-3 py-2 text-base text-danger">
          {error}
        </p>
      )}
      <Field label={t('이름')}>
        <Input
          value={draft.name}
          maxLength={NAME_LIMIT}
          onChange={(e) => edit({ name: e.target.value })}
          placeholder={t('예: 주간 현황 보고')}
        />
      </Field>
      <Field label={t('설명')} hint={t('무엇을 하는지, 언제 쓰는지. 모델이 이 설명을 보고 스킬을 고릅니다.')}>
        <Textarea
          rows={2}
          value={draft.description}
          onChange={(e) => edit({ description: e.target.value })}
          placeholder={t('예: 지난주 작업을 성과·막힌 점·다음 할 일로 정리합니다. 진행 상황 요약을 부탁받았을 때 씁니다.')}
        />
      </Field>
      <LineNumberedEditor
        label={t('절차')}
        value={draft.body}
        onChange={(body) => edit({ body })}
        placeholder={t('1. 입력 자료와 판단 기준을 확인한다\n2. 결과와 근거, 미확인 항목을 구분한다')}
        rows={current ? 14 : 16}
      />

      <Group title={t('더 설정하기')} hint={current ? t('사용 시점, 필요한 도구') : t('사용 시점')}>
        <Field label={t('사용 시점')} hint={t('비워 두면 설명을 그대로 씁니다.')}>
          <Textarea
            rows={2}
            value={ownWhen ? draft.whenToUse : ''}
            onChange={(e) => {
              setOwnWhen(e.target.value.trim() !== '')
              edit({ whenToUse: e.target.value })
            }}
            placeholder={draft.description || t('사용자가 의사결정 자료를 붙여넣고 리스크 검토를 요청할 때')}
          />
        </Field>
        {current && (
          <Block label={t('필요한 도구')} hint={t('고른 도구를 지금 쓸 수 있을 때만 이 스킬을 실행합니다.')}>
            <ToolPicker
              value={draft.requiredTools}
              onChange={(next) => edit({ requiredTools: next ?? [] })}
              tools={availableTools}
              inheritable={false}
            />
          </Block>
        )}
      </Group>

      <div className={cn('sticky bottom-0 z-10 -mx-4 flex flex-wrap items-center gap-2 border-t border-line bg-bg/95 px-4 py-3 sm:-mx-6 sm:px-6', aboveBottomBar && 'max-sm:bottom-[4.25rem]')}>
        <span className="mr-auto flex items-center gap-1">
          <Switch checked={!draft.enabled} onChange={(v) => edit({ enabled: !v })} label={t('초안')} />
          <span className="text-sm text-muted">
            {draft.enabled ? t('초안') : t('초안 · 아직 입력창에 안 나와요')}
          </span>
        </span>
        {done && current && (
          <span role="status" className="text-sm text-success">
            {t('저장했습니다.')}
          </span>
        )}
        {onCancel && (
          <Button type="button" onClick={onCancel}>
            {t('취소')}
          </Button>
        )}
        <Button type="submit" variant="primary" disabled={saving || !draft.name.trim()}>
          {saving ? t('저장 중…') : current ? t('저장') : t('만들기')}
        </Button>
      </div>
    </form>
  )
}
