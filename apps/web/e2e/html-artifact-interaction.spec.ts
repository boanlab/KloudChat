import { randomUUID } from 'node:crypto'
import { readFile, rm } from 'node:fs/promises'
import { pathToFileURL } from 'node:url'
import { expect, test as base, type FrameLocator, type Page } from '@playwright/test'
import { E2E_ADMIN, signIn } from './helpers'

// Deliberately fixed HTML: these tests exercise storage, browser execution and export,
// not a model's ability to write a calculator. The sandbox denies form submission,
// so the calculator uses button events. No generation endpoint is called.
const CALCULATOR = `<!doctype html>
<html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>계산기</title><style>
* { box-sizing: border-box; } body { margin: 0; padding: 20px; font-family: sans-serif; color: #202020; background: white; }
main { width: 100%; max-width: 360px; margin: auto; } label { display: block; margin: 12px 0; }
input, button { width: 100%; min-height: 40px; font: inherit; } button { margin: 4px 0; }
output { display: block; margin-top: 16px; font-size: 24px; }
</style></head><body><main><h1>계산기</h1><form id="calculator">
<label>첫 번째 수<input id="first" type="number" step="any" required></label>
<label>두 번째 수<input id="second" type="number" step="any" required></label>
<button type="button" id="add">더하기</button><button type="reset">초기화</button>
<output aria-label="계산 결과" id="result">0</output></form></main>
<script>
const form = document.getElementById('calculator');
const result = document.getElementById('result');
document.getElementById('add').addEventListener('click', () => {
  const total = Number(document.getElementById('first').value) + Number(document.getElementById('second').value);
  result.textContent = String(Number(total.toPrecision(12)));
});
form.addEventListener('reset', () => { result.textContent = '0'; });
</script></body></html>`

const ISOLATION_PROBE = `<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>Isolation probe</title></head><body>
<output id="boot">not-started</output><button id="probe">Check isolation</button><output id="checks"></output>
<form id="form-probe"><button>Submit check</button><output id="submitted">not-submitted</output></form>
<script>
document.getElementById('boot').textContent = 'ready';
document.getElementById('form-probe').addEventListener('submit', (event) => {
  event.preventDefault(); document.getElementById('submitted').textContent = 'submitted';
});
document.getElementById('probe').addEventListener('click', () => {
  const checks = {};
  for (const [name, attempt] of Object.entries({
    parentDocument: () => parent.document.documentElement.getAttribute('data-html-qa'),
    parentMutation: () => parent.document.documentElement.setAttribute('data-html-qa', 'changed'),
    parentStorage: () => parent.localStorage.getItem('html-qa-marker'),
    frameStorage: () => localStorage.getItem('html-qa-marker'),
  })) {
    try { attempt(); checks[name] = 'allowed'; }
    catch (error) { checks[name] = error.name; }
  }
  document.getElementById('checks').textContent = JSON.stringify(checks);
});
</script></body></html>`

type SeedArtifact = (content: string, label: string) => Promise<{ id: string; title: string }>

const test = base.extend<{ seedArtifact: SeedArtifact }>({
  seedArtifact: async ({ page }, runFixture) => {
    await signIn(page)
    const login = await page.request.post('/api/auth/login', { data: E2E_ADMIN })
    expect(login.ok(), 'fixture account must be active').toBe(true)
    const { accessToken } = await login.json()
    const headers = { Authorization: `Bearer ${accessToken}` }
    const ids: string[] = []
    try {
      await runFixture(async (content, label) => {
        const title = `${label} ${randomUUID()}`
        const response = await page.request.post('/api/artifacts', {
          headers,
          data: { kind: 'html', title, data: { kind: 'html', language: 'html', content } },
        })
        expect(response.status()).toBe(201)
        const artifact = await response.json()
        ids.push(artifact.id)
        return { id: artifact.id as string, title }
      })
    } finally {
      for (const id of ids) {
        const removed = await page.request.delete(`/api/artifacts/${id}`, { headers })
        expect(removed.status(), 'synthetic artifact cleanup').toBe(204)
        const missing = await page.request.get(`/api/artifacts/${id}`, { headers })
        expect(missing.status()).toBe(404)
      }
    }
  },
})

async function openArtifact(page: Page, title: string) {
  await page.goto('/artifacts')
  await page.getByRole('button', { name: `${title} 열기`, exact: true }).click()
  const dialog = page.getByRole('dialog')
  await expect(dialog).toBeVisible()
  await expect(dialog.locator('iframe')).toHaveAttribute('srcdoc', /<!doctype html>/i)
  return dialog
}

