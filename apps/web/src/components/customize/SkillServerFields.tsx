import { ChevronRight, FileCode } from 'lucide-react'
import { useEffect, useState } from 'react'
import { Badge, Field, Input, Select, Switch } from '@/components/ui'
import { skillsApi, type ServerRef, type SkillServerCatalogue } from '@/lib/api'
import { useT } from '@/lib/useT'
import { cn } from '@/lib/utils'
import type { SkillScript, SkillServer } from '@/types'
import { GapQuestionsEditor } from '@/components/agents/GapQuestionsEditor'
import { useStore } from '@/store/useStore'
import { Block } from './AgentForm'

let cached: Promise<SkillServerCatalogue> | null = null
/** Shipped scripts and checks a skill may name; read once per page load. */
function useServerCatalogue(): SkillServerCatalogue | null {
  const [value, setValue] = useState<SkillServerCatalogue | null>(null)
  useEffect(() => {
    cached ??= skillsApi.serverCatalogue()
    let live = true
    cached.then((v) => live && setValue(v)).catch(() => {
      cached = null
    })
    return () => {
      live = false
    }
  }, [])
  return value
}

const toggle = (list: string[], item: string) => (list.includes(item) ? list.filter((x) => x !== item) : [...list, item])

/** Checkbox per shipped script: skill name, reference, purpose. */
function RefList({
  refs,
  value,
  onChange,
  testId,
}: {
  refs: ServerRef[]
  value: string[]
  onChange: (next: string[]) => void
  testId: string
}) {
  const t = useT()
  return (
    <ul className="grid gap-1.5 sm:grid-cols-2" data-testid={testId}>
      {refs.map((r) => {
        const id = `${testId}-${r.ref}`
        return (
          <li key={r.ref} className="flex min-w-0 items-start gap-2 rounded-control border border-line px-2.5 py-2">
            <input
              id={id}
              type="checkbox"
              checked={value.includes(r.ref)}
              onChange={() => onChange(toggle(value, r.ref))}
              className="mt-1 size-4 shrink-0 accent-[var(--accent)]"
            />
            <label htmlFor={id} className="min-w-0 cursor-pointer">
              <span className="block text-base text-fg">{t(r.skill)}</span>
              <span className="block truncate font-mono text-xs text-faint">{r.ref}</span>
              {r.summary && <span className="block text-sm text-muted">{r.summary}</span>}
            </label>
          </li>
        )
      })}
    </ul>
  )
}

function SwitchRow({ checked, onChange, label }: { checked: boolean; onChange: (v: boolean) => void; label: string }) {
  return (
    <div className="flex items-center gap-2">
      <Switch checked={checked} onChange={onChange} label={label} />
      <span className="text-base text-fg" aria-hidden>
        {label}
      </span>
    </div>
  )
}

/** One of the person's scripts: name, purpose, code behind 「코드 보기」. */
export function ScriptRow({ script }: { script: SkillScript }) {
  const t = useT()
  const [open, setOpen] = useState(false)
  return (
    <li className="rounded-control border border-line" data-testid="skill-script">
      <div className="flex min-w-0 items-start gap-2 px-2.5 py-2">
        <FileCode size={15} aria-hidden className="mt-1 shrink-0 text-faint" />
        <div className="min-w-0 flex-1">
          <p className="truncate font-mono text-sm text-fg">{`scripts/${script.name}.py`}</p>
          {script.summary && <p className="text-sm text-muted">{script.summary}</p>}
        </div>
        <span className="shrink-0 text-xs text-faint">{`${Math.max(1, Math.round(script.size / 1024))}KB`}</span>
        {script.source && (
          <button
            type="button"
            aria-expanded={open}
            onClick={() => setOpen(!open)}
            className="flex shrink-0 items-center gap-0.5 text-sm text-muted hover:text-fg"
          >
            <ChevronRight size={13} aria-hidden className={cn('transition-transform', open && 'rotate-90')} />
            {t('코드 보기')}
          </button>
        )}
      </div>
      {open && script.source && (
        <pre className="max-h-72 overflow-auto border-t border-line bg-elevated px-3 py-2 font-mono text-xs leading-5 text-fg">
          {script.source}
        </pre>
      )}
    </li>
  )
}

