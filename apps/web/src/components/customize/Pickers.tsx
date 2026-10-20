import { ChevronDown, Search, X } from 'lucide-react'
import { useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import { Link } from 'react-router-dom'
import { Input } from '@/components/ui'
import { directionParticle } from '@/lib/formCheck'
import { kindMeta, skillsOffSurface } from '@/lib/kinds'
import { isListed } from '@/lib/listed'
import { useT } from '@/lib/useT'
import { cn } from '@/lib/utils'
import type { SessionKind, Skill, StoreSkill, ToolCatalogEntry } from '@/types'
import { Markdown } from '@/components/chat/Markdown'

const BUILTIN = '기본 도구'

interface Option {
  id: string
  title: string
  description: string
  group: string
  disabled?: boolean
  /** Why it cannot be picked. */
  why?: string
  /** Raw identifier: hover and 「기술 정보」 only. */
  tech?: string
}

/** 「기본값 따르기」 / 「직접 고르기」 as pressed-button pair. */
function ModeSwitch({
  inherit,
  labels,
  onInherit,
  onPick,
}: {
  inherit: boolean
  labels: [string, string]
  onInherit: () => void
  onPick: () => void
}) {
  return (
    <div className="inline-flex rounded-control border border-line bg-elevated p-0.5" role="group">
      {[
        { on: inherit, label: labels[0], act: onInherit },
        { on: !inherit, label: labels[1], act: onPick },
      ].map((b) => (
        <button
          key={b.label}
          type="button"
          aria-pressed={b.on}
          onClick={b.act}
          className={cn(
            'rounded-control px-3 py-1 text-sm font-medium transition-colors',
            b.on ? 'bg-panel text-fg shadow-raised' : 'text-muted hover:text-fg',
          )}
        >
          {b.label}
        </button>
      ))}
    </div>
  )
}

/** Searchable grouped checklist under its button. */
function PickerPanel({
  label,
  options,
  chosen,
  onToggle,
  searchLabel,
  empty,
}: {
  label: string
  options: Option[]
  chosen: string[]
  onToggle: (id: string) => void
  searchLabel: string
  /** Shown under an empty search instead of 「검색 결과가 없습니다」 when nothing is listed. */
  empty?: ReactNode
}) {
  const t = useT()
  const [open, setOpen] = useState(false)
  const [query, setQuery] = useState('')
  const ref = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!open) return
    const onDown = (e: MouseEvent) => {
      if (!ref.current?.contains(e.target as Node)) setOpen(false)
    }
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        e.stopPropagation()
        setOpen(false)
      }
    }
    document.addEventListener('mousedown', onDown)
    document.addEventListener('keydown', onKey, true)
    return () => {
      document.removeEventListener('mousedown', onDown)
      document.removeEventListener('keydown', onKey, true)
    }
  }, [open])

  const needle = query.trim().toLowerCase()
  const groups = useMemo(() => {
    const out = new Map<string, Option[]>()
    for (const o of options) {
      if (needle && !`${o.title} ${o.description} ${o.group}`.toLowerCase().includes(needle)) continue
      const list = out.get(o.group)
      if (list) list.push(o)
      else out.set(o.group, [o])
    }
    return [...out.entries()]
  }, [options, needle])

  return (
    <div ref={ref} className="relative">
      <button
        type="button"
        aria-expanded={open}
        onClick={() => setOpen((v) => !v)}
        className="inline-flex h-8 items-center gap-1.5 rounded-control border border-line bg-panel px-2.5 text-sm font-medium text-fg hover:bg-elevated"
      >
        {label}
        <ChevronDown size={14} className="text-faint" />
      </button>
      {open && (
        <div
          role="dialog"
          aria-label={label}
          className="animate-fade-up absolute left-0 z-[120] mt-1.5 w-[min(26rem,calc(100vw-2rem))] rounded-card border border-line bg-panel p-2 shadow-overlay"
        >
          <div className="relative mb-2">
            <Search size={13} className="pointer-events-none absolute top-1/2 left-2.5 -translate-y-1/2 text-faint" />
            <Input
              autoFocus
              aria-label={searchLabel}
              placeholder={searchLabel}
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              className="h-8 pl-8 text-sm"
            />
          </div>
          <div className="max-h-72 space-y-2 overflow-y-auto">
            {groups.map(([group, rows]) => (
              <fieldset key={group}>
                <legend className="px-1.5 pb-1 text-xs font-semibold text-faint">{t(group)}</legend>
                {rows.map((o) => {
                  const on = chosen.includes(o.id)
                  return (
                    <label
                      key={o.id}
                      title={o.tech ?? o.why}
                      className={cn(
                        'flex cursor-pointer items-start gap-2.5 rounded-control px-1.5 py-1.5 hover:bg-elevated',
                        o.disabled && !on && 'cursor-not-allowed opacity-55',
                      )}
                    >
                      <input
                        type="checkbox"
                        checked={on}
                        disabled={o.disabled && !on}
                        onChange={() => onToggle(o.id)}
                        className="mt-0.5 size-4 shrink-0 accent-[var(--color-accent)]"
                      />
                      <span className="min-w-0">
                        <span className="block text-base text-fg">{t(o.title)}</span>
                        {(o.description || (o.disabled && o.why)) && (
                          <span className="block text-sm text-muted">{o.disabled && !on && o.why ? o.why : t(o.description)}</span>
                        )}
                      </span>
                    </label>
                  )
                })}
              </fieldset>
            ))}
            {groups.length === 0 &&
              (options.length === 0 && empty ? (
                <div className="px-1.5 py-3 text-sm text-faint">{empty}</div>
              ) : (
                <p className="px-1.5 py-3 text-sm text-faint">{t('검색 결과가 없습니다')}</p>
              ))}
          </div>
        </div>
      )}
    </div>
  )
}

