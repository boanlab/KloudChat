import { BookOpen } from 'lucide-react'
import { useCallback, useState, type ReactNode } from 'react'
import { Button } from '@/components/ui'
import { CitationContext, type CitationTarget } from '@/lib/shelfCitations'
import { SHELF_NAMES } from '@/lib/shelfNames'
import { useT } from '@/lib/useT'
import { SourceViewer, type SourceTarget } from './SourceViewer'

/**
 * Around a project conversation's transcript: a citation of project material (「[2]」 under
 * which the answer's source list names a project file) opens the source viewer at the passage.
 * Outside a project the children render as they are.
 */
export function CitationLayer({ projectId, children }: { projectId: string | null | undefined; children: ReactNode }) {
  const [target, setTarget] = useState<SourceTarget | null>(null)
  const open = useCallback(
    (cited: CitationTarget) => {
      if (!projectId) return
      setTarget({ projectId, name: cited.source.name, page: cited.source.page, quote: cited.claim, n: cited.n })
    },
    [projectId],
  )
  if (!projectId) return <>{children}</>
  return (
    <CitationContext.Provider value={open}>
      {children}
      <SourceViewer target={target} onClose={() => setTarget(null)} />
    </CitationContext.Provider>
  )
}

/** The top bar's 자료 button of a project conversation. */
export function SourcesButton({ open, onToggle }: { open: boolean; onToggle: () => void }) {
  const t = useT()
  return (
    <Button size="sm" variant={open ? 'primary' : 'secondary'} aria-pressed={open} onClick={onToggle}>
      <BookOpen size={14} />
      {t(SHELF_NAMES.sources)}
    </Button>
  )
}
