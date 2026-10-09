import { useNavigate } from 'react-router-dom'
import { csvSource, htmlIsDeck } from '@/components/artifacts/artifactExport'
import { errorMessage, fetchArtifactExport, filesApi } from '@/lib/api'
import { currentLang, translate } from '@/lib/i18n'
import { useStore } from '@/store/useStore'
import type { Artifact, SessionKind } from '@/types'

const tr = (text: string) => translate(currentLang(), text)

/** Both open a new conversation of the artifact's own kind with it attached; 「이걸로 새로 시작」
 *  also writes the request into the box, 「새 대화에 첨부」 leaves the box for the person. */
export type ReuseHow = 'restart' | 'attach'

/** Artifacts whose words can travel to a new conversation as a file; pictures and clips cannot. */
export function reusable(artifact: Artifact): boolean {
  return ['report', 'deck', 'html', 'code', 'chart'].includes(artifact.kind)
}

/** The surface a 「새로 시작」 opens: the kind of thing it was. */
function sameKind(artifact: Artifact, templateKind?: string): SessionKind {
  if (artifact.kind === 'report') return 'report'
  if (artifact.kind === 'deck') return 'slides'
  if (artifact.kind === 'html') return htmlIsDeck(artifact.content, templateKind) ? 'slides' : 'report'
  return 'chat'
}

function stem(title: string): string {
  return title.replace(/[\\/:*?"<>|]+/g, '_').slice(0, 60) || 'document'
}

/** The artifact as a text file, by the export the download menu uses. */
async function asFile(artifact: Artifact): Promise<File> {
  const name = stem(artifact.title)
  if (artifact.kind === 'chart') {
    return new File([csvSource(artifact)], `${name}.csv`, { type: 'text/csv' })
  }
  if (artifact.kind === 'code') {
    const res = await fetchArtifactExport(artifact.id, 'source')
    const ext = artifact.language && /^[a-z0-9]{1,8}$/i.test(artifact.language) ? artifact.language : 'txt'
    return new File([await res.blob()], `${name}.${ext}`, { type: 'text/plain' })
  }
  const res = await fetchArtifactExport(artifact.id, 'md')
  return new File([await res.blob()], `${name}.md`, { type: 'text/markdown' })
}

/**
 * Starts a new conversation with the artifact attached as source material, through the
 * ordinary upload path: the composer picks the file up (`pendingAttachment`) and a
 * 「새로 시작」 also drops a request into the box. Nothing is sent on the person's behalf.
 */
export function useReuseArtifact() {
  const navigate = useNavigate()
  const setPendingAttachment = useStore((s) => s.setPendingAttachment)
  const setDraft = useStore((s) => s.setDraft)
  const setNotice = useStore((s) => s.setNotice)
  const enabledKinds = useStore((s) => s.enabledKinds)
  const designTemplates = useStore((s) => s.designTemplates)
  const openArtifact = useStore((s) => s.openArtifact)
  return async (artifact: Artifact, how: ReuseHow) => {
    try {
      const file = await asFile(artifact)
      const row = await filesApi.upload(file)
      const templateKind = 'templateId' in artifact
        ? designTemplates.find((d) => d.id === artifact.templateId)?.kind
        : undefined
      const wanted = sameKind(artifact, templateKind)
      const kind = enabledKinds.includes(wanted) ? wanted : 'chat'
      openArtifact(null)
      setPendingAttachment(row)
      if (how === 'restart') {
        setDraft(
          (kind === 'slides'
            ? tr('「{title}」 내용을 바탕으로 새 슬라이드를 만들어 주세요.')
            : kind === 'report'
              ? tr('「{title}」 내용을 바탕으로 새 보고서를 써 주세요.')
              : tr('「{title}」 내용을 바탕으로 새로 써 주세요.')
          ).replace('{title}', artifact.title),
        )
      }
      navigate(`/new/${kind}`)
    } catch (err) {
      setNotice(errorMessage(err, tr('결과물을 새 대화에 넣지 못했습니다.')))
    }
  }
}
