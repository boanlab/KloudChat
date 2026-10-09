import { expect, test, type Page, type TestInfo } from '@playwright/test'
import { shellRead } from './shell-mocks'

test.use({ serviceWorkers: 'block' })
// A phone folds ▦ and the option chips behind 「더보기」; the desktop composer is what is checked.
test.skip(({ viewport }) => (viewport?.width ?? 1440) < 800, 'desktop composer')

/** Image, audio and video 서식 are retired: ▦ is offered on the writing surfaces only, and the
 *  image surface's 구조도 · 흐름도 · 개념도 chips draw through the server's shapes, never the
 *  image model. No backend: every call is answered here. */

const at = '2026-10-09T00:00:00.000Z'
const sessionId = '33333333333333333333333333333333'
const SHOTS = process.env.SHOTS_DIR ?? ''

const minutes = {
  id: 'doc-minutes', kind: 'document', surface: 'report', name: '회의록', description: '결정과 실행 항목',
  category: '업무', fills: ['회의 메모'], examplePrompt: '회의록을 써 줘.', nameEn: 'Minutes',
  descriptionEn: '', categoryEn: 'Work', fillsEn: [], examplePromptEn: '', checks: ['결정이 있다'],
  formFormat: '', hasPreview: false, slideCount: 0,
}

type Seen = { diagrams: { figure: string; description: string }[]; images: number; stores: number }

async function fixture(page: Page, testInfo: TestInfo, diagram: 'drawn' | 'undrawable' = 'drawn') {
  const origin = new URL(String(testInfo.project.use.baseURL)).origin
  const seen: Seen = { diagrams: [], images: 0, stores: 0 }
  const session = {
    id: sessionId, title: '그림', kind: 'image', model: 'fixture/image', routingMode: 'manual',
    projectId: null, agentId: null, artifactId: null, pinned: false, messages: [], messageCount: 0,
    made: null, createdAt: at, updatedAt: at,
  }
  const user = {
    id: 'fixture-user', name: 'Media fixture', email: 'fixture@example.test', role: 'user',
    status: 'active', monthlyCredits: 1000, creditsUsed: 0, avatarColor: '#168267', allowedModels: [],
    createdAt: at, preferences: { autoMemory: false, showUsage: false, streamResponses: true },
  }
  const model = (id: string, kinds: string[], modality: string) => ({
    id, label: id, name: id, vendor: 'Fixture', provider: 'fixture', kinds, modality,
    dataBoundary: 'external', creditCost: 1, inputCreditCost: 1, creditPerImage: 4,
    supportsTools: false, contextWindow: 64000,
  })
  await page.context().routeWebSocket('**/*', (socket) => socket.close())
  await page.context().route('**/*', async (route) => {
    const request = route.request()
    const url = new URL(request.url())
    if (url.origin !== origin) return route.abort('blockedbyclient')
    if (!url.pathname.startsWith('/api/')) return route.continue()
    const path = url.pathname.slice(4)
    const method = request.method()
    if (method === 'POST' && path === '/auth/refresh') {
      return route.fulfill({ json: { accessToken: 'fixture-only', expiresIn: 3600, user } })
    }
    if (method === 'GET' && path === '/auth/config') return route.fulfill({ json: {
      brand: { name: 'KloudChat', logo: '' }, enabledKinds: ['chat', 'report', 'image', 'av'],
      privacy: { externalDataGuard: false }, passwordResetEnabled: false, dictationEnabled: false,
    } })
    if (method === 'GET' && path === '/models') return route.fulfill({ json: {
      models: [model('fixture/chat', ['chat', 'report'], 'chat'), model('fixture/image', ['image'], 'image')],
      defaultChatModel: 'fixture/chat',
      defaults: { chat: 'fixture/chat', report: 'fixture/chat', image: 'fixture/image' },
      litellmAvailable: false, autoRouting: { enabled: false, available: false },
    } })
    // The catalogue as the server now ships it: writing 서식 only.
    if (method === 'GET' && path === '/design-templates') return route.fulfill({ json: [minutes] })
    if (method === 'GET' && path === '/credits') return route.fulfill({ json: { monthlyCredits: 1000, creditsUsed: 0, creditsRemaining: 1000 } })
    if (method === 'GET' && path === '/artifacts/counts') return route.fulfill({ json: { counts: {}, total: 0 } })
    if (method === 'POST' && path === '/sessions') return route.fulfill({ json: session })
    if (method === 'GET' && path === `/sessions/${sessionId}`) return route.fulfill({ json: session })
    if (method === 'POST' && path === `/sessions/${sessionId}/diagrams`) {
      seen.diagrams.push(request.postDataJSON())
      if (diagram === 'undrawable') {
        return route.fulfill({ status: 422, json: { detail: 'diagram_not_drawable' } })
      }
      return route.fulfill({ json: {
        source: 'flowchart LR\n  a(요청) --> b(게이트웨이) --> c(모델)', caption: '요청 흐름',
        model: 'fixture/chat', credits: 0,
        artifact: {
          id: 'f1', kind: 'image', title: '요청 흐름', sessionId, projectId: null, version: 1,
          createdAt: at, updatedAt: at,
          data: { kind: 'image', src: '/api/files/f1/content', style: 'figure', figure: 'flow',
            source: 'flowchart LR\n  a(요청) --> b(게이트웨이) --> c(모델)', caption: '요청 흐름',
            renderer: 'pptx', width: 640, height: 360, aspect: '16:9' },
        },
      } })
    }
    // The browser-drawn store route is gone; a call to it would be a regression.
    if (path.endsWith('/diagrams/store')) {
      seen.stores += 1
      return route.fulfill({ status: 404, json: { detail: 'not_found' } })
    }
    if (method === 'POST' && path === `/sessions/${sessionId}/images`) {
      seen.images += 1
      return route.fulfill({ json: [] })
    }
    const shell = shellRead(method, path)
    if (shell) return route.fulfill(shell)
    if (method === 'GET') return route.fulfill({ json: [] })
    return route.abort('blockedbyclient')
  })
  return seen
}

