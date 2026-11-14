import { FileText, Film, Link2, Music, NotebookPen, Type } from 'lucide-react'
import type { SourceKind } from '@/lib/shelf'

/** Icon and label (Korean source string) of each source kind. */
export const SOURCE_ICONS: Record<SourceKind, typeof FileText> = {
  file: FileText,
  web: Link2,
  text: Type,
  note: NotebookPen,
  video: Film,
  audio: Music,
}

export const SOURCE_KIND_LABELS: Record<SourceKind, string> = {
  file: '파일',
  web: '웹페이지',
  text: '텍스트',
  note: '메모',
  video: '동영상',
  audio: '녹음',
}
