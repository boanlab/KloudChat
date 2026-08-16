import type { SessionKind } from '@/types'

/**
 * Starting points, named after the document someone has to produce.
 *
 * A template carries two things only: **what is being made**, and **what has
 * to be filled in**. The second is `fills`, and it is what the gallery shows.
 * Structure is decided by the surface, and stance by the agent.
 */
export interface Template {
  id: string
  kind: SessionKind
  group: string
  title: string
  /** What you get. One line, no feature list. */
  description: string
  /** What you have to bring. Rendered as chips; keep each to a word or two. */
  fills: string[]
  /** Ends mid-sentence where the person takes over. */
  prompt: string
}

export const templates: Template[] = [
  /* ── report ─────────────────────────────────────────────────────── */
  {
    id: 't_essay',
    kind: 'report',
    group: '업무',
    title: '업무·기술 보고서',
    description: '목적과 독자를 정하고 근거와 다음 행동으로 맺는 문서',
    fills: ['목적', '독자', '분량'],
    prompt:
      '업무·기술 보고서가 필요하다. 확인되지 않은 수치는 쓰지 말고 결론에 다음 행동을 적어 줘.\n\n목적과 독자: ',
  },
  {
    id: 't_lab',
    kind: 'report',
    group: '학업',
    title: '실험 리포트',
    description: '측정값과 오차까지 포함한 실험 보고',
    fills: ['실험 내용', '측정 데이터'],
    prompt: '실험 리포트를 써야 한다. 수식은 LaTeX 로 써 줘.\n\n실험: ',
  },
  {
    id: 't_survey',
    kind: 'report',
    group: '연구',
    title: '설문 분석',
    description: '올린 응답 데이터를 집계하고 해석한 보고서',
    fills: ['설문 파일', '알고 싶은 것'],
    prompt:
      '첨부한 설문 데이터로 보고서를 써 줘. 표본 수를 밝히고, 근거 없는 해석은 하지 말아 줘.\n\n특히 알고 싶은 것: ',
  },
  {
    id: 't_techreview',
    kind: 'report',
    group: '업무',
    title: '기술 검토',
    description: '후보를 비교하고 도입 여부를 판단할 근거',
    fills: ['검토 대상', '판단 기준'],
    prompt:
      '기술 검토 보고서가 필요하다. 수치에는 출처와 조사 시점을 붙여 줘.\n\n검토 대상: ',
  },
  {
    id: 't_incident',
    kind: 'report',
    group: '업무',
    title: '장애 보고',
    description: '무슨 일이 있었고 다시 안 나게 무엇을 바꾸는가',
    fills: ['발생 경위', '로그'],
    prompt:
      '장애 보고서를 써야 한다. 시간순 경위, 원인, 영향 범위, 재발 방지책이 필요하다. 확인되지 않은 원인은 추정이라고 적어 줘.\n\n경위: ',
  },
  {
    id: 't_proposal',
    kind: 'report',
    group: '영업',
    title: '제안서',
    description: '고객 과제에서 시작해 도입 효과로 맺는 문서',
    fills: ['고객사', '과제', '예산 범위'],
    prompt:
      '고객 제안서를 써야 한다. 확인되지 않은 성과 수치는 쓰지 말아 줘.\n\n고객사와 과제: ',
  },

  /* ── slides ─────────────────────────────────────────────────────── */
  {
    id: 't_seminar',
    kind: 'slides',
    group: '학업',
    title: '세미나 발표',
    description: '문제에서 시작해 한계까지 말하는 연구 발표',
    fills: ['주제', '발표 시간'],
    prompt: '연구 세미나 발표 자료가 필요하다.\n\n주제와 시간: ',
  },
  {
    id: 't_case',
    kind: 'slides',
    group: '학업',
    title: '케이스 분석',
    description: '상황을 정리하고 대안을 비교해 하나를 권하는 발표',
    fills: ['기업·상황', '발표 시간'],
    prompt: '케이스 분석 발표 자료가 필요하다. 재무 수치는 출처를 붙여 줘.\n\n기업과 상황: ',
  },
  {
    id: 't_pitch',
    kind: 'slides',
    group: '영업',
    title: '고객 제안 발표',
    description: '고객의 문제에서 열고 다음 단계로 닫는 덱',
    fills: ['고객사', '업종·규모', '미팅 시간'],
    prompt: '고객 미팅용 제안 발표 자료가 필요하다.\n\n고객사: ',
  },
  {
    id: 't_periodic',
    kind: 'slides',
    group: '업무',
    title: '정기 보고',
    description: '실적·이슈·계획 세 덩어리의 주간·월간 보고',
    fills: ['기간', '지표', '이슈'],
    prompt: '정기 보고 자료가 필요하다. 장마다 핵심 메시지 한 줄을 제목으로 써 줘.\n\n기간과 내용: ',
  },
  {
    id: 't_onboarding',
    kind: 'slides',
    group: '업무',
    title: '사내 교육 자료',
    description: '처음 듣는 사람이 따라올 수 있는 설명 덱',
    fills: ['주제', '대상', '사전 지식'],
    prompt:
      '사내 교육 자료가 필요하다. 듣는 사람이 처음이라고 보고, 용어는 처음 나올 때 풀어 써 줘.\n\n주제와 대상: ',
  },

  /* ── chat ───────────────────────────────────────────────────────── */
  {
    id: 't_translate',
    kind: 'chat',
    group: '학업',
    title: '원문 읽기',
    description: '번역과 함께 전공 용어를 정리해 준다',
    fills: ['원문'],
    prompt: '이 원문을 읽어야 한다. 전공 용어는 원어를 함께 적어 줘.\n\n원문: ',
  },
  {
    id: 't_debug',
    kind: 'chat',
    group: '개발',
    title: '장애 원인 좁히기',
    description: '스택 트레이스에서 가설과 확인 방법까지',
    fills: ['에러 로그', '재현 조건'],
    prompt:
      '이 에러의 원인을 좁혀야 한다. 확신이 없는 것은 확인할 방법을 알려 줘.\n\n에러: ',
  },
  {
    id: 't_schema',
    kind: 'chat',
    group: '개발',
    title: '쿼리 짜기',
    description: '스키마를 보고 원하는 집계를 뽑는 SQL',
    fills: ['테이블 구조', '뽑고 싶은 것'],
    prompt: '이 스키마에서 쿼리를 짜야 한다.\n\n스키마와 뽑고 싶은 것: ',
  },
  {
    id: 't_email',
    kind: 'chat',
    group: '업무',
    title: '메일 초안',
    description: '용건이 첫 문장에 오는 짧은 메일',
    fills: ['용건', '받는 사람'],
    prompt: '메일을 써야 한다. 길게 쓰지 말아 줘.\n\n용건과 받는 사람: ',
  },
  {
    id: 't_meeting_prep',
    kind: 'chat',
    group: '영업',
    title: '미팅 준비',
    description: '상대가 물어볼 것과 이쪽이 확인할 것',
    fills: ['고객사', '지난 접촉'],
    prompt: '이 고객사와 미팅을 준비해야 한다.\n\n고객사와 지금까지의 경위: ',
  },
  {
    id: 't_compare',
    kind: 'chat',
    group: '업무',
    title: '자료 대조',
    description: '두 문서의 차이를 항목별로 짚는다',
    fills: ['문서 둘'],
    prompt:
      '두 자료를 대조해야 한다. 달라진 항목만 짚어 주고, 같은 것은 넘어가 줘.\n\n무엇과 무엇: ',
  },

  /* ── image ──────────────────────────────────────────────────────── */
  {
    id: 't_cover',
    kind: 'image',
    group: '학업',
    title: '표지 그림',
    description: '글자 없이 주제만 암시하는 표지용 이미지',
    fills: ['주제'],
    prompt: '표지에 쓸 그림. 글자는 넣지 말고, 주제를 암시하는 정도로. 주제: ',
  },
  {
    id: 't_slidebg',
    kind: 'image',
    group: '업무',
    title: '슬라이드 배경',
    description: '가운데를 비워 둔 저채도 배경',
    fills: ['분위기'],
    prompt: '발표 슬라이드 배경. 글자가 얹힐 가운데는 비우고 채도는 낮게. 분위기: ',
  },
  {
    id: 't_diagram',
    kind: 'image',
    group: '개발',
    title: '개념도',
    description: '구조를 보여 주는 선 위주의 그림',
    fills: ['구조'],
    prompt: '구조를 설명하는 개념도. 선 위주로 단순하게, 라벨 자리는 비워서. 구조: ',
  },

  /* ── audio / video ──────────────────────────────────────────────── */
  {
    id: 't_narration',
    kind: 'av',
    group: '업무',
    title: '발표 내레이션',
    description: '슬라이드에 얹을 음성 해설',
    fills: ['읽을 내용'],
    prompt: '발표에 얹을 내레이션. 또박또박, 문어체 말고 말하듯이.\n\n내용: ',
  },
  {
    id: 't_intro',
    kind: 'av',
    group: '영업',
    title: '오프닝 클립',
    description: '발표 시작에 트는 짧은 영상',
    fills: ['분위기'],
    prompt: '발표 오프닝에 쓸 짧은 영상. 어두운 화면에서 서서히 밝아지는 정도로. 분위기: ',
  },
  {
    id: 't_product',
    kind: 'av',
    group: '영업',
    title: '제품 소개 클립',
    description: '미팅에서 트는 짧은 사용 장면',
    fills: ['제품', '쓰는 장면'],
    prompt: '제품 소개 클립. 자막이 들어갈 아래쪽은 비워서. 제품과 장면: ',
  },
  {
    id: 't_bgm',
    kind: 'av',
    group: '업무',
    title: '배경 음악',
    description: '말소리를 가리지 않는 짧은 루프',
    fills: ['분위기'],
    prompt: '영상 뒤에 깔 배경 음악. 내레이션을 가리지 않게 잔잔하게. 분위기: ',
  },
]

export function templatesFor(kind: SessionKind): Template[] {
  return templates.filter((t) => t.kind === kind)
}
