import { expect, test, type Page, type TestInfo } from '@playwright/test'
import { shellRead } from './shell-mocks'

/**
 * Keyboard, focus and announcements against fixture data: forms that say
 * what is missing instead of disabling the button, the sign-in errors, the administrator's
 * credit dialog, the phone drawer as a modal, the settings menu, the model menu's radios,
 * the project's web-page error, the skip link and the shortcuts. Every request is mocked.
 */

const at = '2026-10-11T07:00:00.000Z'
const chatId = 'c0000000000000000000000000000001'
const projectId = 'p0000000000000000000000000000001'

const me = {
  id: 'ux-admin', email: 'admin@example.test', name: '관리자', role: 'admin', status: 'active',
  monthlyCredits: 1000, creditsUsed: 0, avatarColor: '#168267', allowedModels: [], createdAt: at,
  cycleResetsAt: at, lastActiveAt: at, emailVerifiedAt: at, litellmKeyPreview: 'sk-…abcd',
  preferences: { autoMemory: false, showUsage: false, streamResponses: true },
}
const member = { ...me, id: 'u-kim', email: 'kim@example.test', name: '김민지', role: 'user', monthlyCredits: 500_000 }

function model(id: string, name: string, extra: Record<string, unknown> = {}) {
  return {
    id, label: `Qwen · ${name}`, name, vendor: 'Qwen', provider: 'fixture', kinds: ['chat', 'report', 'slides'],
    modality: 'chat', dataBoundary: 'hybrid', strictLocal: false, privacyOnly: false, creditCost: 0,
    inputCreditCost: 0, supportsTools: true, supportsVision: false, contextWindow: 64000, ...extra,
  }
}

function session(id: string, title: string) {
  return {
    id, kind: 'chat', title, model: 'local/qwen3.8-27b', routingMode: 'manual', projectId: null, agentId: null,
    artifactId: null, pending: null, skillIds: [], renderTemplateId: null, pinned: false, messageCount: 0,
    createdAt: at, updatedAt: at, messages: [], preview: null, made: null,
  }
}

const project = {
  id: projectId, name: '출장 규정 도우미', description: '출장 규정을 묻고 답합니다.', emoji: '📁',
  instructions: '', files: [], sessionIds: [], skillIds: [], designSystemId: null, renderTemplates: {}, updatedAt: at,
}

interface Options {
  signedIn?: boolean
  signupMode?: 'open' | 'approval'
}

