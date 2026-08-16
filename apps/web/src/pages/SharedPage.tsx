import { FileText, Layers, Lock } from 'lucide-react'
import { useEffect, useState } from 'react'
import { useParams } from 'react-router-dom'
import { Markdown } from '@/components/chat/Markdown'
import { Badge } from '@/components/ui'
import { ApiError, sharesApi, type SharedPayload } from '@/lib/api'
import { currentLang, translate } from '@/lib/i18n'
import { useT } from '@/lib/useT'

/**
 * A shared artifact or conversation: a read-only screen for someone who may
 * not have an account here.
 *
 * Deliberately outside the app shell — no sidebar, no navigation. The URL is
 * the entire permission, so this shows the one thing that was shared and
 * offers no route anywhere else, not even to who shared it.
 */
export function SharedPage() {
  const t = useT()
  const { token = '' } = useParams()
  const [payload, setPayload] = useState<SharedPayload | null>(null)
  const [error, setError] = useState<'gone' | 'signin' | 'failed' | null>(null)

  useEffect(() => {
    let live = true
    sharesApi
      .read(token)
      .then((p) => live && setPayload(p))
      .catch((err) => {
        if (!live) return
        if (err instanceof ApiError && err.status === 401) setError('signin')
        else if (err instanceof ApiError && err.status === 404) setError('gone')
        else setError('failed')
      })
    return () => {
      live = false
    }
  }, [token])

  if (error) {
    return (
      <div className="mx-auto flex min-h-dvh max-w-md flex-col justify-center px-6 text-center">
        <Lock size={22} className="mx-auto mb-3 text-faint" />
        <h1 className="text-lg font-semibold">
          {error === 'signin' ? t('로그인이 필요한 링크입니다') : t('열 수 없는 링크입니다')}
        </h1>
        <p className="mt-1.5 text-base text-muted">
          {error === 'signin'
            ? t('워크스페이스 구성원에게만 공개된 자료입니다. 로그인한 뒤 다시 열어 주세요.')
            : /* 만료·철회·오타를 구분해 말하지 않는다. 모르는 사람에게 그 링크가
                 유효했다고 알려 주는 것은 남의 계정에 대해 말하는 것이다. */
              t('링크가 철회되었거나 주소가 올바르지 않습니다.')}
        </p>
      </div>
    )
  }

  if (!payload) {
    return (
      <div className="mx-auto flex min-h-dvh max-w-md items-center justify-center text-base text-faint">
        {t('불러오는 중…')}
      </div>
    )
  }

  const conversation =
    payload.kind === 'artifact'
      ? ''
      : payload.messages
          .map((m) => `**${m.role === 'user' ? t('질문') : t('답변')}**\n\n${m.content}`)
          .join('\n\n---\n\n')

  // The result first, the conversation that produced it after. A shared deck
  // opened to a one-line prompt and nothing else, which is the wrong way round:
  // the recipient came for the document, not for how it was asked for.
  const body =
    payload.kind === 'artifact'
      ? renderArtifact(payload)
      : payload.artifact
        ? `${renderArtifact(payload.artifact)}\n\n---\n\n## ${t('대화 기록')}\n\n${conversation}`
        : conversation

  return (
    <div className="mx-auto min-h-dvh max-w-3xl px-6 py-10">
      <header className="mb-6 border-b border-line pb-5">
        <div className="mb-2 flex items-center gap-2">
          {payload.kind === 'artifact' ? <FileText size={15} /> : <Layers size={15} />}
          <Badge>{payload.kind === 'artifact' ? t('공유된 결과물') : t('공유된 대화')}</Badge>
          <Badge>{t('읽기 전용')}</Badge>
        </div>
        <h1 className="text-2xl font-semibold tracking-tight">{payload.title}</h1>
      </header>
      <Markdown>{body}</Markdown>
      <footer className="mt-10 border-t border-line pt-4 text-xs text-faint">
        {t('공유된 자료입니다. 원본은 공유한 사람만 수정할 수 있습니다.')}
      </footer>
    </div>
  )
}

/** Artifacts are stored per kind; only the readable ones have a shape here. */
function renderArtifact(payload: { data: unknown }): string {
  const data = (payload.data ?? {}) as {
    sections?: { heading: string; content: string }[]
    slides?: { title: string; bullets?: string[]; body?: string; notes?: string }[]
    content?: string
  }
  if (data.sections) {
    return data.sections.map((s) => `## ${s.heading}\n\n${s.content}`).join('\n\n')
  }
  if (data.slides) {
    return data.slides
      .map((s, i) => {
        const lines = [`## ${i + 1}. ${s.title}`]
        for (const b of s.bullets ?? []) lines.push(`- ${b}`)
        if (s.body) lines.push(`\n> ${s.body}`)
        if (s.notes) lines.push(`\n_${s.notes}_`)
        return lines.join('\n')
      })
      .join('\n\n')
  }
  if (data.content) return `\`\`\`\n${data.content}\n\`\`\``
  return '_' + translate(currentLang(), '이 종류의 결과물은 아직 공유 화면에서 보여 줄 수 없습니다.') + '_'
}