/** Chosen chips with remove buttons. */
function ChosenChips({ options, chosen, onRemove }: { options: Option[]; chosen: string[]; onRemove: (id: string) => void }) {
  const t = useT()
  if (chosen.length === 0) return null
  return (
    <div className="flex flex-wrap gap-1.5">
      {chosen.map((id) => {
        const o = options.find((x) => x.id === id)
        const title = o ? t(o.title) : t('알 수 없는 항목')
        return (
          <span
            key={id}
            title={o?.tech}
            className="inline-flex items-center gap-1 rounded-full border border-accent/25 bg-accent-soft py-0.5 pr-1 pl-2.5 text-sm text-accent"
          >
            {title}
            <button
              type="button"
              aria-label={t('{name} 빼기').replace('{name}', title)}
              onClick={() => onRemove(id)}
              className="grid size-5 place-items-center rounded-full hover:bg-accent/15"
            >
              <X size={12} />
            </button>
          </span>
        )
      })}
    </div>
  )
}

/** Display name of a tool row, for servers without `title` and `group`. */
function toolOption(tool: ToolCatalogEntry, hasKnowledge = false, t: (s: string) => string = (s) => s): Option {
  const connector = tool.name.startsWith('mcp__')
  const runtime = tool.name === 'search_knowledge' ? hasKnowledge : tool.available
  return {
    id: tool.name,
    title: tool.title || (connector ? tool.label.split(' · ').slice(1).join(' · ') || tool.label : tool.label),
    description: tool.description ?? '',
    group: tool.group || (connector ? tool.label.split(' · ')[0] || '커넥터' : BUILTIN),
    disabled: !runtime,
    why:
      tool.name === 'search_knowledge'
        ? t('에이전트에 읽을 수 있는 지식 문서를 먼저 추가하세요.')
        : t('현재 사용할 수 없는 도구입니다.'),
    tech: tool.name,
  }
}

