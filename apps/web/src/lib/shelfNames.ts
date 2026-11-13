/**
 * Every user-facing 지식 서가 feature name, in one place: a rename is an edit here and in the
 * English dictionary (`i18n.ts`, 「지식 서가」 section). Strings that mention a name take it
 * through a placeholder (`{studio}`), so they do not change when the name does. The server
 * keeps its own copy for the titles it writes (`services/shelf_names.py`).
 */
export const SHELF_NAMES = {
  /** The feature as a whole (working name). */
  shelf: '지식 서가',
  sources: '자료',
  guide: '자료 안내',
  studio: '스튜디오',
  notes: '메모함',
} as const

export type StudioKind =
  | 'study_guide'
  | 'faq'
  | 'briefing'
  | 'timeline'
  | 'glossary'
  | 'mindmap'
  | 'quiz'
  | 'flashcards'
  | 'audio'

/** Studio outputs by kind: name and one-line description (Korean source strings). */
export const STUDIO_KINDS: Record<StudioKind, { name: string; about: string }> = {
  study_guide: { name: '학습 가이드', about: '핵심 개념, 복습 문제와 답을 한 문서로' },
  faq: { name: 'FAQ', about: '자료에서 답할 수 있는 질문과 답' },
  briefing: { name: '브리핑 문서', about: '바쁜 사람을 위한 요점·근거·시사점' },
  timeline: { name: '타임라인', about: '자료에 날짜가 있는 사건을 시간순으로' },
  glossary: { name: '용어집', about: '자료에 나오는 용어와 뜻' },
  mindmap: { name: '마인드맵', about: '주제의 구조를 한 장의 도식으로' },
  quiz: { name: '퀴즈', about: '객관식·단답형, 해설과 출처 포함' },
  flashcards: { name: '플래시카드', about: '앞면·뒷면 카드로 외우기' },
  audio: { name: '오디오 개요', about: '두 사람이 대화하듯 설명하는 대본과 음성' },
}

export const DOCUMENT_KINDS: StudioKind[] = ['study_guide', 'faq', 'briefing', 'timeline', 'glossary', 'mindmap']
export const LEARNING_KINDS: StudioKind[] = ['quiz', 'flashcards', 'audio']

/** Fills `{shelf}`, `{studio}`, `{notes}`, `{guide}`, `{sources}` with the translated names. */
export function withNames(text: string, t: (s: string) => string): string {
  return text
    .replace('{shelf}', t(SHELF_NAMES.shelf))
    .replace('{studio}', t(SHELF_NAMES.studio))
    .replace('{notes}', t(SHELF_NAMES.notes))
    .replace('{guide}', t(SHELF_NAMES.guide))
    .replace('{sources}', t(SHELF_NAMES.sources))
}
