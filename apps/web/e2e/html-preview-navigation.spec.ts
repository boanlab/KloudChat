import { randomUUID } from 'node:crypto'
import { readFile } from 'node:fs/promises'
import { expect, test } from '@playwright/test'
import { E2E_ADMIN, signIn } from './helpers'

const BODY = '<a href="#details">Details</a><button id="count" onclick="this.textContent=Number(this.textContent)+1">0</button><div style="height:1400px"></div><h2 id="details">The same document</h2><div style="height:600px"></div>'
const documents = {
  complete: `<!doctype html><html><head><title>Page</title></head><body>${BODY}</body></html>`,
  uppercase: `<!DOCTYPE html><HTML><HEAD><TITLE>Page</TITLE></HEAD><BODY>${BODY}</BODY></HTML>`,
  fragment: BODY,
  existingBase: `<!doctype html><html><head><base href="https://example.invalid/"><title>Page</title></head><body>${BODY}</body></html>`,
  commentedBase: `<!-- <base href="https://example.invalid/"> --><html><head><!-- <base href="https://example.invalid/"> --></head><body>${BODY}</body></html>`,
}

for (const [name, content] of Object.entries(documents)) {
  test(`HTML fragment links keep the preview document: ${name}`, async ({ page }) => {
    await signIn(page)
    const login = await page.request.post('/api/auth/login', { data: E2E_ADMIN })
    expect(login.ok()).toBe(true)
    const { accessToken } = await login.json()
    const headers = { Authorization: `Bearer ${accessToken}` }
    const title = `Preview navigation ${name} ${randomUUID()}`
    const created = await page.request.post('/api/artifacts', {
      headers,
      data: { kind: 'html', title, data: { kind: 'html', language: 'html', content } },
    })
    expect(created.status()).toBe(201)
    const { id } = await created.json()
    const external: string[] = []
    await page.route('https://example.invalid/**', async (route) => {
      external.push(route.request().url())
      await route.abort()
    })
    try {
      for (const viewport of [{ width: 1440, height: 900 }, { width: 390, height: 844 }]) {
        await page.setViewportSize(viewport)
        await page.goto('/artifacts')
        await page.getByRole('button', { name: `${title} 열기`, exact: true }).click()
        const dialog = page.getByRole('dialog')
        const iframe = dialog.locator('iframe')
        await expect(iframe).toHaveAttribute('sandbox', 'allow-scripts')
        const frame = dialog.frameLocator('iframe')
        await frame.locator('#count').click()
        await expect(frame.locator('#count')).toHaveText('1')
        await frame.getByRole('link', { name: 'Details' }).click()
        await expect(frame.getByRole('heading', { name: 'The same document' })).toBeInViewport()
        // Fragment navigation must neither reload the app nor reset this document's state.
        await expect(frame.locator('#count')).toHaveText('1')
        expect(await frame.locator('html').evaluate(() => location.href)).toBe('about:srcdoc#details')
        await expect(page).toHaveURL(/\/artifacts$/)

        const downloaded = page.waitForEvent('download')
        await dialog.getByRole('button', { name: '내보내기', exact: true }).click()
        await page.getByRole('menuitem', { name: /원본 HTML/ }).click()
        const file = await downloaded
        expect(await readFile((await file.path())!, 'utf8')).toBe(content)
        await file.delete()
      }
      expect(external).toEqual([])
      const saved = await page.request.get(`/api/artifacts/${id}`, { headers })
      expect((await saved.json()).data.content).toBe(content)
    } finally {
      const removed = await page.request.delete(`/api/artifacts/${id}`, { headers })
      expect(removed.status()).toBe(204)
    }
  })
}