/** 「서버가 돌리는 점검」: shipped scripts and checks only; own scripts only via sandboxed `run_skill_script`. */
export function SkillServerFields({
  value,
  onChange,
  scripts,
}: {
  value: SkillServer
  onChange: (next: SkillServer) => void
  /** Own uploaded scripts. */
  scripts: SkillScript[]
}) {
  const t = useT()
  const catalogue = useServerCatalogue()
  const set = (patch: Partial<SkillServer>) => onChange({ ...value, ...patch })
  const focus = typeof value.postJudge === 'string' ? value.postJudge : ''
  const judging = value.postJudge !== false && value.postJudge !== ''
  if (!catalogue) return <p className="text-sm text-faint">{t('불러오는 중…')}</p>
  const ownRefs: ServerRef[] = scripts.map((s) => ({ ref: s.name, skill: t('직접 올린 스크립트'), summary: s.summary }))
  return (
    <div className="space-y-4" data-testid="skill-server">
      <Block
        label={t('답 점검 스크립트')}
        hint={t('이 스킬로 쓴 챗 답을 서버가 기본 제공 스크립트로 점검하고, 코드로 고칠 수 있는 것은 고칩니다.')}
      >
        <RefList refs={catalogue.postChecks} value={value.postCheck} onChange={(postCheck) => set({ postCheck })} testId="skill-post-check" />
      </Block>
      <div className="space-y-2">
        <SwitchRow
          checked={judging}
          onChange={(on) => set({ postJudge: on ? focus || true : false })}
          label={t('자료에 없는 문장도 모델이 찾기')}
        />
        {judging && (
          <Field label={t('특히 볼 것')} hint={t('비워 두면 모든 사실 문장을 봅니다.')}>
            <Input
              value={focus}
              maxLength={400}
              onChange={(e) => set({ postJudge: e.target.value || true })}
              placeholder={t('예: 가격·일정·담당자처럼 자료에 없는 약속')}
            />
          </Field>
        )}
        <SwitchRow checked={value.tables} onChange={(tables) => set({ tables })} label={t('비교표 칸마다 출처 확인')} />
      </div>
      <div className="space-y-2">
        <SwitchRow
          checked={value.copyCheck}
          onChange={(copyCheck) => set({ copyCheck, copyKinds: copyCheck ? value.copyKinds : [] })}
          label={t('카피로 점검하기')}
        />
        {value.copyCheck && (
          <Block label={t('이것만 점검')} hint={t('하나도 고르지 않으면 모두 점검합니다.')}>
            <div className="flex flex-wrap gap-1.5" data-testid="skill-copy-kinds">
              {catalogue.copyKinds.map((k) => (
                <button
                  key={k.kind}
                  type="button"
                  aria-pressed={value.copyKinds.includes(k.kind)}
                  onClick={() => set({ copyKinds: toggle(value.copyKinds, k.kind) })}
                  className={cn(
                    'rounded-full border px-3 py-1 text-sm',
                    value.copyKinds.includes(k.kind) ? 'border-accent bg-accent-soft text-accent' : 'border-line text-muted',
                  )}
                >
                  {t(k.label)}
                </button>
              ))}
            </div>
          </Block>
        )}
      </div>
      <div className="grid gap-3 sm:grid-cols-2">
        <Field label={t('검사 스킬로 쓰기')} hint={t('에이전트의 「내 검사 스킬」에서 켜면 쓰기 전·후에 서버가 돌립니다.')}>
          <Select
            value={value.stage}
            onChange={(e) => {
              const stage = e.target.value as SkillServer['stage']
              set({ stage, entry: stage ? (catalogue.entries[stage][0]?.ref ?? '') : '', ownStage: false })
            }}
          >
            <option value="">{t('쓰지 않음')}</option>
            <option value="pre">{t('쓰기 전에 (빠진 정보 묻기)')}</option>
            <option value="post">{t('다 쓴 뒤에 (고치고 다시 확인)')}</option>
          </Select>
        </Field>
        {value.stage && (
          <Field label={t('돌릴 검사')}>
            <Select value={value.entry} onChange={(e) => set({ entry: e.target.value })} data-testid="skill-entry">
              {catalogue.entries[value.stage].map((r) => (
                <option key={r.ref} value={r.ref}>
                  {`${t(r.skill)} · ${r.ref}`}
                </option>
              ))}
            </Select>
          </Field>
        )}
      </div>
      <Block
        label={t('스킬이 실행할 스크립트')}
        hint={t('지침에서 run_skill_script 로 부르는 스크립트입니다. 직접 올린 스크립트는 코드 실행 샌드박스에서만 돕니다.')}
      >
        <RefList refs={[...ownRefs, ...catalogue.scripts]} value={value.uses} onChange={(uses) => set({ uses })} testId="skill-uses" />
      </Block>
      {scripts.length > 0 && (
        <Block label={t('직접 올린 스크립트')} hint={t('서버는 이 코드를 불러오지 않습니다. 샌드박스(30초·1GB·서버 파일 없음)에서만 실행합니다.')}>
          <ul className="space-y-1.5">
            {scripts.map((s) => (
              <ScriptRow key={s.name} script={s} />
            ))}
          </ul>
        </Block>
      )}
      <DocumentFields value={value} set={set} catalogue={catalogue} />
      {value.stage === '' && value.postCheck.length === 0 && !value.copyCheck && !judging && value.uses.length === 0 &&
        !value.template && !(value.checks?.length) && !(value.questions?.length) && (
        <p className="text-sm text-faint">
          <Badge>{t('없음')}</Badge> {t('서버는 이 스킬에 아무것도 돌리지 않습니다.')}
        </p>
      )}
    </div>
  )
}

