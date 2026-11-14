import { RotateCcw, Shuffle } from 'lucide-react'
import { useEffect, useMemo, useState } from 'react'
import { Badge, Button, Modal } from '@/components/ui'
import { shuffled, type Flashcard, type StudioItem } from '@/lib/shelf'
import { useT } from '@/lib/useT'
import { Explained, SetExports } from './QuizPlayer'

/** Flashcards: flip (click or space), know / not yet, then the ones not known again; shuffle. */
export function FlashcardPlayer({ item, onClose }: { item: StudioItem | null; onClose: () => void }) {
  const t = useT()
  const all = useMemo(() => item?.content?.cards ?? [], [item])
  const [deck, setDeck] = useState<Flashcard[] | null>(null)
  const [at, setAt] = useState(0)
  const [flipped, setFlipped] = useState(false)
  const [missed, setMissed] = useState<Flashcard[]>([])
  const [error, setError] = useState<string | null>(null)
  const cards = deck ?? all
  const card = cards[at]

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === ' ' && (event.target as HTMLElement)?.tagName !== 'BUTTON') {
        event.preventDefault()
        setFlipped((v) => !v)
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [])

  if (!item) return null
  const restart = (next: Flashcard[]) => {
    setDeck(next)
    setAt(0)
    setFlipped(false)
    setMissed([])
  }
  const mark = (known: boolean) => {
    if (!card) return
    if (!known) setMissed((current) => [...current, card])
    setAt(at + 1)
    setFlipped(false)
  }

  return (
    <Modal open onClose={onClose} title={item.title} width="max-w-xl">
      <div className="flex items-center gap-2" data-testid="flashcard-player">
        <Badge tone="accent">
          {card ? t('{n}/{total}장').replace('{n}', String(at + 1)).replace('{total}', String(cards.length)) : t('결과')}
        </Badge>
        <span className="ml-auto flex gap-1.5">
          <Button size="sm" variant="ghost" onClick={() => restart(shuffled(all))}>
            <Shuffle size={13} />
            {t('섞기')}
          </Button>
          <SetExports item={item} onError={setError} />
        </span>
      </div>
      {error && <p role="alert" className="text-sm text-danger">{error}</p>}
      {card ? (
        <>
          {flipped ? (
            // The back holds source buttons, so it is not itself a button.
            <div
              data-testid="flashcard"
              data-side="back"
              className="min-h-48 w-full rounded-card border border-line-strong bg-elevated px-5 py-6 text-base"
            >
              <Explained item={item} text={card.back} cite={card.cite} />
              <Button size="sm" variant="ghost" className="mt-2" onClick={() => setFlipped(false)}>
                {t('앞면 보기')}
              </Button>
            </div>
          ) : (
            <button
              type="button"
              data-testid="flashcard"
              data-side="front"
              aria-label={t('뒷면 보기')}
              onClick={() => setFlipped(true)}
              className="grid min-h-48 w-full place-items-center rounded-card border border-line-strong bg-elevated px-5 py-6 text-center"
            >
              <span className="text-lg font-semibold">{card.front}</span>
            </button>
          )}
          <p className="text-center text-xs text-faint">{t('카드를 누르거나 스페이스를 눌러 뒤집습니다')}</p>
          <div className="flex justify-center gap-2">
            <Button onClick={() => mark(false)}>{t('아직 모름')}</Button>
            <Button variant="primary" onClick={() => mark(true)}>{t('알아요')}</Button>
          </div>
        </>
      ) : (
        <div className="space-y-3 text-center" data-testid="flashcard-result">
          <p className="text-lg font-semibold">
            {t('{known}/{total}장을 알고 있습니다').replace('{known}', String(cards.length - missed.length)).replace('{total}', String(cards.length))}
          </p>
          <div className="flex justify-center gap-2">
            <Button variant="primary" disabled={!missed.length} onClick={() => restart(missed)}>
              <RotateCcw size={14} />
              {t('모르는 카드만 다시')}
            </Button>
            <Button onClick={() => restart(all)}>{t('처음부터')}</Button>
          </div>
        </div>
      )}
    </Modal>
  )
}
