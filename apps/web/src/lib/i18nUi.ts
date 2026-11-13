/** English for the shared table, export, copy, tab-title and 404 wording (merged into `EN`). */
export const EN_UI: Record<string, string> = {
  // Sortable tables
  '{column} 기준으로 정렬': 'Sort by {column}',
  '{column} 오름차순으로 정렬됨': '{column}, sorted ascending',
  '{column} 내림차순으로 정렬됨': '{column}, sorted descending',
  최신순: 'Newest first',
  '최근 수정순': 'Recently updated',
  이름순: 'By name',
  '대화 많은 순': 'Most conversations',
  '오래된 순': 'Oldest first',
  행위순: 'By action',
  대상순: 'By target',
  '접속 위치순': 'By location',
  // CSV export
  'CSV를 내보내지 못했습니다. 잠시 후 다시 시도하세요.': 'Could not export the CSV. Try again shortly.',
  '내보낼 사용자가 없습니다': 'No users to export',
  '지금 탭·검색·정렬로 모든 쪽을 CSV로 받습니다. 내보낸 일은 감사 로그에 남습니다.':
    'Downloads every page under the current tab, search and order as CSV. The export is recorded in the audit log.',
  '내보낼 기록이 없습니다': 'No records to export',
  '지금 조건과 정렬로 모든 쪽을 CSV로 받습니다. 내보낸 일도 감사 로그에 남습니다.':
    'Downloads every page under the current filters and order as CSV. The export itself is recorded in the audit log.',
  '사용자 목록 내보내기': 'Users exported',
  '감사 로그 내보내기': 'Audit log exported',
  // Copy buttons
  '{what} 복사': 'Copy {what}',
  '{what} 복사됨': '{what} copied',
  '복사하지 못했습니다': 'Could not copy',
  '기록 ID': 'Record ID',
  '챗봇 주소': 'Chatbot address',
  '챗봇 검색': 'Search chatbots',
  // strict-local priority list
  '우선순위': 'Priority',
  '{name} 위로': 'Move {name} up',
  '{name} 아래로': 'Move {name} down',
  '이미 첫 번째입니다': 'Already first',
  '이미 마지막입니다': 'Already last',
  '{name} · {n}번째': '{name}, position {n}',
  '내부 자료 때문에 strict-local로 옮길 때는 사용자가 고른 모델의 strict-local 판을 먼저 쓰고, 그것이 없으면 이 목록의 첫 번째 모델을 씁니다. 개인정보 확인 창에서는 체크된 모델 중 하나를 사용자가 고를 수 있으며, 처음에는 첫 번째 모델이 골라져 있습니다.':
    "When internal material moves a turn to strict-local, the chosen model's strict-local version is used first, else the first model of this list. On the privacy card the person can pick any checked model; the first is preselected.",
  '프록시가 self-hosted이며 외부 대체 모델이 없다고 명시한 모델만 선택할 수 있습니다. 화살표나 끌어서 순서를 바꿉니다.':
    'Only models the proxy declares self-hosted with no external fallback can be chosen. Reorder with the arrows or by dragging.',
  '전환할 로컬 모델': 'Local model to switch to',
  '첨부 파일·프로젝트 파일·메모리가 들어간 턴은 선택한 모델 대신 같은 모델의 strict-local 판으로 답하고, 그 세션은 이후에도 외부 모델로 승격되지 않습니다. 쌍이 없으면 아래 안전 모델 목록의 첫 번째 모델이 맡고, 목록이 비어 있으면 전송을 거절합니다.':
    'A turn that includes attachments, project files or memory is answered by the strict-local version of the chosen model instead, and the conversation never moves to an external model afterwards. Without that version, the first model of the safe model list below answers; with an empty list, the request is refused.',
  '정책을 저장하지 못했습니다. 화면은 저장된 값으로 돌아갑니다.':
    'Could not save the policy. The screen shows the saved values again.',
  // Password boxes (no 「비밀번호」 in the toggle's name: the box keeps that label to itself)
  '입력한 글자 보기': 'Show characters',
  '입력한 글자 숨기기': 'Hide characters',
  'Caps Lock이 켜져 있습니다': 'Caps Lock is on',
  // Bulk approval of sign-ups
  '{n}명 선택': '{n} selected',
  '승인 대기 {n}명': '{n} awaiting approval',
  '고른 계정 중 승인 대기인 계정이 없습니다': 'None of the selected accounts is awaiting approval',
  '선택 승인': 'Approve selected',
  '선택 반려': 'Reject selected',
  '{n}명을 승인할까요?': 'Approve {n} accounts?',
  '{n}명의 가입을 반려할까요?': 'Reject {n} sign-ups?',
  '기본 플랜 크레딧으로 승인하고 각 계정의 게이트웨이 키를 만듭니다. 승인 대기가 아닌 계정은 건너뜁니다.':
    'Approves them with the basic plan credits and creates each account\'s gateway key. Accounts not awaiting approval are skipped.',
  '반려한 계정은 정지 상태가 되어 로그인할 수 없습니다. 승인 대기가 아닌 계정은 건너뜁니다.':
    'Rejected accounts are suspended and cannot sign in. Accounts not awaiting approval are skipped.',
  '{n}명을 승인했습니다.': 'Approved {n} accounts.',
  '{n}명의 가입을 반려했습니다.': 'Rejected {n} sign-ups.',
  // Project theme and default template
  '말투·색·서체는 모든 보고서 형식(웹 보고서·한글·Word)과 모든 슬라이드 형식(웹 슬라이드·PowerPoint)에 적용되고, 말투는 대화에도 적용됩니다. 이미지에는 스타일이 적용되고, 오디오·동영상에는 적용되지 않습니다.':
    'Tone, colours and fonts apply to every report format (web report, HWP, Word) and every slide format (web slides, PowerPoint); the tone also applies to chat. Images get the style; audio and video get nothing.',
  '이 프로젝트에서 새로 시작하는 보고서(웹 보고서·한글·Word)와 슬라이드(웹 슬라이드·PowerPoint)가 어떤 템플릿으로 나올지 정합니다. 어느 형식으로 내보내도 같은 템플릿을 따르며, 대화마다 다시 고를 수 있습니다.':
    'Sets the template new reports (web report, HWP, Word) and slides (web slides, PowerPoint) in this project start from. Every format follows the same template, and each conversation can pick again.',
  '보고서 (웹·한글·Word)': 'Report (web · HWP · Word)',
  '슬라이드 (웹·PowerPoint)': 'Slides (web · PowerPoint)',
  '명조 계열 서체': 'Serif typeface',
  '고딕 계열 서체': 'Sans-serif typeface',
  // Report and slides starts: the same chips on the home screen and in an empty session
  '웹 보고서·한글·Word로 씁니다. 형식과 템플릿을 고르고 주제를 적으세요':
    'Written as a web report, HWP or Word. Pick the format and template, then give the topic',
  '웹 슬라이드·PowerPoint로 만듭니다. 형식과 템플릿을 고르고 주제를 적으세요':
    'Made as web slides or PowerPoint. Pick the format and template, then give the topic',
  '템플릿: {name}': 'Template: {name}',
  템플릿: 'Template',
  // Empty states
  '새 대화 시작': 'Start a conversation',
  '검색 지우기': 'Clear search',
  // Session, 404, tab titles
  '로그인이 만료되었습니다. 다시 로그인하면 하던 화면으로 돌아갑니다.':
    'Your sign-in expired. Sign in again to return to where you were.',
  '페이지를 찾을 수 없음': 'Page not found',
  '이 주소에는 화면이 없습니다.': 'There is no screen at this address.',
  '주소가 바뀌었거나 잘못 입력된 링크입니다. 찾던 대화나 파일은 검색으로 찾을 수 있습니다.':
    'The address changed or the link is mistyped. Search can find the conversation or file you were looking for.',
  '새 보고서': 'New report',
  '새 슬라이드': 'New slides',
}
