import { Bot, Boxes, FileText, Layers, LayoutGrid, Lock } from 'lucide-react'
import { useEffect, useState } from 'react'
import { useParams } from 'react-router-dom'
import { Markdown } from '@/components/chat/Markdown'
import { Badge } from '@/components/ui'
import { ApiError, sharesApi, type SharedContext, type SharedPayload } from '@/lib/api'
import { currentLang, translate } from '@/lib/i18n'
import { useT } from '@/lib/useT'

/**
 * What shaped the answers below, said in the words the app's own empty screen
 * says them in.
 *
 * `StartingFrom` in `pages/SessionPage.tsx` tells the person who is about to
 * type; this tells the person who was sent the result, and both use the same
 * sentences on purpose. A recipient reading a report has no way to know that
 * an agent wrote it, that a project's material was in play, or that the shape
 * was decided before a word was asked — and no one to ask.
 *
 * No 디자인 row, though the in-app panel carries one: what a recipient sees
 * here is the artifact turned back into Markdown, not the document in its own
 * look, and a line promising a look they are not being shown is exactly the
 * untruth that panel exists to end.
 */
function StartedWithPanel({ context }: { context: SharedContext }) {
  const t = useT()
  const english = currentLang() === 'en'
  const rows = [
    context.agent && {
      key: 'agent',
      icon: <Bot size={13} />,
      name: context.agent,
      label: t('이 에이전트가 답합니다'),
    },
    context.project && {
      key: 'project',
      icon: <Boxes size={13} />,
      name: context.project,
      label: t('이 프로젝트의 지침과 자료를 함께 씁니다'),
    },
    context.format && {
      key: 'format',
      icon: <LayoutGrid size={13} />,
      name: (english && context.format.nameEn) || context.format.name,
      label: t('결과물이 이 서식으로 나옵니다'),
    },
  ].filter(Boolean) as { key: string; icon: React.ReactNode; name: string; label: string }[]

  if (!rows.length) return null

  return (
    <div className="mt-4 rounded-card border border-line bg-panel">
      <p className="border-b border-line px-3.5 py-2 text-xs font-medium tracking-wide text-faint uppercase">
        {t('이 대화가 가지고 시작하는 것')}
      </p>
      <div className="divide-y divide-line">
        {rows.map((row) => (
          <div key={row.key} className="flex items-start gap-2.5 px-3.5 py-2.5">
            <span className="mt-0.5 grid size-6 shrink-0 place-items-center rounded-control bg-faint text-white">
              {row.icon}
            </span>
            <p className="min-w-0 text-base">
              <span className="font-medium">{row.name}</span>
              <span className="ml-1.5 text-sm text-faint">{row.label}</span>
            </p>
          </div>
        ))}
      </div>
    </div>
  )
}

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
          .map((m) => {
            // Named on the turn it began, the way the transcript in the app
            // names it. Without this a recipient reads 질문 over a sentence
            // that was only half of what was asked, and has no way to see the
            // other half. The 서식 is left off, because the panel above says
            // it once for the whole document rather than on every turn.
            const from =
              m.role === 'user' && m.startedFrom
                ? `\n\n_${t('시작점 {name}').replace('{name}', m.startedFrom.title)}_`
                : ''
            return `**${m.role === 'user' ? t('질문') : t('답변')}**${from}\n\n${m.content}`
          })
          .join('\n\n---\n\n')

  // Sent with a conversation and never with a bare artifact, which has no
  // conversation to account for.
  const startedWith =
    payload.kind === 'artifact'
      ? null
      : (payload.startedWith ?? null)

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
        {startedWith && <StartedWithPanel context={startedWith} />}
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