/** 기본 도구 first, then connectors by name. */
function orderTools(options: Option[]): Option[] {
  return [...options].sort((a, b) =>
    a.group === b.group ? 0 : a.group === BUILTIN ? -1 : b.group === BUILTIN ? 1 : a.group.localeCompare(b.group),
  )
}

/** Agent tools (null inherits, list is a hard allowlist) or skill's required tools (`inheritable` false). */
export function ToolPicker({
  value,
  onChange,
  tools,
  hasKnowledge = false,
  inheritable = true,
}: {
  value: string[] | null
  onChange: (next: string[] | null) => void
  tools: ToolCatalogEntry[]
  hasKnowledge?: boolean
  inheritable?: boolean
}) {
  const t = useT()
  const chosen = value ?? []
  const options = orderTools([
    ...tools.map((tool) => toolOption(tool, hasKnowledge, t)),
    ...chosen
      .filter((name) => !tools.some((tool) => tool.name === name))
      .map((name) => toolOption({ name, label: name, available: false }, hasKnowledge, t)),
  ])
  const toggle = (id: string) => onChange(chosen.includes(id) ? chosen.filter((x) => x !== id) : [...chosen, id])
  const picking = !inheritable || value !== null

  return (
    <div className="space-y-2" data-testid="tool-picker">
      {inheritable && (
        <ModeSwitch
          inherit={value === null}
          labels={[t('기본값 따르기'), t('직접 고르기')]}
          onInherit={() => onChange(null)}
          onPick={() => onChange(value ?? [])}
        />
      )}
      {inheritable && value === null && (
        <p className="text-sm text-faint">{t('이 대화에서 쓸 수 있는 도구를 그대로 씁니다.')}</p>
      )}
      {picking && (
        <>
          <ChosenChips options={options} chosen={chosen} onRemove={toggle} />
          {inheritable && chosen.length === 0 && <p className="text-sm text-faint">{t('도구를 쓰지 않습니다.')}</p>}
          <PickerPanel
            label={t('도구 고르기')}
            options={options}
            chosen={chosen}
            onToggle={toggle}
            searchLabel={t('도구 검색')}
          />
          {chosen.length > 0 && (
            <details className="text-sm text-faint">
              <summary className="cursor-pointer select-none hover:text-fg">{t('기술 정보')}</summary>
              <ul className="mt-1 space-y-0.5 font-mono text-xs">
                {chosen.map((id) => (
                  <li key={id}>{id}</li>
                ))}
              </ul>
            </details>
          )}
        </>
      )}
    </div>
  )
}

/** Says which chosen skills none of the agent's surfaces will run, and why. */
export function OffSurfaceNote({ ids, skills, surfaces }: { ids: string[]; skills: Skill[]; surfaces: SessionKind[] }) {
  const t = useT()
  const off = skillsOffSurface(ids, skills, surfaces)
  if (off.length === 0) return null
  const words = (kinds: SessionKind[]) => kinds.map((k) => t(kindMeta[k].label)).join('·')
  return (
    <ul role="status" data-testid="skill-surface-warning" className="space-y-1 rounded-control border border-warn/30 bg-warn/10 px-3 py-2 text-sm text-warn">
      {off.map((s) => (
        <li key={s.id}>
          {t('「{name}」 스킬은 {skill} 화면에서만 쓰여 이 에이전트의 화면({agent})에서는 적용되지 않습니다. 고급 › 사용할 화면에서 {skill} 화면을 더하거나 스킬을 빼세요.')
            .replace('{name}', s.name)
            .replaceAll('{skill}', words(s.kinds))
            .replace('{agent}', words(surfaces))}
        </li>
      ))}
    </ul>
  )
}

/** Kept-on skills that run on one of the agent's screens but not the one it opens on: said
 * there, since a new conversation would start where they do nothing. */
