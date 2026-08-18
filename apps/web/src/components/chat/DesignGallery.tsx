import { LayoutGrid } from 'lucide-react'
import { useEffect, useMemo, useState } from 'react'
import { Badge, Button, Modal } from '@/components/ui'
import {
  designTemplatePreviewUrl,
  designTemplatesApi,
  templateText,
  type DesignTemplateRow,
} from '@/lib/api'
import { currentLang } from '@/lib/i18n'
import { cn } from '@/lib/utils'
import { useStore } from '@/store/useStore'
import type { SessionKind } from '@/types'
import { useT } from '@/lib/useT'

/**
 * Shapes the answer can come out in.
 *
 * The prompt gallery beside it answers "what do I ask for"; this one answers
 * "what should it look like when it arrives". They are separate because the
 * two choices are independent — any prompt can be written into any shape.
 *
 * Every card shows the template's *own* seed filled with its own sample, so
 * what is on the card is the thing that will be produced rather than a
 * screenshot of it. The frame is sandboxed with no permissions at all, which
 * is also why the seeds carry no script.
 */
export function DesignGallery({ kind }: { kind: SessionKind }) {
  const t = useT()
  const [open, setOpen] = useState(false)
  const [rows, setRows] = useState<DesignTemplateRow[]>([])
  const [category, setCategory] = useState<string | 'all'>('all')
  const setDraft = useStore((s) => s.setDraft)
  const setPendingTemplate = useStore((s) => s.setPendingTemplate)
  const cached = useStore((s) => s.designTemplates)

  // The store's copy is what the workspace load already fetched; the request
  // here is the fallback for a screen opened before that landed.
  useEffect(() => {
    if (!open) return
    if (cached.length) {
      setRows(cached)
      return
    }
    void designTemplatesApi.list().then(setRows).catch(() => setRows([]))
  }, [open, cached])

  const english = currentLang() === 'en'
  const forSurface = useMemo(
    () => rows.filter((r) => r.surface === kind).map((r) => ({ row: r, text: templateText(r, english) })),
    [rows, kind, english],
  )
  const categories = useMemo(
    () => [...new Set(forSurface.map((c) => c.text.category))],
    [forSurface],
  )
  const visible =
    category === 'all' ? forSurface : forSurface.filter((c) => c.text.category === category)

  // Nothing to offer on this surface — chat and a/v have no rendering
  // catalogue, and an empty modal behind a button is worse than no button.
  if (!useStore.getState().designTemplates.some((r) => r.surface === kind) && !forSurface.length) {
    return null
  }

  const pick = (row: DesignTemplateRow, examplePrompt: string) => {
    setPendingTemplate(row)
    setDraft(examplePrompt)
    setOpen(false)
  }

  return (
    <>
      <Button variant="secondary" size="sm" onClick={() => setOpen(true)}>
        <LayoutGrid size={14} />
        {t('디자인 고르기')}
      </Button>

      <Modal
        open={open}
        onClose={() => setOpen(false)}
        title={t('디자인 고르기')}
        description={t('고르면 예시 문장이 입력창에 들어갑니다. 문장은 바꿔도 됩니다.')}
        width="max-w-3xl"
      >
        {categories.length > 1 && (
          <div className="mb-3 flex flex-wrap gap-1.5">
            {(['all', ...categories] as const).map((c) => (
              <button
                key={c}
                onClick={() => setCategory(c)}
                className={cn(
                  'rounded-full border px-2.5 py-1 text-sm transition-colors',
                  category === c
                    ? 'border-accent bg-accent-soft text-accent'
                    : 'border-line text-muted hover:text-fg',
                )}
              >
                {c === 'all' ? t('전체') : c}
              </button>
            ))}
          </div>
        )}

        <div className="grid gap-3 sm:grid-cols-2">
          {visible.map(({ row, text }) => (
            <div
              key={row.id}
              className="group overflow-hidden rounded-card border border-line bg-panel transition-colors hover:border-line-strong"
            >
              {row.hasPreview && (
                <div className="pointer-events-none h-40 overflow-hidden border-b border-line bg-white">
                  {/* Scaled down rather than cropped: a card should show the
                      whole shape, and a 400px-wide slice of a slide is not a
                      preview of it. */}
                  <iframe
                    title={text.name}
                    src={designTemplatePreviewUrl(row.id)}
                    sandbox=""
                    loading="lazy"
                    tabIndex={-1}
                    className="h-[500px] w-[1000px] origin-top-left border-0"
                    style={{ transform: 'scale(0.42)' }}
                  />
                </div>
              )}
              <div className="space-y-2 p-3">
                <div className="flex items-start justify-between gap-2">
                  <p className="text-base font-medium">{text.name}</p>
                  <Badge>{text.category}</Badge>
                </div>
                <p className="text-sm text-muted">{text.description}</p>
                {text.fills.length > 0 && (
                  <div className="flex flex-wrap gap-1">
                    {text.fills.map((fill) => (
                      <span
                        key={fill}
                        className="rounded-full border border-line px-2 py-0.5 text-xs text-faint"
                      >
                        {fill}
                      </span>
                    ))}
                  </div>
                )}
                <Button size="sm" onClick={() => pick(row, text.examplePrompt)}>
                  {t('이 디자인으로 시작')}
                </Button>
              </div>
            </div>
          ))}
        </div>
      </Modal>
    </>
  )
}