/** What the skill brings to a turn it is on, as an agent's own fields do: 템플릿, 검사, 빠진 정보 질문, 진행 설정. */
function DocumentFields({
  value,
  set,
  catalogue,
}: {
  value: SkillServer
  set: (patch: Partial<SkillServer>) => void
  catalogue: SkillServerCatalogue
}) {
  const t = useT()
  const templates = useStore((s) => s.designTemplates).filter((row) => row.surface === 'report' || row.surface === 'slides')
  const process = value.process ?? {}
  const setProcess = (patch: Partial<NonNullable<SkillServer['process']>>) => {
    const next: Record<string, unknown> = { ...process, ...patch }
    for (const key of Object.keys(next)) if (next[key] === undefined) delete next[key]
    set({ process: next as SkillServer['process'] })
  }
  return (
    <div className="space-y-4 border-t border-line pt-4" data-testid="skill-document-fields">
      <Field label={t('쓸 템플릿')} hint={t('보고서·발표에서 이 스킬을 켜고 템플릿을 고르지 않았으면 이 템플릿에 씁니다.')}>
        <Select value={value.template ?? ''} onChange={(e) => set({ template: e.target.value })} data-testid="skill-template">
          <option value="">{t('정하지 않음')}</option>
          {templates.map((row) => (
            <option key={row.id} value={row.id}>
              {t(row.name)}
            </option>
          ))}
        </Select>
      </Field>
      {catalogue.checks && (
        <Block label={t('켜면 붙는 검사')} hint={t('이 스킬을 켠 대화에서 서버가 쓰기 전·후에 돌립니다.')}>
          <RefList refs={catalogue.checks} value={value.checks ?? []} onChange={(checks) => set({ checks })} testId="skill-checks" />
        </Block>
      )}
      {(catalogue.checkOptions ?? []).map((o) => (
        <SwitchRow
          key={o.option}
          checked={(value.checkOptions ?? []).includes(o.option)}
          onChange={() => set({ checkOptions: toggle(value.checkOptions ?? [], o.option) })}
          label={t(o.label)}
        />
      ))}
      <div className="grid gap-3 sm:grid-cols-2">
        <Field label={t('웹 검색')}>
          <Select
            value={process.search === undefined ? '' : process.search ? 'on' : 'off'}
            onChange={(e) => setProcess({ search: e.target.value === '' ? undefined : e.target.value === 'on' })}
            data-testid="skill-search"
          >
            <option value="">{t('템플릿 설정을 따름')}</option>
            <option value="on">{t('쓰기 전에 찾아봄')}</option>
            <option value="off">{t('찾지 않음(자료만)')}</option>
          </Select>
        </Field>
        <Field label={t('그림')}>
          <Select
            value={process.draws ?? ''}
            onChange={(e) => setProcess({ draws: (e.target.value || undefined) as 'auto' | 'when-asked' | undefined })}
            data-testid="skill-draws"
          >
            <option value="">{t('템플릿 설정을 따름')}</option>
            <option value="auto">{t('알맞으면 넣음')}</option>
            <option value="when-asked">{t('요청할 때만')}</option>
          </Select>
        </Field>
      </div>
      <Block label={t('빠진 정보 질문')} hint={t('「빠진 정보 묻기」 검사가 붙어 있을 때, 자료가 답하지 않으면 쓰기 전에 묻습니다.')}>
        <GapQuestionsEditor
          value={value.questions ?? []}
          onChange={(questions) => set({ questions })}
          detectors={catalogue.gapDetectors ?? []}
        />
      </Block>
    </div>
  )
}
