import { Download, Loader2, Mic, ShieldCheck } from 'lucide-react'
import { useEffect, useState } from 'react'
import { Badge, Button, Modal } from '@/components/ui'
import { ApiError, fileBlob, shelfApi } from '@/lib/api'
import { poll, saveBlob, type SpeechPlan, type StudioItem } from '@/lib/shelf'
import { useT } from '@/lib/useT'
import { cn } from '@/lib/utils'
import { shelfFailure, speechNote } from './failures'

/** Why speech was refused, in words. */
function speechFailure(code: string | undefined, t: (s: string) => string): string {
  if (code === 'strict_local_no_tts') {
    return t('이 프로젝트 자료는 안에서만 다루도록 정해져 있고, 안에서 돌아가는 음성 모델이 없어 음성을 만들 수 없습니다. 대본은 그대로 쓸 수 있습니다.')
  }
  if (code === 'no_speech_model') return t('쓸 수 있는 음성 모델이 없습니다.')
  if (code === 'speech_failed') return t('음성을 만들지 못했습니다. 다시 시도하세요.')
  return shelfFailure(code, t)
}

/**
 * An audio overview: the two speakers' script, and its speech. A strict-local speech model is
 * used when there is one; an outside one only where internal data may leave and only after
 * the person agrees to send the script out. Otherwise the script alone, with the reason.
 */
export function AudioOverview({ item: initial, onClose }: { item: StudioItem | null; onClose: () => void }) {
  const t = useT()
  const [item, setItem] = useState(initial)
  const [plan, setPlan] = useState<SpeechPlan | null>(null)
  const [consenting, setConsenting] = useState(false)
  const [audio, setAudio] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => setItem(initial), [initial])
  useEffect(() => {
    if (!initial) return
    shelfApi.speechPlan(initial.projectId, initial.options.length ?? 'standard').then(setPlan).catch(() => setPlan(null))
  }, [initial])
  const speaking = item?.speech?.state === 'running'
  useEffect(() => {
    if (!speaking || !item) return
    return poll(() => shelfApi.item(item.id), (next) => next.speech?.state === 'running', setItem)
  }, [speaking, item])
  useEffect(() => {
    if (!item?.audioFileId) return
    let url: string | null = null
    let alive = true
    fileBlob(item.audioFileId).then((blob) => {
      url = URL.createObjectURL(blob)
      if (alive) setAudio(url)
      else URL.revokeObjectURL(url)
    }).catch(() => undefined)
    return () => {
      alive = false
      if (url) URL.revokeObjectURL(url)
    }
  }, [item?.audioFileId])

  if (!item?.content?.lines) return null
  const lines = item.content.lines
  const english = item.content.language === 'en'
  const names = english ? { A: 'Host', B: 'Guide' } : { A: '진행자', B: '해설자' }
  const speak = async (consent: boolean) => {
    setError(null)
    setConsenting(false)
    try {
      setItem(await shelfApi.speech(item.id, consent))
    } catch (err) {
      setError(speechFailure(err instanceof ApiError ? err.detail : undefined, t))
    }
  }
  const script = lines.map((line) => `${names[line.speaker]}: ${line.text}`).join('\n')

  return (
    <Modal open onClose={onClose} title={item.title} width="max-w-2xl">
      <div className="flex flex-wrap items-center gap-2" data-testid="audio-overview">
        {item.content.strict && <Badge tone="success"><ShieldCheck size={11} />strict-local</Badge>}
        <span className="text-sm text-muted">{t('대본 {n}줄').replace('{n}', String(lines.length))}</span>
        <span className="ml-auto flex gap-1.5">
          <Button size="sm" variant="ghost" onClick={() => saveBlob(new Blob([script], { type: 'text/plain' }), `${item.title}.txt`)}>
            <Download size={13} />
            {t('대본 내려받기')}
          </Button>
          {plan && plan.mode !== 'none' && item.speech?.state !== 'done' && (
            <Button
              size="sm"
              variant="primary"
              disabled={speaking}
              onClick={() => (plan.mode === 'outside' ? setConsenting(true) : void speak(false))}
            >
              {speaking ? <Loader2 size={13} className="animate-spin" /> : <Mic size={13} />}
              {speaking
                ? t('음성 만드는 중 {done}/{total}').replace('{done}', String(item.speech?.done ?? 0)).replace('{total}', String(item.speech?.total ?? lines.length))
                : t('음성 만들기')}
            </Button>
          )}
        </span>
      </div>
      {plan && <p className="text-sm text-muted" data-testid="speech-note">{speechNote(plan, t)}</p>}
      {consenting && plan?.mode === 'outside' && (
        <div role="alertdialog" aria-label={t('바깥 음성 모델로 보내기')} className="space-y-2 rounded-control border border-warn/40 bg-warn/10 px-3 py-2.5 text-sm">
          <p>
            {t('대본 {n}자가 바깥 음성 모델({model})로 갑니다. 대본 속 개인정보는 가려서 보냅니다. 보낼까요?')
              .replace('{n}', String(script.length))
              .replace('{model}', plan.model ?? '')}
          </p>
          <div className="flex gap-2">
            <Button size="sm" variant="primary" onClick={() => void speak(true)}>{t('보내고 만들기')}</Button>
            <Button size="sm" onClick={() => setConsenting(false)}>{t('취소')}</Button>
          </div>
        </div>
      )}
      {item.speech?.state === 'failed' && <p role="alert" className="text-sm text-danger">{speechFailure(item.error || 'speech_failed', t)}</p>}
      {error && <p role="alert" className="text-sm text-danger">{error}</p>}
      {audio && (
        // eslint-disable-next-line jsx-a11y/media-has-caption -- the script below is the caption
        <audio controls src={audio} className="w-full" data-testid="audio-player" />
      )}
      <ol className="max-h-[50vh] space-y-2 overflow-y-auto" data-testid="audio-script">
        {lines.map((line, i) => (
          <li key={i} className={cn('flex gap-2 text-base', line.speaker === 'B' && 'pl-4')}>
            <span className={cn('shrink-0 font-medium', line.speaker === 'A' ? 'text-accent' : 'text-success')}>
              {names[line.speaker]}
            </span>
            <span>{line.text}</span>
          </li>
        ))}
      </ol>
    </Modal>
  )
}
