import { ImagePlus, Loader2, Sparkles } from 'lucide-react'
import { useEffect, useState } from 'react'
import { ArtifactPreview } from '@/components/artifacts/ArtifactPanel'
import { Button, Input } from '@/components/ui'
import { errorMessage, sessionsApi } from '@/lib/api'
import { cn } from '@/lib/utils'
import { useT } from '@/lib/useT'
import { useStore } from '@/store/useStore'

/**
 * Choosing a picture for a slide or a section — or making one, here.
 *
 * Both places used to offer only the choosing half, and when there was nothing
 * to choose they offered a link to the image screen. That link is a page turn
 * in the middle of a sentence: it leaves the document, loses which slide was
 * being filled, and asks somebody to come back and find the control again. The
 * commonest reason for the picker to be empty is that nobody has made a picture
 * *for this document*, which is exactly the moment the page turn costs most.
 *
 * So the prompt lives in the picker. `POST /sessions/{id}/images` is
 * synchronous — the upstream is a completion whose answer is a PNG — so there
 * is nothing to poll and nothing to leave for. What comes back is an ordinary
 * image artifact, which is what the grid below shows and what the insert route
 * already takes: this adds a way in, not a second kind of picture.
 *
 * The image screen is still there and still the place for making several and
 * comparing them. This is for the one you need now.
 */
export function PicturePicker({
  sessionId,
  aspect,
  picked,
  onPick,
  caption,
  onCaption,
  /** What the picture is for, so the prompt box can suggest rather than sit blank. */
  about,
  /** The document's own name, so the suggestion belongs to this document. */
  title,
  /** What this 장/절 already says, so the suggestion does not redraw it. */
  context,
}: {
  /** Whose session the picture is charged to and stored under. */
  sessionId?: string | null
  /** `16:9` for a slide, `4:3` for a figure in a document. */
  aspect: string
  picked: string | null
  onPick: (id: string | null) => void
  caption: string
  onCaption: (value: string) => void
  about?: string
  title?: string
  context?: string
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
  //: Whether what is in the box came from the suggestion rather than a person.
  const [suggested, setSuggested] = useState(false)

  // Same session first: the picture somebody made while writing this document
  // is almost always the one they want in it.
  const pictures = artifacts
    .filter((a) => a.kind === 'image')
    .sort((a, b) => {
      const mine = Number(b.sessionId === sessionId) - Number(a.sessionId === sessionId)
      return mine || +new Date(b.updatedAt) - +new Date(a.updatedAt)
    })
    .slice(0, 24)

  /*
   * The suggestion, asked for the moment the picker opens.
   *
   * The box used to open empty with the 장's name in the placeholder, which
   * asks somebody who wanted a picture here to first become somebody who can
   * describe one — and describing a picture to an image model is a skill, not
   * a preference. Now the proposal arrives written and the decision left is
   * the one that was always theirs: whether this is worth a credit. Editable,
   * replaceable, and never drawn without pressing 만들기.
   *
   * Runs once per opening. A failed suggestion is silent: what it leaves
   * behind is the empty box this had before, which is not an error state.
   */
  useEffect(() => {
    if (!canMake || !sessionId || prompt.trim()) return
    let alive = true
    setSuggesting(true)
    void sessionsApi
      .suggestFigure(sessionId, { title, about, context })
      .then((row) => {
        if (!alive || !row.prompt) return
        setPrompt(row.prompt)
        setSuggested(true)
        // Only when the person has not written one. A caption they typed is
        // theirs and outranks anything proposed here.
        if (row.caption) onCaption(caption.trim() || row.caption)
      })
      .catch(() => undefined)
      .finally(() => {
        if (alive) setSuggesting(false)
      })
    return () => {
      alive = false
    }
    // Once, on mount. Re-running on `caption` would overwrite what somebody is
    // in the middle of typing.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const make = async () => {
    const asked = prompt.trim()
    if (!asked || !sessionId) return
    setMaking(true)
    setError(null)
    try {
      const rows = await sessionsApi.images(sessionId, {
        prompt: asked,
        aspect,
        style: '',
        count: 1,
        // Into a slide, not of one. Without this the first picture anybody made
        // came back as a whole slide — title across the top, chart, three
        // labelled cards — and went inside a slide that already had a title.
        figure: true,
      })
      await loadArtifacts()
      // Selected, not just made. Otherwise the picture appears in the grid and
      // 넣기 stays disabled until somebody notices they have to click it.
      if (rows[0]) onPick(rows[0].id)
      setPrompt('')
    } catch (err) {
      setError(errorMessage(err, t('그림을 만들지 못했습니다.')))
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
                if (e.key === 'Enter' && !e.nativeEvent.isComposing) {
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
            {suggested
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

      {/* Only when there is a box to point at. Where making is unavailable the
          notice above has already said why, and a second panel telling somebody
          to use a control that is not on the screen is worse than silence. */}
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
