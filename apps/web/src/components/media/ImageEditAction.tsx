import { WandSparkles } from 'lucide-react'
import { useId, useState } from 'react'
import { Button } from '@/components/ui'
import { editBlock, imageModelId, type ImageEditSource } from '@/lib/imageEdit'
import { cn } from '@/lib/utils'
import { useT } from '@/lib/useT'
import { useStore } from '@/store/useStore'

/** 「이미지 고치기」: puts the picture in the composer of `sessionId`; disabled with a reason when no model can edit it. */
export function ImageEditAction({
  sessionId,
  source,
  onStart,
  iconOnly = false,
  className,
}: {
  /** The conversation the edit goes to; null: a new image conversation (the file box). */
  sessionId: string | null
  source: Omit<ImageEditSource, 'sessionId'>
  /** Instead of the composer of `sessionId` (the file box opens a conversation first). */
  onStart?: (source: ImageEditSource) => void
  iconOnly?: boolean
  className?: string
}) {
  const t = useT()
  const models = useStore((s) => s.models)
  const sessions = useStore((s) => s.sessions)
  const modelByKind = useStore((s) => s.modelByKind)
  const internalRule = useStore((s) => s.internalDataStrictLocal)
  const setImageEdit = useStore((s) => s.setImageEdit)
  const setSessionModel = useStore((s) => s.setSessionModel)
  const setModel = useStore((s) => s.setModel)
  const setToast = useStore((s) => s.setToast)
  // the reason said under the actions, where a row has room for it
  const [said, setSaid] = useState(false)
  const session = sessionId ? sessions.find((c) => c.id === sessionId) : undefined
  const block = editBlock({
    session,
    modelId: imageModelId(session, modelByKind),
    models,
    internal: source.origin === 'upload' || !!source.internal,
    internalRule,
    madeBy: source.madeBy,
    t,
  })
  const tipId = useId()
  const label = t('이미지 고치기')
  const switchTo = block?.switchTo
  const switchModel = () => {
    if (!switchTo) return
    if (session?.kind === 'image' && sessionId) void setSessionModel(sessionId, switchTo.id)
    else setModel('image', switchTo.id)
  }
  const start = () => {
    if (block && !iconOnly) {
      setSaid((v) => !v)
      return
    }
    if (block) {
      setToast({
        text: block.reason,
        ...(switchTo ? { action: { label: t('모델 바꾸기'), run: switchModel } } : {}),
      })
      return
    }
    const full: ImageEditSource = { ...source, sessionId: sessionId ?? '' }
    if (onStart) onStart(full)
    else setImageEdit(full)
  }
  const button = (
    <Button
      variant="ghost"
      size={iconOnly ? 'icon' : 'sm'}
      data-testid="image-edit"
      aria-disabled={block ? 'true' : undefined}
      aria-describedby={block ? tipId : undefined}
      aria-label={iconOnly ? t('{name} 고치기').replace('{name}', source.name) : undefined}
      title={block ? undefined : t('고칠 내용을 입력창에 적습니다')}
      className={cn(block && 'cursor-not-allowed text-muted opacity-70', className)}
      onClick={start}
    >
      <WandSparkles size={13} />
      {!iconOnly && label}
    </Button>
  )
  return (
    <>
      {block ? (
        // the reason shows on hover, keyboard focus and a tap, and the button names it to a screen reader
        <span className="group relative inline-flex">
          {button}
          <span
            id={tipId}
            role="tooltip"
            data-testid="image-edit-tip"
            className="pointer-events-none absolute bottom-full right-0 z-30 mb-1 hidden w-max max-w-[16rem] rounded-md border border-line bg-elevated px-2.5 py-1.5 text-xs text-fg shadow-lg group-focus-within:block group-hover:block"
          >
            {block.reason}
          </span>
        </span>
      ) : (
        button
      )}
      {switchTo && !iconOnly && (
        <Button variant="ghost" size="sm" data-testid="image-edit-switch" title={switchTo.label} onClick={() => {
          setSaid(false)
          switchModel()
        }}>
          {t('고칠 수 있는 모델로 바꾸기')}
        </Button>
      )}
      {block && said && (
        <span role="status" data-testid="image-edit-reason" className="basis-full px-2.5 text-xs text-muted">
          {block.reason}
        </span>
      )}
    </>
  )
}
