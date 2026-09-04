/** Work scenario catalogue: persona × job × evidence source × follow-up. */
import { promptFor } from './work-prompts'

export const workPersonas = [
  ['hum-undergrad', '인문대 학부생'],
  ['social-undergrad', '사회과학대 학부생'],
  ['engineering-undergrad', '공대 학부생'],
  ['masters', '석사과정 대학원생'],
  ['doctoral', '박사과정 대학원생'],
  ['administration', '행정직 직장인'],
  ['office', '사무직 직장인'],
  ['research', '연구직 직장인'],
] as const

export const workKinds = [
  ['concept', '낯선 개념을 사례와 반례로 학습한다', 'chat'],
  ['literature', '선행연구와 최신 자료를 찾아 근거표를 만든다', 'chat'],
  ['factcheck', '주장과 수치를 교차 검증하고 출처를 남긴다', 'chat'],
  ['analysis', '표 데이터를 분석하고 차트를 만든다', 'chat'],
  ['report', '목차가 있는 보고서를 작성한다', 'report'],
  ['brief', '한 페이지 의사결정 문서를 작성한다', 'report'],
  ['minutes', '회의 자료를 회의록과 할 일로 바꾼다', 'report'],
  ['slides', '발표 시간에 맞는 발표자료와 발표자 노트를 만든다', 'slides'],
  ['proposal', '대상 독자에 맞춘 제안서를 만든다', 'slides'],
  ['visual', '설명용 도식이나 이미지를 만든다', 'image'],
  ['automation', '반복 업무를 처리하는 코드를 만들고 검증한다', 'chat'],
  ['trend', '최근 동향을 조사해 비교표와 전망을 만든다', 'report'],
] as const

export const evidenceKinds = [
  ['prompt', '사용자가 입력한 조건'],
  ['attachment', '첨부한 PDF·문서·표'],
  ['web', '웹 검색으로 확인한 최신 자료'],
] as const

export const followUps = [
  ['revise', '특정 부분을 수정하고 이전 버전과 비교한다'],
  ['export', '업무 파일로 내보내 다시 연다'],
  ['share', '읽기 전용 링크로 공유하고 권한을 확인한다'],
  ['resume', '기록이나 프로젝트에서 다시 찾아 이어서 작업한다'],
] as const

export interface WorkScenario {
  id: string
  personaId: (typeof workPersonas)[number][0]
  persona: (typeof workPersonas)[number][1]
  workId: (typeof workKinds)[number][0]
  work: (typeof workKinds)[number][1]
  surface: (typeof workKinds)[number][2]
  evidenceId: (typeof evidenceKinds)[number][0]
  evidence: (typeof evidenceKinds)[number][1]
  followUpId: (typeof followUps)[number][0]
  followUp: (typeof followUps)[number][1]
  /** The request as typed, from `work-prompts`. */
  prompt: string
  /** What the finished work has to contain for the row to have passed. */
  expect: string[]
}

export const workScenarios: WorkScenario[] = workPersonas.flatMap(([personaId, persona]) =>
  workKinds.flatMap(([workId, work, surface]) =>
    evidenceKinds.flatMap(([evidenceId, evidence]) =>
      followUps.map(([followUpId, followUp]) => {
        const written = promptFor(personaId, workId)
        return {
          id: `${personaId}.${workId}.${evidenceId}.${followUpId}`,
          personaId,
          persona,
          workId,
          work,
          surface,
          evidenceId,
          evidence,
          followUpId,
          followUp,
          prompt: written.text,
          expect: written.expect,
        }
      }),
    ),
  ),
)
