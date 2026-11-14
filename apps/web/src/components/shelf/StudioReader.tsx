import { ExternalLink } from 'lucide-react'
import { useNavigate } from 'react-router-dom'
import { Markdown } from '@/components/chat/Markdown'
import { Badge, Button, Modal } from '@/components/ui'
import { readableDocument, type StudioItem } from '@/lib/shelf'
import { STUDIO_KINDS } from '@/lib/shelfNames'
import { useT } from '@/lib/useT'
import { CitationLayer } from './CitationLayer'

/** A studio document read in place, each 「[n]」 opening its source at the cited passage; 열기
 * takes it to the document panel (edit, versions, 한글/Word export). */
export function StudioReader({ item, onClose }: { item: StudioItem | null; onClose: () => void }) {
  const t = useT()
  const navigate = useNavigate()
  if (!item?.content) return null
  const content = item.content
  return (
    <Modal open onClose={onClose} title={item.title} description={t(STUDIO_KINDS[item.kind].name)} width="max-w-3xl">
      <div className="flex flex-wrap items-center gap-2">
        {content.strict && <Badge tone="success">strict-local</Badge>}
        <span className="text-sm text-muted">
          {t('자료 {n}개에서').replace('{n}', String(content.sources.length))}
        </span>
        {item.sessionId && item.artifactId && (
          <Button size="sm" className="ml-auto" onClick={() => navigate(`/s/${item.sessionId}?artifact=${item.artifactId}`)}>
            <ExternalLink size={13} />
            {t('문서로 열기')}
          </Button>
        )}
      </div>
      {content.partial.length > 0 && (
        <p className="text-sm text-warn">
          {t('분량이 커서 다음 자료는 고르게 일부만 읽었습니다: {names}').replace('{names}', content.partial.join(', '))}
        </p>
      )}
      <div data-testid="studio-reader">
        <CitationLayer projectId={item.projectId}>
          <Markdown>{readableDocument(content)}</Markdown>
        </CitationLayer>
      </div>
    </Modal>
  )
}
