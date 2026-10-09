import { AudioLines, BarChart3, Code2, FileText, Image as ImageIcon, Presentation, Video } from 'lucide-react'
import type { ArtifactKind } from '@/types'

export const artifactIcon: Record<ArtifactKind, typeof FileText> = {
  report: FileText,
  deck: Presentation,
  chart: BarChart3,
  image: ImageIcon,
  audio: AudioLines,
  video: Video,
  code: Code2,
  html: FileText,
}

export const artifactLabel: Record<ArtifactKind, string> = {
  report: '보고서',
  deck: '슬라이드',
  chart: '차트',
  image: '이미지',
  audio: '오디오',
  video: '동영상',
  code: '코드',
  html: 'HTML',
}

/** The measure that tells one result from another: sections, slides, rows, seconds. */
export function sizeLine(
  kind: string,
  data: Record<string, unknown> | null | undefined,
  t: (text: string) => string,
): string {
  const count = (key: string) => (Array.isArray(data?.[key]) ? (data![key] as unknown[]).length : 0)
  switch (kind) {
    case 'report':
      return t('{n}개 절').replace('{n}', String(count('sections')))
    case 'deck':
      return t('{n}장').replace('{n}', String(count('slides')))
    case 'chart': {
      const table = data?.table as { rows?: unknown[] } | undefined
      return t('데이터 {n}행').replace('{n}', String(table?.rows?.length ?? 0))
    }
    case 'audio':
    case 'video':
      return data?.durationSec ? t('{n}초').replace('{n}', String(data.durationSec)) : ''
    case 'image':
      return data?.width && data?.height ? `${data.width}×${data.height}` : String(data?.aspect ?? '')
    case 'code':
    case 'html': {
      const content = typeof data?.content === 'string' ? data.content : ''
      return content ? t('{n}줄').replace('{n}', String(content.split('\n').length)) : ''
    }
    default:
      return ''
  }
}

