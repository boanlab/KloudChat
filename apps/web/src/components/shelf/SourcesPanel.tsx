import { ChevronDown, ChevronRight, Loader2, Plus, ShieldCheck, X } from 'lucide-react'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { ListSearch } from '@/components/ui/ListSearch'
import { Button } from '@/components/ui'
import { errorMessage, shelfApi } from '@/lib/api'
import type { ShelfSource } from '@/lib/shelf'
import { SHELF_NAMES, withNames } from '@/lib/shelfNames'
import { useMediaQuery } from '@/lib/useMediaQuery'
import { useT } from '@/lib/useT'
import { cn, formatTokens } from '@/lib/utils'
import { useStore } from '@/store/useStore'
import { AddTextModal } from './AddTextModal'
import { SourceGuide } from './SourceGuide'
import { SOURCE_ICONS, SOURCE_KIND_LABELS } from './sourceKinds'

/**
 * The conversation's 자료 panel: the project's sources with a switch each that decides what
 * this conversation's next questions may use (all on by default; kept per conversation),
 * select all/none, search and counts, each source's guide, and the project's suggested
 * questions. A question is dropped into the composer, never sent on the person's behalf.
 */
export function SourcesPanel({
  sessionId,
  projectId,
  onClose,
}: {
  sessionId: string
  projectId: string
  onClose: () => void
}) {
  const t = useT()
  const narrow = useMediaQuery('(max-width: 1023px)')
  const setDraft = useStore((s) => s.setDraft)
  const addProjectFile = useStore((s) => s.addProjectFile)
  const [sources, setSources] = useState<ShelfSource[] | null>(null)
  const [strict, setStrict] = useState(true)
  const [off, setOff] = useState<Set<string>>(new Set())
  const [query, setQuery] = useState('')
  const [open, setOpen] = useState<string | null>(null)
  const [projectGuide, setProjectGuide] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [saving, setSaving] = useState(false)
  const [adding, setAdding] = useState(false)
  // The latest selection to store; a save in flight stores what was asked last.
  const pending = useRef<string[] | null>(null)

  const load = useCallback(async () => {
    try {
      const [listing, selection] = await Promise.all([shelfApi.sources(projectId), shelfApi.selection(sessionId)])
      setSources(listing.sources)
      setStrict(listing.strictLocal)
      setOff(new Set(selection.off))
    } catch (err) {
      setError(errorMessage(err, t('자료를 불러오지 못했습니다.')))
    }
  }, [projectId, sessionId, t])

  useEffect(() => {
    void load()
  }, [load])

  const store = async (next: Set<string>) => {
    setOff(next)
    pending.current = [...next]
    if (saving) return
    setSaving(true)
    setError(null)
    try {
      while (pending.current) {
        const ids = pending.current
        pending.current = null
        await shelfApi.setSelection(sessionId, ids)
      }
    } catch (err) {
      setError(errorMessage(err, t('선택을 저장하지 못했습니다. 다시 시도하세요.')))
    } finally {
      setSaving(false)
    }
  }

  const shown = useMemo(() => {
    const words = query.trim().toLowerCase()
    return (sources ?? []).filter((s) => !words || s.name.toLowerCase().includes(words))
  }, [sources, query])

  const total = sources?.length ?? 0
  const used = (sources ?? []).filter((s) => !off.has(s.id)).length
  const ask = (question: string) => {
    setDraft(question)
    if (narrow) onClose()
  }

  return (
    <aside
      aria-label={t(SHELF_NAMES.sources)}
      data-testid="sources-panel"
      className={cn(
        'flex min-h-0 flex-col border-l border-line bg-panel',
        narrow ? 'absolute inset-0 z-30' : 'w-80 shrink-0',
      )}
    >
      <header className="flex items-center gap-2 border-b border-line px-3 py-2.5">
        <p className="text-base font-medium">{t(SHELF_NAMES.sources)}</p>
        <span className="text-sm text-muted" data-testid="sources-count">
          {t('{used}/{total}개 사용').replace('{used}', String(used)).replace('{total}', String(total))}
        </span>
        {saving && <Loader2 size={12} className="animate-spin text-faint" aria-label={t('저장 중…')} />}
        <Button variant="ghost" size="icon" className="ml-auto" onClick={onClose} aria-label={t('닫기')}>
          <X size={15} />
        </Button>
      </header>
      <div className="min-h-0 flex-1 space-y-3 overflow-y-auto px-3 py-3">
        {strict && (
          <p className="flex items-start gap-1.5 text-xs text-muted">
            <ShieldCheck size={13} className="mt-0.5 shrink-0 text-success" />
            {t('이 자료는 내부 자료라 strict-local 모델만 읽습니다.')}
          </p>
        )}
        <div className="flex flex-wrap items-center gap-1.5">
          <ListSearch value={query} onChange={setQuery} label={t('자료 검색')} className="w-full" />
          <Button size="sm" variant="ghost" disabled={!total || used === total} onClick={() => void store(new Set())}>
            {t('모두 선택')}
          </Button>
          <Button
            size="sm"
            variant="ghost"
            disabled={!total || used === 0}
            onClick={() => void store(new Set((sources ?? []).map((s) => s.id)))}
          >
            {t('모두 해제')}
          </Button>
          <Button size="sm" className="ml-auto" onClick={() => setAdding(true)}>
            <Plus size={13} />
            {t('텍스트')}
          </Button>
        </div>
        {total > 0 && used === 0 && (
          <p role="status" className="rounded-control bg-warn/10 px-2.5 py-1.5 text-sm text-warn">
            {t('자료를 모두 껐습니다. 다음 질문은 자료 없이 답합니다.')}
          </p>
        )}
        {error && <p role="alert" className="text-sm text-danger">{error}</p>}
        {sources === null && !error && (
          <p className="flex items-center gap-2 text-sm text-muted">
            <Loader2 size={13} className="animate-spin" />
            {t('불러오는 중…')}
          </p>
        )}
        {sources?.length === 0 && (
          <p className="text-sm text-muted">{t('이 프로젝트에 자료가 없습니다. 프로젝트 화면에서 파일이나 웹 자료를 넣으세요.')}</p>
        )}
        <ul className="space-y-1">
          {shown.map((source) => {
            const Icon = SOURCE_ICONS[source.kind]
            const on = !off.has(source.id)
            const expanded = open === source.id
            return (
              <li key={source.id} data-source={source.id} className="rounded-control border border-line">
                <div className="flex items-center gap-2 px-2 py-1.5">
                  <input
                    type="checkbox"
                    checked={on}
                    aria-label={t('{name} 사용').replace('{name}', source.name)}
                    onChange={() => {
                      const next = new Set(off)
                      if (on) next.add(source.id)
                      else next.delete(source.id)
                      void store(next)
                    }}
                    className="size-4 shrink-0 accent-[var(--accent)]"
                  />
                  <Icon size={14} className={cn('shrink-0', on ? 'text-accent' : 'text-faint')} />
                  <button
                    type="button"
                    onClick={() => setOpen(expanded ? null : source.id)}
                    aria-expanded={expanded}
                    className={cn('min-w-0 flex-1 text-left text-sm', !on && 'text-faint')}
                  >
                    <span className="block truncate">{source.name}</span>
                    <span className="block text-xs text-faint">
                      {t(SOURCE_KIND_LABELS[source.kind])} · {t('{n} 토큰').replace('{n}', formatTokens(source.tokens))}
                    </span>
                  </button>
                  {expanded ? <ChevronDown size={14} className="text-faint" /> : <ChevronRight size={14} className="text-faint" />}
                </div>
                {expanded && (
                  <div className="border-t border-line px-2.5 py-2">
                    <SourceGuide projectId={projectId} subject={source.id} onAsk={ask} compact />
                  </div>
                )}
              </li>
            )
          })}
        </ul>
        {total > 0 && (
          <div className="rounded-control border border-dashed border-line-strong">
            <button
              type="button"
              aria-expanded={projectGuide}
              onClick={() => setProjectGuide((v) => !v)}
              className="flex w-full items-center gap-1.5 px-2.5 py-2 text-left text-sm font-medium"
            >
              {projectGuide ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
              {withNames(t('프로젝트 전체 {guide}'), t)}
            </button>
            {projectGuide && (
              <div className="border-t border-line px-2.5 py-2">
                <SourceGuide projectId={projectId} subject="project" onAsk={ask} compact />
              </div>
            )}
          </div>
        )}
      </div>
      <AddTextModal
        projectId={projectId}
        open={adding}
        onClose={() => setAdding(false)}
        onAdded={(row) => {
          addProjectFile(projectId, row)
          void load()
        }}
      />
    </aside>
  )
}
