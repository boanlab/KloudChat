import { Clapperboard, FileText, Image, MessageSquare, Presentation } from 'lucide-react'
import type { SessionKind } from '@/types'

/** Single source of truth for the five surfaces: nav, empty states, badges and routing read from here. */
export const kindMeta: Record<
  SessionKind,
  {
    label: string
    icon: typeof MessageSquare
    color: string
    /** One line saying what this surface makes; all five must read the same shape. */
    tagline: string
    panelLabel: string
  }
> = {
  chat: {
    label: '챗',
    icon: MessageSquare,
    color: '#5b53e8',
    tagline: '묻고, 파일을 올리고, 필요하면 검색과 코드까지 씁니다',
    panelLabel: '아티팩트 열기',
  },
  report: {
    label: '보고서',
    icon: FileText,
    color: '#0e8a5f',
    tagline: '개요를 잡고 절 단위로 써서 문서로 내보냅니다',
    panelLabel: '보고서 열기',
  },
  slides: {
    label: '슬라이드',
    icon: Presentation,
    color: '#b4780a',
    tagline: '발표 시간에 맞춰 장수를 정하고 노트를 붙입니다',
    panelLabel: '덱 열기',
  },
  image: {
    label: '이미지',
    icon: Image,
    color: '#8b5cf6',
    tagline: '비율과 스타일을 정해 여러 장을 한 번에 만듭니다',
    panelLabel: '이미지 열기',
  },
  av: {
    label: '오디오/동영상',
    icon: Clapperboard,
    color: '#c0392b',
    tagline: '길이와 형식을 정해 만들고 대화 안에서 바로 확인합니다',
    panelLabel: '미디어 열기',
  },
}

export const kindOrder: SessionKind[] = ['chat', 'report', 'slides', 'image', 'av']

/** Icons a project can wear. Shared so the create and edit forms agree. */
export const PROJECT_EMOJIS = ['🧪', '📚', '🛠️', '📊', '🚀', '🧠', '🗂️', '🔬'] as const
