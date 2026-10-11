import { CheckCircle2, ChevronRight, ImagePlus, Loader2, Pin, ShieldAlert, X } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { Button, Modal, Textarea } from '@/components/ui'
import { ApiError, errorCode, filesApi, issuesApi, type IssueKind, type IssueSessionInfo } from '@/lib/api'
import {
  CREATE_ERROR,
  KIND_LABEL,
  MAX_BODY,
  MAX_SCREENSHOT_BYTES,
  SCREENSHOT_TYPES,
  clientInfo,
  useIssues,
} from '@/lib/issues'
import { kindMeta } from '@/lib/kinds'
import { useT } from '@/lib/useT'
import { cn, fileSize } from '@/lib/utils'
import type { SessionKind } from '@/types'

const KINDS: IssueKind[] = ['bug', 'feature', 'other']

/** 「문의·신고」 form. Mounted once in the shell; opened through `useIssues().openReport`. */
export function IssueReportDialog() {
  const open = useIssues((s) => s.open)
  // Remounted per opening so a closed form starts clean next time.
  return open ? <IssueForm /> : null
}

function IssueForm() {
  const t = useT()
  const navigate = useNavigate()
  const target = useIssues((s) => s.target)
  const close = useIssues((s) => s.close)
  const [kind, setKind] = useState<IssueKind>(target.kind ?? 'bug')
  const [text, setText] = useState('')
  const [shot, setShot] = useState<File | null>(null)
  const [share, setShare] = useState(false)
  const [confirmInternal, setConfirmInternal] = useState(false)
  const [pinMessage, setPinMessage] = useState(Boolean(target.messageId))
  const [pinArtifact, setPinArtifact] = useState(Boolean(target.artifactId))
  const [info, setInfo] = useState<IssueSessionInfo | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [done, setDone] = useState(false)
  const fileInput = useRef<HTMLInputElement>(null)
  const preview = useRef<HTMLCanvasElement>(null)
  const sessionId = target.sessionId

  useEffect(() => {
    if (!sessionId) return
    let live = true
    issuesApi.sessionInfo(sessionId).then(
      (row) => live && setInfo(row),
      () => live && setInfo(null),
    )
    return () => {
      live = false
    }
  }, [sessionId])

  // The preview is drawn from the decoded picture, so no URL made from the picked file
  // reaches the page.
  useEffect(() => {
    const canvas = preview.current
    if (!shot || !canvas) return
    let live = true
    createImageBitmap(shot).then(
      (bitmap) => {
        const context = canvas.getContext('2d')
        if (live && context) {
          // Covers the box like `object-cover`: scaled to fill, centred, the overflow cut.
          const scale = Math.max(canvas.width / bitmap.width, canvas.height / bitmap.height)
          const width = bitmap.width * scale
          const height = bitmap.height * scale
          context.clearRect(0, 0, canvas.width, canvas.height)
          context.drawImage(bitmap, (canvas.width - width) / 2, (canvas.height - height) / 2, width, height)
        }
        bitmap.close()
      },
      () => undefined,
    )
    return () => {
      live = false
    }
  }, [shot])

  const pickShot = (file: File | undefined) => {
    setError(null)
    if (!file) return
    if (!SCREENSHOT_TYPES.includes(file.type)) {
      setError(t('스크린샷은 PNG, JPEG, GIF, WebP 그림만 올릴 수 있습니다.'))
      return
    }
    if (file.size > MAX_SCREENSHOT_BYTES) {
      setError(t('스크린샷은 5MB 이하로 올려 주세요.'))
      return
    }
    setShot(file)
  }

  const strict = Boolean(share && info?.strictLocal)
  const canSend = text.trim().length > 0 && !busy && (!strict || confirmInternal)

  const submit = async () => {
    if (!canSend) return
    setBusy(true)
    setError(null)
    try {
      const uploaded = shot ? await filesApi.upload(shot) : null
      await issuesApi.create({
        kind,
        body: text.trim(),
        sessionId: info ? sessionId : undefined,
        shareSession: Boolean(info) && share,
        confirmInternal: strict && confirmInternal,
        messageId: info && pinMessage ? target.messageId : undefined,
        artifactId: pinArtifact ? target.artifactId : undefined,
        screenshotFileId: uploaded?.id,
        client: clientInfo(),
      })
      setDone(true)
    } catch (err) {
      const code = errorCode(err)
      setError(
        t(CREATE_ERROR[code] ?? (err instanceof ApiError && err.status === 413
          ? '스크린샷은 5MB 이하로 올려 주세요.'
          : '보내지 못했습니다. 잠시 뒤 다시 시도해 주세요.')),
      )
    } finally {
      setBusy(false)
    }
  }

  if (done) {
    return (
      <Modal
        open
        onClose={close}
        title={t('문의·신고')}
        width="max-w-md"
        footer={
          <>
            <Button
              onClick={() => {
                close()
                navigate('/issues')
              }}
            >
              {t('문의·신고 보기')}
            </Button>
            <Button variant="primary" onClick={close}>{t('닫기')}</Button>
          </>
        }
      >
        <div role="status" className="flex items-start gap-2.5 text-base">
          <CheckCircle2 size={18} className="mt-0.5 shrink-0 text-success" />
          <div>
            <p className="font-medium">{t('접수되었습니다')}</p>
            <p className="mt-0.5 text-muted">{t('관리자가 확인하면 문의·신고에서 답변과 처리 상태를 볼 수 있습니다.')}</p>
          </div>
        </div>
      </Modal>
    )
  }

  const sessionKind = info?.kind as SessionKind | undefined

  return (
    <Modal
      open
      onClose={close}
      title={t('문의·신고')}
      description={t('오류를 알리거나, 필요한 기능을 요청하거나, 궁금한 점을 묻습니다.')}
      width="max-w-xl"
      footer={
        <>
          <Button onClick={close}>{t('취소')}</Button>
          <Button variant="primary" disabled={!canSend} onClick={() => void submit()}>
            {busy && <Loader2 size={14} className="animate-spin" />}
            {t('보내기')}
          </Button>
        </>
      }
    >
      <fieldset>
        <legend className="mb-1.5 text-base font-medium">{t('종류')}</legend>
        <div role="radiogroup" aria-label={t('종류')} className="flex flex-wrap gap-1.5">
          {KINDS.map((k) => (
            <button
              key={k}
              type="button"
              role="radio"
              aria-checked={kind === k}
              onClick={() => setKind(k)}
              className={cn(
                'rounded-control border px-3 py-1.5 text-base transition-colors',
                kind === k ? 'border-accent bg-accent-soft font-medium text-accent' : 'border-line text-muted hover:bg-elevated hover:text-fg',
              )}
            >
              {t(KIND_LABEL[k])}
            </button>
          ))}
        </div>
      </fieldset>

      <label className="block space-y-1.5">
        <span className="text-base font-medium">{t('내용')}</span>
        <Textarea
          autoFocus
          rows={5}
          maxLength={MAX_BODY}
          value={text}
          onChange={(e) => setText(e.target.value)}
          placeholder={kind === 'feature'
            ? t('어떤 기능이 있으면 좋을지, 어디에 쓰려는지 적어 주세요.')
            : kind === 'other'
              ? t('궁금한 점이나 알리고 싶은 내용을 적어 주세요.')
              : t('무엇을 했을 때 어떤 결과가 나왔는지, 무엇을 기대했는지 적어 주세요.')}
        />
        <span className="block text-right text-xs tabular-nums text-faint">{text.length}/{MAX_BODY}</span>
      </label>

      {(target.messageId || target.artifactId) && (
        <div className="space-y-1.5">
          <p className="text-base font-medium">{t('함께 볼 곳')}</p>
          <div className="flex flex-wrap gap-1.5">
            {target.messageId && pinMessage && info && (
              <PinChip
                label={t('답변')}
                text={target.messageSnippet || t('고른 답변')}
                onRemove={() => setPinMessage(false)}
              />
            )}
            {target.artifactId && pinArtifact && (
              <PinChip
                label={t('아티팩트')}
                text={target.artifactTitle || t('고른 아티팩트')}
                onRemove={() => setPinArtifact(false)}
              />
            )}
          </div>
        </div>
      )}

      <div className="space-y-1.5">
        <p className="text-base font-medium">{t('스크린샷')} <span className="text-sm font-normal text-faint">{t('(선택)')}</span></p>
        <input
          ref={fileInput}
          type="file"
          accept={SCREENSHOT_TYPES.join(',')}
          className="sr-only"
          aria-label={t('스크린샷 고르기')}
          onChange={(e) => {
            pickShot(e.target.files?.[0])
            e.target.value = ''
          }}
        />
        {shot ? (
          <div className="flex items-center gap-3 rounded-control border border-line bg-elevated/50 p-2">
            <canvas ref={preview} width={160} height={112} role="img" aria-label={t('스크린샷 미리보기')}
              className="h-14 w-20 shrink-0 rounded-control" />
            <span className="min-w-0 flex-1 truncate text-sm">{shot.name} · {fileSize(shot.size)}</span>
            <Button variant="ghost" size="icon" aria-label={t('스크린샷 빼기')} onClick={() => setShot(null)}>
              <X size={14} />
            </Button>
          </div>
        ) : (
          <Button size="sm" onClick={() => fileInput.current?.click()}>
            <ImagePlus size={14} />
            {t('그림 올리기')}
          </Button>
        )}
        <p className="text-xs text-faint">{t('PNG, JPEG, GIF, WebP · 5MB 이하')}</p>
      </div>

      {sessionId && info && (
        <div className="space-y-2 rounded-card border border-line p-3">
          <p className="text-base font-medium">{t('세션 첨부')}</p>
          <p className="text-sm text-muted">
            {sessionKind && kindMeta[sessionKind] ? `${t(kindMeta[sessionKind].label)} · ` : ''}
            <span className="font-medium text-fg">{info.title || t('제목 없는 대화')}</span>
            {' · '}
            {t('메시지 {n}개').replace('{n}', String(info.messageCount))}
            {info.artifactCount > 0 && ` · ${t('아티팩트 {n}개').replace('{n}', String(info.artifactCount))}`}
          </p>
          <label className="flex items-start gap-2 text-base">
            <input
              type="checkbox"
              className="mt-1 size-4 shrink-0 accent-[var(--color-accent)]"
              checked={share}
              onChange={(e) => {
                setShare(e.target.checked)
                if (!e.target.checked) setConfirmInternal(false)
              }}
            />
            <span>{t('이 세션을 관리자에게 공유합니다')}</span>
          </label>
          <p className="text-sm text-faint">
            {share
              ? t('대화 내용, 만든 아티팩트의 판, 단계 기록, 대화의 파일을 지금 상태 그대로 복사해 보냅니다. 나중에 대화를 고치거나 지워도 신고에 담긴 사본은 바뀌지 않습니다.')
              : t('체크하지 않으면 대화 내용은 보내지 않고, 사용한 모델과 단계 상태 같은 기록만 함께 갑니다.')}
          </p>
          {strict && (
            <div role="alert" className="space-y-2 rounded-control border border-warn/40 bg-warn/10 p-2.5 text-sm">
              <p className="flex items-start gap-1.5 text-warn">
                <ShieldAlert size={14} className="mt-0.5 shrink-0" />
                <span>{t('이 대화는 내부 자료 보호 경로(strict-local)로 처리되었습니다. 공유하면 내부 자료가 담긴 대화 내용이 관리자에게 복사됩니다.')}</span>
              </p>
              <label className="flex items-start gap-2 text-fg">
                <input
                  type="checkbox"
                  className="mt-0.5 size-4 shrink-0 accent-[var(--color-accent)]"
                  checked={confirmInternal}
                  onChange={(e) => setConfirmInternal(e.target.checked)}
                />
                <span>{t('내부 자료가 포함된 것을 알고 공유합니다')}</span>
              </label>
            </div>
          )}
        </div>
      )}

      <details className="group text-sm">
        <summary className="flex cursor-pointer list-none items-center gap-1 text-faint hover:text-fg">
          <ChevronRight size={12} className="transition-transform group-open:rotate-90" />
          {t('함께 보내는 정보')}
        </summary>
        <ul className="mt-1.5 list-disc space-y-0.5 pl-5 text-muted">
          <li>{t('앱 빌드, 브라우저, 화면 크기, 언어')}</li>
          {info && <li>{t('대화 종류, 사용한 모델, 에이전트·진행 과정, 단계 상태와 실패 사유, 아티팩트 판 번호')}</li>}
        </ul>
      </details>

      {error && <p role="alert" className="text-sm text-danger">{error}</p>}
    </Modal>
  )
}

function PinChip({ label, text, onRemove }: { label: string; text: string; onRemove: () => void }) {
  const t = useT()
  return (
    <span className="inline-flex max-w-full items-center gap-1.5 rounded-control border border-accent/30 bg-accent-soft px-2 py-1 text-sm text-accent">
      <Pin size={12} className="shrink-0" />
      <span className="shrink-0 font-medium">{label}</span>
      <span className="min-w-0 truncate text-fg">{text}</span>
      <button type="button" onClick={onRemove} aria-label={t('{name} 빼기').replace('{name}', label)} className="shrink-0 rounded-control p-0.5 hover:bg-elevated">
        <X size={12} />
      </button>
    </span>
  )
}