export function StartSurfaceNote({
  ids,
  skills,
  surfaces,
  start,
}: {
  ids: string[]
  skills: Skill[]
  surfaces: SessionKind[]
  start: SessionKind
}) {
  const t = useT()
  const shown = ids
    .map((id) => skills.find((s) => s.id === id))
    .filter((s): s is Skill => !!s && s.kinds.length > 0 && s.kinds.some((k) => surfaces.includes(k)))
  if (shown.length === 0) return null
  const words = (kinds: SessionKind[]) => kinds.map((k) => t(kindMeta[k].label)).join('·')
  const off = shown.filter((s) => !s.kinds.includes(start))
  return (
    <ul
      role="status"
      data-testid="skill-start-note"
      className={cn(
        'space-y-1 rounded-control border px-3 py-2 text-sm',
        off.length > 0 ? 'border-warn/30 bg-warn/10 text-warn' : 'border-line bg-elevated text-muted',
      )}
    >
      {shown.map((s) => (
        <li key={s.id}>
          {s.kinds.includes(start)
            ? t('「{name}」 스킬은 {skill} 화면에서 작동합니다. 이 에이전트는 {start} 화면에서 시작합니다.')
                .replace('{name}', s.name)
                .replace('{skill}', words(s.kinds))
                .replace('{start}', t(kindMeta[start].label))
            : t('「{name}」 스킬은 {skill} 화면에서만 작동해, {start} 화면에서 시작하는 새 대화에서는 쓰이지 않습니다. 고급 › 처음 여는 화면을 {skill}{로} 바꾸세요.')
                .replace('{name}', s.name)
                .replaceAll('{skill}', words(s.kinds))
                .replace('{로}', directionParticle(words(s.kinds)))
                .replace('{start}', t(kindMeta[start].label))}
        </li>
      ))}
    </ul>
  )
}

/** Kept-on skills per agent, and their estimated tokens together (`workspace_context.py`). */
export const AGENT_SKILL_MAX = 6
export const AGENT_SKILL_TOKENS = 8000

/** Nothing to add: where skills come from. */
function NoSkills() {
  const t = useT()
  return (
    <Link to="/customize?tab=skills&view=explore" className="text-accent hover:underline">
      {t('탐색에서 스킬 추가')}
    </Link>
  )
}

/** A skill by id: the person's own, else a store row not installed yet (installed on save). */
function skillById(id: string, skills: Skill[], store: StoreSkill[]): { skill: Skill; pending: boolean } | null {
  const own = skills.find((s) => s.id === id)
  if (own) return { skill: own, pending: false }
  const row = store.find((s) => s.id === id)
  return row ? { skill: row, pending: true } : null
}

/** 「항상 적용」 / 「필요할 때」 for one skill. */
function ApplyToggle({
  name,
  pinned,
  pinWhy,
  onPin,
  onUnpin,
}: {
  name: string
  pinned: boolean
  /** Why it cannot be pinned now; empty when it can. */
  pinWhy: string
  onPin: () => void
  onUnpin: () => void
}) {
  const t = useT()
  const options = [
    { on: pinned, label: t('항상 적용'), act: onPin, disabled: !pinned && !!pinWhy, title: pinned ? undefined : pinWhy || undefined },
    { on: !pinned, label: t('필요할 때'), act: onUnpin, disabled: false, title: undefined },
  ]
  return (
    <div
      role="radiogroup"
      aria-label={t('{name} 적용 방식').replace('{name}', name)}
      className="inline-flex shrink-0 rounded-control border border-line bg-elevated p-0.5"
    >
      {options.map((o) => (
        <button
          key={o.label}
          type="button"
          role="radio"
          aria-checked={o.on}
          disabled={o.disabled}
          title={o.title}
          onClick={() => !o.on && o.act()}
          className={cn(
            'rounded-control px-2.5 py-1 text-sm font-medium transition-colors disabled:cursor-not-allowed disabled:opacity-50',
            o.on ? 'bg-panel text-fg shadow-raised' : 'text-muted hover:text-fg',
          )}
        >
          {o.label}
        </button>
      ))}
    </div>
  )
}

