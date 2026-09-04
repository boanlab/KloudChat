/** Personas for coverage and layout review: each need is a task the UI must make possible.
 *  `personas.spec.ts` asserts the `needs`. */

export type Surface = 'chat' | 'report' | 'slides' | 'image' | 'av'

export interface Need {
  /** Stable id used in the coverage report. */
  id: string
  /** What the persona is trying to do, in their words. */
  task: string
  /** Where in the UI it must be reachable. */
  where: string
}

export interface Persona {
  id: string
  name: string
  role: string
  context: string
  /** The surfaces this persona lives in, most-used first. */
  surfaces: Surface[]
  /** Screen width they actually work on. */
  viewport: 'desktop' | 'laptop' | 'tablet'
  needs: Need[]
}

export const personas: Persona[] = [
  {
    id: 'humanities',
    name: '한지민',
    role: '인문대 학부생 (사학과 3학년)',
    context:
      '기말 리포트를 쓴다. 1차 사료 PDF를 읽고 인용해야 하고, 참고문헌은 시카고 양식이어야 한다. 표절 검사를 통과해야 하므로 출처가 분명해야 한다.',
    surfaces: ['report', 'chat'],
    viewport: 'laptop',
    needs: [
      { id: 'hum-upload', task: '읽어야 할 PDF를 대화에 올린다', where: '입력창 첨부' },
      { id: 'hum-citation', task: '인용 형식(시카고)을 만든다', where: '커넥터 · Zotero' },
      { id: 'hum-sources', task: '보고서 본문의 주장이 어디서 왔는지 확인한다', where: '보고서 패널 출처' },
      { id: 'hum-export-docx', task: '제출용 워드 파일로 내려받는다', where: '보고서 내보내기' },
      { id: 'hum-template', task: '리포트 형식을 처음부터 잡지 않고 시작한다', where: '시작점' },
      { id: 'hum-compare', task: '같은 질문을 두 모델에 물어 답을 비교한다', where: '입력창 모델 비교' },
    ],
  },
  {
    id: 'business',
    name: '박서준',
    role: '경영대 학부생 (경영학과 4학년)',
    context:
      '팀 프로젝트로 기업 케이스를 분석한다. 재무 데이터가 엑셀에 있고, 결과는 15분 발표로 만들어야 한다. 팀원 4명과 나눠 본다.',
    surfaces: ['slides', 'chat', 'report'],
    viewport: 'laptop',
    needs: [
      { id: 'biz-chart', task: '분석 결과를 차트로 만든다', where: '아티팩트 종류' },
      { id: 'biz-deck', task: '발표 시간에 맞춰 덱을 만든다', where: '슬라이드' },
      { id: 'biz-pptx', task: '.pptx로 내려받아 팀 템플릿에 붙인다', where: '슬라이드 내보내기' },
      { id: 'biz-share', task: '팀원에게 결과물 링크를 보낸다', where: '공유' },
      { id: 'biz-notes', task: '발표자 노트를 확인한다', where: '슬라이드 패널' },
      { id: 'biz-factcheck', task: '발표에서 읽을 수치에 근거가 있는지 확인한다', where: '슬라이드 팩트체크' },
    ],
  },
  {
    id: 'socialsci',
    name: '이수빈',
    role: '사회과학대 학부생 (심리학과 3학년)',
    context:
      '설문 200부를 돌렸다. CSV를 정리하고 기술통계·교차분석을 내서 보고서에 넣어야 한다. 선행연구 인용도 필요하다.',
    surfaces: ['chat', 'report'],
    viewport: 'laptop',
    needs: [
      { id: 'soc-csv', task: '설문 CSV를 올려 분석한다', where: '입력창 첨부' },
      { id: 'soc-chart', task: '분포와 교차표를 시각화한다', where: '아티팩트 종류' },
      { id: 'soc-stats-db', task: '데이터베이스에서 직접 집계한다', where: '커넥터 · PostgreSQL' },
      { id: 'soc-citation', task: '선행연구를 찾아 인용한다', where: '커넥터 · 연구' },
      { id: 'soc-websearch', task: '최신 통계 자료를 웹에서 찾는다', where: '입력창 웹 검색' },
    ],
  },
  {
    id: 'engineering',
    name: '최도현',
    role: '공대 학부생 (전자공학과 2학년)',
    context:
      '실험 리포트를 쓴다. 회로 해석 수식이 많고, MATLAB 코드와 그래프를 넣어야 한다. 조교가 수식 전개를 본다.',
    surfaces: ['report', 'chat'],
    viewport: 'laptop',
    needs: [
      { id: 'eng-math', task: '수식이 제대로 렌더링된 것을 확인한다', where: '메시지 · 수식' },
      { id: 'eng-code', task: '코드를 복사해 바로 실행한다', where: '코드 블록 복사' },
      { id: 'eng-chart', task: '측정값을 그래프로 만든다', where: '아티팩트 종류' },
      { id: 'eng-arxiv', task: '관련 논문을 찾는다', where: '커넥터 · arXiv' },
      { id: 'eng-report-toc', task: '리포트 구조를 잡고 섹션별로 채운다', where: '보고서 목차' },
    ],
  },
  {
    id: 'grad',
    name: '윤채원',
    role: '대학원생 (박사과정 4년차)',
    context:
      '학위논문 초고를 쓴다. 실험 로그와 선행연구가 수백 건이고, 지도교수 피드백마다 특정 절을 다시 쓴다. 어떤 버전을 보냈는지 헷갈린다.',
    surfaces: ['report', 'chat', 'slides'],
    viewport: 'desktop',
    needs: [
      { id: 'grad-project', task: '논문 맥락을 프로젝트로 고정한다', where: '프로젝트' },
      { id: 'grad-knowledge', task: '선행연구 파일을 프로젝트에 쌓는다', where: '프로젝트 지식' },
      { id: 'grad-version', task: '이전 버전과 무엇이 달라졌는지 본다', where: '아티팩트 버전 이력' },
      { id: 'grad-memory', task: '반복되는 지시를 기억시킨다', where: '메모리' },
      { id: 'grad-zotero', task: '문헌 라이브러리를 연결한다', where: '커넥터 · Zotero' },
      { id: 'grad-section-regen', task: '특정 절만 다시 쓴다', where: '보고서 섹션' },
    ],
  },
  {
    id: 'researcher',
    name: '강민호',
    role: '연구직 (기업 부설연구소 선임)',
    context:
      '경쟁 기술 검토 보고서를 매 분기 낸다. 근거 없는 문장은 그대로 리스크라, 모든 주장에 출처가 붙어야 한다. 사내 위키와 DB도 봐야 한다.',
    surfaces: ['report', 'chat'],
    viewport: 'desktop',
    needs: [
      { id: 'res-websearch', task: '최신 동향을 웹에서 조사한다', where: '입력창 웹 검색' },
      { id: 'res-sources', task: '보고서 문장마다 근거를 확인한다', where: '보고서 출처' },
      { id: 'res-custom-mcp', task: '사내 시스템을 직접 연결한다', where: '커넥터 직접 추가' },
      { id: 'res-agent', task: '검토 관점을 고정한 작업자를 만든다', where: '에이전트' },
      { id: 'res-export-pdf', task: '보고서를 PDF로 배포한다', where: '보고서 내보내기' },
      { id: 'res-apikey', task: '분석 스크립트에서 API로 호출한다', where: 'API 키' },
      { id: 'res-compare', task: '비싼 모델이 이 작업에 값하는지 비교한다', where: '입력창 모델 비교' },
      { id: 'res-agent-share', task: '검토 에이전트를 팀에 공유한다', where: '에이전트 스토어' },
    ],
  },
  {
    id: 'office',
    name: '정해원',
    role: '사무직 (총무팀 대리)',
    context:
      '주 3회 회의록을 쓰고, 공문·안내메일을 양식에 맞춰 보낸다. 일정 조율이 업무의 절반이다. 엑셀 정리도 많다.',
    surfaces: ['chat', 'report', 'slides'],
    viewport: 'laptop',
    needs: [
      { id: 'off-voice', task: '회의 녹음을 올려 회의록으로 옮긴다', where: '입력창 첨부' },
      { id: 'off-template', task: '공문 양식을 골라 시작한다', where: '시작점' },
      { id: 'off-drive', task: '드라이브 문서를 불러온다', where: '커넥터 · Drive' },
      { id: 'off-docx', task: '워드로 내보내 결재 올린다', where: '보고서 내보내기' },
      { id: 'off-search', task: '지난 회의록을 찾는다', where: '검색' },
      { id: 'off-audit', task: '누가 무엇을 했는지 기록을 확인한다', where: '관리자 · 감사 로그' },
      { id: 'off-pii', task: '개인정보가 외부로 나가지 않는지 확인한다', where: '관리자 · 정책' },
      { id: 'off-usage', task: '부서 사용량을 집계해 보고한다', where: '관리자 · 사용량' },
    ],
  },
  {
    id: 'developer',
    name: '오지훈',
    role: '개발직 (백엔드 4년차)',
    context:
      '장애 대응과 코드 리뷰가 주 업무다. 스택 트레이스, PR diff, DB 스키마를 오가며 원인을 좁힌다. 사내 도구를 CLI에서도 쓴다.',
    surfaces: ['chat', 'report'],
    viewport: 'desktop',
    needs: [
      { id: 'dev-github', task: 'PR과 이슈를 읽어 온다', where: '커넥터 · GitHub' },
      { id: 'dev-db', task: 'DB 스키마를 확인하고 쿼리한다', where: '커넥터 · PostgreSQL' },
      { id: 'dev-tool-scope', task: '쓰기 권한이 있는 도구를 구분해 끈다', where: '커넥터 도구 권한' },
      { id: 'dev-code-artifact', task: '코드 결과물을 옆 패널에서 본다', where: '아티팩트 패널' },
      { id: 'dev-apikey', task: 'CLI에서 쓸 키를 발급한다', where: 'API 키' },
      { id: 'dev-steps', task: '어떤 도구를 왜 호출했는지 추적한다', where: '스텝 타임라인' },
    ],
  },
  {
    id: 'sales',
    name: '신유진',
    role: '영업직 (B2B 솔루션 영업 6년차)',
    context:
      '주간 5~6개 고객사를 만난다. 미팅 전에 고객 이력을 훑고, 미팅 후 제안서와 후속 메일을 당일에 보낸다. 이동 중 태블릿으로 확인한다.',
    surfaces: ['chat', 'slides', 'report'],
    viewport: 'tablet',
    needs: [
      { id: 'sal-deck', task: '고객 맞춤 제안 덱을 만든다', where: '슬라이드' },
      { id: 'sal-template', task: '제안서 양식으로 바로 시작한다', where: '시작점' },
      { id: 'sal-mobile', task: '태블릿에서 좁은 화면으로 쓴다', where: '반응형 레이아웃' },
      { id: 'sal-share', task: '고객에게 결과물을 공유한다', where: '공유' },
    ],
  },
]
