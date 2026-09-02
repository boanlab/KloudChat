import { expect, test } from '@playwright/test'

test('공유된 슬라이드는 Markdown이 아니라 저장된 디자인으로 보인다', async ({ page }) => {
  await page.route('**/api/shared/designed-deck', (route) => route.fulfill({
    json: {
      kind: 'artifact', title: '공유 디자인 덱',
      data: {
        kind: 'deck', theme: '청록', design: { accent: '#0f766e', ink: '#1a1a1a', muted: '#666666', font: 'gothic', visualStyle: 'poster' },
        slides: [
          { id: 's1', layout: 'title', title: '공유에서도 같은 표지', body: '디자인이 사라지지 않는다' },
          { id: 's2', layout: 'bullets', title: '근거', bullets: ['실제 슬라이드 렌더러 사용'] },
        ],
      },
    },
  }))
  await page.goto('/share/designed-deck')
  await expect(page.getByText('공유에서도 같은 표지')).toBeVisible()
  await expect(page.locator('.aspect-video')).toHaveCount(2)
  await expect(page.getByText('디자인이 사라지지 않는다')).toBeVisible()
  await expect(page.getByText('1. 공유에서도 같은 표지')).toHaveCount(0)
})

test('공유된 보고서도 선택한 표지와 절 제목 디자인을 유지한다', async ({ page }) => {
  await page.route('**/api/shared/designed-report', (route) => route.fulfill({
    json: {
      kind: 'artifact', title: '공유 디자인 보고서',
      data: {
        kind: 'report', design: { accent: '#b91c1c', ink: '#1a1a1a', muted: '#666666', font: 'serif', visualStyle: 'poster' },
        sections: [{ id: 'r1', heading: '핵심 결과', status: 'done', level: 1, content: '결과 본문입니다.' }],
        sources: [], citationStyle: 'APA', wordCount: 3,
      },
    },
  }))
  await page.goto('/share/designed-report')
  const cover = page.getByRole('heading', { name: '공유 디자인 보고서', level: 2 }).locator('..')
  await expect(cover).toHaveCSS('color', 'rgb(255, 255, 255)')
  await expect(cover).toHaveCSS('background-image', /linear-gradient/)
  await expect(page.getByRole('heading', { name: '핵심 결과', level: 3 })).toHaveCSS('color', 'rgb(185, 28, 28)')
  await expect(page.getByText('결과 본문입니다.')).toBeVisible()
})
