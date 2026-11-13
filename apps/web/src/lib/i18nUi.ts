/** English for the shared table, export, copy, tab-title and 404 wording (merged into `EN`). */
export const EN_UI: Record<string, string> = {
  // Sortable tables
  '{column} 기준으로 정렬': 'Sort by {column}',
  '{column} 오름차순으로 정렬됨': '{column}, sorted ascending',
  '{column} 내림차순으로 정렬됨': '{column}, sorted descending',
  최신순: 'Newest first',
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
  '{name}: {n}번째로 옮겼습니다': '{name}: moved to position {n}',
  '내부 자료 때문에 strict-local로 옮길 때는 사용자가 고른 모델의 strict-local 판을 먼저 쓰고, 그것이 없으면 이 목록의 첫 번째 모델을 씁니다. 개인정보 확인 창에서는 체크된 모델 중 하나를 사용자가 고를 수 있으며, 처음에는 첫 번째 모델이 골라져 있습니다.':
    "When internal material moves a turn to strict-local, the chosen model's strict-local version is used first, else the first model of this list. On the privacy card the person can pick any checked model; the first is preselected.",
  '프록시가 self-hosted이며 외부 대체 모델이 없다고 명시한 모델만 선택할 수 있습니다. 화살표나 끌어서 순서를 바꿉니다.':
    'Only models the proxy declares self-hosted with no external fallback can be chosen. Reorder with the arrows or by dragging.',
  '전환할 로컬 모델': 'Local model to switch to',
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
