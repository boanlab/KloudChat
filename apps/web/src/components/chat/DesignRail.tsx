import { useEffect, useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { Badge } from '@/components/ui'
import {
  argumentText,
  designTemplatePreviewUrl,
  designTemplatesApi,
  fillPrompt,
  templateText,
  type DesignTemplateRow,
} from '@/lib/api'
import { currentLang } from '@/lib/i18n'
import { kindMeta } from '@/lib/kinds'
import { useStore } from '@/store/useStore'
import { useT } from '@/lib/useT'

/**
 * The rendering catalogue, on the screen everybody lands on.
 *
 * It was reachable only from the empty state of a new session, behind a
 * secondary button — which is to say the whole catalogue was invisible to
 * anybody who did not already know it existed. This is the front door: what
 * the answer can look like, beside the choice of what to make.
 *
 * Picking one here fills its blanks with their own defaults rather than
 * showing the form. The gallery is where you fill them in; from the home
 * screen the point is to *start*, and the sentence lands in the composer where
 * every word of it is still editable.
 */
export function DesignRail() {
  const t = useT()
  const navigate = useNavigate()
  const cached = useStore((s) => s.designTemplates)
  const enabledKinds = useStore((s) => s.enabledKinds)
  const setDraft = useStore((s) => s.setDraft)
  const setPendingTemplate = useStore((s) => s.setPendingTemplate)
  const setImageOptions = useStore((s) => s.setImageOptions)
  const setAvOptions = useStore((s) => s.setAvOptions)
  const [rows, setRows] = useState<DesignTemplateRow[]>(cached)

  // The home screen is reached before the workspace load on a cold open.
  useEffect(() => {
    if (cached.length) {
      setRows(cached)
      return
    }
    void designTemplatesApi.list().then(setRows).catch(() => setRows([]))
  }, [cached])

  const english = currentLang() === 'en'
  const visible = useMemo(
    () =>
      rows
        .filter((row) => enabledKinds.includes(row.surface))
        .map((row) => ({ row, text: templateText(row, english) })),
    [rows, enabledKinds, english],
  )
  if (visible.length === 0) return null

  const start = (row: DesignTemplateRow, prompt: string) => {
    setPendingTemplate(row)
    setDraft(
      fillPrompt(
        prompt,
        Object.fromEntries(
          row.arguments.map((a) => [a.name, argumentText(a, english).initial]),
        ),
      ),
    )
    const d = row.defaults ?? {}
    if (row.kind === 'image') {
      setImageOptions({
        ...(typeof d.aspect === 'string' ? { aspect: d.aspect } : {}),
        ...(typeof d.style === 'string' ? { style: d.style } : {}),
      })
    } else if (row.kind === 'video' || row.kind === 'audio') {
      setAvOptions({
        mode: row.kind === 'audio' ? 'audio' : 'video',
        ...(typeof d.aspect === 'string' ? { aspect: d.aspect } : {}),
        ...(typeof d.seconds === 'number' ? { durationSec: d.seconds } : {}),
        ...(d.resolution === '720p' || d.resolution === '1080p'
          ? { resolution: d.resolution }
          : {}),
        ...(d.audioKind === 'narration' || d.audioKind === 'music'
          ? { audioKind: d.audioKind }
          : {}),
        // A narration template names its reader; until the composer had a
        // voice chip this was the one default that went nowhere.
        ...(typeof d.voice === 'string' && d.voice ? { voice: d.voice } : {}),
      })
    }
    navigate(`/new/${row.surface}`)
  }

  return (
    <section aria-label={t('서식에서 시작')} className="mb-8">
      <div className="mb-2.5 flex items-baseline gap-2">
        <h2 className="text-base font-semibold">{t('서식에서 시작')}</h2>
        <p className="text-sm text-muted">{t('결과물이 어떤 모양으로 나올지 먼저 고릅니다')}</p>
      </div>
      {/* A rail rather than a grid: this is the second thing on the screen,
          and a wall of ten cards would push the recent work off it. */}
      <div className="-mx-1 flex snap-x gap-3 overflow-x-auto px-1 pb-2">
        {visible.map(({ row, text }) => {
          const meta = kindMeta[row.surface]
          const Icon = meta.icon
          return (
            <button
              key={row.id}
              onClick={() => start(row, text.examplePrompt)}
              title={text.description}
              className="group w-56 shrink-0 snap-start overflow-hidden rounded-card border border-line bg-panel text-left transition-colors hover:border-line-strong"
            >
              {row.hasPreview && (
                <div className="pointer-events-none h-28 overflow-hidden border-b border-line bg-white">
                  {/* Drawn in the default look, and asking for no other one is
                      the honest answer here: a card on the home screen starts
                      a session with no project, so the defaults are what its
                      deck will actually come out in. The same gallery opened
                      inside a project passes that project's design system. */}
                  <iframe
                    title={text.name}
                    src={designTemplatePreviewUrl(row.id)}
                    sandbox=""
                    loading="lazy"
                    tabIndex={-1}
                    className="h-[440px] w-[880px] origin-top-left border-0"
                    style={{ transform: 'scale(0.255)' }}
                  />
                </div>
              )}
              <div className="space-y-1 p-2.5">
                <div className="flex items-center gap-1.5">
                  <Icon size={12} style={{ color: meta.color }} />
                  <span className="min-w-0 flex-1 truncate text-base font-medium">
                    {text.name}
                  </span>
                  <Badge>{text.category}</Badge>
                </div>
                <p className="line-clamp-2 text-sm text-muted">{text.description}</p>
              </div>
            </button>
          )
        })}
      </div>
    </section>
  )
}
