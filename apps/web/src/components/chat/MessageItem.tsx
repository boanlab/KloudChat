import {
  AudioLines,
  BarChart3,
  Check,
  Copy,
  FileText,
  Image as ImageIcon,
  Paperclip,
  Presentation,
  Sparkles,
  ThumbsDown,
  ThumbsUp,
  TriangleAlert,
  Video,
} from 'lucide-react'
import { useState } from 'react'
import { Button } from '@/components/ui'
import { cn, formatTokens } from '@/lib/utils'
import { useStore } from '@/store/useStore'
import type { ArtifactKind, Message } from '@/types'
import { CompareView } from './CompareView'
import { Markdown } from './Markdown'
import { StepTimeline } from './StepTimeline'
import { copyText } from '@/lib/clipboard'
import { useT } from '@/lib/useT'

const artifactIcon: Record<ArtifactKind, typeof FileText> = {
  report: FileText,
  deck: Presentation,
  chart: BarChart3,
  image: ImageIcon,
  audio: AudioLines,
  video: Video,
  code: FileText,
  html: FileText,
}

const artifactLabel: Record<ArtifactKind, string> = {
  report: '보고서',
  deck: '슬라이드',
  chart: '차트',
  image: '이미지',
  audio: '오디오',
  video: '동영상',
  code: '코드',
  html: 'HTML',
}

export function MessageItem({
  message,
  sessionId,
  streaming,
}: {
  message: Message
  sessionId: string
  streaming?: boolean
}) {
  const t = useT()
  const { artifacts, openArtifact, rateMessage, models, user } = useStore()
  const [copied, setCopied] = useState(false)
  const model = models.find((m) => m.id === message.model)

  const copyButton = (label: string) => (
    <Button
      variant="ghost"
      size="icon"
      aria-label={label}
      onClick={async () => {
        if (!(await copyText(message.content))) return
        setCopied(true)
        setTimeout(() => setCopied(false), 1400)
      }}
    >
      {copied ? <Check size={14} /> : <Copy size={14} />}
    </Button>
  )

  if (message.role === 'user') {
    return (
      // A prompt is worth copying as often as an answer is — to run again with
      // one word changed, to paste into a colleague's chat, to keep. It was
      // reachable only by selecting the text by hand.
      <div className="group animate-fade-up flex items-start justify-end gap-1">
        <span className="mt-1 opacity-0 transition-opacity group-hover:opacity-100 focus-within:opacity-100">
          {copyButton('프롬프트 복사')}
        </span>
        <div className="max-w-[80%] space-y-2">
          {message.attachments?.map((a) => (
            <div
              key={a.name}
              className="ml-auto flex w-fit items-center gap-2 rounded-lg border border-line bg-panel px-2.5 py-1.5 text-[13px]"
            >
              <Paperclip size={13} className="text-faint" />
              <span>{a.name}</span>
              <span className="text-faint">{a.size}</span>
            </div>
          ))}
          <div className="rounded-2xl rounded-br-md bg-elevated px-4 py-2.5 text-[15px] leading-[1.7] whitespace-pre-wrap">
            {message.content}
          </div>
        </div>
      </div>
    )
  }

  const linked = (message.artifactIds ?? [])
    .map((id) => artifacts.find((a) => a.id === id))
    .filter((a) => a !== undefined)

  return (
    <div className="animate-fade-up group flex gap-3">
      <div className="mt-0.5 grid size-7 shrink-0 place-items-center rounded-lg bg-accent text-accent-fg">
        <Sparkles size={14} />
      </div>
      <div className="min-w-0 flex-1">
        {message.steps && message.steps.length > 0 && (
          <StepTimeline steps={message.steps} live={!!streaming} />
        )}

        {message.variants ? (
          <CompareView
            variants={message.variants}
            sessionId={sessionId}
            messageId={message.id}
          />
        ) : message.content ? (
          <Markdown>{message.content}</Markdown>
        ) : (
          // Not while an error is showing: a failed turn with a blinking
          // "thinking…" under it reads as still running.
          !message.steps?.length &&
          !message.error && (
            <p className="animate-blink text-[15px] text-faint">{t('생각하는 중…')}</p>
          )
        )}
        {streaming && message.content && (
          <span className="ml-0.5 inline-block h-4 w-[2px] translate-y-0.5 animate-blink bg-accent" />
        )}

        {/* Below the answer, not instead of it. A turn that failed halfway has
            two things to say — what it managed to write, and that it stopped —
            and the reader needs both to decide whether to run it again. */}
        {message.error && (
          <div
            role="status"
            className="mt-3 flex items-start gap-2 rounded-xl border border-danger/30 bg-danger/5 px-3 py-2.5 text-[13px] text-danger"
          >
            <TriangleAlert size={14} className="mt-0.5 shrink-0" />
            <span>{message.error}</span>
          </div>
        )}

        {linked.length > 0 && (
          <div className="mt-3 flex flex-wrap gap-2">
            {linked.map((a) => {
              const Icon = artifactIcon[a.kind]
              return (
                <button
                  key={a.id}
                  onClick={() => openArtifact(a.id)}
                  className="flex items-center gap-2 rounded-xl border border-line bg-panel px-3 py-2 text-left transition-colors hover:border-accent hover:bg-elevated"
                >
                  <span className="grid size-7 place-items-center rounded-lg bg-accent-soft text-accent">
                    <Icon size={14} />
                  </span>
                  <span>
                    <span className="block text-[13px] font-medium">{a.title}</span>
                    <span className="block text-[11px] text-faint">
                      {artifactLabel[a.kind]} · v{a.version}
                    </span>
                  </span>
                </button>
              )
            })}
          </div>
        )}

        {!streaming && message.content && !message.variants && (
          <div className="mt-2 flex items-center gap-1 text-faint">
            <span className="flex items-center gap-1 opacity-0 transition-opacity group-hover:opacity-100 focus-within:opacity-100">
            {copyButton(t('복사'))}
            <Button
              variant="ghost"
              size="icon"
              aria-label={t('좋아요')}
              className={cn(message.liked === 'up' && 'text-success')}
              onClick={() => rateMessage(sessionId, message.id, 'up')}
            >
              <ThumbsUp size={14} />
            </Button>
            <Button
              variant="ghost"
              size="icon"
              aria-label={t('싫어요')}
              className={cn(message.liked === 'down' && 'text-danger')}
              onClick={() => rateMessage(sessionId, message.id, 'down')}
            >
              <ThumbsDown size={14} />
            </Button>
            </span>
            {/* Which model answered and what it cost. Kept visible rather than
                behind a hover: on a shared proxy with a monthly allowance, "what
                did that just cost me" is not a detail you go looking for. */}
            {message.usage && user?.preferences.showUsage !== false && (
              <span className="ml-1 text-[11px]">
                {model?.label ?? message.model} · {formatTokens(message.usage.inputTokens)} in ·{' '}
                {formatTokens(message.usage.outputTokens)} out ·{' '}
                {message.usage.credits > 0 ? (
                  t('{n} 크레딧').replace('{n}', message.usage.credits.toLocaleString())
                ) : (
                  <span
                    title={
                      message.model?.startsWith('local/')
                        ? t('직접 운영하는 모델이라 크레딧이 차감되지 않습니다')
                        : t('OpenRouter 가 무료로 제공하는 모델입니다')
                    }
                  >
                    {t('무료')}
                  </span>
                )}
              </span>
            )}
          </div>
        )}
      </div>
    </div>
  )
}
