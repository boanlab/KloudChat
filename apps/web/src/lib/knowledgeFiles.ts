/** What a project's 참고 파일 can read; unreadable files refused before upload. Mirrors `extract_text`. */

import { overLimit } from './uploadLimit'

/** What a project's knowledge accepts, by kind: the file picker and the guidance text both read this. */
const KINDS: { ko: string; en: string; accept: string[] }[] = [
  { ko: 'PDF', en: 'PDF', accept: ['.pdf'] },
  { ko: 'Word', en: 'Word', accept: ['.docx'] },
  { ko: 'PowerPoint', en: 'PowerPoint', accept: ['.pptx'] },
  { ko: '한글', en: 'HWP', accept: ['.hwp', '.hwpx'] },
  { ko: 'Excel·CSV', en: 'Excel, CSV', accept: ['.xlsx', '.csv', '.tsv'] },
  {
    ko: '텍스트·마크다운',
    en: 'text, Markdown',
    accept: ['.txt', '.md', '.markdown', '.json', '.yaml', '.yml', '.toml', '.ini', '.log', '.html', '.xml', '.tex'],
  },
  {
    ko: '코드',
    en: 'code',
    accept: ['.py', '.js', '.ts', '.tsx', '.jsx', '.java', '.c', '.h', '.cpp', '.go', '.rs', '.rb', '.sh', '.sql', '.css', '.r'],
  },
  { ko: '그림', en: 'pictures', accept: ['image/*'] },
  { ko: '음성', en: 'audio', accept: ['audio/*'] },
  { ko: '영상', en: 'video', accept: ['video/*'] },
]

/** For `<input type="file" accept>`. Pictures and recordings are read too. */
export const KNOWLEDGE_ACCEPT = KINDS.flatMap((kind) => kind.accept).join(',')

/** The accepted kinds as one phrase: 「PDF, Word, …, 영상」 / "PDF, Word, … and video". */
export function knowledgeKinds(lang: 'ko' | 'en'): string {
  if (lang === 'ko') return KINDS.map((kind) => kind.ko).join(', ')
  const names = KINDS.map((kind) => kind.en)
  return `${names.slice(0, -1).join(', ')} and ${names.at(-1)}`
}

const PROGRAMS = /\.(exe|dll|msi|bat|cmd|com|scr|apk|dmg|iso|bin|so|dylib|jar|app)$/i

/** Why `file` is not worth uploading as knowledge, or null when it may be. */
export function knowledgeRefusal(file: File, t: (text: string) => string): string | null {
  if (file.size === 0) return t('{name}: 빈 파일이라 올리지 않았습니다.').replace('{name}', file.name)
  if (PROGRAMS.test(file.name)) {
    return t('{name}: 실행 파일은 읽을 수 없어 올리지 않았습니다. 문서·PDF·텍스트 파일을 올리세요.')
      .replace('{name}', file.name)
  }
  const tooLarge = overLimit(file)
  if (tooLarge) return `${file.name}: ${tooLarge}`
  return null
}