async function app(page: Page, testInfo: TestInfo, { signedIn = true, signupMode = 'approval' }: Options = {}) {
  const origin = new URL(String(testInfo.project.use.baseURL)).origin
  const state = {
    problems: [] as string[],
    unexpected: [] as string[],
    logins: 0,
    credits: [] as unknown[],
    suspended: [] as string[],
  }
  page.on('pageerror', (error) => state.problems.push(`pageerror: ${error.message}`))
  const users = [me, member]

  await page.context().routeWebSocket('**/*', (socket) => socket.close())
  await page.context().route('**/*', async (route) => {
    const request = route.request()
    const url = new URL(request.url())
    if (url.origin !== origin) return route.abort('blockedbyclient')
    if (!url.pathname.startsWith('/api/')) return route.continue()
    const path = url.pathname.slice(4)
    const method = request.method()
    const json = (value: unknown, status = 200) => route.fulfill({ status, json: value })
    const body = (() => {
      try {
        return (request.postDataJSON() ?? {}) as Record<string, unknown>
      } catch {
        return {}
      }
    })()

    if (path === '/auth/refresh') {
      return signedIn
        ? json({ accessToken: 'fixture-only', expiresIn: 3600, user: me })
        : json({ detail: 'no_refresh_token' }, 401)
    }
    if (method === 'GET' && path === '/auth/me') return json(me)
    if (method === 'GET' && path === '/auth/config') return json({
      brand: { name: 'KloudChat', logo: '' }, enabledKinds: ['chat', 'report', 'slides'], contactEmail: '',
      privacy: { externalDataGuard: false, allowUserRawExternal: false }, passwordResetEnabled: false,
      dictationEnabled: false, idleTimeoutMinutes: 0,
      signup: { mode: signupMode, domains: [], emailVerification: false },
      password: { minLength: 10 }, login: { maxFailures: 5, lockoutMinutes: 15 },
    })
    if (method === 'POST' && path === '/auth/login') {
      state.logins += 1
      return json({ detail: 'invalid_credentials' }, 401)
    }
    if (method === 'POST' && path === '/auth/signup') {
      return json({ detail: [{ type: 'value_error', loc: ['body', 'email'], msg: 'value is not a valid email address: The part after the @-sign is not valid.' }] }, 422)
    }
    if (method === 'GET' && path === '/models') return json({
      litellmAvailable: true, defaultChatModel: 'local/qwen3.8-27b',
      defaults: { chat: 'local/qwen3.8-27b', report: 'local/qwen3.8-27b', slides: 'local/qwen3.8-27b' },
      models: [
        model('local/qwen3.8-27b', 'Qwen3.8 27b (local)'),
        model('strict-local/qwen3.8-27b', 'Qwen3.8 27b (strict-local)', { strictLocal: true, dataBoundary: 'self_hosted' }),
      ],
      autoRouting: { enabled: false, available: false, qualityAvailable: false, reason: null, qualityReason: null, economyModelIds: [] },
    })
    if (method === 'GET' && path === '/credits') return json({ monthlyCredits: 1000, creditsUsed: 0, creditsRemaining: 1000 })
    if (method === 'GET' && path === '/sessions') return json([session(chatId, '대한민국 수도'), session('c0000000000000000000000000000002', '출장비 정산')])
    if (method === 'GET' && path === `/sessions/${chatId}`) return json(session(chatId, '대한민국 수도'))
    if (method === 'GET' && path === `/sessions/${chatId}/messages`) return json([])
    if (method === 'GET' && path === '/projects') return json([project])
    if (method === 'POST' && path === `/projects/${projectId}/knowledge/url`) {
      return json({ detail: '주소의 호스트를 찾을 수 없습니다.' }, 400)
    }
    if (method === 'GET' && path === '/artifacts/counts') return json({ counts: {}, total: 0 })
    if (method === 'GET' && path === '/admin/users') return json(users)
    if (method === 'GET' && path === '/admin/settings') return json({})
    const suspend = path.match(/^\/admin\/users\/([^/]+)\/suspend$/)
    if (suspend && method === 'POST') {
      state.suspended.push(suspend[1])
      return json({ ...users.find((u) => u.id === suspend[1])!, status: 'suspended' })
    }
    const credit = path.match(/^\/admin\/users\/([^/]+)\/credits$/)
    if (credit && method === 'POST') {
      state.credits.push(body.monthlyCredits)
      const row = users.find((u) => u.id === credit[1])!
      return json({ ...row, monthlyCredits: body.monthlyCredits })
    }
    const shell = shellRead(method, path)
    if (shell) return route.fulfill(shell)
    if (method === 'GET') {
      // Lists the screens read around these: empty is enough here.
      return json(path === '/user-templates/catalogue' || path === '/agent-bases' ? {} : [])
    }
    state.unexpected.push(`${method} ${path}`)
    return json({ detail: 'Unmocked fixture request' }, 501)
  })
  return state
}

const phone = (testInfo: TestInfo) => testInfo.project.name === 'phone'