/** 「+ 스킬 추가 (스토어 포함)」: the person's skills not listed yet, then store skills to install. */
function AddSkillPanel({
  skills,
  store,
  listed,
  onAdd,
  onInstall,
}: {
  skills: Skill[]
  store: StoreSkill[]
  listed: string[]
  onAdd: (id: string) => void
  onInstall: (storeId: string) => Promise<string | null>
}) {
  const t = useT()
  const [open, setOpen] = useState(false)
  const [query, setQuery] = useState('')
  const [installing, setInstalling] = useState<string | null>(null)
  const ref = useRef<HTMLDivElement>(null)
  useEffect(() => {
    if (!open) return
    const onDown = (e: MouseEvent) => {
      if (!ref.current?.contains(e.target as Node)) setOpen(false)
    }
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        e.stopPropagation()
        setOpen(false)
      }
    }
    document.addEventListener('mousedown', onDown)
    document.addEventListener('keydown', onKey, true)
    return () => {
      document.removeEventListener('mousedown', onDown)
      document.removeEventListener('keydown', onKey, true)
    }
  }, [open])
  const needle = query.trim().toLowerCase()
  const hit = (s: Skill) => !needle || `${s.name} ${s.description}`.toLowerCase().includes(needle)
  const mine = skills.filter((s) => s.enabled && isListed(s) && !listed.includes(s.id) && hit(s))
  const toInstall = store.filter(
    (s) =>
      s.enabled &&
      isListed(s) &&
      !listed.includes(s.id) &&
      !skills.some((m) => m.id === s.id || m.originId === s.id) &&
      hit(s),
  )
  const install = (id: string) => {
    if (installing) return
    setInstalling(id)
    void onInstall(id)
      .then((copy) => copy && onAdd(copy))
      .finally(() => setInstalling(null))
  }
  const row = (s: Skill, action: ReactNode) => (
    <li key={s.id} className="flex items-start gap-2 rounded-control px-1.5 py-1.5 hover:bg-elevated">
      <span className="min-w-0 flex-1">
        <span className="block text-base text-fg">{t(s.name)}</span>
        {s.description && <span className="block text-sm text-muted">{t(s.description)}</span>}
      </span>
      {action}
    </li>
  )
  const button = 'shrink-0 rounded-control border border-line bg-panel px-2 py-0.5 text-sm font-medium hover:bg-elevated disabled:opacity-50'
  return (
    <div ref={ref} className="relative">
      <button
        type="button"
        aria-expanded={open}
        onClick={() => setOpen((v) => !v)}
        className="inline-flex h-8 items-center gap-1.5 rounded-control border border-dashed border-line-strong bg-panel px-2.5 text-sm font-medium text-fg hover:bg-elevated"
      >
        {t('+ 스킬 추가 (스토어 포함)')}
      </button>
      {open && (
        <div
          role="dialog"
          aria-label={t('스킬 추가')}
          className="animate-fade-up absolute left-0 z-[120] mt-1.5 w-[min(28rem,calc(100vw-2rem))] rounded-card border border-line bg-panel p-2 shadow-overlay"
        >
          <div className="relative mb-2">
            <Search size={13} className="pointer-events-none absolute top-1/2 left-2.5 -translate-y-1/2 text-faint" />
            <Input
              autoFocus
              aria-label={t('스킬 검색')}
              placeholder={t('스킬 검색')}
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              className="h-8 pl-8 text-sm"
            />
          </div>
          <div className="max-h-72 space-y-2 overflow-y-auto">
            {mine.length > 0 && (
              <section aria-label={t('내 스킬')}>
                <p className="px-1.5 pb-1 text-xs font-semibold text-faint">{t('내 스킬')}</p>
                <ul>
                  {mine.map((s) =>
                    row(
                      s,
                      <button type="button" className={button} onClick={() => onAdd(s.id)}>
                        {t('추가')}
                      </button>,
                    ),
                  )}
                </ul>
              </section>
            )}
            {toInstall.length > 0 && (
              <section aria-label={t('스토어')}>
                <p className="px-1.5 pb-1 text-xs font-semibold text-faint">{t('스토어 · 설치하면 내 스킬에 복사됩니다')}</p>
                <ul>
                  {toInstall.map((s) =>
                    row(
                      s,
                      <button type="button" className={button} disabled={installing !== null} onClick={() => install(s.id)}>
                        {installing === s.id ? t('설치하는 중…') : t('설치하고 추가')}
                      </button>,
                    ),
                  )}
                </ul>
              </section>
            )}
            {mine.length === 0 && toInstall.length === 0 && (
              <div className="px-1.5 py-3 text-sm text-faint">{needle ? t('검색 결과가 없습니다') : <NoSkills />}</div>
            )}
          </div>
        </div>
      )}
    </div>
  )
}

