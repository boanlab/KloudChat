import type { MemoryType } from '@/types'

/** What a memory's type is called on screen; `type` itself stays the stored word. */
export const memoryTypeLabel: Record<MemoryType, string> = {
  user: '사용자',
  feedback: '피드백',
  project: '프로젝트',
  reference: '참조',
}
