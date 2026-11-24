import {
  AudioLines, BarChart3, Code2, FileText, FileType, Image as ImageIcon, Presentation, Table2, Video,
} from 'lucide-react'
import type { ArtifactKind } from '@/types'
import type { Translator } from '@/lib/useT'

export const artifactIcon: Record<ArtifactKind, typeof FileText> = {
  report: FileText,
  deck: Presentation,
  chart: BarChart3,
  image: ImageIcon,
  audio: AudioLines,
  video: Video,
  code: Code2,
  html: FileText,
  hwp: FileType,
  docx: FileType,
  pptx: Presentation,
  xlsx: Table2,
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
  hwp: '한글 문서',
  docx: 'Word 문서',
  pptx: 'PowerPoint 문서',
  xlsx: 'Excel 문서',
}

/** The measure that tells one result from another: sections, slides, rows, seconds. */
export function sizeLine(
  kind: string,
  data: Record<string, unknown> | null | undefined,
  t: Translator,
): string {
  // A listing card carries only its first few sections or slides; `partCount` is the whole.
  const count = (key: string) =>
    (key === 'sections' || key === 'slides') && typeof data?.partCount === 'number'
      ? data.partCount
      : Array.isArray(data?.[key]) ? (data![key] as unknown[]).length : 0
  switch (kind) {
    case 'report':
      return t.n('{n}개 절', count('sections'))
    case 'deck':
      return t.n('{n}장', count('slides'), 'deck')
    case 'chart': {
      const table = data?.table as { rows?: unknown[] } | undefined
      return t('데이터 {n}행').replace('{n}', String(table?.rows?.length ?? 0))
    }
    case 'audio':
    case 'video':
      return data?.durationSec ? t.n('{n}초', Number(data.durationSec)) : ''
    case 'image':
      return data?.width && data?.height ? `${data.width}×${data.height}` : String(data?.aspect ?? '')
    case 'code':
    case 'html': {
      const content = typeof data?.content === 'string' ? data.content : ''
      return content ? t.n('{n}줄', content.split('\n').length) : ''
    }
    case 'hwp':
    case 'docx':
    case 'pptx':
    case 'xlsx':
      return typeof data?.size === 'number' ? `${Math.max(1, Math.round(data.size / 1024))} KB` : ''
    default:
      return ''
  }
}

