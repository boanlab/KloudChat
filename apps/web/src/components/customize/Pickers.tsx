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

const BUILTIN = '기본 도구'
/** Official skills not yet added, as the chat's 「+ › 스킬」 lists them. */
const OFFICIAL_TO_ADD = '공식 스킬 · 고르면 내 스킬에 추가됩니다'
/** Prefix of an official store row's option id until its copy exists. */
const STORE = 'store:'

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

/** The person's skills, then official ones not added yet (picking one adds it, as in the chat). */
function skillOptions(skills: Skill[], chosen: string[], store: StoreSkill[]): Option[] {
  // A copy just added comes back without 「원본: 공식」; its store row says so.
  const officialIds = new Set(store.filter((s) => s.official).map((s) => s.id))
  const mine: Option[] = skills
    .filter((s) => (s.enabled && isListed(s)) || chosen.includes(s.id))
    .map((s) => ({
      id: s.id,
      title: s.name,
      description: s.description,
      group: s.originOfficial || officialIds.has(s.originId ?? '') ? '공식 스킬' : s.originId ? '공유받은 스킬' : '내 스킬',
    }))
  const official: Option[] = store
    .filter((s) => s.official && s.enabled && isListed(s) && !skills.some((m) => m.originId === s.id))
    .map((s) => ({ id: `${STORE}${s.id}`, title: s.name, description: s.description, group: OFFICIAL_TO_ADD }))
  return [...mine, ...official]
}

/** Toggles a skill id; an official row is added to the person's skills first, its copy picked.
 * The row shows as picked while it is being added. */
function useSkillToggle(chosen: string[], apply: (next: string[]) => void, add?: (storeId: string) => Promise<string | null>) {
  const [adding, setAdding] = useState<string | null>(null)
  const toggle = (id: string) => {
    if (!id.startsWith(STORE)) return apply(chosen.includes(id) ? chosen.filter((x) => x !== id) : [...chosen, id])
    if (!add || adding) return
    setAdding(id)
    void add(id.slice(STORE.length))
      .then((copy) => {
        if (copy && !chosen.includes(copy)) apply([...chosen, copy])
      })
      .finally(() => setAdding(null))
  }
  const shown = adding ? [...chosen, adding] : chosen
  return { toggle, shown }
}

/** Nothing to pick at all: where skills come from. */
function NoSkills() {
  const t = useT()
  return (
    <Link to="/customize?tab=skills&view=explore" className="text-accent hover:underline">
      {t('탐색에서 스킬 추가')}
    </Link>
  )
}

/** Agent skill allowlist; null follows the turn's selection. */
export function SkillPicker({
  value,
  onChange,
  skills,
  store = [],
  onAddOfficial,
}: {
  value: string[] | null
  onChange: (next: string[] | null) => void
  skills: Skill[]
  /** The skill store: its official rows not added yet are offered too. */
  store?: StoreSkill[]
  /** Adds an official skill; resolves to the person's copy. */
  onAddOfficial?: (storeId: string) => Promise<string | null>
}) {
  const t = useT()
  const chosen = value ?? []
  const options = skillOptions(skills, chosen, onAddOfficial ? store : [])
  const { toggle, shown } = useSkillToggle(chosen, onChange, onAddOfficial)
  return (
    <div className="space-y-2" data-testid="skill-picker">
      <ModeSwitch
        inherit={value === null}
        labels={[t('기본값 따르기'), t('직접 고르기')]}
        onInherit={() => onChange(null)}
        onPick={() => onChange(value ?? [])}
      />
      {value === null ? (
        <p className="text-sm text-faint">{t('입력창에서 고른 스킬을 그대로 씁니다.')}</p>
      ) : (
        <>
          <ChosenChips options={options} chosen={chosen} onRemove={toggle} />
          {chosen.length === 0 && <p className="text-sm text-faint">{t('스킬을 쓰지 않습니다.')}</p>}
          <PickerPanel
            label={t('스킬 고르기')}
            options={options}
            chosen={shown}
            onToggle={toggle}
            searchLabel={t('스킬 검색')}
            empty={<NoSkills />}
          />
        </>
      )}
    </div>
  )
}

/** The skills an agent keeps on in every turn: what it is built from (at most six). */

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

export function ActiveSkillPicker({
  value,
  onChange,
  skills,
  store = [],
  onAddOfficial,
}: {
  value: string[]
  onChange: (next: string[]) => void
  skills: Skill[]
  /** The skill store: its official rows not added yet are offered too. */
  store?: StoreSkill[]
  /** Adds an official skill; resolves to the person's copy. */
  onAddOfficial?: (storeId: string) => Promise<string | null>
}) {
  const t = useT()
  const full = value.length >= 6
  const options = skillOptions(skills, value, onAddOfficial ? store : []).map((o) =>
    full && o.id.startsWith(STORE) ? { ...o, disabled: true, why: t('최대 6개까지 켜 둘 수 있습니다.') } : o,
  )
  const { toggle, shown } = useSkillToggle(
    value,
    (next) => onChange(next.length > value.length && full ? value : next),
    onAddOfficial,
  )
  return (
    <div className="space-y-2" data-testid="active-skill-picker">
      <ChosenChips options={options} chosen={value} onRemove={toggle} />
      {value.length === 0 && <p className="text-sm text-faint">{t('켜 둔 스킬이 없습니다.')}</p>}
      {full && <p className="text-sm text-faint">{t('최대 6개까지 켜 둘 수 있습니다.')}</p>}
      <PickerPanel
        label={t('켜 둘 스킬 고르기')}
        options={options}
        chosen={shown}
        onToggle={toggle}
        searchLabel={t('스킬 검색')}
        empty={<NoSkills />}
      />
    </div>
  )
}
