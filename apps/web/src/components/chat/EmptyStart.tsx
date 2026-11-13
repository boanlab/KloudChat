import { kindMeta } from '@/lib/kinds'
import { useT } from '@/lib/useT'
import { cn } from '@/lib/utils'
import type { SessionKind } from '@/types'
import { fillComposer } from './Composer'

/**
 * What an empty start shows, the same on the home screen and in a conversation with nothing
 * sent yet (a new one, one started from a project or an agent, one reopened from history):
 * a heading and the surface's examples, a click on one filling the prompt box.
 */

/** Per-surface examples under the greeting; click fills the composer. */
const START_EXAMPLES: Record<SessionKind, string[]> = {
  chat: [
    '첨부한 문서를 세 줄로 요약해 줘',
    '다음 주 회의 안건 초안을 같이 잡아 줘',
    '이 문장을 더 자연스럽게 다듬어 줘',
    '엑셀 수식이 왜 오류가 나는지 찾아 줘',
  ],
  report: [
    '신규 서비스 도입을 검토하는 보고서를 써 줘',
    '지난 분기 사업 실적 보고서 — 요약, 성과, 남은 과제',
    '두 가지 안을 기준별로 비교하는 의사결정 보고서',
    '첨부한 자료로 연구 과제 중간 보고서 초안을 써 줘',
  ],
  // Only what the shown deck template (사내 브리핑) makes.
  slides: [
    '다음 주 회의 전에 돌릴 사내 브리핑 5장',
    '프로젝트 결과 보고 15분 발표',
    '새 제도 시행을 팀에 알리는 사내 브리핑',
    '이번 분기 진행 현황과 결정할 것을 정리한 발표',
  ],
  image: [
    '봄 행사 포스터에 쓸 밝은 파스텔 톤 일러스트',
    '홈페이지 메인 배너용 사무실 풍경 사진',
    '사용자 → 게이트웨이 → 모델로 이어지는 흐름도',
    '미니멀한 제품 아이콘 네 개',
  ],
  av: [
    '제품을 소개하는 30초 내레이션',
    '발표 시작에 틀 잔잔한 배경 음악 30초',
    '바다 위로 해가 뜨는 6초 영상',
  ],
}

/** Also the `/new/:kind` entry. */

/** The start's heading: who or what, and one line of what to do. */
export function StartHeading({ title, subtitle }: { title: string; subtitle: string }) {
  return (
    <>
      <h1 className="text-2xl font-semibold tracking-tight">{title}</h1>
      <p className="mt-1 text-base text-muted">{subtitle}</p>
    </>
  )
}

export function StartExamples({ kind, className }: { kind: SessionKind; className?: string }) {
  const t = useT()
  const Icon = kindMeta[kind].icon
  const examples = START_EXAMPLES[kind] ?? []
  if (examples.length === 0) return null
  return (
    <section aria-label={t('예시')} className={cn('mx-auto w-full max-w-3xl', className)}>
      <div className="grid gap-2 sm:grid-cols-2">
        {examples.map((example, index) => (
          <button
            key={example}
            type="button"
            onClick={() => fillComposer(t(example))}
            // Phone shows three; a fourth pushes the composer's neighbours off screen.
            className={cn(
              'flex min-h-11 items-center gap-2 rounded-card border border-line bg-panel px-3 py-2 text-left text-base text-muted transition-colors hover:bg-elevated hover:text-fg',
              index >= 3 && 'phone:hidden',
            )}
          >
            <Icon size={14} className="shrink-0 text-faint" />
            <span className="min-w-0 flex-1">{t(example)}</span>
          </button>
        ))}
      </div>
    </section>
  )
}
