/**
 * What the composer's file picker offers, and what it turns away before uploading. The server
 * (`services/files.py` `extract_text`) reads documents, text and code, pictures and recordings;
 * anything else is tried as text there, so only plainly binary programs and archives are refused
 * here, and empty files, which have nothing to read.
 */

const DOCUMENTS = ['.pdf', '.hwp', '.hwpx', '.docx', '.pptx', '.xlsx', '.doc', '.ppt', '.xls', '.csv', '.tsv']
const TEXT = [
  '.txt', '.md', '.markdown', '.json', '.yaml', '.yml', '.toml', '.ini', '.log', '.py', '.js', '.ts',
  '.tsx', '.jsx', '.java', '.c', '.h', '.cpp', '.go', '.rs', '.rb', '.sh', '.sql', '.html', '.css',
  '.xml', '.tex', '.r',
]

/** The picker's filter; the system dialog still offers 「모든 파일」. */
export const ATTACH_ACCEPT = [...DOCUMENTS, ...TEXT, 'text/*', 'image/*', 'audio/*', 'video/*'].join(',')

const BINARY = new Set([
  '.exe', '.dll', '.msi', '.bat', '.cmd', '.com', '.scr', '.app', '.dmg', '.pkg', '.apk', '.ipa',
  '.bin', '.iso', '.img', '.zip', '.7z', '.rar', '.tar', '.gz', '.tgz', '.bz2', '.xz', '.jar',
  '.so', '.dylib', '.class', '.o', '.obj',
])

export const SUPPORTED_FORMATS = 'PDF·한글(HWP·HWPX)·워드·엑셀·파워포인트·텍스트·CSV·그림·녹음'

/** Why `file` is not uploaded, or null when it may be. */
export function attachRefusal(file: File, t: (key: string) => string): string | null {
  if (file.size === 0) return t('빈 파일입니다')
  const dot = file.name.lastIndexOf('.')
  const suffix = dot >= 0 ? file.name.slice(dot).toLowerCase() : ''
  if (BINARY.has(suffix)) {
    return t('읽을 수 없는 형식입니다. {formats} 파일을 올려 주세요.').replace('{formats}', t(SUPPORTED_FORMATS))
  }
  return null
}