/** The agent's skills, each 「항상 적용」 (`activeSkillIds`, every turn) or 「필요할 때」 (offered to
 * the model through use_skill). 「직접 고르기」 makes the list the agent's allowlist (`skillIds`). */
export function AgentSkillList({
  skillIds,
  activeSkillIds,
  onChange,
  skills,
  store = [],
  onInstall,
}: {
  skillIds: string[] | null
  activeSkillIds: string[]
  onChange: (next: { skillIds: string[] | null; activeSkillIds: string[] }) => void
  skills: Skill[]
  store?: StoreSkill[]
  /** Installs a store skill; resolves to the person's copy. */
  onInstall: (storeId: string) => Promise<string | null>
}) {
  const t = useT()
  // 「필요할 때」 rows under 「기본값 따르기」: shown while editing, every skill is offered anyway.
  const [loose, setLoose] = useState<string[]>([])
  const inherit = skillIds === null
  const ids = [...new Set([...activeSkillIds, ...(skillIds ?? []), ...(inherit ? loose : [])])]
  const tokensOf = (id: string) => skillById(id, skills, store)?.skill.estimatedTokens ?? 0
  const pinnedTokens = activeSkillIds.reduce((sum, id) => sum + tokensOf(id), 0)
  const pinWhy = (id: string) =>
    activeSkillIds.length >= AGENT_SKILL_MAX
      ? t('항상 적용은 {n}개까지입니다.').replace('{n}', String(AGENT_SKILL_MAX))
      : activeSkillIds.length > 0 && pinnedTokens + tokensOf(id) > AGENT_SKILL_TOKENS
        ? t('항상 적용 스킬은 합계 약 {n} 토큰까지입니다.').replace('{n}', AGENT_SKILL_TOKENS.toLocaleString())
        : ''
  const listWith = (id: string) => (skillIds === null || skillIds.includes(id) ? skillIds : [...skillIds, id])
  const pin = (id: string) => {
    setLoose((rows) => rows.filter((x) => x !== id))
    onChange({ skillIds: listWith(id), activeSkillIds: [...activeSkillIds, id] })
  }
  const unpin = (id: string) => {
    if (inherit) setLoose((rows) => [...rows.filter((x) => x !== id), id])
    onChange({ skillIds: listWith(id), activeSkillIds: activeSkillIds.filter((x) => x !== id) })
  }
  const remove = (id: string) => {
    setLoose((rows) => rows.filter((x) => x !== id))
    onChange({
      skillIds: skillIds === null ? null : skillIds.filter((x) => x !== id),
      activeSkillIds: activeSkillIds.filter((x) => x !== id),
    })
  }
  // Under 「기본값 따르기」 an added skill is one to keep on (all are offered when needed already).
  const add = (id: string) => {
    if (ids.includes(id)) return
    if (inherit && !pinWhy(id)) pin(id)
    else if (inherit) setLoose((rows) => [...rows, id])
    else onChange({ skillIds: [...(skillIds ?? []), id], activeSkillIds })
  }

  return (
    <div className="space-y-3" data-testid="agent-skill-list">
      <ModeSwitch
        inherit={inherit}
        labels={[t('기본값 따르기'), t('직접 고르기')]}
        onInherit={() => onChange({ skillIds: null, activeSkillIds })}
        onPick={() => onChange({ skillIds: skillIds ?? ids, activeSkillIds })}
      />
      <p className="text-sm text-faint">
        {t('항상 적용: 매 턴 지시에 넣습니다. 필요할 때: 모델이 설명을 보고 고릅니다.')}
      </p>
      {inherit ? (
        <p className="text-sm text-faint" data-testid="skill-mode-hint">
          {t('내 스킬은 모두 「필요할 때」로 쓸 수 있습니다. 늘 쓸 스킬만 더해 「항상 적용」으로 두세요.')}
        </p>
      ) : (
        <p
          data-testid="skill-mode-hint"
          className={cn('text-sm', ids.length === 0 ? 'rounded-control border border-warn/30 bg-warn/10 px-3 py-2 text-warn' : 'text-faint')}
        >
          {ids.length === 0
            ? t('「직접 고르기」에서 아무 스킬도 두지 않으면 이 에이전트는 스킬을 쓰지 않습니다. 입력창에서 골라도 쓰이지 않습니다.')
            : t('이 목록의 스킬만 씁니다. 입력창에서도 이 스킬만 고를 수 있습니다.')}
        </p>
      )}
      {ids.length > 0 && (
        <ul className="space-y-2">
          {ids.map((id) => {
            const found = skillById(id, skills, store)
            const name = found ? t(found.skill.name) : t('알 수 없는 항목')
            const pinned = activeSkillIds.includes(id)
            const body = found ? found.skill.body.trim() || found.skill.description : ''
            return (
              <li key={id} data-testid="agent-skill-row" data-skill={id} className="rounded-control border border-line bg-panel px-3 py-2">
                <div className="flex flex-wrap items-center gap-2">
                  <b className="min-w-0 flex-1 truncate text-base font-medium">{name}</b>
                  <ApplyToggle name={name} pinned={pinned} pinWhy={pinWhy(id)} onPin={() => pin(id)} onUnpin={() => unpin(id)} />
                  <button
                    type="button"
                    aria-label={t('{name} 빼기').replace('{name}', name)}
                    onClick={() => remove(id)}
                    className="grid size-7 shrink-0 place-items-center rounded-control text-faint hover:bg-elevated hover:text-fg"
                  >
                    <X size={14} />
                  </button>
                </div>
                <p className="mt-1 text-sm text-faint">
                  {found?.pending && <span className="text-accent">{t('저장하면 내 스킬에 설치됩니다.')} </span>}
                  {t('약 {n} 토큰').replace('{n}', tokensOf(id).toLocaleString())}
                </p>
                {body && (
                  <details className="mt-1 text-sm">
                    <summary className="cursor-pointer text-muted select-none hover:text-fg">{t('본문 미리 보기')}</summary>
                    <div className="mt-1 max-h-40 overflow-y-auto rounded-control bg-elevated px-2 py-1.5 text-muted" data-testid="skill-body-preview">
                      <Markdown className="text-sm leading-relaxed">{body.length > 1200 ? `${body.slice(0, 1200)}…` : body}</Markdown>
                    </div>
                  </details>
                )}
              </li>
            )
          })}
        </ul>
      )}
      {activeSkillIds.length > 0 && (
        <p className="text-sm text-faint" data-testid="pinned-total">
          {t('항상 적용 {n}/{max}개 · 약 {tokens} / {limit} 토큰')
            .replace('{n}', String(activeSkillIds.length))
            .replace('{max}', String(AGENT_SKILL_MAX))
            .replace('{tokens}', pinnedTokens.toLocaleString())
            .replace('{limit}', AGENT_SKILL_TOKENS.toLocaleString())}
        </p>
      )}
      <AddSkillPanel skills={skills} store={store} listed={ids} onAdd={add} onInstall={onInstall} />
    </div>
  )
}
