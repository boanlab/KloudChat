import { Children, type ReactNode, useEffect, useId, useRef } from 'react'
import { cn } from '@/lib/utils'

type RibbonTab<T extends string = string> = {
  id: T
  label: string
}

/** A labelled command group inside a ribbon tab, like Office's Clipboard or Font group. */
export function RibbonGroup({ label, children }: { label: string; children: ReactNode }) {
  return (
    <section aria-label={label} className="flex min-w-max items-center px-1.5 first:pl-0 last:pr-0">
      <div className="flex items-center gap-px [&_button]:h-8 [&_button]:min-w-8 [&_button]:flex-row [&_button]:gap-1.5 [&_button]:rounded-sm [&_button]:border-0 [&_button:not([data-variant=primary])]:bg-transparent [&_button]:px-2 [&_button]:py-1 [&_button]:text-xs [&_button]:font-medium [&_button]:leading-none [&_button]:shadow-none [&_button:not([data-variant=primary]):hover]:bg-elevated [&_button[aria-pressed=true]]:bg-accent/10 [&_button[aria-pressed=true]]:text-accent [&_button:not([data-variant=primary]):disabled]:bg-transparent [&_svg]:shrink-0 max-sm:[&_button]:h-10 max-sm:[&_button]:min-w-10">
        {children}
      </div>
    </section>
  )
}

/** Word/PowerPoint-style command surface shared by reports and slide decks. */
export function ArtifactRibbon<T extends string>({
  label,
  tabs,
  active,
  onChange,
  children,
}: {
  label: string
  tabs: readonly RibbonTab<T>[]
  active: T
  onChange: (tab: T) => void
  children: ReactNode
}) {
  const commands = Children.toArray(children)
  const ribbonId = useId().replaceAll(':', '')
  const tabsRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    tabsRef.current
      ?.querySelector<HTMLElement>(`[role="tab"][data-tab-id="${active}"]`)
      ?.scrollIntoView({ block: 'nearest', inline: 'nearest' })
  }, [active])

  return (
    <div className="-mx-4 -mb-2.5 min-w-0 basis-full border-t border-line bg-elevated max-sm:-mx-2">
      <div
        role="tablist"
        aria-label={label}
        ref={tabsRef}
        className="flex min-w-0 items-end gap-0 overflow-x-auto px-2"
        onKeyDown={(event) => {
          if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return
          const buttons = [...event.currentTarget.querySelectorAll<HTMLButtonElement>('[role="tab"]')]
          if (!buttons.length) return
          const current = Math.max(0, buttons.indexOf(event.target as HTMLButtonElement))
          const next = event.key === 'Home'
            ? 0
            : event.key === 'End'
              ? buttons.length - 1
              : event.key === 'ArrowRight'
                ? (current + 1) % buttons.length
                : (current - 1 + buttons.length) % buttons.length
          event.preventDefault()
          buttons[next].focus()
          buttons[next].click()
        }}
      >
        {tabs.map((tab) => (
          <button
            key={tab.id}
            type="button"
            role="tab"
            id={`${ribbonId}-tab-${tab.id}`}
            data-tab-id={tab.id}
            aria-controls={`${ribbonId}-panel`}
            aria-selected={active === tab.id}
            tabIndex={active === tab.id ? 0 : -1}
            onClick={() => onChange(tab.id)}
            className={cn(
              'relative h-8 shrink-0 border-b-2 px-3 text-xs font-medium transition-colors max-sm:h-9',
              active === tab.id
                ? 'border-accent bg-panel text-fg'
                : 'border-transparent text-muted hover:bg-panel/60 hover:text-fg',
            )}
          >
            {tab.label}
          </button>
        ))}
      </div>
      {commands.length > 0 && <div id={`${ribbonId}-panel`} role="tabpanel" aria-labelledby={`${ribbonId}-tab-${active}`}
        className="min-h-11 min-w-0 overflow-x-auto border-t border-line bg-panel px-2 py-1.5 shadow-[0_1px_2px_rgba(0,0,0,0.04)]">
        <div
          role="toolbar"
          aria-label={tabs.find((tab) => tab.id === active)?.label}
          className="flex w-max min-w-full items-center overflow-visible bg-transparent divide-x divide-line/80 [&>div]:flex [&>div]:shrink-0"
        >
          {commands.map((child, index) => (
            <div key={index}>{child}</div>
          ))}
        </div>
      </div>}
    </div>
  )
}

/** Small permanent actions beside the title; labels collapse before icons do. */
export function QuickAccess({ children, label }: { children: ReactNode; label: string }) {
  return <div aria-label={label} className="flex shrink-0 items-center gap-0.5 [&_button]:h-8 max-sm:[&_button]:h-10 max-sm:[&_button]:w-10 max-sm:[&_button]:px-0 max-sm:[&_button]:text-[0px]">{children}</div>
}
