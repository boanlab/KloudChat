/**
 * What each persona actually types.
 *
 * The catalogue used to build its prompt by filling a sentence:
 * `인문대 학부생로서 첨부한 PDF를 근거로 보고서를 작성한다.` Every row was
 * well-formed, no row was a request anybody would make, and a run of them
 * exercised one code path 1,152 times. A model given a description of a task
 * writes about the task; a model given the task does the task, and only the
 * second one can be judged.
 *
 * So a prompt here is the sentence the person would type, in their own
 * vocabulary, with the nouns of their field in it — and each carries the thing
 * that makes the answer checkable: a figure to be found, a term to be defined,
 * a comparison to be drawn. `expect` is what a reader of the result would look
 * for, and the runner checks it rather than checking that bytes arrived.
 */

export interface WorkPrompt {
  /** The request, as typed. */
  text: string
  /**
   * Substrings the finished work must contain, judged case-insensitively.
   * Kept to things the *request itself* forces — a topic word, a required
   * section, a unit — never to a fact the model would have to invent.
   */
  expect: string[]
}

/** `persona.work` → what that person types for that job. */
export const workPrompts: Record<string, WorkPrompt> = {
  // ── 인문대 학부생 ──────────────────────────────────────────────────
  'hum-undergrad.concept': {
    text: '사학과 3학년입니다. 「미시사(microhistory)」가 무엇인지, 기존 사회사와 무엇이 다른지 사례 두 개와 반례 하나를 들어 설명해 주세요.',
    expect: ['미시사'],
  },
  'hum-undergrad.literature': {
    text: '조선 후기 신분제 동요를 다룬 국내 연구 흐름을 정리해 주세요. 주요 논쟁 축과 대표 연구자를 표로 만들고, 확인하지 못한 서지는 "확인 필요"로 표시해 주세요.',
    expect: ['조선'],
  },
  'hum-undergrad.factcheck': {
    text: '"조선시대 평균 수명은 24세였다"는 주장이 자주 인용됩니다. 이 수치의 출처와 산출 방식을 확인하고, 영아 사망률이 평균에 미치는 영향을 함께 설명해 주세요.',
    expect: ['수명'],
  },
  'hum-undergrad.analysis': {
    text: '연도별 서원 건립 수를 정리한 표가 있습니다. 100년 단위로 집계하고 증감 추세를 막대 차트로 보여 주세요.',
    expect: ['서원'],
  },
  'hum-undergrad.report': {
    text: '「기말 리포트: 조선 후기 서원의 사회적 기능 변화」를 씁니다. 서론·본론 3절·결론 구성으로, 각 절에 근거를 달아 주세요. 인용은 시카고 양식으로 표기해 주세요.',
    expect: ['서원'],
  },
  'hum-undergrad.brief': {
    text: '지도교수님께 드릴 한 장짜리 논문 주제 제안서를 써 주세요. 연구 질문, 사료, 예상 기여, 남은 위험 순서로 담아 주세요.',
    expect: ['연구'],
  },
  'hum-undergrad.minutes': {
    text: '학과 세미나 녹취를 회의록으로 바꿔 주세요. 결정 사항, 반론, 다음 발표자와 기한을 나눠 정리해 주세요.',
    expect: ['결정'],
  },
  'hum-undergrad.slides': {
    text: '수업 발표 12분 분량 슬라이드를 만들어 주세요. 주제는 「사료 비판의 방법」이고, 장마다 실제로 말할 발표 노트를 붙여 주세요.',
    expect: ['사료'],
  },
  'hum-undergrad.proposal': {
    text: '학부 연구지원사업 신청 발표자료를 만들어 주세요. 심사위원은 타 전공 교수님들이니 전문 용어는 풀어서 써 주세요.',
    expect: ['연구'],
  },
  'hum-undergrad.visual': {
    text: '「사료의 종류와 비판 절차」를 한 장으로 보여 주는 개념도를 만들어 주세요. 글자는 넣지 말고 자리만 남겨 주세요.',
    expect: [],
  },
  'hum-undergrad.automation': {
    text: '참고문헌 목록이 담긴 텍스트에서 저자·연도·제목을 뽑아 시카고 양식으로 다시 쓰는 파이썬 코드를 만들고, 예시 5건으로 실행해 결과를 보여 주세요.',
    expect: ['python', '파이썬', '저자'],
  },
  'hum-undergrad.trend': {
    text: '최근 5년 디지털 인문학 연구 동향을 조사해 주세요. 주요 방법론, 대표 프로젝트, 국내 도입 현황을 비교표로 만들고 출처를 남겨 주세요.',
    expect: ['인문학'],
  },

  // ── 사회과학대 학부생 ──────────────────────────────────────────────
  'social-undergrad.concept': {
    text: '심리학과 3학년입니다. 「생태학적 타당도(ecological validity)」와 「외적 타당도」의 차이를 실험 사례로 설명해 주세요.',
    expect: ['타당도'],
  },
  'social-undergrad.literature': {
    text: 'SNS 사용과 청소년 우울의 관계를 다룬 연구들을 정리해 주세요. 표본, 측정 도구, 효과 크기, 결론을 표로 만들고 상반된 결과도 함께 실어 주세요.',
    expect: ['우울'],
  },
  'social-undergrad.factcheck': {
    text: '"한국 청소년의 스마트폰 과의존 비율이 40%를 넘는다"는 보도를 검증해 주세요. 조사 주체, 정의, 표본, 연도를 확인하고 다른 조사와 비교해 주세요.',
    expect: ['과의존'],
  },
  'social-undergrad.analysis': {
    text: '설문 응답 표가 있습니다. 문항별 평균과 표준편차를 구하고, 성별 차이가 통계적으로 유의한지 검정한 뒤 결과를 차트로 보여 주세요.',
    expect: ['평균'],
  },
  'social-undergrad.report': {
    text: '「청소년 SNS 사용과 자아존중감」 조사 보고서를 써 주세요. 연구 문제·방법·결과·논의·한계 구성으로, 수치에는 반드시 출처를 달아 주세요.',
    expect: ['자아존중감'],
  },
  'social-undergrad.brief': {
    text: '학회 발표 초록을 한 장으로 써 주세요. 배경, 방법, 결과, 함의를 각 한 문단으로 담아 주세요.',
    expect: ['방법'],
  },
  'social-undergrad.minutes': {
    text: '연구팀 회의 메모를 회의록으로 정리해 주세요. 결정·조치·미결로 나누고 담당자와 기한을 표로 만들어 주세요.',
    expect: ['조치'],
  },
  'social-undergrad.slides': {
    text: '15분짜리 조사 결과 발표자료를 만들어 주세요. 방법론 한 장, 결과 세 장, 한계 한 장으로 구성하고 발표 노트를 붙여 주세요.',
    expect: ['결과'],
  },
  'social-undergrad.proposal': {
    text: '교내 연구윤리위원회(IRB)에 낼 연구계획 발표자료를 만들어 주세요. 참가자 보호와 동의 절차를 반드시 한 장으로 다뤄 주세요.',
    expect: ['동의'],
  },
  'social-undergrad.visual': {
    text: '「매개효과와 조절효과의 차이」를 보여 주는 도식을 만들어 주세요. 화살표 구조만 보이고 이름표는 넣지 말아 주세요.',
    expect: [],
  },
  'social-undergrad.automation': {
    text: '설문 CSV에서 역문항을 되돌리고 척도별 합산 점수를 만드는 파이썬 코드를 작성하고, 예시 데이터로 검산해 주세요.',
    expect: ['python', '파이썬'],
  },
  'social-undergrad.trend': {
    text: '최근 3년 사회과학 분야의 사전등록(preregistration) 확산 동향을 조사해 주세요. 학술지 정책 변화와 국내 현황을 비교표로 정리하고 출처를 남겨 주세요.',
    expect: ['등록'],
  },

  // ── 공대 학부생 ────────────────────────────────────────────────────
  'engineering-undergrad.concept': {
    text: '전자공학과 2학년입니다. 「나이퀴스트 샘플링 정리」를 에일리어싱 사례와 함께 설명하고, 실제로 언제 문제가 되는지 알려 주세요.',
    expect: ['샘플링'],
  },
  'engineering-undergrad.literature': {
    text: '저전력 임베디드 추론 가속기 연구 흐름을 정리해 주세요. 접근 방식, 대표 논문, 보고된 성능을 표로 만들어 주세요.',
    expect: ['전력'],
  },
  'engineering-undergrad.factcheck': {
    text: '"USB-C는 최대 240W까지 전력을 공급한다"는 설명이 맞는지 규격 기준으로 확인하고, 조건과 예외를 정리해 주세요.',
    expect: ['USB'],
  },
  'engineering-undergrad.analysis': {
    text: '측정 로그가 있습니다. 시간에 따른 전압 변동의 평균·분산을 구하고 이상치를 표시한 선 차트를 만들어 주세요.',
    expect: ['전압'],
  },
  'engineering-undergrad.report': {
    text: '「RC 저역통과 필터 특성 실험」 결과 보고서를 써 주세요. 목적·이론·장치·절차·결과·오차 분석 구성으로, 측정값의 단위와 유효숫자를 맞춰 주세요.',
    expect: ['필터'],
  },
  'engineering-undergrad.brief': {
    text: '캡스톤 팀에 낼 한 장짜리 설계 변경 제안서를 써 주세요. 무엇을 왜 바꾸는지, 비용과 일정 영향, 되돌릴 방법을 담아 주세요.',
    expect: ['설계'],
  },
  'engineering-undergrad.minutes': {
    text: '캡스톤 주간 회의 메모를 회의록으로 정리해 주세요. 결정, 블로커, 담당자, 기한으로 나눠 주세요.',
    expect: ['기한'],
  },
  'engineering-undergrad.slides': {
    text: '캡스톤 중간발표 10분 슬라이드를 만들어 주세요. 문제 정의, 설계, 구현 현황, 남은 일정 순서로 만들고 발표 노트를 붙여 주세요.',
    expect: ['설계'],
  },
  'engineering-undergrad.proposal': {
    text: '산학 과제 제안 발표자료를 만들어 주세요. 기업 담당자가 듣는다는 전제로 기술 용어를 최소화하고 기대 효과를 수치로 보여 주세요.',
    expect: ['효과'],
  },
  'engineering-undergrad.visual': {
    text: '「센서에서 클라우드까지의 데이터 흐름」을 왼쪽에서 오른쪽으로 흐르는 구조도로 만들어 주세요. 글자는 넣지 말아 주세요.',
    expect: [],
  },
  'engineering-undergrad.automation': {
    text: '오실로스코프 CSV 여러 개를 읽어 채널별 RMS를 계산하고 표로 출력하는 파이썬 코드를 만들고, 예시로 실행해 검산해 주세요.',
    expect: ['python', '파이썬', 'rms'],
  },
  'engineering-undergrad.trend': {
    text: '최근 2년 온디바이스 AI 반도체 동향을 조사해 주세요. 주요 업체, 발표 스펙, 전력 대비 성능을 비교표로 만들고 출처를 남겨 주세요.',
    expect: ['온디바이스'],
  },

  // ── 석사과정 대학원생 ──────────────────────────────────────────────
  'masters.concept': {
    text: '석사과정입니다. 「도메인 적응(domain adaptation)」과 「전이학습」의 관계를 정리하고, 언제 어느 쪽 용어를 써야 하는지 알려 주세요.',
    expect: ['전이학습'],
  },
  'masters.literature': {
    text: '소량 데이터 환경의 파인튜닝 기법 선행연구를 정리해 주세요. 기법, 데이터 규모, 보고된 성능, 한계를 표로 만들고 미확인 항목은 표시해 주세요.',
    expect: ['파인튜닝'],
  },
  'masters.factcheck': {
    text: '제 초록에 "제안 기법이 기존 대비 12% 향상"이라고 썼습니다. 이 표현이 무엇 대비인지, 어떤 지표인지 분명한지 검토하고 고쳐 써 주세요.',
    expect: ['지표'],
  },
  'masters.analysis': {
    text: '실험 로그가 있습니다. epoch별 loss와 macro F1을 정리하고 최고 성능 지점을 표로 만든 뒤 학습 곡선을 차트로 보여 주세요.',
    expect: ['epoch'],
  },
  'masters.report': {
    text: '학위논문 3장 「제안 방법」 초안을 써 주세요. 문제 정의, 전체 구조, 구성 요소별 설명, 복잡도 분석 순서로, 수식에는 기호 정의를 붙여 주세요.',
    expect: ['제안'],
  },
  'masters.brief': {
    text: '지도교수님 면담용 한 장 요약을 써 주세요. 이번 주 진행, 막힌 지점, 다음 주 계획, 결정이 필요한 것 순서로 담아 주세요.',
    expect: ['계획'],
  },
  'masters.minutes': {
    text: '랩 미팅 메모를 회의록으로 바꿔 주세요. 피드백, 수용 여부, 실험 재설계 항목을 표로 정리해 주세요.',
    expect: ['피드백'],
  },
  'masters.slides': {
    text: '랩 세미나 20분 발표자료를 만들어 주세요. 배경, 문제, 제안 방법, 실험 설계, 예비 결과 순서로 만들고 발표 노트를 붙여 주세요.',
    expect: ['실험'],
  },
  'masters.proposal': {
    text: '학위논문 연구계획 발표자료를 만들어 주세요. 심사위원 질문을 예상해 마지막에 대비 장을 넣어 주세요.',
    expect: ['연구'],
  },
  'masters.visual': {
    text: '제안 모델의 전체 구조를 논문 그림처럼 만들어 주세요. 층으로 쌓인 형태에 가는 선의 도면 느낌으로, 글자는 넣지 말아 주세요.',
    expect: [],
  },
  'masters.automation': {
    text: '실험 결과 JSON 여러 개를 모아 설정별 평균과 표준편차를 표로 만드는 파이썬 코드를 작성하고, 예시로 실행해 결과를 보여 주세요.',
    expect: ['python', '파이썬'],
  },
  'masters.trend': {
    text: '최근 1년 파라미터 효율 파인튜닝(PEFT) 동향을 조사해 주세요. 기법 계열, 대표 연구, 메모리·성능 절충을 비교표로 만들고 출처를 남겨 주세요.',
    expect: ['peft', '파인튜닝'],
  },

  // ── 박사과정 대학원생 ──────────────────────────────────────────────
  'doctoral.concept': {
    text: '박사과정 4년차입니다. 「인과추론에서의 백도어 기준」을 그래프 예시와 함께 설명하고, 프론트도어 기준과 언제 갈리는지 알려 주세요.',
    expect: ['백도어'],
  },
  'doctoral.literature': {
    text: '제 분야의 최근 5년 리뷰 논문을 찾아 연구 지형을 정리해 주세요. 주요 분파, 미해결 문제, 제 연구가 놓일 자리를 표로 만들어 주세요.',
    expect: ['연구'],
  },
  'doctoral.factcheck': {
    text: '심사에서 "베이스라인 선정이 편향적"이라는 지적을 받았습니다. 어떤 근거가 필요한지 정리하고, 반박 또는 수용을 위한 추가 실험을 제안해 주세요.',
    expect: ['베이스라인'],
  },
  'doctoral.analysis': {
    text: '여러 시드로 돌린 결과 표가 있습니다. 평균과 신뢰구간을 구하고, 제안 기법과 베이스라인의 차이가 유의한지 검정한 뒤 차트로 보여 주세요.',
    expect: ['신뢰구간'],
  },
  'doctoral.report': {
    text: '학술지 투고용 원고의 「실험」 절을 써 주세요. 데이터, 설정, 지표, 결과, 절제 실험(ablation) 순서로, 재현에 필요한 값을 빠짐없이 적어 주세요.',
    expect: ['실험'],
  },
  'doctoral.brief': {
    text: '공동연구자에게 보낼 한 장 진행 보고를 써 주세요. 확정된 결과, 흔들리는 결과, 다음 결정이 필요한 지점으로 나눠 주세요.',
    expect: ['결과'],
  },
  'doctoral.minutes': {
    text: '심사 예비발표 피드백을 회의록으로 정리해 주세요. 지적 사항, 대응 방향, 논문 반영 위치를 표로 만들어 주세요.',
    expect: ['대응'],
  },
  'doctoral.slides': {
    text: '학회 구두 발표 15분 자료를 만들어 주세요. 한 장에 메시지 하나 원칙으로, 결과는 수치를 크게 보여 주고 발표 노트를 붙여 주세요.',
    expect: ['결과'],
  },
  'doctoral.proposal': {
    text: '박사후연구원 지원용 연구계획 발표자료를 만들어 주세요. 지금까지의 성과와 앞으로 3년 계획을 나눠 담아 주세요.',
    expect: ['계획'],
  },
  'doctoral.visual': {
    text: '논문 티저 그림을 만들어 주세요. 제안 방법이 기존과 어디서 갈리는지 한눈에 보이게, 글자는 넣지 말고 여백을 남겨 주세요.',
    expect: [],
  },
  'doctoral.automation': {
    text: '실험 스윕 결과 디렉터리를 순회하며 설정별 최고 성능과 그 체크포인트 경로를 표로 뽑는 파이썬 코드를 만들고 실행해 주세요.',
    expect: ['python', '파이썬'],
  },
  'doctoral.trend': {
    text: '제 분야 상위 학회의 최근 2년 채택 논문 주제 분포 동향을 조사해 주세요. 급증·급감 주제를 비교표로 만들고 출처를 남겨 주세요.',
    expect: ['학회'],
  },

  // ── 행정직 직장인 ──────────────────────────────────────────────────
  'administration.concept': {
    text: '대학 행정팀입니다. 「예산 이월」과 「불용액」의 차이를 실제 처리 절차와 함께 설명해 주세요.',
    expect: ['이월'],
  },
  'administration.literature': {
    text: '타 대학의 학사경고 제도 운영 사례를 조사해 정리해 주세요. 기준, 구제 절차, 재적 처리 방식을 비교표로 만들어 주세요.',
    expect: ['학사경고'],
  },
  'administration.factcheck': {
    text: '"등록금 반환은 개강 후 30일까지 가능하다"는 안내가 규정과 맞는지 확인하고, 시점별 반환 비율을 표로 정리해 주세요.',
    expect: ['반환'],
  },
  'administration.analysis': {
    text: '학과별 재학생 수 표가 있습니다. 최근 3년 증감률을 계산하고 감소 폭이 큰 학과를 차트로 보여 주세요.',
    expect: ['재학생'],
  },
  'administration.report': {
    text: '「2026학년도 교육과정 개편 추진 계획」 보고서를 써 주세요. 배경, 개편 내용, 일정, 소요 예산, 협조 부서 순서로 담아 주세요.',
    expect: ['개편'],
  },
  'administration.brief': {
    text: '처장님 결재용 한 장 보고를 써 주세요. 결정할 것, 대안 두 개, 각각의 위험, 권고안을 담아 주세요.',
    expect: ['권고'],
  },
  'administration.minutes': {
    text: '학사운영위원회 회의 메모를 공식 회의록으로 바꿔 주세요. 안건별 심의 결과와 이행 부서를 표로 정리해 주세요.',
    expect: ['안건'],
  },
  'administration.slides': {
    text: '교무위원회 보고용 10분 발표자료를 만들어 주세요. 공문 문체로, 결정이 필요한 사항을 첫 장에 두고 발표 노트를 붙여 주세요.',
    expect: ['보고'],
  },
  'administration.proposal': {
    text: '신규 제도 도입 설명회 자료를 만들어 주세요. 대상은 학과 조교들이니 바뀌는 절차를 단계별로 보여 주세요.',
    expect: ['절차'],
  },
  'administration.visual': {
    text: '「학적 변동 처리 절차」를 한 장으로 보여 주는 흐름도를 만들어 주세요. 글자는 넣지 말고 자리만 남겨 주세요.',
    expect: [],
  },
  'administration.automation': {
    text: '엑셀에서 내려받은 수강신청 명단에서 중복과 미납자를 걸러 학과별로 나누는 파이썬 코드를 만들고 예시로 실행해 주세요.',
    expect: ['python', '파이썬'],
  },
  'administration.trend': {
    text: '최근 대학 행정의 AI 도입 동향을 조사해 주세요. 도입 영역, 국내 사례, 도입 시 유의점을 비교표로 만들고 출처를 남겨 주세요.',
    expect: ['도입'],
  },

  // ── 사무직 직장인 ──────────────────────────────────────────────────
  'office.concept': {
    text: '총무팀 대리입니다. 「전자세금계산서 역발행」이 무엇이고 일반 발행과 절차가 어떻게 다른지 설명해 주세요.',
    expect: ['세금계산서'],
  },
  'office.literature': {
    text: '중소기업 재택근무 제도 운영 사례를 조사해 주세요. 근무 형태, 근태 관리 방식, 도입 후 문제를 비교표로 만들어 주세요.',
    expect: ['재택'],
  },
  'office.factcheck': {
    text: '"연차는 입사 1년 미만이면 쓸 수 없다"는 말이 맞는지 근로기준법 기준으로 확인하고, 월 단위 발생 규정을 정리해 주세요.',
    expect: ['연차'],
  },
  'office.analysis': {
    text: '부서별 월별 비품 지출 표가 있습니다. 분기 합계와 전년 대비 증감을 구하고 상위 5개 부서를 차트로 보여 주세요.',
    expect: ['지출'],
  },
  'office.report': {
    text: '「사무용품 구매 프로세스 개선 방안」 보고서를 써 주세요. 현황, 문제점, 개선안, 기대효과, 소요 비용 순서로 담아 주세요.',
    expect: ['개선'],
  },
  'office.brief': {
    text: '팀장님께 드릴 한 장 보고를 써 주세요. 결정할 것, 기한, 대안과 각각의 비용을 담고 마지막 칸은 다음 행동으로 끝내 주세요.',
    expect: ['기한'],
  },
  'office.minutes': {
    text: '주간 팀 회의 메모를 회의록으로 정리해 주세요. 결정·조치·미결로 나누고 담당자와 기한을 붙여 주세요.',
    expect: ['담당'],
  },
  'office.slides': {
    text: '월간 업무보고 8분 발표자료를 만들어 주세요. 실적, 이슈, 다음 달 계획 순서로 만들고 발표 노트를 붙여 주세요.',
    expect: ['계획'],
  },
  'office.proposal': {
    text: '사내 복지제도 개편안 설명 자료를 만들어 주세요. 전 직원이 대상이니 바뀌는 점과 그대로인 점을 나눠 보여 주세요.',
    expect: ['복지'],
  },
  'office.visual': {
    text: '「구매 요청부터 정산까지의 절차」를 한 장으로 보여 주는 도식을 만들어 주세요. 글자는 넣지 말아 주세요.',
    expect: [],
  },
  'office.automation': {
    text: '매달 받는 지출결의 CSV에서 계정과목별 합계를 내고 전월 대비 증감을 붙여 표로 출력하는 파이썬 코드를 만들고 실행해 주세요.',
    expect: ['python', '파이썬'],
  },
  'office.trend': {
    text: '최근 사무 자동화(RPA·AI 비서) 도입 동향을 조사해 주세요. 적용 업무, 국내 도입 사례, 도입 비용을 비교표로 만들고 출처를 남겨 주세요.',
    expect: ['자동화'],
  },

  // ── 연구직 직장인 ──────────────────────────────────────────────────
  'research.concept': {
    text: '기업 부설연구소 선임입니다. 「기술성숙도(TRL)」 단계 구분을 실제 과제 사례와 함께 설명해 주세요.',
    expect: ['trl', '기술성숙도'],
  },
  'research.literature': {
    text: '고체 전해질 배터리의 상용화 장벽을 다룬 최근 연구를 정리해 주세요. 쟁점, 대표 연구, 보고된 수치를 표로 만들고 미확인 항목은 표시해 주세요.',
    expect: ['전해질'],
  },
  'research.factcheck': {
    text: '경쟁사 발표의 "에너지 밀도 400Wh/kg 달성" 주장을 검증해 주세요. 측정 조건, 셀 단위인지 팩 단위인지, 비교 가능한 기준인지 확인해 주세요.',
    expect: ['에너지 밀도'],
  },
  'research.analysis': {
    text: '시험 결과 표가 있습니다. 조건별 평균과 편차를 구하고 규격 상한을 넘은 항목을 표시한 차트를 만들어 주세요.',
    expect: ['조건'],
  },
  'research.report': {
    text: '「신규 소재 적용 타당성 검토」 보고서를 써 주세요. 배경, 시험 방법, 결과, 양산 적용 시 위험, 권고 순서로 담고 수치에 단위를 붙여 주세요.',
    expect: ['타당성'],
  },
  'research.brief': {
    text: '연구소장 보고용 한 장 요약을 써 주세요. 확인된 것, 확인되지 않은 것, 다음 결정이 필요한 것으로 나눠 주세요.',
    expect: ['확인'],
  },
  'research.minutes': {
    text: '과제 착수 회의 메모를 회의록으로 정리해 주세요. 역할 분담, 마일스톤, 위험 요소를 표로 만들어 주세요.',
    expect: ['마일스톤'],
  },
  'research.slides': {
    text: '과제 중간평가 15분 발표자료를 만들어 주세요. 목표 대비 달성도를 수치로 보여 주고 미달 항목의 대책을 넣어 주세요. 발표 노트도 붙여 주세요.',
    expect: ['달성'],
  },
  'research.proposal': {
    text: '국책과제 신청 발표자료를 만들어 주세요. 심사위원이 보는 자료이니 차별성과 파급효과를 앞에 두고 근거를 붙여 주세요.',
    expect: ['효과'],
  },
  'research.visual': {
    text: '「소재 개발부터 양산 검증까지의 단계」를 한 장으로 보여 주는 도식을 만들어 주세요. 글자는 넣지 말아 주세요.',
    expect: [],
  },
  'research.automation': {
    text: '시험 장비가 뱉는 로그에서 조건별 측정값을 뽑아 규격 초과 여부를 판정하는 파이썬 코드를 만들고, 예시로 실행해 검산해 주세요.',
    expect: ['python', '파이썬'],
  },
  'research.trend': {
    text: '최근 2년 전고체 배터리 기술 동향을 조사해 주세요. 주요 기업, 발표 스펙, 양산 시점 전망을 비교표로 만들고 출처를 남겨 주세요.',
    expect: ['배터리'],
  },
}

/** The prompt for a scenario, or a readable failure if the pair is missing. */
export function promptFor(personaId: string, workId: string): WorkPrompt {
  const found = workPrompts[`${personaId}.${workId}`]
  if (!found) throw new Error(`업무 프롬프트가 없습니다: ${personaId}.${workId}`)
  return found
}
