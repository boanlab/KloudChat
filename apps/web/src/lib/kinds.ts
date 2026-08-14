import { Clapperboard, FileText, Image, MessageSquare, Presentation } from 'lucide-react'
import type { SessionKind } from '@/types'

/**
 * Single source of truth for the five surfaces. Nav, empty states, badges, and
 * routing all read from here so adding a sixth kind is one entry, not a sweep.
 */
export const kindMeta: Record<
  SessionKind,
  {
    label: string
    icon: typeof MessageSquare
    color: string
    /**
     * One parallel line: what this surface makes. Used on the home cards, the
     * empty session screen and the sign-in list, so all five have to read the
     * same shape — a greeting for one and two sentences for another is what
     * makes those lists look ragged.
     */
    tagline: string
    panelLabel: string
    examples: string[]
  }
> = {
  chat: {
    label: '챗',
    icon: MessageSquare,
    color: '#5b53e8',
    tagline: '묻고, 파일을 올리고, 필요하면 검색과 코드까지 씁니다',
    panelLabel: '아티팩트 열기',
    examples: [
      '이 에러 로그에서 원인이 될 만한 것부터 짚어줘',
      '올린 CSV에서 부서별 합계를 내줘',
      '이 문단을 팀에 공유할 메일 한 통으로 줄여줘',
      '두 계약서에서 달라진 조항만 찾아줘',
    ],
  },
  report: {
    label: '보고서',
    icon: FileText,
    color: '#0e8a5f',
    tagline: '개요를 잡고 절 단위로 써서 문서로 내보냅니다',
    panelLabel: '보고서 열기',
    examples: [
      '올린 설문 결과로 분석 보고서를 써줘',
      '도입 후보 3종을 비교하는 기술 검토 보고서',
      '어제 회의 메모를 회의록으로 정리해줘',
      '지난 분기 운영 현황 보고서',
    ],
  },
  slides: {
    label: '슬라이드',
    icon: Presentation,
    color: '#b4780a',
    tagline: '발표 시간에 맞춰 장수를 정하고 노트를 붙입니다',
    panelLabel: '덱 열기',
    examples: [
      '신입 사원 교육용 보안 기초, 20분',
      '이 보고서를 발표 자료로 바꿔줘',
      '고객 미팅용 제안 발표, 10장 이내',
      '학회 발표용 5분 라이트닝 토크',
    ],
  },
  image: {
    label: '이미지',
    icon: Image,
    color: '#8b5cf6',
    tagline: '비율과 스타일을 정해 여러 장을 한 번에 만듭니다',
    panelLabel: '이미지 열기',
    examples: [
      '보고서 표지에 쓸 그림, 글자 없이',
      '발표 슬라이드 배경, 가운데는 비워서',
      '서비스 구조를 보여주는 개념도',
      '사내 공지에 넣을 단순한 아이콘',
    ],
  },
  av: {
    label: '오디오/동영상',
    icon: Clapperboard,
    color: '#c0392b',
    tagline: '길이와 형식을 정해 만들고 작업 카드로 확인합니다',
    panelLabel: '미디어 열기',
    examples: [
      '발표 오프닝에 쓸 4초 영상',
      '슬라이드에 얹을 30초 내레이션',
      '제품 사용 장면을 보여주는 짧은 클립',
      '영상 뒤에 깔 잔잔한 배경음악',
    ],
  },
}

export const kindOrder: SessionKind[] = ['chat', 'report', 'slides', 'image', 'av']

/** Icons a project can wear. Shared so the create and edit forms agree. */
export const PROJECT_EMOJIS = ['🧪', '📚', '🛠️', '📊', '🚀', '🧠', '🗂️', '🔬'] as const
