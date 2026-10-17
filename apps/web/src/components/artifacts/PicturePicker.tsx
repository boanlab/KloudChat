import { ImagePlus, Loader2, Sparkles } from 'lucide-react'
import { useEffect, useState } from 'react'
import { ArtifactPreview } from '@/components/artifacts/ArtifactPanel'
import { Button, Input } from '@/components/ui'
import { ApiError, sessionsApi } from '@/lib/api'
import { mediaFailure } from '@/lib/failures'
import { cn } from '@/lib/utils'
import { useT } from '@/lib/useT'
import { drawFigure, useStore } from '@/store/useStore'
import { isImeKey } from '@/lib/ime'

/** On-screen name of a figure kind. */
const FIGURE_NAMES: Record<string, string> = { method: '구조도', flow: '흐름도', concept: '개념도', compare: '비교도' }

/** Picks an image artifact for a slide or section, or generates one via `POST /sessions/{id}/images`. */
export function PicturePicker({
  sessionId,
  aspect,
  picked,
  onPick,
  caption,
  onCaption,
  about,
  title,
  context,
  visualStyle,
}: {
  /** Session charged and stored under. */
  sessionId?: string | null
  /** `16:9` for a slide, `4:3` for a document figure. */
  aspect: string
  picked: string | null
  onPick: (id: string | null) => void
  caption: string
  onCaption: (value: string) => void
  /** Subject; seeds the prompt suggestion. */
  about?: string
  /** Document title, passed to the suggestion. */
  title?: string
  /** Surrounding text, so the suggestion doesn't redraw it. */
  context?: string
  /** Document look, so the picture matches. */
  visualStyle?: string
}) {
  const t = useT()
  const [prompt, setPrompt] = useState('')
  const [suggesting, setSuggesting] = useState(false)
  const [making, setMaking] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const artifacts = useStore((s) => s.artifacts)
  const loadArtifacts = useStore((s) => s.loadArtifacts)
  const imageOn = useStore((s) => s.enabledKinds).includes('image')
  const canMake = imageOn && Boolean(sessionId)
  // Box holds the untouched suggestion.
  const [suggested, setSuggested] = useState(false)
  // Suggestion's choice: figure kind → server-drawn shapes, else a picture.
  const [chosen, setChosen] = useState<{ figure: string; style: string } | null>(null)
  const chatModel = useStore((s) => s.modelByKind.chat)

  // Same-session pictures first.
  const pictures = artifacts
    .filter((a) => a.kind === 'image')
    .sort((a, b) => {
      const mine = Number(b.sessionId === sessionId) - Number(a.sessionId === sessionId)
      return mine || +new Date(b.updatedAt) - +new Date(a.updatedAt)
    })
    .slice(0, 24)

  // Fetched once on open; failure leaves the box empty.
  useEffect(() => {
    if (!canMake || !sessionId || prompt.trim()) return
    let alive = true
    setSuggesting(true)
    void sessionsApi
      .suggestFigure(sessionId, { title, about, context, visualStyle })
      .then((row) => {
        if (!alive || !(row.prompt || row.figure)) return
        // Figure: its description; picture: its prompt.
        setPrompt((row.figure ? row.description : row.prompt) ?? '')
        setSuggested(true)
        setChosen({ figure: row.figure ?? '', style: row.style ?? '' })
        // Typed caption outranks the suggestion.
        if (row.caption) onCaption(caption.trim() || row.caption)
      })
      .catch(() => undefined)
      .finally(() => {
        if (alive) setSuggesting(false)
      })
    return () => {
      alive = false
    }
    // Mount only; re-running on `caption` would overwrite typing.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const make = async () => {
    const asked = prompt.trim()
    if (!asked || !sessionId) return
    setMaking(true)
    setError(null)
    try {
      let rows: { id: string }[]
      if (chosen?.figure && suggested) {
        // Figure: box holds the description drawn as shapes.
        rows = [await drawFigure(sessionId, asked, chosen.figure, chatModel || undefined)]
      } else {
        rows = await sessionsApi.images(sessionId, {
          prompt: asked,
          aspect,
          style: chosen?.style ?? '',
          count: 1,
          // An illustration for a slide, not a rendering of it.
          figure: true,
        })
      }
      await loadArtifacts()
      // Select it so insert is enabled.
      if (rows[0]) onPick(rows[0].id)
      setPrompt('')
    } catch (err) {
      // Drawing step's own sentences (undrawable figure) shown as is.
      setError(
        err instanceof Error && !(err instanceof ApiError) && err.message
          ? err.message
          : mediaFailure(err, t('그림을 만들지 못했습니다.'), t),
      )
    } finally {
      setMaking(false)
    }
  }

  return (
    <>
      {canMake ? (
        <div className="mb-3">
          <div className="flex items-center gap-2">
            <Input
              value={prompt}
              onChange={(e) => {
                setPrompt(e.target.value)
                setSuggested(false)
              }}
              onKeyDown={(e) => {
                if (e.key === 'Enter' && !isImeKey(e)) {
                  e.preventDefault()
                  void make()
                }
              }}
              disabled={making || suggesting}
              aria-label={t('그림 설명')}
              placeholder={
                suggesting
                  ? t('넣을 만한 그림을 찾는 중…')
                  : about
                    ? t('{name}에 넣을 그림을 설명해 주세요').replace('{name}', about)
                    : t('넣을 그림을 설명해 주세요')
              }
              className="min-w-0 flex-1"
            />
            <Button
              variant="secondary"
              onClick={() => void make()}
              disabled={making || suggesting || !prompt.trim()}
            >
              {making || suggesting ? (
                <Loader2 size={13} className="animate-spin" />
              ) : (
                <ImagePlus size={13} />
              )}
              {making ? t('만드는 중…') : t('만들기')}
            </Button>
          </div>
          <p className="mt-1.5 flex items-center gap-1 text-xs text-faint">
            {suggested && <Sparkles size={11} className="shrink-0 text-accent" />}
            {suggested && chosen?.figure
              ? t('「{name}」로 그립니다. 고쳐 쓰거나 지우고 직접 적어도 됩니다 — 만들기를 눌러야 크레딧이 듭니다.').replace(
                  '{name}',
                  t(FIGURE_NAMES[chosen.figure] ?? '도식'),
                )
              : suggested
                ? t(
                    '이 장을 읽고 넣을 만한 그림을 적어 두었습니다. 고쳐 쓰거나 지우고 직접 적어도 됩니다 — 만들기를 눌러야 크레딧이 듭니다.',
                  )
                : t('문서 안에 들어갈 삽화로 만듭니다 — 글자는 넣지 않습니다. 크레딧이 듭니다.')}
          </p>
          {error && <p className="mt-2 text-base text-danger">{error}</p>}
        </div>
      ) : (
        <div className="mb-3 rounded-control border border-dashed border-line px-4 py-3 text-center">
          <p className="text-base text-muted">
            {imageOn
              ? t('이 문서는 대화에 매여 있지 않아 여기서 그림을 만들 수 없습니다.')
              : t('이미지 기능이 꺼져 있어 그림을 만들 수 없습니다. 관리자가 설정에서 켤 수 있습니다.')}
          </p>
        </div>
      )}

      {pictures.length === 0 ? (
        canMake && (
          <p className="rounded-control border border-dashed border-line px-4 py-6 text-center text-base text-muted">
            {t('아직 그림이 없습니다. 위에 설명을 적어 만들어 보세요.')}
          </p>
        )
      ) : (
        <div className="grid max-h-64 grid-cols-3 gap-2 overflow-y-auto">
          {pictures.map((picture) => (
            <button
              key={picture.id}
              onClick={() => onPick(picture.id)}
              aria-label={picture.title}
              aria-pressed={picked === picture.id}
              className={cn(
                'aspect-video overflow-hidden rounded-control border-2 transition-colors',
                picked === picture.id ? 'border-accent' : 'border-line hover:border-line-strong',
              )}
            >
              <ArtifactPreview artifact={picture} />
            </button>
          ))}
        </div>
      )}

      {pictures.length > 0 && (
        <div className="mt-3">
          <Input
            value={caption}
            onChange={(e) => onCaption(e.target.value)}
            aria-label={t('설명')}
            placeholder={t('그림 아래에 붙일 설명 (선택)')}
          />
        </div>
      )}
    </>
  )
}
