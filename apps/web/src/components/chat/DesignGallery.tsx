import { LayoutGrid } from 'lucide-react'
import { useEffect, useMemo, useState } from 'react'
import { Badge, Button, Modal } from '@/components/ui'
import {
  argumentText,
  designTemplatePreviewUrl,
  designTemplatesApi,
  fillPrompt,
  templateText,
  type DesignTemplateRow,
} from '@/lib/api'
import { Input } from '@/components/ui'
import { currentLang } from '@/lib/i18n'
import { cn } from '@/lib/utils'
import { useStore } from '@/store/useStore'
import type { SessionKind } from '@/types'
import { useT } from '@/lib/useT'

/**
 * The blanks a media template leaves, and the button that fills them in.
 *
 * The filled sentence goes to the composer rather than to the model: on these
 * surfaces the prompt is the entire input, so a template that sent something
 * the person never read would be one they could not correct. Every value here
 * starts at the template's own default, so the card is usable without typing
 * anything — which is the point of a starting sentence.
 */
function Blanks({
  row,
  english,
  prompt,
  onPick,
}: {
  row: DesignTemplateRow
  english: boolean
  prompt: string
  onPick: (row: DesignTemplateRow, filled: string) => void
}) {
  const t = useT()
  const [values, setValues] = useState<Record<string, string>>(() =>
    Object.fromEntries(row.arguments.map((a) => [a.name, argumentText(a, english).initial])),
  )
  return (
    <div className="space-y-2">
      {row.arguments.map((argument) => {
        const { label, options } = argumentText(argument, english)
        const value = values[argument.name] ?? ''
        const set = (next: string) => setValues((v) => ({ ...v, [argument.name]: next }))
        return (
          <label key={argument.name} className="block space-y-1">
            <span className="text-xs text-muted">{label}</span>
            {options.length > 0 ? (
              <select
                aria-label={label}
                value={value}
                onChange={(e) => set(e.target.value)}
                className="h-8 w-full rounded-control border border-line bg-panel px-2 text-sm focus:border-accent focus:outline-none"
              >
                {options.map((option) => (
                  <option key={option} value={option}>
                    {option}
                  </option>
                ))}
              </select>
            ) : (
              <Input
                aria-label={label}
                value={value}
                onChange={(e) => set(e.target.value)}
                className="h-8 text-sm"
              />
            )}
          </label>
        )
      })}
      <Button size="sm" onClick={() => onPick(row, fillPrompt(prompt, values))}>
        {t('이 디자인으로 시작')}
      </Button>
    </div>
  )
}

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
export function DesignGalleryModal({
  kind,
  open,
  onClose,
}: {
  kind: SessionKind
  open: boolean
  onClose: () => void
}) {
  const t = useT()
  const [rows, setRows] = useState<DesignTemplateRow[]>([])
  const [category, setCategory] = useState<string | 'all'>('all')
  const setDraft = useStore((s) => s.setDraft)
  const setPendingTemplate = useStore((s) => s.setPendingTemplate)
  const setImageOptions = useStore((s) => s.setImageOptions)
  const setAvOptions = useStore((s) => s.setAvOptions)
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

  /**
   * The chips a template implies, set from its own metadata.
   *
   * Only the keys it names: a template that says nothing about duration
   * leaves whatever the person last chose, rather than resetting it to a
   * default they did not ask for.
   */
  const applyDefaults = (row: DesignTemplateRow) => {
    const d = row.defaults ?? {}
    if (row.kind === 'image') {
      setImageOptions({
        ...(typeof d.aspect === 'string' ? { aspect: d.aspect } : {}),
        ...(typeof d.style === 'string' ? { style: d.style } : {}),
        ...(typeof d.count === 'number' ? { count: d.count } : {}),
      })
      return
    }
    if (row.kind === 'video' || row.kind === 'audio') {
      setAvOptions({
        mode: row.kind === 'audio' ? 'audio' : 'video',
        ...(typeof d.aspect === 'string' ? { aspect: d.aspect } : {}),
        ...(typeof d.seconds === 'number' ? { durationSec: d.seconds } : {}),
        ...(d.resolution === '720p' || d.resolution === '1080p'
          ? { resolution: d.resolution }
          : {}),
        ...(typeof d.audio === 'boolean' ? { withAudio: d.audio } : {}),
        ...(d.audioKind === 'narration' || d.audioKind === 'music'
          ? { audioKind: d.audioKind }
          : {}),
      })
    }
  }

  const pick = (row: DesignTemplateRow, prompt: string) => {
    setPendingTemplate(row)
    setDraft(prompt)
    applyDefaults(row)
    onClose()
  }

  return (
    <Modal
      open={open}
      onClose={onClose}
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
                {row.arguments.length > 0 ? (
                  <Blanks row={row} english={english} prompt={text.examplePrompt} onPick={pick} />
                ) : (
                  <>
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
                  </>
                )}
              </div>
            </div>
          ))}
      </div>
    </Modal>
  )
}

/**
 * The button that opens it, where a surface has anything to offer.
 *
 * Rendered on the empty state of a new session and in the composer's own menu,
 * so the choice is reachable after the first turn as well — a shape you can
 * only pick before you start is one you cannot change your mind about.
 */
export function DesignGallery({ kind }: { kind: SessionKind }) {
  const t = useT()
  const [open, setOpen] = useState(false)
  const has = useStore((s) => s.designTemplates.some((row) => row.surface === kind))
  if (!has) return null
  return (
    <>
      <Button variant="secondary" size="sm" onClick={() => setOpen(true)}>
        <LayoutGrid size={14} />
        {t('디자인 고르기')}
      </Button>
      <DesignGalleryModal kind={kind} open={open} onClose={() => setOpen(false)} />
    </>
  )
}
