import { ChevronDown, Search, X } from 'lucide-react'
import { useEffect, useMemo, useRef, useState } from 'react'
import { Input } from '@/components/ui'
import { isListed } from '@/lib/listed'
import { useT } from '@/lib/useT'
import { cn } from '@/lib/utils'
import type { Skill, ToolCatalogEntry } from '@/types'

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
}: {
  label: string
  options: Option[]
  chosen: string[]
  onToggle: (id: string) => void
  searchLabel: string
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
            {groups.length === 0 && <p className="px-1.5 py-3 text-sm text-faint">{t('검색 결과가 없습니다')}</p>}
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

/** Agent skill allowlist; null follows the turn's selection. */
export function SkillPicker({
  value,
  onChange,
  skills,
}: {
  value: string[] | null
  onChange: (next: string[] | null) => void
  skills: Skill[]
}) {
  const t = useT()
  const chosen = value ?? []
  const options: Option[] = skills
    .filter((s) => (s.enabled && isListed(s)) || chosen.includes(s.id))
    .map((s) => ({
      id: s.id,
      title: s.name,
      description: s.description,
      group: s.source === 'built-in' ? '기본 스킬' : '내 스킬',
    }))
  const toggle = (id: string) => onChange(chosen.includes(id) ? chosen.filter((x) => x !== id) : [...chosen, id])
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
            chosen={chosen}
            onToggle={toggle}
            searchLabel={t('스킬 검색')}
          />
        </>
      )}
    </div>
  )
}
