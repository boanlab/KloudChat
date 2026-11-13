/** The tab title: 「(unread) page · brand」, the page named as the sidebar and menus name it. */

/** Korean source labels (translated where used); the first match wins. */
const ROUTES: [RegExp, string][] = [
  [/^\/$/, '새 대화'],
  [/^\/new\/report/, '새 보고서'],
  [/^\/new\/slides/, '새 슬라이드'],
  [/^\/new\//, '새 대화'],
  [/^\/projects/, '프로젝트'],
  [/^\/s\//, '대화'],
  [/^\/artifacts/, '아티팩트'],
  [/^\/files/, '파일함'],
  [/^\/customize/, '사용자 지정'],
  [/^\/history/, '대화 기록'],
  [/^\/usage/, '사용량'],
  [/^\/issues/, '문의·신고'],
  [/^\/agent-setup/, 'AI 에이전트 연동'],
  [/^\/api-setup/, 'API 연동'],
  [/^\/settings\/preferences/, '환경설정'],
  [/^\/settings\/personalization/, '개인 맞춤 설정'],
  [/^\/settings\/memory/, '메모리'],
  [/^\/settings\/themes/, '테마'],
  [/^\/settings\/keys/, 'API 키'],
  [/^\/settings\/access/, '보안'],
  [/^\/settings/, '설정'],
  [/^\/admin\/users/, '사용자 · 크레딧'],
  [/^\/admin\/usage/, '사용량'],
  [/^\/admin\/performance/, '성능 모니터링'],
  [/^\/admin\/system/, '시스템'],
  [/^\/admin\/governance/, '보안 · 감사'],
  [/^\/admin\/issues/, '문의·신고'],
  [/^\/admin\/publications/, '공개 챗봇'],
  [/^\/b\/?$/, '공개 챗봇'],
]

/** The page's label for `pathname`, or null when the page names itself (a conversation, a project). */
export function routeTitle(pathname: string): string | null {
  return ROUTES.find(([re]) => re.test(pathname))?.[1] ?? null
}

let brand = ''
let page = ''
let claimed: string | null = null
let unread = 0

function write() {
  const parts = [claimed ?? page, brand].filter(Boolean)
  const text = parts.join(' · ') || 'KloudChat'
  document.title = unread > 0 ? `(${unread > 99 ? '99+' : unread}) ${text}` : text
}

/** The instance name (`applyBrand`). */
export function setBrandTitle(name: string) {
  brand = name.trim()
  write()
}

/** What this screen is; '' leaves only the brand. */
export function setPageTitle(name: string) {
  page = name.trim()
  write()
}

/** Replies and approvals waiting, shown in front so a background tab says so. */
export function setUnreadTitle(count: number) {
  unread = Math.max(0, count)
  write()
}

/** A screen that names itself (a 404) over the address's label; null gives it back. */
export function claimPageTitle(name: string | null) {
  claimed = name
  write()
}