test.describe('signed out', () => {
  test('sign-in: an empty form names the field, a wrong password is cleared and warns before the lock', async ({ page }, testInfo) => {
    const state = await app(page, testInfo, { signedIn: false })
    await page.goto('/')
    const form = page.locator('form')
    const submit = form.getByRole('button', { name: '로그인' })
    await expect(submit).toBeEnabled({ timeout: 15_000 })

    // Nothing typed: focus goes to the address, and the reason is under it.
    await submit.click()
    await expect(form.getByRole('alert')).toHaveText('이메일을 입력하세요.')
    await expect(form.getByLabel('이메일')).toBeFocused()
    await expect(form.getByLabel('이메일')).toHaveAttribute('aria-invalid', 'true')
    expect(state.logins).toBe(0)

    await form.getByLabel('이메일').fill('kim@example.test')
    await expect(form.getByRole('alert')).toHaveCount(0)
    await form.getByLabel('비밀번호').fill('wrong-password-1')
    await form.getByLabel('비밀번호').press('Enter')
    const alert = form.getByRole('alert')
    await expect(alert).toHaveText('이메일 또는 비밀번호가 올바르지 않습니다.')
    // Cleared and focused for the next try.
    await expect(form.getByLabel('비밀번호')).toHaveValue('')
    await expect(form.getByLabel('비밀번호')).toBeFocused()

    await form.getByLabel('비밀번호').fill('wrong-password-2')
    await form.getByLabel('비밀번호').press('Enter')
    await expect(alert).toContainText('3번 더 틀리면 15분 동안 잠깁니다.')
    expect(state.logins).toBe(2)
    expect(state.problems).toEqual([])
  })

  test('signup: open signup says so, ticks the rule as typed, and a 422 reads as Korean', async ({ page }, testInfo) => {
    const state = await app(page, testInfo, { signedIn: false, signupMode: 'open' })
    await page.goto('/')
    const tab = page.getByRole('button', { name: '회원가입', exact: true })
    await expect(tab).toBeVisible({ timeout: 30_000 })
    await tab.click()
    await expect(page.getByText('가입하면 바로 시작할 수 있습니다.')).toBeVisible()
    const form = page.locator('form')
    await expect(form.getByText('* 표시는 꼭 채워야 하는 칸입니다.')).toBeVisible()
    await expect(form.getByText('숫자와 기호')).toHaveCount(0)
    const submit = form.getByRole('button', { name: '가입하기' })

    await submit.click()
    await expect(form.getByRole('alert')).toHaveText('이름을 입력하세요.')
    await expect(form.getByLabel('이름')).toBeFocused()

    await form.getByLabel('이름').fill('김민지')
    await form.getByLabel('이메일').fill('kim@example.test')
    const password = form.getByLabel('비밀번호')
    await password.fill('short')
    // The hint describes the field, it is not part of its name.
    const hintId = (await password.getAttribute('aria-describedby')) ?? ''
    await expect(page.locator(`[id="${hintId}"]`)).toContainText('10자 이상 · 지금 5자')
    await submit.click()
    await expect(form.getByRole('alert')).toHaveText('비밀번호는 10자 이상이어야 합니다.')
    await expect(password).toBeFocused()

    await password.fill('long-enough-pass')
    await expect(page.locator(`[id="${hintId}"]`)).not.toContainText('지금')
    await submit.click()
    await expect(form.getByRole('alert')).toHaveText('이메일 주소 형식이 올바르지 않습니다.')
    await expect(form.getByRole('alert')).not.toContainText(/value is not/)
    expect(state.problems).toEqual([])
  })
})

test('admin credits: a bad value keeps the dialog open with the reason; 0 asks again; saving names who and how much', async ({ page }, testInfo) => {
  const state = await app(page, testInfo)
  await page.goto('/admin/users')
  const row = page.getByRole('row').filter({ hasText: 'kim@example.test' })
  await row.getByRole('button', { name: '크레딧' }).click()
  const dialog = page.getByRole('dialog', { name: '월 크레딧 한도' })
  const field = dialog.getByRole('textbox', { name: '월 크레딧' })
  await expect(field).toHaveAttribute('inputmode', 'numeric')

  await field.fill('abc')
  await dialog.getByRole('button', { name: '저장' }).click()
  await expect(dialog.getByRole('alert')).toHaveText('0부터 100,000,000 사이의 정수로 입력하세요.')
  await expect(field).toHaveAttribute('aria-invalid', 'true')
  await expect(field).toBeFocused()
  await field.fill('')
  await dialog.getByRole('button', { name: '저장' }).click()
  await expect(dialog.getByRole('alert')).toBeVisible()
  expect(state.credits).toEqual([])

  await field.fill('0')
  await dialog.getByRole('button', { name: '저장' }).click()
  await expect(dialog.getByRole('alert')).toHaveText('이 사용자는 다음 리필까지 모델을 쓸 수 없습니다.')
  expect(state.credits).toEqual([])

  // Written as the list shows it, with separators.
  await field.fill('2,000,000')
  await dialog.getByRole('button', { name: '저장' }).click()
  await expect(dialog).toHaveCount(0)
  await expect(page.getByTestId('toast')).toContainText('김민지의 월 크레딧을 2,000,000으로 바꿨습니다.')
  expect(state.credits).toEqual([2_000_000])
  expect(state.problems).toEqual([])
})