async function calculate(surface: FrameLocator | Page) {
  const first = surface.getByLabel('첫 번째 수')
  const second = surface.getByLabel('두 번째 수')
  const result = surface.getByLabel('계산 결과')
  await expect(result).toHaveText('0')
  for (const [left, right, sum] of [['1', '3', '4'], ['1.25', '2.5', '3.75'], ['0.1', '0.2', '0.3']]) {
    await first.fill(left)
    await second.fill(right)
    await surface.getByRole('button', { name: '더하기', exact: true }).click()
    await expect(result).toHaveText(sum)
  }
  await surface.getByRole('button', { name: '초기화', exact: true }).click()
  await expect(first).toHaveValue('')
  await expect(second).toHaveValue('')
  await expect(result).toHaveText('0')
}

for (const viewport of [{ width: 1440, height: 900 }, { width: 390, height: 844 }]) {
  test.describe(`HTML 결과물 ${viewport.width}x${viewport.height}`, () => {
    test.use({ viewport })

    test('계산기는 미리보기·재접속·오프라인 HTML 파일에서 작동한다', async ({ page, browser, seedArtifact }, testInfo) => {
      const artifact = await seedArtifact(CALCULATOR, '계산기 회귀')
      let dialog = await openArtifact(page, artifact.title)
      await calculate(dialog.frameLocator('iframe'))

      // A fresh navigation discards the client store and reads the saved artifact again.
      await page.reload()
      dialog = await openArtifact(page, artifact.title)
      await calculate(dialog.frameLocator('iframe'))
      const frame = dialog.locator('iframe')
      const box = await frame.boundingBox()
      expect(box).not.toBeNull()
      expect(box!.width).toBeGreaterThan(240)
      expect(box!.height).toBeGreaterThan(300)
      expect(box!.x).toBeGreaterThanOrEqual(0)
      expect(box!.x + box!.width).toBeLessThanOrEqual(viewport.width)
      expect(await dialog.frameLocator('iframe').locator('html').evaluate((node) => node.scrollWidth <= innerWidth)).toBe(true)

      const downloadReady = page.waitForEvent('download')
      await dialog.getByRole('button', { name: '내보내기', exact: true }).click()
      await page.getByRole('menuitem', { name: /원본 HTML/ }).click()
      const download = await downloadReady
      expect(download.suggestedFilename()).toMatch(/\.html$/)
      const saved = testInfo.outputPath('calculator.html')
      const offline = await browser.newContext({ offline: true, viewport })
      try {
        await download.saveAs(saved)
        expect(await readFile(saved, 'utf8')).toBe(CALCULATOR)
        const standalone = await offline.newPage()
        await standalone.goto(pathToFileURL(saved).href)
        await calculate(standalone)
        await standalone.reload()
        await calculate(standalone)
      } finally {
        await offline.close()
        await rm(saved, { force: true })
      }
    })

    test('갤러리는 스크립트를 실행하지 않고 전체 미리보기는 부모 화면에서 격리된다', async ({ page, seedArtifact }) => {
      const artifact = await seedArtifact(ISOLATION_PROBE, 'HTML 격리 회귀')
      await page.goto('/artifacts')
      const card = page.getByRole('button', { name: `${artifact.title} 열기`, exact: true })
      const thumbnail = card.locator('iframe')
      await expect(thumbnail).toHaveAttribute('srcdoc', /Check isolation/)
      await expect(thumbnail).toHaveAttribute('sandbox', '')
      await expect(card.frameLocator('iframe').locator('#boot')).toHaveText('not-started')

      await page.evaluate(() => {
        document.documentElement.setAttribute('data-html-qa', 'unchanged')
        localStorage.setItem('html-qa-marker', 'synthetic-marker')
      })
      await card.click()
      const dialog = page.getByRole('dialog')
      await expect(dialog.locator('iframe')).toHaveAttribute('sandbox', 'allow-scripts')
      const preview = dialog.frameLocator('iframe')
      await expect(preview.locator('#boot')).toHaveText('ready')
      await preview.getByRole('button', { name: 'Check isolation' }).click()
      await expect(preview.locator('#checks')).toHaveText(JSON.stringify({
        parentDocument: 'SecurityError',
        parentMutation: 'SecurityError',
        parentStorage: 'SecurityError',
        frameStorage: 'SecurityError',
      }))
      expect(await page.evaluate(() => ({
        document: document.documentElement.getAttribute('data-html-qa'),
        storage: localStorage.getItem('html-qa-marker'),
      }))).toEqual({ document: 'unchanged', storage: 'synthetic-marker' })

      // This is an intentional limitation: a submit-only calculator cannot run here.
      // Do not make the fixture pass by adding allow-forms to the preview sandbox.
      const blockedSubmit = page.waitForEvent('console', (message) =>
        message.text().includes('Blocked form submission') && message.text().includes('allow-forms'))
      await preview.getByRole('button', { name: 'Submit check' }).click()
      await blockedSubmit
      await expect(preview.locator('#submitted')).toHaveText('not-submitted')
    })
  })
}
