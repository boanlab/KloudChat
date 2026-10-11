/** What a project's 참고 파일 can read; unreadable files refused before upload. Mirrors `extract_text`. */

const READABLE = [
  '.pdf', '.docx', '.pptx', '.xlsx', '.csv', '.tsv', '.hwp', '.hwpx',
  '.txt', '.md', '.markdown', '.json', '.yaml', '.yml', '.toml', '.ini', '.log',
  '.py', '.js', '.ts', '.tsx', '.jsx', '.java', '.c', '.h', '.cpp', '.go', '.rs', '.rb', '.sh',
  '.sql', '.html', '.css', '.xml', '.tex', '.r',
]

/** For `<input type="file" accept>`. Pictures and recordings are read too. */
export const KNOWLEDGE_ACCEPT = [...READABLE, 'image/*', 'audio/*', 'video/*'].join(',')

const PROGRAMS = /\.(exe|dll|msi|bat|cmd|com|scr|apk|dmg|iso|bin|so|dylib|jar|app)$/i

/** Why `file` is not worth uploading as knowledge, or null when it may be. */
export function knowledgeRefusal(file: File, t: (text: string) => string): string | null {
  if (file.size === 0) return t('{name}: 빈 파일이라 올리지 않았습니다.').replace('{name}', file.name)
  if (PROGRAMS.test(file.name)) {
    return t('{name}: 실행 파일은 읽을 수 없어 올리지 않았습니다. 문서·PDF·텍스트 파일을 올리세요.')
      .replace('{name}', file.name)
  }
  return null
}