async function shot(page: Page, name: string) {
  if (SHOTS) await page.screenshot({ path: `${SHOTS}/${name}.png` })
}

test('보고서에는 서식 고르기가 있고, 이미지·오디오/동영상에는 없다', async ({ page }, testInfo) => {
  await fixture(page, testInfo)
  await page.goto('/new/report')
  await page.getByRole('button', { name: '서식 고르기' }).first().click()
  const dialog = page.getByRole('dialog')
  await expect(dialog.getByText('회의록', { exact: true })).toBeVisible()
  // No media blanks are promised any more.
  await expect(dialog.getByText('빈칸')).toHaveCount(0)
  // Settle the open animation for the picture.
  await page.waitForTimeout(400)
  await shot(page, 'report-gallery')
  await dialog.getByRole('button', { name: '이 서식으로 시작' }).click()
  await expect(page.getByRole('button', { name: '회의록 서식 해제' })).toBeVisible()

  await page.goto('/new/image')
  await expect(page.getByLabel('프롬프트 입력')).toBeVisible()
  await expect(page.getByRole('button', { name: '서식 고르기' })).toHaveCount(0)
  await shot(page, 'image-composer')

  await page.goto('/new/av')
  await expect(page.getByLabel('프롬프트 입력')).toBeVisible()
  await expect(page.getByRole('button', { name: '서식 고르기' })).toHaveCount(0)
})

test('흐름도 칩은 서버의 도형 그림으로 가고 이미지 모델을 부르지 않는다', async ({ page }, testInfo) => {
  const seen = await fixture(page, testInfo)
  await page.goto('/new/image')
  await page.getByRole('button', { name: /^스타일/ }).click()
  // The 도식 chip (an image-model diagram) is gone; the three figure kinds stand in.
  await expect(page.getByRole('menuitemcheckbox', { name: '도식', exact: true })).toHaveCount(0)
  for (const name of ['구조도', '흐름도', '개념도']) {
    await expect(page.getByRole('menuitemcheckbox', { name, exact: true })).toBeVisible()
  }
  await shot(page, 'image-style-chips')
  await page.getByRole('menuitemcheckbox', { name: '흐름도', exact: true }).click()
  await expect(page.getByText('도식은 글 모델이 씁니다 · 그림 요금이 아닙니다')).toBeVisible()
  await page.getByLabel('프롬프트 입력').fill('요청이 게이트웨이를 거쳐 모델로 간다')
  await page.getByLabel('프롬프트 입력').press('Enter')
  await expect.poll(() => seen.diagrams.length).toBe(1)
  expect(seen.diagrams[0].figure).toBe('flow')
  expect(seen.images).toBe(0)
  // Nothing is drawn or stored by the browser: the server's artifact is the figure, and the
  // turn ends without an error.
  await expect(page.getByRole('button', { name: '전송' })).toBeVisible()
  await expect(page.getByText(/도식을 만들지 못했습니다|그릴 수 없습니다/)).toHaveCount(0)
  expect(seen.stores).toBe(0)
})

test('그릴 수 없는 도식은 그림 없이 안내만 남긴다', async ({ page }, testInfo) => {
  const seen = await fixture(page, testInfo, 'undrawable')
  await page.goto('/new/image')
  await page.getByRole('button', { name: /^스타일/ }).click()
  await page.getByRole('menuitemcheckbox', { name: '개념도', exact: true }).click()
  await page.getByLabel('프롬프트 입력').fill('스무 단계가 넘는 절차')
  await page.getByLabel('프롬프트 입력').press('Enter')
  await expect.poll(() => seen.diagrams.length).toBe(1)
  expect(seen.diagrams[0].figure).toBe('concept')
  await expect(page.getByText('이 도식은 그릴 수 없습니다. 단계를 줄여 다시 시도해 주세요.')).toBeVisible()
  expect(seen.images).toBe(0)
})