test('정지 asks first and says the person\'s bots stop too', async ({ page }, testInfo) => {
  const state = await app(page, testInfo)
  await page.goto('/admin/users')
  const row = page.getByRole('row').filter({ hasText: 'kim@example.test' })
  await row.getByRole('button', { name: '김민지 정지', exact: true }).click()
  const dialog = page.getByRole('dialog', { name: '김민지님의 접속을 막을까요?' })
  await expect(dialog).toContainText('공개한 챗봇도 함께 멈춥니다')
  await dialog.getByRole('button', { name: '취소' }).click()
  expect(state.suspended).toEqual([])
  await row.getByRole('button', { name: '김민지 정지', exact: true }).click()
  await page.getByRole('dialog', { name: '김민지님의 접속을 막을까요?' }).getByRole('button', { name: '정지', exact: true }).click()
  await expect.poll(() => state.suspended).toEqual(['u-kim'])
  expect(state.problems).toEqual([])
})

test('settings is a menu of links, and the password form lives at the top of 보안', async ({ page }, testInfo) => {
  const state = await app(page, testInfo)
  await page.goto('/settings')
  const nav = page.getByRole('navigation', { name: '설정 메뉴' })
  await expect(nav.getByRole('link', { name: '프로필' })).toHaveAttribute('aria-current', 'page', { timeout: 15_000 })
  await expect(page.getByRole('tab')).toHaveCount(0)
  await page.getByRole('link', { name: '비밀번호 변경으로 가기' }).click()
  await expect(page).toHaveURL(/\/settings\/access#password$/)
  await expect(nav.getByRole('link', { name: '보안' })).toHaveAttribute('aria-current', 'page')
  await expect(page.getByLabel('현재 비밀번호')).toBeFocused()
  await page.getByRole('button', { name: '비밀번호 변경' }).click()
  await expect(page.getByRole('alert').first()).toHaveText('현재 비밀번호를 입력하세요.')
  expect(state.problems).toEqual([])
})

test('the model menu marks the pick as a radio and offers no Auto the administrator has not turned on', async ({ page }, testInfo) => {
  const state = await app(page, testInfo)
  await page.goto(`/s/${chatId}`)
  const trigger = page.getByRole('button', { name: 'Qwen · Qwen3.8 27b (local)' }).last()
  await expect(trigger).toBeVisible({ timeout: 15_000 })
  if (phone(testInfo)) await expect(trigger).toContainText('27b · 로컬')
  await trigger.click()
  const menu = page.getByRole('menu')
  await expect(menu.getByText(/Auto · /)).toHaveCount(0)
  await expect(menu.getByRole('menuitemradio', { name: /27b \(local\)/ })).toHaveAttribute('aria-checked', 'true')
  await expect(menu.getByRole('menuitemradio', { name: /strict-local/ }).first()).toHaveAttribute('aria-checked', 'false')
  await page.keyboard.press('Escape')
  expect(state.problems).toEqual([])
})

test('a web page that cannot be read says why inside the dialog, under the address', async ({ page }, testInfo) => {
  const state = await app(page, testInfo)
  await page.goto(`/projects/${projectId}`)
  await page.getByRole('tab', { name: /지식/ }).click()
  await page.getByRole('button', { name: '웹 자료' }).click()
  const dialog = page.getByRole('dialog', { name: '웹 자료 추가' })
  const field = dialog.getByRole('textbox', { name: '웹페이지 주소' })
  // The address, not the title bar's 닫기, takes focus first.
  await expect(field).toBeFocused()
  await dialog.getByRole('button', { name: '읽어서 보관' }).click()
  await expect(dialog.getByRole('alert')).toHaveText('웹페이지 주소를 입력하세요.')
  await field.fill('https://nonexistent.example.invalid/page')
  await field.press('Enter')
  await expect(dialog.getByRole('alert')).toHaveText('이 주소를 열 수 없습니다(호스트 없음). 주소를 확인하거나 PDF로 저장해 올리세요.')
  await expect(field).toBeFocused()
  await expect(field).toHaveAttribute('aria-invalid', 'true')
  expect(state.problems).toEqual([])
})

test('a new project names the missing name instead of a dead 만들기', async ({ page }, testInfo) => {
  const state = await app(page, testInfo)
  await page.goto('/projects')
  await page.getByRole('button', { name: '새 프로젝트' }).first().click()
  const dialog = page.getByRole('dialog', { name: '새 프로젝트' })
  const name = dialog.getByRole('textbox', { name: '이름' })
  await expect(name).toBeFocused()
  await expect(name).toHaveAttribute('aria-required', 'true')
  await dialog.getByRole('button', { name: '만들기' }).click()
  await expect(dialog.getByRole('alert')).toHaveText('이름을 입력하세요.')
  await expect(name).toBeFocused()
  expect(state.problems).toEqual([])
})

test.describe('desktop keyboard', () => {
  test.skip(({ viewport }) => (viewport?.width ?? 0) < 1024, 'keyboard shortcuts and the skip link are desktop')

  test('the first Tab offers 본문으로 건너뛰기; sidebar rows are links with a named menu', async ({ page }, testInfo) => {
    const state = await app(page, testInfo)
    await page.goto(`/s/${chatId}`)
    const sidebar = page.locator('aside')
    await expect(sidebar.getByRole('link', { name: '대한민국 수도' })).toHaveAttribute('href', `/s/${chatId}`, { timeout: 15_000 })
    await expect(sidebar.getByRole('link', { name: '대한민국 수도' })).toHaveAttribute('aria-current', 'page')
    await expect(sidebar.getByRole('button', { name: '출장비 정산 메뉴' })).toBeVisible()

    // A screen with nothing focused on arrival: the first Tab is the skip link.
    await page.goto('/projects')
    await expect(page.getByRole('heading', { name: '프로젝트' }).first()).toBeVisible({ timeout: 15_000 })
    await page.keyboard.press('Tab')
    const skip = page.getByRole('link', { name: '본문으로 건너뛰기' })
    await expect(skip).toBeFocused()
    await expect(skip).toBeInViewport()
    await page.keyboard.press('Enter')
    await expect(page.locator('main#main')).toBeFocused()
    expect(state.problems).toEqual([])
  })

  test('shortcuts the browser keeps are not used: / focuses the composer, Alt+Shift+N opens a new chat', async ({ page }, testInfo) => {
    const state = await app(page, testInfo)
    await page.goto(`/s/${chatId}`)
    const composer = page.getByLabel('프롬프트 입력')
    await expect(composer).toBeVisible({ timeout: 15_000 })
    await page.locator('main h1, main').first().click({ position: { x: 5, y: 5 } })
    await composer.blur()
    await page.keyboard.press('/')
    await expect(composer).toBeFocused()
    // Typed inside the box, / is a character, not a shortcut.
    await page.keyboard.type('/')
    await expect(composer).toHaveValue('/')
    await page.keyboard.press('Alt+Shift+KeyN')
    await expect(page).toHaveURL(/\/new\/chat$/)
    await page.keyboard.press('Control+/')
    const dialog = page.getByRole('dialog', { name: '키보드 단축키' })
    await expect(dialog).toContainText('Alt')
    await expect(dialog).not.toContainText('Esc')
    expect(state.problems).toEqual([])
  })
})

test.describe('phone drawer', () => {
  test.skip(({ viewport }) => (viewport?.width ?? 0) >= 1024, 'the drawer is the narrow layout')

  test('opens as a modal: focus moves in, the page behind is inert, Escape closes and returns focus', async ({ page }, testInfo) => {
    const state = await app(page, testInfo)
    await page.goto(`/s/${chatId}`)
    const toggle = page.getByRole('button', { name: '사이드바 토글' })
    await expect(toggle).toBeVisible({ timeout: 15_000 })
    await toggle.focus()
    await page.keyboard.press('Enter')
    const drawer = page.getByRole('dialog', { name: '탐색' })
    await expect(drawer).toBeVisible()
    await expect(drawer).toHaveAttribute('aria-modal', 'true')
    await expect.poll(() => drawer.evaluate((el) => el.contains(document.activeElement))).toBe(true)
    await expect(page.locator('main')).toHaveAttribute('inert', '')
    // Tab stays among the drawer's own controls.
    for (let i = 0; i < 6; i++) {
      await page.keyboard.press('Tab')
      const inMain = await page.evaluate(() => Boolean(document.activeElement?.closest('main')))
      expect(inMain).toBe(false)
    }
    await page.keyboard.press('Escape')
    await expect(drawer).toHaveCount(0)
    await expect(toggle).toBeFocused()
    await expect(page.locator('main')).not.toHaveAttribute('inert', '')
    expect(state.problems).toEqual([])
  })
})
